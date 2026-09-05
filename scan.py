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
from decimal import ROUND_HALF_UP, Context, Decimal, InvalidOperation
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

# Used for Decimal.quantize() in price/size formatting instead of the ambient
# default context (28 significant digits): that default can raise
# InvalidOperation for large-but-legitimately-displayable values (e.g. 1e26),
# which must not be conflated with genuine display overflow -- the
# NUMERIC_DISPLAY_LIMIT length check that follows is what actually decides
# "Unknown", not the Decimal context's precision.
_QUANTIZE_CONTEXT = Context(prec=1000)

_MARKDOWN_CHARS = ("\\", "`", "*", "_", "~", "|", "[", "]", "<", ">")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
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
        try:
            text = str(int(value))
        except ValueError:
            # Python's int-to-str conversion has a digit-count limit
            # (sys.get_int_max_str_digits); an oversized magnitude must be
            # rejected as a malformed identity, not crash normalization.
            return None
        if len(text) > 64:
            return None
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
    """Finite, nonboolean number (or plain numeric string) as a Decimal.

    Type-checked explicitly rather than gated on pd.api.types.is_scalar():
    that check does not reliably recognize decimal.Decimal as scalar, which
    would otherwise silently reject valid finite Decimal inputs.
    """
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
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            dec = Decimal(text)
        except InvalidOperation:
            return None
        return dec if dec.is_finite() else None
    # Anything else (None, pandas NA/NaT, lists, timestamps, ...) is missing/unsupported.
    return None


def _authority_has_forbidden_syntax(netloc: str) -> bool:
    """True if netloc carries any userinfo ("@...") or port ("...:...") syntax.

    Checked against the raw netloc rather than parts.username/.password/.port:
    those properties can return None for edge cases like an explicitly empty
    userinfo ("@host") or an explicitly empty port ("host:"), silently
    letting forbidden syntax through.
    """
    return "@" in netloc or ":" in netloc


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
    if _authority_has_forbidden_syntax(parts.netloc):
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
    try:
        text = str(int(dec))
    except (ValueError, OverflowError):
        # Extremely large magnitudes can exceed Python's int-to-str digit
        # limit (or otherwise fail to convert); treat as display-unknown
        # rather than raising out of a single row's formatting.
        return "Unknown"
    return text if len(text) <= NUMERIC_DISPLAY_LIMIT else "Unknown"


# --------------------------------------------------------------------------
# Text sanitization / truncation for Discord display
# --------------------------------------------------------------------------


def sanitize_text(value: str) -> str:
    # Whitespace collapse runs before control-char removal: \s already covers
    # \t/\n/\r, so collapsing first turns them into a single space; removing
    # remaining (non-whitespace) control chars afterward cannot then fuse
    # words that were only separated by a control character.
    text = _WHITESPACE_RE.sub(" ", value).strip()
    text = _CONTROL_RE.sub("", text)
    # Second collapse/strip: removing controls can itself leave whitespace
    # behind (e.g. "\x07 \x07" -> " " after control removal), which must not
    # survive as a truthy "just a space" result -- otherwise a caller's
    # nonempty-string fallback check would wrongly treat it as real content.
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
    # Decimal count is decided by the ORIGINAL value's integrality, not the
    # post-rounding result: e.g. 400000.001 is non-integral and must display
    # two decimals even though it rounds to a whole dollar amount.
    is_integral = price == price.to_integral_value()
    try:
        if is_integral:
            text = f"${int(price):,}"
        else:
            quant = price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP, context=_QUANTIZE_CONTEXT)
            text = f"${quant:,.2f}"
    except (InvalidOperation, OverflowError, ValueError):
        return "Unknown"
    return text if len(text) <= NUMERIC_DISPLAY_LIMIT else "Unknown"


def format_size(sqft: Decimal) -> str:
    try:
        quant = sqft.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP, context=_QUANTIZE_CONTEXT)
        text = f"{quant:,.2f}"
    except (InvalidOperation, OverflowError, ValueError):
        return "Unknown"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    text = f"{text} sq ft"
    return text if len(text) <= NUMERIC_DISPLAY_LIMIT else "Unknown"


def build_address_display(fields: dict) -> str:
    # Emptiness is checked AFTER sanitization/truncation, not before: a value
    # that is nonempty pre-sanitization (e.g. a lone control character) can
    # sanitize down to nothing, and must fall through to the next source
    # rather than produce an empty address component.
    formatted = normalize_text(fields.get("formatted_address"))
    if formatted:
        sanitized = truncate_utf16(sanitize_text(formatted), ADDRESS_COMPONENT_LIMIT)
        if sanitized:
            return sanitized
    street = normalize_text(fields.get("full_street_line"))
    if street:
        zip_code = normalize_text(fields.get("zip_code"))
        city_state = f"Boca Raton, FL {zip_code}" if zip_code else "Boca Raton, FL"
        street_sanitized = truncate_utf16(sanitize_text(street), ADDRESS_COMPONENT_LIMIT)
        city_state_sanitized = truncate_utf16(sanitize_text(city_state), ADDRESS_COMPONENT_LIMIT)
        if street_sanitized:
            return f"{street_sanitized}, {city_state_sanitized}"
    return f"Boca Raton, FL — property {fields['property_id']}"


# --------------------------------------------------------------------------
# Row normalization, duplicate grouping, eligibility
# --------------------------------------------------------------------------


@dataclass
class ScanCounts:
    malformed_identity: int = 0
    malformed_required_field: int = 0
    duplicate_group: int = 0
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


def _normalize_row(row: pd.Series) -> tuple[Optional[str], str, Optional[dict]]:
    """Returns (identity_or_None, kind, fields_or_None).

    kind is "malformed_identity" (identity is None), "malformed_required"
    (identity present, a required field failed to normalize), or "valid".
    A malformed-required row still carries its identity so it can be grouped
    with any same-identity sibling rather than silently discarded before
    duplicate/conflict resolution.
    """
    property_id = normalize_identity_component(_row_get(row, "property_id"))
    listing_id = normalize_identity_component(_row_get(row, "listing_id"))
    if property_id is None or listing_id is None:
        return None, "malformed_identity", None
    identity = f"{property_id}:{listing_id}"

    status = normalize_text(_row_get(row, "status"))
    style = normalize_text(_row_get(row, "style"))
    state = normalize_text(_row_get(row, "state"))
    city = normalize_text(_row_get(row, "city"))
    price = normalize_number(_row_get(row, "list_price"))
    sqft = normalize_number(_row_get(row, "sqft"))
    url = normalize_property_url(_row_get(row, "property_url"))

    if None in (status, style, state, city, price, sqft, url):
        return identity, "malformed_required", None

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
        "hoa_value": hoa_value,
        "property_url": url,
        "beds": _row_get(row, "beds"),
        "full_baths": _row_get(row, "full_baths"),
        "half_baths": _row_get(row, "half_baths"),
        "list_date": _row_get(row, "list_date"),
        "formatted_address": _row_get(row, "formatted_address"),
        "full_street_line": _row_get(row, "full_street_line"),
        "zip_code": _row_get(row, "zip_code"),
    }
    return identity, "valid", fields


# hoa_value (the actual normalized fee, or None) is compared here rather than
# hoa_class: two nonzero fees of different amounts (or an unknown vs. an
# explicit zero) must count as a real disagreement, not silently "agree"
# because they share the same three-way bucket.
_AGREEMENT_KEYS = (
    "status",
    "style",
    "state",
    "city",
    "price",
    "sqft",
    "hoa_value",
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
    groups: dict[str, list[tuple[str, Optional[dict]]]] = {}

    for idx, row in df.iterrows():
        identity, kind, fields = _normalize_row(row)
        if kind == "malformed_identity":
            counts.malformed_identity += 1
            log_event("malformed_identity_row", level=logging.WARNING, row_index=idx)
            continue
        if kind == "malformed_required":
            counts.malformed_required_field += 1
            log_event("malformed_required_field_row", level=logging.WARNING, row_index=idx)
        groups.setdefault(identity, []).append((kind, fields))

    eligible: dict[str, dict] = {}
    for identity, members in groups.items():
        # A malformed-required sibling must not be silently hidden behind an
        # otherwise-qualifying row for the same identity: treat the pairing
        # itself as a conflicting duplicate rather than letting the valid
        # member through alone. A lone malformed row (no sibling) is already
        # accounted for above and simply produces nothing eligible.
        if any(kind == "malformed_required" for kind, _ in members):
            if len(members) > 1:
                counts.conflicting_duplicate += 1
            continue

        rows = [fields for _, fields in members]
        if len(rows) > 1:
            first = rows[0]
            if any(
                any(r[key] != first[key] for key in _AGREEMENT_KEYS) for r in rows[1:]
            ):
                counts.conflicting_duplicate += 1
                continue
            # Agreeing duplicates are a distinct, required accounting bucket
            # from conflicting ones -- otherwise an identical-duplicate group
            # collapses into the eligible count with no record it existed.
            counts.duplicate_group += 1
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
        if len(parts) != 2 or not all(
            re.fullmatch(r"[0-9]{1,64}", p) and set(p) != {"0"} for p in parts
        ):
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
    except (OSError, UnicodeDecodeError) as exc:
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
    if _authority_has_forbidden_syntax(parts.netloc):
        raise WebhookConfigError("webhook URL must not contain credentials or an explicit port")
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

    embed = payload["embeds"][0]

    # Validate the ACTUAL final strings against the project's own per-field
    # budgets, independent of trusting that the construction helpers above
    # (truncate_utf16 etc.) enforced them correctly -- this is deliberately
    # a second, independent check on the finished payload, not a restatement
    # of the truncation logic.
    if _utf16_len(embed["title"]) > TITLE_LIMIT:
        raise PayloadError("title exceeds project budget")
    for f in embed["fields"]:
        if _utf16_len(f["value"]) > FIELD_VALUE_LIMIT:
            raise PayloadError("field value exceeds project budget")
    if _utf16_len(embed["footer"]["text"]) > FOOTER_LIMIT:
        raise PayloadError("footer exceeds project budget")

    total_text = (
        _utf16_len(embed["title"])
        + sum(_utf16_len(f["name"]) + _utf16_len(f["value"]) for f in embed["fields"])
        + _utf16_len(embed["footer"]["text"])
    )
    if total_text > DISCORD_TOTAL_TEXT_LIMIT:
        raise PayloadError("payload exceeds Discord aggregate text budget")

    return payload


# --------------------------------------------------------------------------
# Discord delivery
# --------------------------------------------------------------------------


@dataclass
class PostResult:
    # "confirmed": 200 + valid id, not_before set only if a valid exhausted-
    #   bucket delay was present.
    # "confirmed_unknown_exhaustion": 200 + valid id, but X-RateLimit-Remaining
    #   parsed to zero with no valid Reset-After delay to build a gate from
    #   (either absent, or a delay value that exists but can't be turned into
    #   a usable deadline -- see _safe_not_before). The message WAS delivered,
    #   so the identity is still confirmed, but the caller must stop the
    #   batch rather than guess or invent a bucket duration.
    # "rate_limited": 429; not_before/delay_seconds set only if a valid,
    #   representable delay was found.
    # "permanent_failure": 401/403/404.
    # "other_failure": anything else (malformed confirmation, timeouts, 5xx,
    #   400, connection errors, ...).
    kind: str
    not_before: Optional[datetime] = None
    # Same delay (in seconds, including the +0.25 pad) used to derive
    # not_before, captured separately so the caller can anchor an in-process
    # monotonic deadline to it. Monotonic tracking is immune to wall-clock
    # jumps in either direction, which a UTC-only gate is not: this is what
    # lets the delivery loop honor "both the monotonic delay and the saved
    # UTC gate" rather than just the persisted one.
    delay_seconds: Optional[float] = None
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


def _remaining_is_exhausted(response: requests.Response) -> bool:
    remaining = response.headers.get("X-RateLimit-Remaining")
    if remaining is None:
        return False
    try:
        return float(remaining) == 0
    except ValueError:
        return False


def _parse_reset_after(response: requests.Response) -> Optional[float]:
    reset_after = response.headers.get("X-RateLimit-Reset-After")
    if reset_after is None:
        return None
    try:
        value = float(reset_after)
    except ValueError:
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def _safe_not_before(delay: float) -> Optional[datetime]:
    """Turn a delay (seconds) into a UTC deadline, or None if the arithmetic
    can't be represented (e.g. an absurd server-supplied value would overflow
    datetime's year-9999 ceiling). An unrepresentable delay must be treated
    as an unusable/invalid one, never clamped or guessed at, per spec."""
    try:
        return round_up_to_whole_second(_utcnow() + _timedelta(delay + 0.25))
    except (OverflowError, ValueError, OSError):
        return None


def post_once(session: requests.Session, url: str, payload: dict) -> PostResult:
    """Perform exactly one POST and classify the response. No sleeping, no
    retry loop, no state mutation: retry/gate/budget/state decisions are the
    caller's responsibility so they can be made durable before another wait
    or POST happens (spec section 6)."""
    try:
        response = session.post(
            url,
            params={"wait": "true"},
            json=payload,
            timeout=(5, 15),
            allow_redirects=False,
        )
    except requests.RequestException:
        return PostResult(kind="other_failure")

    if response.status_code == 200:
        try:
            body = response.json()
        except ValueError:
            return PostResult(kind="other_failure", http_status=200)
        message_id = body.get("id") if isinstance(body, dict) else None
        if not (isinstance(message_id, str) and re.fullmatch(r"[0-9]+", message_id)):
            return PostResult(kind="other_failure", http_status=200)

        if _remaining_is_exhausted(response):
            reset_delay = _parse_reset_after(response)
            not_before = None if reset_delay is None else _safe_not_before(reset_delay)
            if not_before is None:
                # Either no reset delay was supplied, or it existed but could
                # not be turned into a usable deadline (see _safe_not_before).
                # Either way the message was still delivered: confirm it, but
                # give the caller no gate to persist and let it stop the
                # batch rather than invent one.
                return PostResult(kind="confirmed_unknown_exhaustion", http_status=200)
            return PostResult(
                kind="confirmed",
                not_before=not_before,
                delay_seconds=reset_delay + 0.25,
                http_status=200,
            )
        return PostResult(kind="confirmed", not_before=None, http_status=200)

    if response.status_code == 429:
        delay = _parse_retry_after(response)
        not_before = None if delay is None else _safe_not_before(delay)
        if not_before is None:
            # Same treatment as an absent delay: an unrepresentable delay
            # must never be clamped or guessed at to authorize an early
            # retry.
            return PostResult(kind="rate_limited", not_before=None, http_status=429)
        return PostResult(
            kind="rate_limited",
            not_before=not_before,
            delay_seconds=delay + 0.25,
            http_status=429,
        )

    if response.status_code in (401, 403, 404):
        return PostResult(kind="permanent_failure", http_status=response.status_code)

    return PostResult(kind="other_failure", http_status=response.status_code)


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


def _await_gate(
    monotonic_deadline: Optional[float],
    utc_gate: Optional[datetime],
    started: float,
) -> bool:
    """Sleep, possibly in more than one increment, until BOTH the in-process
    monotonic deadline and the persisted UTC gate have passed, honoring the
    overall app budget and the single-sleep bound throughout.

    Two independent clocks are checked because either one alone can be
    fooled: a UTC-only check can be satisfied early if the wall clock jumps
    forward (or was never advancing in lockstep with real time), while a
    monotonic-only check has no persisted meaning across runs. Rechecking
    both after every sleep (rather than sleeping once and trusting a single
    up-front computation) protects against the wall clock moving backward
    mid-sleep, which would otherwise let a stale computed sleep duration
    authorize an early retry.

    Returns False (without having necessarily waited long enough) if the
    required wait would exceed the single-sleep bound or the remaining
    execution budget -- the caller treats that as budget exhaustion.
    """
    for _ in range(6):  # bounded: real sleeps make each iteration converge
        mono_wait = 0.0 if monotonic_deadline is None else max(0.0, monotonic_deadline - time.monotonic())
        utc_wait = 0.0 if utc_gate is None else max(0.0, (utc_gate - _utcnow()).total_seconds())
        wait = max(mono_wait, utc_wait)
        if wait <= 0:
            return True
        if wait > MAX_SLEEP_SECONDS:
            return False
        if _remaining_budget(started) < wait + POST_RESERVE_SECONDS:
            return False
        time.sleep(wait)
    return False


def _save_state_safe(state: dict) -> bool:
    try:
        save_state(state)
        return True
    except (OSError, TypeError, ValueError) as exc:
        log_event("state_write_failed", level=logging.ERROR, error_class=type(exc).__name__)
        return False


_SUMMARY_FIELD_NAMES = (
    "total_fetched",
    "malformed_identity",
    "malformed_required_field",
    "duplicate_group",
    "conflicting_duplicate",
    "status_mismatch",
    "style_mismatch",
    "state_mismatch",
    "city_mismatch",
    "price_out_of_range",
    "sqft_out_of_range",
    "hoa_unknown",
    "hoa_nonzero",
    "eligible",
    "already_seen",
    "candidate",
    "confirmed",
    "unsent",
    "baseline_created",
)


def _main_impl() -> int:
    started = time.monotonic()
    # Populated as each stage runs; a field stays None if that stage was
    # never reached, so the final summary always reports what actually
    # happened rather than inventing zeros for unattempted work.
    summary_fields: dict[str, Any] = {name: None for name in _SUMMARY_FIELD_NAMES}
    summary_fields["baseline_created"] = False
    # Updated as each stage begins; read by the outer except below so an
    # unanticipated exception's log line identifies roughly where in the
    # pipeline it happened, not just the wrapper that caught it.
    phase = "startup"

    def finish(code: int) -> int:
        log_event(
            "scan_summary",
            elapsed_seconds=round(time.monotonic() - started, 3),
            **summary_fields,
        )
        return code

    try:
        phase = "config"
        try:
            dry_run = _read_dry_run_flag()
        except ValueError as exc:
            log_event("config_invalid", level=logging.ERROR, reason=str(exc))
            return finish(1)

        phase = "state_load"
        try:
            state = load_state()
        except StateError as exc:
            log_event("state_invalid", level=logging.ERROR, reason=type(exc).__name__)
            return finish(1)

        webhook_url: Optional[str] = None
        effective_disabled_digest = state["disabled_webhook_sha256"]
        effective_gate = parse_utc(state["discord_not_before"])

        if not dry_run:
            phase = "webhook_config"
            try:
                webhook_url = get_canonical_webhook_url()
            except WebhookConfigError as exc:
                log_event("webhook_config_invalid", level=logging.ERROR, reason=str(exc))
                return finish(1)
            digest = sha256_hex(webhook_url)

            if effective_disabled_digest == digest:
                log_event("webhook_disabled")
                return finish(1)

            if effective_disabled_digest is not None and effective_disabled_digest != digest:
                effective_disabled_digest = None

            now = _utcnow()
            if effective_gate is not None and now < effective_gate:
                log_event("webhook_backoff", not_before=state["discord_not_before"])
                return finish(0)
            if effective_gate is not None and now >= effective_gate:
                effective_gate = None

        phase = "fetch"
        try:
            df = fetch_listings()
        except Exception as exc:  # noqa: BLE001 - any scrape failure must be caught
            log_event("scrape_failed", level=logging.ERROR, error_class=type(exc).__name__)
            return finish(1)

        observed_at = _utcnow()
        if isinstance(df, pd.DataFrame):
            # Recorded even if shape validation below rejects the result, so
            # an empty/capped/malformed-column failure still reports how
            # many rows were actually returned.
            summary_fields["total_fetched"] = len(df)

        phase = "validate_shape"
        try:
            validate_fetch_shape(df)
        except FetchShapeError as exc:
            log_event(exc.reason, level=logging.ERROR)
            return finish(1)

        phase = "normalize"
        eligible, counts = process_dataframe(df)
        eligible_identities = sorted(eligible.keys())
        summary_fields.update(
            total_fetched=counts.total_fetched,
            malformed_identity=counts.malformed_identity,
            malformed_required_field=counts.malformed_required_field,
            duplicate_group=counts.duplicate_group,
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
        )

        if not state["initialized"]:
            phase = "baseline"
            try:
                _build_all_payloads(eligible, eligible_identities, observed_at)
            except PayloadError as exc:
                log_event("payload_invalid", level=logging.ERROR, reason=str(exc))
                return finish(1)
            # Baseline sends nothing by design, in both dry and real mode,
            # and there is no prior seen set for it to compare against.
            summary_fields.update(
                candidate=len(eligible_identities), already_seen=0, confirmed=0, unsent=0
            )
            if dry_run:
                return finish(0)
            new_state = {
                "initialized": True,
                "seen": eligible_identities,
                "disabled_webhook_sha256": effective_disabled_digest,
                "discord_not_before": format_utc(effective_gate) if effective_gate else None,
            }
            if not _save_state_safe(new_state):
                return finish(1)
            summary_fields["baseline_created"] = True
            log_event("baseline_created", eligible_count=len(eligible_identities))
            return finish(0)

        seen_set = set(state["seen"])
        candidates = sorted(identity for identity in eligible_identities if identity not in seen_set)
        # The eligible/seen OVERLAP, not the total historical seen-set size:
        # an unrelated large seen history must not be reported as if it were
        # relevant to this fetch's eligible rows.
        summary_fields["already_seen"] = len(eligible_identities) - len(candidates)
        summary_fields["candidate"] = len(candidates)

        if dry_run:
            phase = "dry_run_candidates"
            try:
                _build_all_payloads(eligible, candidates, observed_at)
            except PayloadError as exc:
                log_event("payload_invalid", level=logging.ERROR, reason=str(exc))
                return finish(1)
            summary_fields.update(confirmed=0, unsent=len(candidates))
            return finish(0)

        if not candidates:
            phase = "no_candidate_recovery"
            current = {
                "initialized": True,
                "seen": state["seen"],
                "disabled_webhook_sha256": effective_disabled_digest,
                "discord_not_before": format_utc(effective_gate) if effective_gate else None,
            }
            if not _states_equal(current, state):
                if not _save_state_safe(current):
                    return finish(1)
            summary_fields.update(confirmed=0, unsent=0)
            return finish(0)

        phase = "payload_construction"
        try:
            payloads = _build_all_payloads(eligible, candidates, observed_at)
        except PayloadError as exc:
            log_event("payload_invalid", level=logging.ERROR, reason=str(exc))
            return finish(1)

        phase = "delivery"
        session = requests.Session()
        confirmed = 0
        working_seen = set(state["seen"])
        working_disabled = effective_disabled_digest
        working_gate = effective_gate
        # In-process only, never persisted: anchors the required wait to a
        # clock immune to wall-clock jumps. See _await_gate.
        working_monotonic_deadline: Optional[float] = None

        def save_current(**overrides: Any) -> bool:
            payload = {
                "initialized": True,
                "seen": sorted(working_seen),
                "disabled_webhook_sha256": working_disabled,
                "discord_not_before": format_utc(working_gate) if working_gate else None,
            }
            payload.update(overrides)
            return _save_state_safe(payload)

        for position, identity in enumerate(candidates):
            attempts = 0
            delivered = False
            while True:
                attempts += 1
                remaining = _remaining_budget(started)
                if remaining < POST_RESERVE_SECONDS:
                    log_event("budget_exhausted", level=logging.ERROR, candidate=identity)
                    summary_fields.update(confirmed=confirmed, unsent=len(candidates) - confirmed)
                    return finish(1)

                # Honor both the monotonic delay and the persisted UTC gate
                # before every attempt, not just the first one for this
                # candidate: this is what makes a 429 retry (and the
                # inter-message pacing after a success, below) both wait
                # correctly even if the wall clock misbehaves mid-run.
                if not _await_gate(working_monotonic_deadline, working_gate, started):
                    log_event(
                        "budget_exhausted_before_sleep", level=logging.ERROR, identity=identity
                    )
                    summary_fields.update(confirmed=confirmed, unsent=len(candidates) - confirmed)
                    return finish(1)

                result = post_once(session, webhook_url, payloads[identity])

                if result.kind in ("confirmed", "confirmed_unknown_exhaustion"):
                    working_seen.add(identity)
                    if result.kind == "confirmed":
                        working_gate = result.not_before
                        working_monotonic_deadline = (
                            time.monotonic() + result.delay_seconds
                            if result.delay_seconds is not None
                            else None
                        )
                    # else: unknown-exhaustion carries no new gate information;
                    # the identity is still durably confirmed before stopping.
                    save_ok = save_current()
                    # The remote message WAS confirmed delivered regardless of
                    # whether the local save just above succeeded: "confirmed"
                    # reflects that delivery fact, with persistence failure
                    # surfaced separately via state_write_failed, so a save
                    # failure right after a real confirmation doesn't leave
                    # the summary claiming nothing happened.
                    confirmed += 1
                    log_event("delivered", identity=identity, http_status=result.http_status)
                    if not save_ok:
                        summary_fields.update(confirmed=confirmed, unsent=len(candidates) - confirmed)
                        return finish(1)
                    delivered = True

                    if result.kind == "confirmed_unknown_exhaustion":
                        log_event(
                            "rate_limit_exhaustion_unknown", level=logging.ERROR, identity=identity
                        )
                        summary_fields.update(confirmed=confirmed, unsent=len(candidates) - confirmed)
                        return finish(1)
                    break

                if result.kind == "rate_limited":
                    if result.not_before is None:
                        log_event("rate_limit_invalid_delay", level=logging.ERROR, identity=identity)
                        summary_fields.update(confirmed=confirmed, unsent=len(candidates) - confirmed)
                        return finish(1)
                    working_gate = result.not_before
                    working_monotonic_deadline = (
                        time.monotonic() + result.delay_seconds
                        if result.delay_seconds is not None
                        else None
                    )
                    if not save_current():
                        return finish(1)
                    if attempts >= MAX_POST_ATTEMPTS:
                        log_event("rate_limited_exhausted", level=logging.ERROR, identity=identity)
                        summary_fields.update(confirmed=confirmed, unsent=len(candidates) - confirmed)
                        return finish(1)
                    # Loop back to the top: the next iteration's _await_gate
                    # call is what actually waits for the gate just set.
                    continue

                if result.kind == "permanent_failure":
                    saved = save_current(disabled_webhook_sha256=sha256_hex(webhook_url))
                    if saved:
                        log_event(
                            "webhook_permanent_failure", level=logging.ERROR, http_status=result.http_status
                        )
                    summary_fields.update(confirmed=confirmed, unsent=len(candidates) - confirmed)
                    return finish(1)

                log_event(
                    "delivery_failed", level=logging.ERROR, identity=identity, http_status=result.http_status
                )
                summary_fields.update(confirmed=confirmed, unsent=len(candidates) - confirmed)
                return finish(1)

            is_last = position == len(candidates) - 1
            if delivered and not is_last:
                # Ordinary inter-message pacing (0.5s minimum) is purely an
                # in-process concern -- there is nothing to persist here
                # unless a rate-limit response already set a gate above. The
                # actual wait happens via the next iteration's top-of-loop
                # _await_gate call, which also enforces budget/bound checks
                # consistently in one place.
                pacing_deadline = time.monotonic() + MIN_POST_INTERVAL_SECONDS
                working_monotonic_deadline = (
                    pacing_deadline
                    if working_monotonic_deadline is None
                    else max(working_monotonic_deadline, pacing_deadline)
                )

        summary_fields.update(confirmed=confirmed, unsent=len(candidates) - confirmed)
        return finish(0)
    except Exception as exc:  # noqa: BLE001 - unexpected-bug safety net (spec section 7)
        # Caught here (not just in main()) so an unanticipated exception
        # still produces a complete scan_summary reflecting whatever was
        # actually observed before the failure, with a phase name
        # identifying roughly where in the pipeline it happened.
        log_event("unexpected_error", level=logging.ERROR, error_class=type(exc).__name__, phase=phase)
        return finish(1)


def _build_all_payloads(
    eligible: dict[str, dict], identities: list[str], observed_at: datetime
) -> dict[str, dict]:
    """Build and validate every candidate payload up front (spec section 6):
    a later invalid payload must prevent even the first candidate from being
    sent, in both dry-run and real delivery."""
    return {identity: build_payload(eligible[identity], observed_at) for identity in identities}


def main() -> int:
    try:
        return _main_impl()
    except Exception as exc:  # noqa: BLE001 - true last resort: _main_impl's own
        # outer handler already covers ordinary unexpected exceptions and still
        # emits a scan_summary; this only fires if something escapes even that
        # (e.g. a failure inside logging/finish() itself), so no summary can be
        # trusted to be safely constructible here.
        log_event("unexpected_error", level=logging.ERROR, error_class=type(exc).__name__, phase="main")
        return 1


if __name__ == "__main__":
    sys.exit(main())
