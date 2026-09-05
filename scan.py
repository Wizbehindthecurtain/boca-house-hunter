"""Boca House Hunter scanner.

Implements docs/codex-review/2026-09-05-codex-spec.md sections 4-7.
No application I/O happens on import; all behavior is reached through main().
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

import numpy as np
import pandas as pd
import requests
from homeharvest import scrape_property

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

STATE_DIR = Path(__file__).resolve().parent
STATE_PATH = STATE_DIR / "seen.json"
STATE_TMP_PATH = STATE_DIR / "seen.json.tmp"

REQUIRED_COLUMNS = (
    "property_id",
    "listing_id",
    "status",
    "style",
    "city",
    "state",
    "list_price",
    "sqft",
    "hoa_fee",
    "property_url",
)
RESULT_CAP = 10000

PRICE_MIN = Decimal(250000)
PRICE_MAX = Decimal(650000)
SQFT_MIN = Decimal(1700)

STATE_KEYS = ("version", "initialized", "seen", "disabled_webhook_sha256", "discord_not_before")

APP_BUDGET_SECONDS = 150.0
POST_RESERVE_SECONDS = 25.0
MIN_POST_INTERVAL_SECONDS = 0.5
MAX_POST_ATTEMPTS = 3
MAX_SLEEP_SECONDS = 30.0

DISCORD_WEBHOOK_ENV = "DISCORD_WEBHOOK_URL"
DISCORD_WEBHOOK_PATH_RE = re.compile(r"^/api(?:/v10)?/webhooks/([0-9]+)/([A-Za-z0-9._-]+)$")

REALTOR_HOSTS = ("realtor.com", "www.realtor.com")

TITLE_LIMIT = 240
FIELD_VALUE_LIMIT = 512
FOOTER_LIMIT = 200
ADDRESS_COMPONENT_LIMIT = 200
NUMERIC_DISPLAY_LIMIT = 64
DISCORD_TOTAL_TEXT_LIMIT = 6000

_MARKDOWN_CHARS = ("\\", "`", "*", "_", "~", "|", "[", "]", "<", ">")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"\s+")

UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class StateError(Exception):
    """Raised when seen.json is missing, unreadable, or invalid."""


class WebhookConfigError(Exception):
    """Raised when DISCORD_WEBHOOK_URL is absent or invalid in real mode."""


class PayloadError(Exception):
    """Raised when a candidate's Discord payload cannot be built validly."""


class FetchShapeError(Exception):
    """Raised when the scrape result fails whole-result validation."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

logger = logging.getLogger("boca_house_hunter")


def _configure_logging() -> None:
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _utcnow() -> datetime:
    """Wall-clock UTC now, indirected so tests can freeze it deterministically."""
    return datetime.now(timezone.utc)


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    _configure_logging()
    ts = _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = [f"ts={ts}", f"level={logging.getLevelName(level)}", f"event={event}"]
    for key, value in fields.items():
        parts.append(f"{key}={value}")
    logger.log(level, " ".join(parts))


# --------------------------------------------------------------------------
# Scalar / normalization helpers
# --------------------------------------------------------------------------


def _is_missing_or_invalid_scalar(value: Any) -> bool:
    if not pd.api.types.is_scalar(value):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return True


def normalize_identity_component(value: Any) -> Optional[str]:
    """1-64 ASCII digit string, or nonnegative integer scalar. No booleans/floats."""
    if not pd.api.types.is_scalar(value):
        return None
    if _is_missing_or_invalid_scalar(value):
        return None
    if isinstance(value, (bool, np.bool_)):
        return None
    if isinstance(value, (float, np.floating)):
        return None
    if isinstance(value, (int, np.integer)):
        if value < 0:
            return None
        text = str(int(value))
        if set(text) == {"0"}:
            return None
        return text
    if isinstance(value, str):
        text = value.strip()
        if not re.fullmatch(r"[0-9]{1,64}", text):
            return None
        if set(text) == {"0"}:
            return None
        return text
    return None


def normalize_text(value: Any) -> Optional[str]:
    if not pd.api.types.is_scalar(value):
        return None
    if _is_missing_or_invalid_scalar(value):
        return None
    if isinstance(value, (bool, np.bool_)):
        return None
    if isinstance(value, str):
        text = value.strip()
        return text if text else None
    return None


def normalize_number(value: Any) -> Optional[Decimal]:
    """Finite, nonboolean number (or plain numeric string) as a Decimal."""
    if not pd.api.types.is_scalar(value):
        return None
    if _is_missing_or_invalid_scalar(value):
        return None
    if isinstance(value, (bool, np.bool_)):
        return None
    if isinstance(value, (int, np.integer)):
        return Decimal(int(value))
    if isinstance(value, (float, np.floating)):
        if math.isinf(value) or math.isnan(value):
            return None
        try:
            dec = Decimal(str(float(value)))
        except InvalidOperation:
            return None
        return dec if dec.is_finite() else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            dec = Decimal(text)
        except InvalidOperation:
            return None
        return dec if dec.is_finite() else None
    return None


def normalize_property_url(value: Any) -> Optional[str]:
    if not pd.api.types.is_scalar(value):
        return None
    if _is_missing_or_invalid_scalar(value):
        return None
    if isinstance(value, (bool, np.bool_)):
        return None
    text = value if isinstance(value, str) else str(value)
    text = text.strip()
    if not text or len(text) > 2048:
        return None
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    if parts.scheme != "https":
        return None
    if parts.username or parts.password:
        return None
    try:
        if parts.port is not None:
            return None
    except ValueError:
        return None
    hostname = parts.hostname
    if hostname not in REALTOR_HOSTS:
        return None
    if not parts.path:
        return None
    clean = urlunsplit((parts.scheme, parts.hostname, parts.path, "", ""))
    if len(clean) > 2048:
        return None
    return clean


def format_list_date(value: Any) -> str:
    if not pd.api.types.is_scalar(value):
        return "Unknown"
    if _is_missing_or_invalid_scalar(value):
        return "Unknown"
    if isinstance(value, (bool, np.bool_, int, np.integer, float, np.floating)):
        return "Unknown"
    try:
        ts = pd.to_datetime(value, errors="coerce", utc=True)
    except (ValueError, TypeError):
        return "Unknown"
    if ts is None or pd.isna(ts):
        return "Unknown"
    return f"{ts.date().isoformat()} (source)"


def format_optional_nonneg_int(value: Any) -> str:
    dec = normalize_number(value)
    if dec is None:
        return "Unknown"
    if dec < 0 or dec != dec.to_integral_value():
        return "Unknown"
    text = str(int(dec))
    return text if len(text) <= NUMERIC_DISPLAY_LIMIT else "Unknown"


# --------------------------------------------------------------------------
# Text sanitization / truncation for Discord display
# --------------------------------------------------------------------------


def sanitize_text(value: str) -> str:
    text = _CONTROL_RE.sub("", value)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    text = text.replace("@", "＠")
    for ch in _MARKDOWN_CHARS:
        text = text.replace(ch, "\\" + ch)
    return text


def _utf16_len(text: str) -> int:
    length = 0
    for ch in text:
        length += 2 if ord(ch) > 0xFFFF else 1
    return length


def truncate_utf16(text: str, limit: int) -> str:
    if _utf16_len(text) <= limit:
        return text
    target = max(limit - 3, 0)
    kept: list[str] = []
    length = 0
    for ch in text:
        ch_len = 2 if ord(ch) > 0xFFFF else 1
        if length + ch_len > target:
            break
        kept.append(ch)
        length += ch_len
    return "".join(kept) + "..."


def format_price(price: Decimal) -> str:
    quant = price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if quant == quant.to_integral_value():
        text = f"${int(quant):,}"
    else:
        text = f"${quant:,.2f}"
    return text if len(text) <= NUMERIC_DISPLAY_LIMIT else "Unknown"


def format_size(sqft: Decimal) -> str:
    quant = sqft.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = f"{quant:,.2f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    text = f"{text} sq ft"
    return text if len(text) <= NUMERIC_DISPLAY_LIMIT else "Unknown"


def build_address_display(fields: dict) -> str:
    formatted = normalize_text(fields.get("formatted_address"))
    if formatted:
        return sanitize_text(formatted)[:ADDRESS_COMPONENT_LIMIT]
    street = normalize_text(fields.get("full_street_line"))
    if street:
        zip_code = normalize_text(fields.get("zip_code"))
        city_state = f"Boca Raton, FL {zip_code}" if zip_code else "Boca Raton, FL"
        street = sanitize_text(street)[:ADDRESS_COMPONENT_LIMIT]
        city_state = sanitize_text(city_state)[:ADDRESS_COMPONENT_LIMIT]
        return f"{street}, {city_state}"
    return f"Boca Raton, FL — property {fields['property_id']}"


# --------------------------------------------------------------------------
# Row normalization, duplicate grouping, eligibility
# --------------------------------------------------------------------------


@dataclass
class ScanCounts:
    malformed_identity: int = 0
    malformed_required_field: int = 0
    conflicting_duplicate: int = 0
    status_mismatch: int = 0
    style_mismatch: int = 0
    state_mismatch: int = 0
    city_mismatch: int = 0
    price_out_of_range: int = 0
    sqft_out_of_range: int = 0
    hoa_unknown: int = 0
    hoa_nonzero: int = 0
    invalid_url: int = 0
    total_fetched: int = 0
    eligible: int = 0


def _row_get(row: pd.Series, key: str) -> Any:
    return row[key] if key in row.index else None


def _normalize_row(row: pd.Series) -> tuple[Optional[str], Optional[dict], Optional[str]]:
    """Returns (identity_or_None, fields_or_None, malformed_reason_or_None)."""
    property_id = normalize_identity_component(_row_get(row, "property_id"))
    listing_id = normalize_identity_component(_row_get(row, "listing_id"))
    if property_id is None or listing_id is None:
        return None, None, "malformed_identity"
    identity = f"{property_id}:{listing_id}"

    status = normalize_text(_row_get(row, "status"))
    style = normalize_text(_row_get(row, "style"))
    state = normalize_text(_row_get(row, "state"))
    city = normalize_text(_row_get(row, "city"))
    price = normalize_number(_row_get(row, "list_price"))
    sqft = normalize_number(_row_get(row, "sqft"))
    url = normalize_property_url(_row_get(row, "property_url"))

    if None in (status, style, state, city, price, sqft, url):
        return identity, None, "malformed_required_field"

    hoa_value = normalize_number(_row_get(row, "hoa_fee"))
    if hoa_value is None:
        hoa_class = "unknown"
    elif hoa_value == 0:
        hoa_class = "zero"
    else:
        hoa_class = "nonzero"

    fields = {
        "property_id": property_id,
        "listing_id": listing_id,
        "status": status.strip().upper(),
        "style": style.strip().upper(),
        "state": state.strip().upper(),
        "city": city.strip().casefold(),
        "price": price,
        "sqft": sqft,
        "hoa_class": hoa_class,
        "property_url": url,
        "beds": _row_get(row, "beds"),
        "full_baths": _row_get(row, "full_baths"),
        "half_baths": _row_get(row, "half_baths"),
        "list_date": _row_get(row, "list_date"),
        "formatted_address": _row_get(row, "formatted_address"),
        "full_street_line": _row_get(row, "full_street_line"),
        "zip_code": _row_get(row, "zip_code"),
    }
    return identity, fields, None


_AGREEMENT_KEYS = (
    "status",
    "style",
    "state",
    "city",
    "price",
    "sqft",
    "hoa_class",
    "property_url",
)


def _tie_break_key(fields: dict) -> tuple:
    address = build_address_display(fields)
    beds = format_optional_nonneg_int(fields.get("beds"))
    full_baths = format_optional_nonneg_int(fields.get("full_baths"))
    half_baths = format_optional_nonneg_int(fields.get("half_baths"))
    list_date = format_list_date(fields.get("list_date"))
    return (address, beds, full_baths, half_baths, list_date)


def process_dataframe(df: pd.DataFrame) -> tuple[dict[str, dict], ScanCounts]:
    counts = ScanCounts(total_fetched=len(df))
    groups: dict[str, list[dict]] = {}

    for _, row in df.iterrows():
        identity, fields, malformed_reason = _normalize_row(row)
        if malformed_reason == "malformed_identity":
            counts.malformed_identity += 1
            continue
        if malformed_reason == "malformed_required_field":
            counts.malformed_required_field += 1
            continue
        groups.setdefault(identity, []).append(fields)

    eligible: dict[str, dict] = {}
    for identity, rows in groups.items():
        if len(rows) > 1:
            first = rows[0]
            if any(
                any(r[key] != first[key] for key in _AGREEMENT_KEYS) for r in rows[1:]
            ):
                counts.conflicting_duplicate += 1
                continue
            chosen = min(rows, key=_tie_break_key)
        else:
            chosen = rows[0]

        if chosen["status"] != "FOR_SALE":
            counts.status_mismatch += 1
            continue
        if chosen["style"] != "SINGLE_FAMILY":
            counts.style_mismatch += 1
            continue
        if chosen["state"] != "FL":
            counts.state_mismatch += 1
            continue
        if chosen["city"] != "boca raton":
            counts.city_mismatch += 1
            continue
        if not (PRICE_MIN <= chosen["price"] <= PRICE_MAX):
            counts.price_out_of_range += 1
            continue
        if chosen["sqft"] < SQFT_MIN:
            counts.sqft_out_of_range += 1
            continue
        if chosen["hoa_class"] == "unknown":
            counts.hoa_unknown += 1
            continue
        if chosen["hoa_class"] == "nonzero":
            counts.hoa_nonzero += 1
            continue

        eligible[identity] = chosen

    counts.eligible = len(eligible)
    return eligible, counts


# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------


def fetch_listings() -> pd.DataFrame:
    return scrape_property(
        location="Boca Raton, FL",
        listing_type="for_sale",
        property_type=["single_family"],
        sqft_min=1700,
        price_min=250000,
        price_max=650000,
        exclude_pending=True,
        mls_only=False,
        extra_property_data=False,
        return_type="pandas",
        limit=10000,
        offset=0,
        parallel=False,
    )


def validate_fetch_shape(df: Any) -> None:
    if not isinstance(df, pd.DataFrame):
        raise FetchShapeError("scan_indeterminate_empty")
    if df.empty:
        raise FetchShapeError("scan_indeterminate_empty")
    if len(df) >= RESULT_CAP:
        raise FetchShapeError("scan_result_cap")
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise FetchShapeError("scan_indeterminate_empty")


# --------------------------------------------------------------------------
# State loading / atomic persistence
# --------------------------------------------------------------------------


def _validate_state_dict(data: Any) -> dict:
    if not isinstance(data, dict):
        raise StateError("root is not an object")
    if set(data.keys()) != set(STATE_KEYS):
        raise StateError("unexpected keys")
    version = data.get("version")
    if type(version) is not int or version != 1:  # noqa: E721 - must reject bool True/False
        raise StateError("unsupported version")
    if not isinstance(data.get("initialized"), bool):
        raise StateError("initialized must be boolean")

    seen = data.get("seen")
    if not isinstance(seen, list):
        raise StateError("seen must be a list")
    for item in seen:
        if not isinstance(item, str):
            raise StateError("seen entries must be strings")
        parts = item.split(":")
        if len(parts) != 2 or not all(re.fullmatch(r"[0-9]{1,64}", p) for p in parts):
            raise StateError("malformed seen identity")
    if len(seen) != len(set(seen)):
        raise StateError("duplicate seen identity")
    if seen != sorted(seen):
        raise StateError("seen must be sorted")
    if not data["initialized"] and seen:
        raise StateError("uninitialized state must have empty seen")

    digest = data.get("disabled_webhook_sha256")
    if digest is not None:
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise StateError("malformed disabled_webhook_sha256")

    gate = data.get("discord_not_before")
    if gate is not None:
        if not isinstance(gate, str) or not UTC_TIMESTAMP_RE.match(gate):
            raise StateError("malformed discord_not_before")
        try:
            datetime.strptime(gate, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise StateError("invalid discord_not_before") from exc

    return {
        "version": 1,
        "initialized": data["initialized"],
        "seen": list(seen),
        "disabled_webhook_sha256": digest,
        "discord_not_before": gate,
    }


def load_state() -> dict:
    """Load and strictly validate seen.json next to this script.

    Performs no scrape, no send, and no mutation. Raises StateError for any
    missing, unreadable, or invalid state.
    """
    try:
        raw = STATE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise StateError(f"unreadable state: {type(exc).__name__}") from exc

    try:
        data = json.loads(raw, parse_constant=_reject_json_constant)
    except json.JSONDecodeError as exc:
        raise StateError("invalid JSON") from exc

    _reject_duplicate_keys(raw)
    return _validate_state_dict(data)


def _reject_json_constant(name: str) -> None:
    raise StateError(f"forbidden JSON constant: {name}")


def _reject_duplicate_keys(raw: str) -> None:
    def hook(pairs):
        seen_keys = set()
        for key, _ in pairs:
            if key in seen_keys:
                raise StateError("duplicate JSON object key")
            seen_keys.add(key)
        return dict(pairs)

    json.loads(raw, object_pairs_hook=hook, parse_constant=_reject_json_constant)


def save_state(state: dict) -> None:
    payload = {
        "version": 1,
        "initialized": bool(state["initialized"]),
        "seen": sorted(set(state["seen"])),
        "disabled_webhook_sha256": state.get("disabled_webhook_sha256"),
        "discord_not_before": state.get("discord_not_before"),
    }
    text = json.dumps(payload, indent=2, allow_nan=False, sort_keys=False) + "\n"
    with open(STATE_TMP_PATH, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(STATE_TMP_PATH, STATE_PATH)


def _states_equal(a: dict, b: dict) -> bool:
    return (
        bool(a["initialized"]) == bool(b["initialized"])
        and sorted(set(a["seen"])) == sorted(set(b["seen"]))
        and a.get("disabled_webhook_sha256") == b.get("disabled_webhook_sha256")
        and a.get("discord_not_before") == b.get("discord_not_before")
    )


# --------------------------------------------------------------------------
# Webhook validation
# --------------------------------------------------------------------------


def canonicalize_webhook_url(raw: str) -> str:
    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        raise WebhookConfigError("unparseable webhook URL") from exc
    if parts.scheme != "https":
        raise WebhookConfigError("webhook URL must be https")
    if parts.hostname != "discord.com":
        raise WebhookConfigError("webhook URL must be discord.com")
    if parts.username or parts.password:
        raise WebhookConfigError("webhook URL must not contain credentials")
    try:
        if parts.port is not None:
            raise WebhookConfigError("webhook URL must not contain an explicit port")
    except ValueError as exc:
        raise WebhookConfigError("invalid webhook URL port") from exc
    if parts.query or parts.fragment:
        raise WebhookConfigError("webhook URL must not contain query/fragment")
    match = DISCORD_WEBHOOK_PATH_RE.match(parts.path)
    if not match:
        raise WebhookConfigError("webhook URL path is not a valid webhook path")
    webhook_id, webhook_token = match.groups()
    return f"https://discord.com/api/v10/webhooks/{webhook_id}/{webhook_token}"


def get_canonical_webhook_url() -> str:
    raw = os.environ.get(DISCORD_WEBHOOK_ENV)
    if not raw:
        raise WebhookConfigError("DISCORD_WEBHOOK_URL is not set")
    return canonicalize_webhook_url(raw)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def format_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(text: Optional[str]) -> Optional[datetime]:
    if text is None:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def round_up_to_whole_second(dt: datetime) -> datetime:
    if dt.microsecond:
        dt = dt.replace(microsecond=0) + __import__("datetime").timedelta(seconds=1)
    return dt


# --------------------------------------------------------------------------
# Discord payload construction
# --------------------------------------------------------------------------


def build_payload(fields: dict, observed_at: datetime) -> dict:
    try:
        # address is already sanitized by build_address_display; re-sanitizing
        # here would double-escape it, so only truncate the composed title.
        address = build_address_display(fields)
        title = truncate_utf16(f"New match: {address}", TITLE_LIMIT)
        price_display = format_price(fields["price"])
        size_display = format_size(fields["sqft"])
        beds_display = format_optional_nonneg_int(fields.get("beds"))
        full_baths_display = format_optional_nonneg_int(fields.get("full_baths"))
        half_baths_display = format_optional_nonneg_int(fields.get("half_baths"))
        listed_display = format_list_date(fields.get("list_date"))
        # property_id/listing_id are already validated ASCII-digit strings and
        # the separators are our own literal text, so no sanitization needed.
        footer_text = truncate_utf16(
            f"Realtor.com via HomeHarvest | {fields['property_id']}:{fields['listing_id']}",
            FOOTER_LIMIT,
        )

        def field_value(text: str) -> str:
            return truncate_utf16(sanitize_text(text), FIELD_VALUE_LIMIT)

        payload = {
            "username": "Boca House Hunter",
            "allowed_mentions": {"parse": []},
            "embeds": [
                {
                    "title": title,
                    "url": fields["property_url"],
                    "color": 3066993,
                    "fields": [
                        {"name": "Price", "value": field_value(price_display), "inline": True},
                        {"name": "Size", "value": field_value(size_display), "inline": True},
                        {"name": "Beds", "value": field_value(beds_display), "inline": True},
                        {
                            "name": "Baths",
                            "value": field_value(
                                f"{full_baths_display} full / {half_baths_display} half"
                            ),
                            "inline": True,
                        },
                        {
                            "name": "HOA fee",
                            "value": field_value("$0 reported; association status unverified"),
                            "inline": False,
                        },
                        {"name": "Listed", "value": field_value(listed_display), "inline": True},
                    ],
                    "footer": {"text": footer_text},
                    "timestamp": format_utc(observed_at),
                }
            ],
        }
    except Exception as exc:  # noqa: BLE001 - construction failure must not leak partials
        raise PayloadError(f"payload construction failed: {type(exc).__name__}") from exc

    total_text = (
        len(payload["embeds"][0]["title"])
        + sum(len(f["name"]) + len(f["value"]) for f in payload["embeds"][0]["fields"])
        + len(payload["embeds"][0]["footer"]["text"])
    )
    if total_text > DISCORD_TOTAL_TEXT_LIMIT:
        raise PayloadError("payload exceeds Discord aggregate text budget")

    return payload


# --------------------------------------------------------------------------
# Discord delivery
# --------------------------------------------------------------------------


@dataclass
class DeliveryOutcome:
    status: str  # "confirmed", "permanent_failure", "retry_exhausted", "other_failure"
    not_before: Optional[datetime] = None
    http_status: Optional[int] = None


def _parse_retry_after(response: requests.Response) -> Optional[float]:
    candidates: list[float] = []
    header_value = response.headers.get("Retry-After")
    if header_value is not None:
        try:
            value = float(header_value)
            if math.isfinite(value) and value >= 0:
                candidates.append(value)
        except (TypeError, ValueError):
            pass
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        value = body.get("retry_after")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if math.isfinite(value) and value >= 0:
                candidates.append(float(value))
    return max(candidates) if candidates else None


def _parse_rate_limit_reset_after(response: requests.Response) -> Optional[float]:
    remaining = response.headers.get("X-RateLimit-Remaining")
    reset_after = response.headers.get("X-RateLimit-Reset-After")
    if remaining is None or reset_after is None:
        return None
    try:
        remaining_value = float(remaining)
        reset_value = float(reset_after)
    except ValueError:
        return None
    if remaining_value != 0:
        return None
    if not math.isfinite(reset_value) or reset_value < 0:
        return None
    return reset_value


def send_one(session: requests.Session, url: str, payload: dict) -> DeliveryOutcome:
    """Send a single candidate with the bounded 429-retry policy from spec section 6."""
    attempts = 0
    current_payload = payload
    while attempts < MAX_POST_ATTEMPTS:
        attempts += 1
        try:
            response = session.post(
                url,
                params={"wait": "true"},
                json=current_payload,
                timeout=(5, 15),
                allow_redirects=False,
            )
        except requests.RequestException:
            return DeliveryOutcome(status="other_failure")

        if response.status_code == 200:
            try:
                body = response.json()
            except ValueError:
                return DeliveryOutcome(status="other_failure")
            message_id = body.get("id") if isinstance(body, dict) else None
            if isinstance(message_id, str) and re.fullmatch(r"[0-9]+", message_id):
                reset_delay = _parse_rate_limit_reset_after(response)
                not_before = None
                if reset_delay is not None:
                    not_before = round_up_to_whole_second(
                        _utcnow() + _timedelta(reset_delay + 0.25)
                    )
                return DeliveryOutcome(status="confirmed", not_before=not_before, http_status=200)
            return DeliveryOutcome(status="other_failure", http_status=200)

        if response.status_code == 429:
            delay = _parse_retry_after(response)
            if delay is None:
                return DeliveryOutcome(status="other_failure", http_status=429)
            not_before = round_up_to_whole_second(
                _utcnow() + _timedelta(delay + 0.25)
            )
            if attempts >= MAX_POST_ATTEMPTS:
                return DeliveryOutcome(
                    status="retry_exhausted", not_before=not_before, http_status=429
                )
            sleep_seconds = delay + 0.25
            if sleep_seconds > MAX_SLEEP_SECONDS:
                return DeliveryOutcome(
                    status="retry_exhausted", not_before=not_before, http_status=429
                )
            time.sleep(sleep_seconds)
            continue

        if response.status_code in (401, 403, 404):
            return DeliveryOutcome(status="permanent_failure", http_status=response.status_code)

        return DeliveryOutcome(status="other_failure", http_status=response.status_code)

    return DeliveryOutcome(status="retry_exhausted")


def _timedelta(seconds: float):
    from datetime import timedelta

    return timedelta(seconds=seconds)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def _read_dry_run_flag() -> bool:
    raw = os.environ.get("DRY_RUN")
    if raw is None or raw == "0":
        return False
    if raw == "1":
        return True
    raise ValueError(f"invalid DRY_RUN value: {raw!r}")


def _remaining_budget(started: float) -> float:
    return APP_BUDGET_SECONDS - (time.monotonic() - started)


def _save_state_safe(state: dict) -> bool:
    try:
        save_state(state)
        return True
    except (OSError, TypeError, ValueError) as exc:
        log_event("state_write_failed", level=logging.ERROR, error_class=type(exc).__name__)
        return False


def _main_impl() -> int:
    started = time.monotonic()

    try:
        dry_run = _read_dry_run_flag()
    except ValueError as exc:
        log_event("config_invalid", level=logging.ERROR, reason=str(exc))
        return 1

    try:
        state = load_state()
    except StateError as exc:
        log_event("state_invalid", level=logging.ERROR, reason=type(exc).__name__)
        return 1

    webhook_url: Optional[str] = None
    digest: Optional[str] = None
    effective_disabled_digest = state["disabled_webhook_sha256"]
    effective_gate = parse_utc(state["discord_not_before"])

    if not dry_run:
        try:
            webhook_url = get_canonical_webhook_url()
        except WebhookConfigError as exc:
            log_event("webhook_config_invalid", level=logging.ERROR, reason=str(exc))
            return 1
        digest = sha256_hex(webhook_url)

        if effective_disabled_digest == digest:
            log_event("webhook_disabled")
            return 1

        if effective_disabled_digest is not None and effective_disabled_digest != digest:
            effective_disabled_digest = None

        now = _utcnow()
        if effective_gate is not None and now < effective_gate:
            log_event("webhook_backoff", not_before=state["discord_not_before"])
            return 0
        if effective_gate is not None and now >= effective_gate:
            effective_gate = None

    try:
        df = fetch_listings()
    except Exception as exc:  # noqa: BLE001 - any scrape failure must be caught
        log_event("scrape_failed", level=logging.ERROR, error_class=type(exc).__name__)
        return 1

    observed_at = _utcnow()

    try:
        validate_fetch_shape(df)
    except FetchShapeError as exc:
        log_event(exc.reason, level=logging.ERROR)
        return 1

    eligible, counts = process_dataframe(df)
    eligible_identities = sorted(eligible.keys())

    def summary(**extra: Any) -> None:
        log_event(
            "scan_summary",
            total_fetched=counts.total_fetched,
            malformed_identity=counts.malformed_identity,
            malformed_required_field=counts.malformed_required_field,
            conflicting_duplicate=counts.conflicting_duplicate,
            status_mismatch=counts.status_mismatch,
            style_mismatch=counts.style_mismatch,
            state_mismatch=counts.state_mismatch,
            city_mismatch=counts.city_mismatch,
            price_out_of_range=counts.price_out_of_range,
            sqft_out_of_range=counts.sqft_out_of_range,
            hoa_unknown=counts.hoa_unknown,
            hoa_nonzero=counts.hoa_nonzero,
            eligible=counts.eligible,
            elapsed_seconds=round(time.monotonic() - started, 3),
            **extra,
        )

    if not state["initialized"]:
        if dry_run:
            summary(baseline_created=False, would_baseline=len(eligible_identities))
            return 0
        new_state = {
            "initialized": True,
            "seen": eligible_identities,
            "disabled_webhook_sha256": effective_disabled_digest,
            "discord_not_before": format_utc(effective_gate) if effective_gate else None,
        }
        if not _save_state_safe(new_state):
            return 1
        summary(baseline_created=True, would_baseline=len(eligible_identities))
        return 0

    seen_set = set(state["seen"])
    candidates = sorted(identity for identity in eligible_identities if identity not in seen_set)

    if dry_run:
        summary(candidate=len(candidates))
        return 0

    if not candidates:
        current = {
            "initialized": True,
            "seen": state["seen"],
            "disabled_webhook_sha256": effective_disabled_digest,
            "discord_not_before": format_utc(effective_gate) if effective_gate else None,
        }
        if not _states_equal(current, state):
            if not _save_state_safe(current):
                return 1
        summary(candidate=0, confirmed=0, unsent=0)
        return 0

    try:
        payloads = {identity: build_payload(eligible[identity], observed_at) for identity in candidates}
    except PayloadError as exc:
        log_event("payload_invalid", level=logging.ERROR, reason=str(exc))
        return 1

    session = requests.Session()
    confirmed = 0
    working_seen = set(state["seen"])
    working_disabled = effective_disabled_digest
    working_gate = effective_gate

    for position, identity in enumerate(candidates):
        remaining = _remaining_budget(started)
        if remaining < POST_RESERVE_SECONDS:
            log_event("budget_exhausted", level=logging.ERROR, candidate=identity)
            return 1

        outcome = send_one(session, webhook_url, payloads[identity])

        if outcome.status == "confirmed":
            working_seen.add(identity)
            working_gate = outcome.not_before if outcome.not_before else None
            if not _save_state_safe(
                {
                    "initialized": True,
                    "seen": sorted(working_seen),
                    "disabled_webhook_sha256": working_disabled,
                    "discord_not_before": format_utc(working_gate) if working_gate else None,
                }
            ):
                return 1
            confirmed += 1
            log_event("delivered", identity=identity, http_status=outcome.http_status)

            is_last = position == len(candidates) - 1
            if not is_last:
                remaining = _remaining_budget(started)
                wait_seconds = MIN_POST_INTERVAL_SECONDS
                if working_gate is not None:
                    gate_wait = (working_gate - _utcnow()).total_seconds()
                    wait_seconds = max(wait_seconds, gate_wait)
                if wait_seconds > MAX_SLEEP_SECONDS or remaining < wait_seconds + POST_RESERVE_SECONDS:
                    log_event("budget_exhausted_before_sleep", level=logging.ERROR)
                    return 1
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
            continue

        if outcome.status == "permanent_failure":
            saved = _save_state_safe(
                {
                    "initialized": True,
                    "seen": sorted(working_seen),
                    "disabled_webhook_sha256": sha256_hex(webhook_url),
                    "discord_not_before": format_utc(working_gate) if working_gate else None,
                }
            )
            if saved:
                log_event(
                    "webhook_permanent_failure",
                    level=logging.ERROR,
                    http_status=outcome.http_status,
                )
            return 1

        if outcome.status == "retry_exhausted" and outcome.not_before is not None:
            working_gate = outcome.not_before
            saved = _save_state_safe(
                {
                    "initialized": True,
                    "seen": sorted(working_seen),
                    "disabled_webhook_sha256": working_disabled,
                    "discord_not_before": format_utc(working_gate),
                }
            )
            if saved:
                log_event("rate_limited", level=logging.ERROR, identity=identity)
            return 1

        log_event("delivery_failed", level=logging.ERROR, identity=identity, http_status=outcome.http_status)
        return 1

    summary(candidate=len(candidates), confirmed=confirmed, unsent=len(candidates) - confirmed)
    return 0


def main() -> int:
    try:
        return _main_impl()
    except Exception as exc:  # noqa: BLE001 - last-resort safety net per spec section 7
        log_event("unexpected_error", level=logging.ERROR, error_class=type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
