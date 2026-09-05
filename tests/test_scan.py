"""Offline behavioral suite for scan.py.

No live network calls are made anywhere in this file. HTTP and sleep are
always mocked. Wall clock is frozen in most tests, but tests that exercise
rate-limit/budget timing use FakeClock (below), which advances a fake
monotonic and UTC clock together on sleep, and can also simulate the wall
clock moving independently of real elapsed time -- see the RateLimitAndBudgetTests
group. See docs/codex-review/2026-09-05-codex-spec.md for the contract these
tests verify against.
"""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import math
import runpy
import shutil
import socket
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

import pandas as pd
import numpy as np

import scan

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "docs" / "codex-review" / "2026-09-05-codex-spec.md"
FROZEN_NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)

_network_guards = None
_network_attempts = []


class UnexpectedNetworkAttempt(BaseException):
    """Must escape application handlers that intentionally catch Exception."""


def _blocked_socket(*args, **kwargs):
    # Do not record arguments: they could contain credentials. The durable
    # record also detects an attempt if a library catches BaseException.
    _network_attempts.append("unexpected network attempt")
    raise UnexpectedNetworkAttempt("no test in this suite may use real transport")


def setUpModule():
    # Suite-wide defense in depth: every test already mocks scrape_property
    # and requests.Session.post directly, but this ensures nothing in this
    # file can silently fall through to a real network call.
    global _network_guards
    _network_attempts.clear()
    _network_guards = ExitStack()
    for target in ("socket.socket", "socket.create_connection", "socket.getaddrinfo",
                   "requests.sessions.Session.request", "requests.sessions.Session.send"):
        _network_guards.enter_context(mock.patch(target, side_effect=_blocked_socket))


def tearDownModule():
    _network_guards.close()
    if _network_attempts:
        raise AssertionError(f"{len(_network_attempts)} unexpected network attempts recorded")


def base_row(**overrides) -> dict:
    row = {
        "property_id": "123456",
        "listing_id": "987654",
        "status": "FOR_SALE",
        "style": "SINGLE_FAMILY",
        "city": "Boca Raton",
        "state": "FL",
        "list_price": 400000,
        "sqft": 2000,
        "hoa_fee": 0,
        "property_url": (
            "https://www.realtor.com/realestateandhomes-detail/"
            "123-Main-St_Boca-Raton_FL_33432_M12345-67890"
        ),
        "beds": 3,
        "full_baths": 2,
        "half_baths": 1,
        "list_date": "2026-09-01T12:00:00Z",
        "formatted_address": "123 Main St, Boca Raton, FL 33432",
        "full_street_line": "123 Main St",
        "zip_code": "33432",
    }
    row.update(overrides)
    return row


def make_df(rows) -> pd.DataFrame:
    return pd.DataFrame(rows)


class FakeResponse:
    def __init__(self, status_code, json_body=None, headers=None, raise_json=False):
        self.status_code = status_code
        self._json_body = json_body
        self.headers = headers or {}
        self._raise_json = raise_json

    def json(self):
        if self._raise_json or self._json_body is None:
            raise ValueError("no json body")
        return self._json_body


def initial_state_dict(**overrides) -> dict:
    state = {
        "version": 1,
        "initialized": False,
        "seen": [],
        "disabled_webhook_sha256": None,
        "discord_not_before": None,
    }
    state.update(overrides)
    return state


class StateFixture:
    """Isolates scan.STATE_PATH/STATE_TMP_PATH to a temp directory."""

    def __enter__(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmpdir.name)
        self.state_path = self.dir / "seen.json"
        self.tmp_path = self.dir / "seen.json.tmp"
        self._patch_path = mock.patch.object(scan, "STATE_PATH", self.state_path)
        self._patch_tmp = mock.patch.object(scan, "STATE_TMP_PATH", self.tmp_path)
        self._patch_path.__enter__()
        self._patch_tmp.__enter__()
        return self

    def write(self, data: dict) -> None:
        self.state_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def read_bytes(self) -> bytes:
        return self.state_path.read_bytes()

    def __exit__(self, *exc):
        self._patch_path.__exit__(*exc)
        self._patch_tmp.__exit__(*exc)
        self._tmpdir.cleanup()


class FakeClock:
    """A coherent fake for scan._utcnow/scan.time.monotonic/scan.time.sleep
    where sleeping advances both the monotonic and UTC sides together, the
    way a real sleep does. jump_utc_only()/rewind_utc_only() advance ONLY
    the UTC side, independent of monotonic/real elapsed time -- this is what
    lets a test simulate the wall clock misbehaving (an NTP jump forward, or
    moving backward mid-sleep) that _await_gate is specifically required to
    tolerate by also checking a monotonic deadline.
    """

    def __init__(self, start_utc: datetime = FROZEN_NOW, start_monotonic: float = 1_000_000.0):
        self._utc = start_utc
        self._mono = start_monotonic
        self.sleep_calls: list[float] = []

    def utcnow(self) -> datetime:
        return self._utc

    def monotonic(self) -> float:
        return self._mono

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self._mono += seconds
        self._utc += timedelta(seconds=seconds)

    def jump_utc_only(self, seconds: float) -> None:
        self._utc += timedelta(seconds=seconds)

    def rewind_utc_only(self, seconds: float) -> None:
        self._utc -= timedelta(seconds=seconds)


@contextmanager
def patched_clock(clock: FakeClock):
    """Patches scan._utcnow/time.monotonic/time.sleep to `clock`'s methods
    and yields the sleep mock, so a test can still assert on sleep calls."""
    with mock.patch.object(scan, "_utcnow", side_effect=clock.utcnow), mock.patch.object(
        scan.time, "monotonic", side_effect=clock.monotonic
    ), mock.patch.object(scan.time, "sleep", side_effect=clock.sleep) as sleep_mock:
        yield sleep_mock


VALID_WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/abcDEF-token_123"
CANONICAL_WEBHOOK = "https://discord.com/api/v10/webhooks/123456789012345678/abcDEF-token_123"

FETCH_KWARGS = dict(
    location="Boca Raton, FL", listing_type="for_sale", property_type=["single_family"],
    sqft_min=1700, price_min=250000, price_max=650000, exclude_pending=True,
    mls_only=False, extra_property_data=False, return_type="pandas", limit=10000,
    offset=0, parallel=False,
)


def assert_summary(test, logs, *, observed=True, **overrides):
    """Check every contract count, including zero and not-yet-observed values."""
    names = (
        "total_fetched malformed_identity malformed_required_field duplicate_group "
        "conflicting_duplicate status_mismatch style_mismatch state_mismatch "
        "city_mismatch price_out_of_range sqft_out_of_range hoa_unknown hoa_nonzero "
        "eligible already_seen candidate confirmed unsent"
    ).split()
    expected = dict.fromkeys(names, 0 if observed else None)
    expected["baseline_created"] = False
    expected.update(overrides)
    lines = [line for line in logs if "event=scan_summary" in line]
    test.assertEqual(len(lines), 1)
    actual = dict(part.split("=", 1) for part in lines[0].split() if "=" in part)
    test.assertEqual({key: actual.get(key) for key in expected},
                     {key: str(value) for key, value in expected.items()})


def run_main(dry_run: bool, webhook: str | None = VALID_WEBHOOK):
    env = {}
    if dry_run:
        env["DRY_RUN"] = "1"
    else:
        env["DRY_RUN"] = "0"
        if webhook is not None:
            env["DISCORD_WEBHOOK_URL"] = webhook
    with mock.patch.dict("os.environ", env, clear=False):
        if not dry_run and webhook is None:
            # ensure it is actually absent, not just unset in this call
            with mock.patch.dict("os.environ", {}, clear=False):
                import os as _os

                _os.environ.pop("DISCORD_WEBHOOK_URL", None)
                return scan.main()
        return scan.main()


# ---------------------------------------------------------------------------
# Group: fetch and required fields
# ---------------------------------------------------------------------------


class FetchAndRequiredFieldsTests(unittest.TestCase):
    def test_fetch_listings_uses_exact_keywords(self):
        with mock.patch.object(scan, "scrape_property") as fake:
            fake.return_value = make_df([base_row()])
            scan.fetch_listings()
        fake.assert_called_once_with(
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

    def test_rejects_non_dataframe(self):
        with self.assertRaises(scan.FetchShapeError):
            scan.validate_fetch_shape([{"not": "a dataframe"}])

    def test_rejects_empty_dataframe(self):
        with self.assertRaises(scan.FetchShapeError) as ctx:
            scan.validate_fetch_shape(make_df([]))
        self.assertEqual(ctx.exception.reason, "scan_indeterminate_empty")

    def test_rejects_result_cap(self):
        df = make_df([base_row(property_id=str(i + 1)) for i in range(5)])
        with mock.patch.object(scan, "RESULT_CAP", 5):
            with self.assertRaises(scan.FetchShapeError) as ctx:
                scan.validate_fetch_shape(df)
        self.assertEqual(ctx.exception.reason, "scan_result_cap")

    def test_rejects_each_missing_required_column(self):
        for column in scan.REQUIRED_COLUMNS:
            row = base_row()
            df = make_df([row]).drop(columns=[column])
            with self.assertRaises(scan.FetchShapeError, msg=f"missing {column}"):
                scan.validate_fetch_shape(df)

    def test_nonempty_zero_eligible_is_healthy_and_produces_no_eligible(self):
        df = make_df([base_row(city="Miami")])
        scan.validate_fetch_shape(df)  # must not raise
        eligible, counts = scan.process_dataframe(df)
        self.assertEqual(eligible, {})
        self.assertEqual(counts.eligible, 0)
        self.assertEqual(counts.city_mismatch, 1)

    def test_scrape_generic_exception_is_caught_and_state_untouched(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=["1:1"]))
            before = fx.read_bytes()
            with mock.patch.object(scan, "scrape_property", side_effect=RuntimeError("boom")):
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 1)
            self.assertEqual(fx.read_bytes(), before)

    def test_scrape_authentication_error_is_not_specially_narrowed(self):
        class FakeAuthError(Exception):
            pass

        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            with mock.patch.object(scan, "scrape_property", side_effect=FakeAuthError("nope")):
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 1)

    def test_total_fetched_is_reported_even_when_shape_validation_rejects_the_result(self):
        # A capped (or otherwise shape-invalid) result still returned a real
        # row count from the DataFrame; that must be visible in the summary
        # rather than reported as if the fetch never produced anything.
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df([base_row(property_id=str(i + 1)) for i in range(5)])
            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan, "RESULT_CAP", 5
            ):
                with self.assertLogs("boca_house_hunter", level="INFO") as logs:
                    rc = run_main(dry_run=False)
            self.assertEqual(rc, 1)
            summary_lines = [line for line in logs.output if "event=scan_summary" in line]
            self.assertEqual(len(summary_lines), 1)
            self.assertIn("total_fetched=5", summary_lines[0])


# ---------------------------------------------------------------------------
# Group: scalar eligibility
# ---------------------------------------------------------------------------


class ScalarEligibilityTests(unittest.TestCase):
    def _eligible_ids(self, rows):
        df = make_df(rows)
        eligible, _ = scan.process_dataframe(df)
        return set(eligible.keys())

    def test_inclusive_price_and_sqft_boundaries(self):
        rows = [
            base_row(property_id="1", list_price=250000, sqft=1700),
            base_row(property_id="2", list_price=650000, sqft=1700),
        ]
        ids = self._eligible_ids(rows)
        self.assertIn("1:987654", ids)
        self.assertIn("2:987654", ids)

    def test_just_outside_boundaries_excluded_without_rounding(self):
        rows = [
            base_row(property_id="1", list_price="249999.99"),
            base_row(property_id="2", list_price="650000.01"),
            base_row(property_id="3", sqft="1699.99"),
        ]
        ids = self._eligible_ids(rows)
        self.assertEqual(ids, set())

    def test_wrong_city_state_style_status_excluded(self):
        rows = [
            base_row(property_id="1", city="Delray Beach"),
            base_row(property_id="2", state="GA"),
            base_row(property_id="3", style="CONDOS"),
            base_row(property_id="4", status="PENDING"),
        ]
        self.assertEqual(self._eligible_ids(rows), set())

    def test_whitespace_and_case_are_normalized(self):
        rows = [
            base_row(
                status=" for_sale ",
                style=" single_family ",
                state=" fl ",
                city=" BOCA RATON ",
            )
        ]
        self.assertEqual(len(self._eligible_ids(rows)), 1)

    def test_hoa_classification_table(self):
        qualifying = [0, 0.0, "0", "0.00"]
        unknown = [None, float("nan"), "", "not-a-number", True, False, float("inf"), float("-inf")]
        nonzero = [50, -50, "125.50", -1]

        for value in qualifying:
            df = make_df([base_row(property_id="1", hoa_fee=value)])
            eligible, counts = scan.process_dataframe(df)
            self.assertEqual(len(eligible), 1, msg=f"expected qualifying for {value!r}")
            self.assertEqual(counts.hoa_unknown, 0)
            self.assertEqual(counts.hoa_nonzero, 0)

        for value in unknown:
            df = make_df([base_row(property_id="1", hoa_fee=value)])
            eligible, counts = scan.process_dataframe(df)
            self.assertEqual(len(eligible), 0, msg=f"expected unknown for {value!r}")
            self.assertEqual(counts.hoa_unknown, 1, msg=f"expected hoa_unknown for {value!r}")

        for value in nonzero:
            df = make_df([base_row(property_id="1", hoa_fee=value)])
            eligible, counts = scan.process_dataframe(df)
            self.assertEqual(len(eligible), 0, msg=f"expected nonzero-rejected for {value!r}")
            self.assertEqual(counts.hoa_nonzero, 1, msg=f"expected hoa_nonzero for {value!r}")

    def test_non_scalar_and_boolean_price_cannot_bypass_checks(self):
        rows = [
            base_row(property_id="1", list_price=[400000]),
            base_row(property_id="2", list_price=True),
        ]
        self.assertEqual(self._eligible_ids(rows), set())

    def test_finite_decimal_scalar_inputs_are_valid_numbers(self):
        self.assertEqual(scan.normalize_number(Decimal("0")), Decimal("0"))
        self.assertEqual(scan.normalize_number(Decimal("400000")), Decimal("400000"))
        self.assertIsNone(scan.normalize_number(Decimal("NaN")))
        self.assertIsNone(scan.normalize_number(Decimal("Infinity")))

    def test_large_but_within_budget_values_display_correctly_not_unknown(self):
        # The ambient default Decimal context (28 significant digits) used to
        # be passed implicitly to quantize(), which raised InvalidOperation
        # for values like 1e26 even though their correct display text fits
        # well within the 64-character NUMERIC_DISPLAY_LIMIT. That must not
        # be conflated with genuine display overflow.
        self.assertEqual(
            scan.format_size(Decimal("1e26")),
            "100,000,000,000,000,000,000,000,000 sq ft",
        )
        self.assertNotEqual(scan.format_size(Decimal("1e40")), "Unknown")
        # A large, non-integral price still needs the quantize() path (for
        # its two-decimal display) and must not hit the same context error.
        large_non_integral_price = Decimal("1e26") + Decimal("0.5")
        self.assertNotEqual(scan.format_price(large_non_integral_price), "Unknown")

    def test_genuinely_oversized_values_still_display_unknown(self):
        # A value whose correct display text would exceed the 64-character
        # budget must still show Unknown -- the R6 fix only stops precision
        # loss from masquerading as overflow, it must not remove the actual
        # overflow check.
        self.assertEqual(scan.format_size(Decimal("1e70")), "Unknown")


# ---------------------------------------------------------------------------
# Group: identity and duplicate handling
# ---------------------------------------------------------------------------


class IdentityAndDuplicateTests(unittest.TestCase):
    def test_leading_zeros_preserved(self):
        row = base_row(property_id="007123", listing_id="000456")
        # "000456" is not all-zero, so it is a valid id with leading zeros preserved.
        df = make_df([row])
        eligible, counts = scan.process_dataframe(df)
        self.assertIn("007123:000456", eligible)

    def test_valid_integer_scalars_accepted(self):
        row = base_row(property_id=123456, listing_id=987654)
        df = make_df([row])
        eligible, _ = scan.process_dataframe(df)
        self.assertIn("123456:987654", eligible)

    def test_integer_identity_enforces_same_64_digit_limit_as_string(self):
        # normalize_identity_component(10**64) is a 65-digit integer - the
        # integer path must apply the same length limit the string path
        # already enforces, or a fetched identity that passes here could
        # later be rejected when the exact same digits arrive as state.
        self.assertIsNone(scan.normalize_identity_component(10**64))
        self.assertIsNone(scan.normalize_identity_component(str(10**64)))
        self.assertIsNotNone(scan.normalize_identity_component(10**63))

    def test_extremely_oversized_integer_rejected_without_raising(self):
        # str(int(10**5000)) exceeds Python's int-to-str conversion digit
        # limit and raises ValueError; this must be caught and treated as a
        # malformed identity (returning None), not propagate out of
        # normalization and abort the whole row/scan.
        self.assertIsNone(scan.normalize_identity_component(10**5000))
        # Built as a single object-dtype Series (not through make_df/a full
        # DataFrame): pandas' own column type-inference can't represent a
        # 5000-digit int alongside ordinary values without raising its own
        # OverflowError first, which would test pandas rather than scan.py.
        row = pd.Series(base_row(property_id=10**5000), dtype=object)
        identity, kind, fields = scan._normalize_row(row)
        self.assertIsNone(identity)
        self.assertEqual(kind, "malformed_identity")

    def test_malformed_identities_rejected_without_nan_identity(self):
        bad_values = [None, float("nan"), 123.0, True, "000", "12a3", "x" * 65, "-5"]
        for value in bad_values:
            df = make_df([base_row(property_id=value)])
            eligible, counts = scan.process_dataframe(df)
            self.assertEqual(eligible, {}, msg=f"expected rejection for property_id={value!r}")
            self.assertEqual(counts.malformed_identity, 1)
            for identity in eligible:
                self.assertNotIn("nan", identity.lower())

    def test_identical_duplicates_yield_one_result(self):
        row = base_row()
        df = make_df([row, dict(row)])
        eligible, counts = scan.process_dataframe(df)
        self.assertEqual(len(eligible), 1)
        # Agreeing duplicates must still be visible in the required
        # accounting as a distinct bucket from conflicting ones - an
        # identical-duplicate group must not silently disappear.
        self.assertEqual(counts.duplicate_group, 1)
        self.assertEqual(counts.conflicting_duplicate, 0)

    def test_lone_row_is_not_counted_as_a_duplicate_group(self):
        df = make_df([base_row()])
        _, counts = scan.process_dataframe(df)
        self.assertEqual(counts.duplicate_group, 0)

    def test_conflicting_duplicates_suppress_entire_identity(self):
        row_a = base_row(list_price=400000)
        row_b = base_row(list_price=500000)  # same identity, disagreeing required field
        df = make_df([row_a, row_b])
        eligible, counts = scan.process_dataframe(df)
        self.assertEqual(eligible, {})
        self.assertEqual(counts.conflicting_duplicate, 1)

    def test_malformed_required_field_sibling_suppresses_qualifying_duplicate(self):
        # A qualifying row must not hide a same-identity sibling whose
        # required fields failed to normalize - the whole identity must be
        # suppressed as a conflicting duplicate, not silently pass through.
        for bad_overrides in (
            {"status": None},
            {"property_url": "invalid"},
            {"list_price": "bad"},
        ):
            row_good = base_row()
            row_bad = base_row(**bad_overrides)
            df = make_df([row_good, row_bad])
            eligible, counts = scan.process_dataframe(df)
            self.assertEqual(eligible, {}, msg=f"bad_overrides={bad_overrides!r}")
            self.assertEqual(counts.conflicting_duplicate, 1, msg=f"bad_overrides={bad_overrides!r}")

    def test_lone_malformed_required_row_is_not_double_counted_as_conflicting(self):
        df = make_df([base_row(status=None)])
        eligible, counts = scan.process_dataframe(df)
        self.assertEqual(eligible, {})
        self.assertEqual(counts.malformed_required_field, 1)
        self.assertEqual(counts.conflicting_duplicate, 0)

    def test_distinct_nonzero_hoa_fees_are_a_real_disagreement(self):
        # Both rows are individually ineligible (nonzero HOA), but they must
        # still be recognized as disagreeing with each other rather than
        # silently "agreeing" because both fall in the same hoa_class bucket.
        row_a = base_row(hoa_fee=50)
        row_b = base_row(hoa_fee=75)
        df = make_df([row_a, row_b])
        eligible, counts = scan.process_dataframe(df)
        self.assertEqual(eligible, {})
        self.assertEqual(counts.conflicting_duplicate, 1)

    def test_agreeing_duplicates_have_deterministic_tie_break_regardless_of_order(self):
        row_a = base_row(formatted_address="200 Zeta Ave, Boca Raton, FL 33432")
        row_b = base_row(formatted_address="100 Alpha Ave, Boca Raton, FL 33432")

        eligible_forward, _ = scan.process_dataframe(make_df([row_a, row_b]))
        eligible_reversed, _ = scan.process_dataframe(make_df([row_b, row_a]))

        key = "123456:987654"
        self.assertEqual(
            eligible_forward[key]["formatted_address"],
            eligible_reversed[key]["formatted_address"],
        )
        self.assertEqual(eligible_forward[key]["formatted_address"], "100 Alpha Ave, Boca Raton, FL 33432")


# ---------------------------------------------------------------------------
# Group: lifecycle / dedup
# ---------------------------------------------------------------------------


class LifecycleTests(unittest.TestCase):
    def test_baseline_creates_state_with_zero_posts(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict())
            df = make_df([base_row()])
            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post"
            ) as post:
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 0)
            post.assert_not_called()
            state = json.loads(fx.read_bytes())
            self.assertTrue(state["initialized"])
            self.assertEqual(state["seen"], ["123456:987654"])

    def test_baseline_with_zero_eligible_rows_is_healthy(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict())
            df = make_df([base_row(city="Miami")])
            with mock.patch.object(scan, "scrape_property", return_value=df):
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 0)
            state = json.loads(fx.read_bytes())
            self.assertTrue(state["initialized"])
            self.assertEqual(state["seen"], [])

    def test_dry_run_never_initializes_state(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict())
            before = fx.read_bytes()
            df = make_df([base_row()])
            with mock.patch.object(scan, "scrape_property", return_value=df):
                rc = run_main(dry_run=True)
            self.assertEqual(rc, 0)
            self.assertEqual(fx.read_bytes(), before)

    def test_repeat_scan_sends_and_writes_nothing(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=["123456:987654"]))
            before = fx.read_bytes()
            df = make_df([base_row()])
            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post"
            ) as post, mock.patch.object(scan, "save_state") as save:
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 0)
            post.assert_not_called()
            save.assert_not_called()
            self.assertEqual(fx.read_bytes(), before)

    def test_new_pair_sends_once(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df([base_row()])
            response = FakeResponse(200, {"id": "111"})
            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", return_value=response
            ) as post, mock.patch.object(scan.time, "sleep"):
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 0)
            post.assert_called_once()
            state = json.loads(fx.read_bytes())
            self.assertEqual(state["seen"], ["123456:987654"])

    def test_same_property_new_listing_id_may_send(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=["123456:111111"]))
            df = make_df([base_row(listing_id="222222")])
            response = FakeResponse(200, {"id": "1"})
            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", return_value=response
            ):
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 0)
            state = json.loads(fx.read_bytes())
            self.assertIn("123456:222222", state["seen"])
            self.assertIn("123456:111111", state["seen"])

    def test_same_pair_disappear_reappear_stays_suppressed(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=["123456:987654"]))
            df_missing = make_df([base_row(property_id="999999")])
            with mock.patch.object(scan, "scrape_property", return_value=df_missing), mock.patch.object(
                scan.requests.Session, "post"
            ) as post:
                run_main(dry_run=False)
            df_back = make_df([base_row()])
            with mock.patch.object(scan, "scrape_property", return_value=df_back), mock.patch.object(
                scan.requests.Session, "post"
            ) as post2:
                run_main(dry_run=False)
                post2.assert_not_called()

    def test_unknown_hoa_becoming_zero_can_first_alert(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df_unknown = make_df([base_row(hoa_fee=None)])
            with mock.patch.object(scan, "scrape_property", return_value=df_unknown), mock.patch.object(
                scan.requests.Session, "post"
            ) as post:
                run_main(dry_run=False)
                post.assert_not_called()

            df_zero = make_df([base_row(hoa_fee=0)])
            response = FakeResponse(200, {"id": "1"})
            with mock.patch.object(scan, "scrape_property", return_value=df_zero), mock.patch.object(
                scan.requests.Session, "post", return_value=response
            ) as post2:
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 0)
            post2.assert_called_once()

    def test_already_seen_price_change_never_alerts(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=["123456:987654"]))
            df = make_df([base_row(list_price=300000)])
            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post"
            ) as post:
                run_main(dry_run=False)
                post.assert_not_called()

    def test_candidate_order_is_lexicographic(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df(
                [
                    base_row(property_id="2", listing_id="1"),
                    base_row(property_id="1", listing_id="1"),
                ]
            )
            sent_order = []

            def fake_post(self, url, params=None, json=None, timeout=None, allow_redirects=None):
                sent_order.append(json["embeds"][0]["footer"]["text"])
                return FakeResponse(200, {"id": str(len(sent_order))})

            clock = FakeClock()
            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", new=fake_post
            ), patched_clock(clock):
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 0)
            self.assertEqual(
                sent_order,
                [
                    "Realtor.com via HomeHarvest | 1:1",
                    "Realtor.com via HomeHarvest | 2:1",
                ],
            )
            # Inter-message pacing must have actually been honored (not
            # skipped), even though both candidates ultimately succeeded.
            self.assertTrue(any(s >= scan.MIN_POST_INTERVAL_SECONDS for s in clock.sleep_calls))


# ---------------------------------------------------------------------------
# Group: state integrity
# ---------------------------------------------------------------------------


class StateIntegrityTests(unittest.TestCase):
    def test_missing_state_file_is_fatal_before_fetch(self):
        with StateFixture():
            with mock.patch.object(scan, "scrape_property") as fake:
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 1)
            fake.assert_not_called()

    def test_unreadable_state_directory_in_place_of_file(self):
        with StateFixture() as fx:
            fx.state_path.mkdir()
            with self.assertRaises(scan.StateError):
                scan.load_state()

    def test_malformed_json_rejected(self):
        with StateFixture() as fx:
            fx.state_path.write_text("{not valid json", encoding="utf-8")
            with self.assertRaises(scan.StateError):
                scan.load_state()

    def test_duplicate_json_keys_rejected(self):
        with StateFixture() as fx:
            fx.state_path.write_text(
                '{"version": 1, "version": 1, "initialized": false, "seen": [], '
                '"disabled_webhook_sha256": null, "discord_not_before": null}',
                encoding="utf-8",
            )
            with self.assertRaises(scan.StateError):
                scan.load_state()

    def test_forbidden_json_constants_rejected(self):
        with StateFixture() as fx:
            fx.state_path.write_text(
                '{"version": 1, "initialized": false, "seen": [], '
                '"disabled_webhook_sha256": null, "discord_not_before": NaN}',
                encoding="utf-8",
            )
            with self.assertRaises(scan.StateError):
                scan.load_state()

    def test_extra_and_missing_keys_rejected(self):
        with StateFixture() as fx:
            data = initial_state_dict()
            data["extra"] = "nope"
            fx.write(data)
            with self.assertRaises(scan.StateError):
                scan.load_state()
        with StateFixture() as fx:
            data = initial_state_dict()
            del data["discord_not_before"]
            fx.write(data)
            with self.assertRaises(scan.StateError):
                scan.load_state()

    def test_boolean_version_is_rejected_not_treated_as_one(self):
        with StateFixture() as fx:
            data = initial_state_dict()
            data["version"] = True
            fx.write(data)
            with self.assertRaises(scan.StateError):
                scan.load_state()

    def test_unsupported_version_rejected(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(version=2))
            with self.assertRaises(scan.StateError):
                scan.load_state()

    def test_unsorted_duplicate_and_malformed_seen_values_rejected(self):
        cases = [
            ["2:2", "1:1"],  # unsorted
            ["1:1", "1:1"],  # duplicate
            ["not-an-identity"],  # malformed
            ["1:1:1"],  # too many parts
        ]
        for seen in cases:
            with StateFixture() as fx:
                fx.write(initial_state_dict(initialized=True, seen=seen))
                with self.assertRaises(scan.StateError, msg=f"seen={seen!r}"):
                    scan.load_state()

    def test_zero_only_seen_components_rejected(self):
        # normalize_identity_component() never produces an all-zero component
        # for a fetched row; the state loader must enforce the same rule so a
        # stored pair like "0:000" (which could never have been produced by a
        # real scan) is not silently accepted as valid.
        for seen in (["0:1"], ["1:0"], ["0:000"], ["00:00"]):
            with StateFixture() as fx:
                fx.write(initial_state_dict(initialized=True, seen=seen))
                with self.assertRaises(scan.StateError, msg=f"seen={seen!r}"):
                    scan.load_state()

    def test_invalid_utf8_state_is_state_invalid_not_unexpected(self):
        with StateFixture() as fx:
            fx.state_path.write_bytes(b"\xff\xfe\x00invalid-utf8")
            with self.assertRaises(scan.StateError):
                scan.load_state()

    def test_nonempty_uninitialized_seen_rejected(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=False, seen=["1:1"]))
            with self.assertRaises(scan.StateError):
                scan.load_state()

    def test_malformed_digest_and_timestamp_rejected(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(disabled_webhook_sha256="not-hex"))
            with self.assertRaises(scan.StateError):
                scan.load_state()
        with StateFixture() as fx:
            fx.write(initial_state_dict(discord_not_before="not-a-timestamp"))
            with self.assertRaises(scan.StateError):
                scan.load_state()

    def test_script_relative_lookup_works_from_another_cwd(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict())
            original_cwd = Path.cwd()
            other_dir = tempfile.mkdtemp()
            try:
                import os as _os

                _os.chdir(other_dir)
                result = scan.load_state()
            finally:
                import os as _os

                _os.chdir(original_cwd)
                shutil.rmtree(other_dir, ignore_errors=True)
            self.assertEqual(result["seen"], [])

    def test_leftover_temp_file_never_used_as_recovery(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=["1:1"]))
            fx.tmp_path.write_text('{"garbage": true}', encoding="utf-8")
            result = scan.load_state()
            self.assertEqual(result["seen"], ["1:1"])


# ---------------------------------------------------------------------------
# Group: atomic writes
# ---------------------------------------------------------------------------


class AtomicWriteTests(unittest.TestCase):
    def test_save_state_exact_formatting(self):
        with StateFixture() as fx:
            scan.save_state(initial_state_dict(initialized=True, seen=["2:2", "1:1"]))
            raw = fx.state_path.read_bytes()
            text = raw.decode("utf-8")
            self.assertTrue(text.endswith("}\n"))
            self.assertNotIn("\r", text)
            data = json.loads(text)
            self.assertEqual(list(data.keys()), list(scan.STATE_KEYS))
            self.assertEqual(data["seen"], ["1:1", "2:2"])
            self.assertFalse(fx.tmp_path.exists())

    def test_flush_fsync_replace_sequence_used(self):
        with StateFixture() as fx:
            with mock.patch.object(scan.os, "fsync") as fsync, mock.patch.object(
                scan.os, "replace", wraps=scan.os.replace
            ) as replace:
                scan.save_state(initial_state_dict())
            fsync.assert_called_once()
            replace.assert_called_once_with(fx.tmp_path, fx.state_path)

    def test_noop_scan_does_not_write(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=["123456:987654"]))
            df = make_df([base_row()])
            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan, "save_state"
            ) as save:
                run_main(dry_run=False)
            save.assert_not_called()

    def test_write_failure_stops_further_sends_and_logs_state_write_failed(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df(
                [
                    base_row(property_id="1", listing_id="1"),
                    base_row(property_id="2", listing_id="1"),
                ]
            )
            response = FakeResponse(200, {"id": "1"})
            call_count = {"n": 0}

            def flaky_replace(src, dst):
                call_count["n"] += 1
                raise OSError("disk full")

            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", return_value=response
            ) as post, mock.patch.object(scan.os, "replace", side_effect=flaky_replace), mock.patch.object(
                scan.time, "sleep"
            ):
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 1)
            post.assert_called_once()  # second candidate never attempted

    def test_write_failure_right_after_confirmation_still_reports_it_as_confirmed(self):
        # Reviewer's probe: the remote message WAS confirmed delivered (a
        # real 200 + valid id came back) even though the immediately
        # following local save failed. The summary must reflect that one
        # real delivery happened, not silently report confirmed=None as if
        # nothing had been observed.
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df(
                [
                    base_row(property_id="1", listing_id="1"),
                    base_row(property_id="2", listing_id="1"),
                ]
            )
            response = FakeResponse(200, {"id": "1"})

            def flaky_replace(src, dst):
                raise OSError("disk full")

            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", return_value=response
            ), mock.patch.object(scan.os, "replace", side_effect=flaky_replace), mock.patch.object(
                scan.time, "sleep"
            ):
                with self.assertLogs("boca_house_hunter", level="INFO") as logs:
                    rc = run_main(dry_run=False)
            self.assertEqual(rc, 1)
            summary_lines = [line for line in logs.output if "event=scan_summary" in line]
            self.assertEqual(len(summary_lines), 1)
            self.assertIn("candidate=2", summary_lines[0])
            self.assertIn("confirmed=1", summary_lines[0])
            self.assertIn("unsent=1", summary_lines[0])


# ---------------------------------------------------------------------------
# Group: payload construction
# ---------------------------------------------------------------------------


class PayloadTests(unittest.TestCase):
    def test_exact_payload_shape(self):
        fields = scan.process_dataframe(make_df([base_row()]))[0]["123456:987654"]
        payload = scan.build_payload(fields, FROZEN_NOW)
        self.assertEqual(payload["username"], "Boca House Hunter")
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertEqual(len(payload["embeds"]), 1)
        embed = payload["embeds"][0]
        self.assertEqual(
            [f["name"] for f in embed["fields"]],
            ["Price", "Size", "Beds", "Baths", "HOA fee", "Listed"],
        )
        self.assertEqual([f["inline"] for f in embed["fields"]], [True, True, True, True, False, True])
        self.assertTrue(embed["title"].startswith("New match:"))
        self.assertEqual(embed["footer"]["text"], "Realtor.com via HomeHarvest | 123456:987654")
        self.assertEqual(embed["timestamp"], "2026-09-05T12:00:00Z")
        self.assertNotIn("content", payload)

    def test_price_and_size_formatting(self):
        self.assertEqual(scan.format_price(Decimal("400000")), "$400,000")
        self.assertEqual(scan.format_price(Decimal("400000.5")), "$400,000.50")
        self.assertEqual(scan.format_size(Decimal("2000")), "2,000 sq ft")
        self.assertEqual(scan.format_size(Decimal("2000.50")), "2,000.5 sq ft")

    def test_price_decimal_count_follows_original_value_not_rounded_result(self):
        # 400000.001 rounds to a whole dollar amount, but the ORIGINAL value
        # is non-integral, so it must still display two decimal places.
        self.assertEqual(scan.format_price(Decimal("400000.001")), "$400,000.00")
        self.assertEqual(scan.format_price(Decimal("400000.00")), "$400,000")

    def test_oversized_numeric_values_fall_back_to_unknown_not_an_exception(self):
        huge = Decimal("1e100")
        self.assertEqual(scan.format_size(huge), "Unknown")
        self.assertEqual(scan.format_price(huge), "Unknown")
        self.assertEqual(scan.format_optional_nonneg_int(Decimal("1e5000")), "Unknown")

    def test_beds_baths_integer_or_unknown(self):
        self.assertEqual(scan.format_optional_nonneg_int(3), "3")
        self.assertEqual(scan.format_optional_nonneg_int(None), "Unknown")
        self.assertEqual(scan.format_optional_nonneg_int(2.5), "Unknown")
        self.assertEqual(scan.format_optional_nonneg_int(-1), "Unknown")

    def test_address_fallback_precedence(self):
        fields = {"property_id": "1", "formatted_address": None, "full_street_line": None, "zip_code": None}
        self.assertEqual(scan.build_address_display(fields), "Boca Raton, FL — property 1")

        fields2 = {
            "property_id": "1",
            "formatted_address": None,
            "full_street_line": "5 Elm St",
            "zip_code": "33432",
        }
        self.assertEqual(scan.build_address_display(fields2), "5 Elm St, Boca Raton, FL 33432")

        fields3 = {
            "property_id": "1",
            "formatted_address": "Real Address 1",
            "full_street_line": "ignored",
            "zip_code": "ignored",
        }
        self.assertEqual(scan.build_address_display(fields3), "Real Address 1")

    def test_address_control_chars_do_not_fuse_adjacent_words(self):
        fields = {
            "property_id": "1",
            "formatted_address": "123\nMain\tSt",
            "full_street_line": None,
            "zip_code": None,
        }
        self.assertEqual(scan.build_address_display(fields), "123 Main St")

    def test_address_falls_back_when_primary_source_sanitizes_to_empty(self):
        # A lone control character is nonempty pre-sanitization, so the
        # emptiness check must happen AFTER sanitizing, or this silently
        # picks an empty display instead of falling back to full_street_line.
        fields = {
            "property_id": "1",
            "formatted_address": "\x07",
            "full_street_line": "5 Elm St",
            "zip_code": "33432",
        }
        self.assertEqual(scan.build_address_display(fields), "5 Elm St, Boca Raton, FL 33432")

        fields_no_fallback = {
            "property_id": "9",
            "formatted_address": "\x07",
            "full_street_line": None,
            "zip_code": None,
        }
        self.assertEqual(
            scan.build_address_display(fields_no_fallback), "Boca Raton, FL — property 9"
        )

    def test_address_falls_back_when_control_removal_leaves_only_whitespace(self):
        # "\x07 \x07" is nonempty even after a naive single sanitize pass:
        # collapsing whitespace first (to avoid fusing words) leaves the
        # single space alone, and removing the two control chars afterward
        # leaves JUST that space behind -- a bare " " is a truthy Python
        # string, so without a second whitespace cleanup pass this would
        # incorrectly win over the street-line fallback.
        self.assertEqual(scan.sanitize_text("\x07 \x07"), "")
        fields = {
            "property_id": "1",
            "formatted_address": "\x07 \x07",
            "full_street_line": "5 Elm St",
            "zip_code": "33432",
        }
        self.assertEqual(scan.build_address_display(fields), "5 Elm St, Boca Raton, FL 33432")

    def test_address_component_is_utf16_truncated_not_sliced_by_code_point(self):
        emoji_house = "\U0001F3E1"  # astral character, 2 UTF-16 code units each
        fields = {
            "property_id": "1",
            "formatted_address": emoji_house * 150,
            "full_street_line": None,
            "zip_code": None,
        }
        result = scan.build_address_display(fields)
        self.assertLessEqual(scan._utf16_len(result), scan.ADDRESS_COMPONENT_LIMIT)
        self.assertTrue(result.endswith("..."))

    def test_non_ascii_control_characters_are_removed(self):
        # C1 control range (\x80-\x9f), not just ASCII \x00-\x1f/\x7f.
        text = scan.sanitize_text("Hello\x85World")
        self.assertNotIn("\x85", text)

    def test_source_date_and_invalid_date_rejection(self):
        self.assertEqual(scan.format_list_date("2026-09-01T00:00:00Z"), "2026-09-01 (source)")
        self.assertEqual(scan.format_list_date(None), "Unknown")
        self.assertEqual(scan.format_list_date(1234567890), "Unknown")
        self.assertEqual(scan.format_list_date("not-a-date"), "Unknown")

    def test_invalid_url_rejected_and_query_fragment_stripped(self):
        self.assertIsNone(scan.normalize_property_url("https://evil.com/listing"))
        self.assertIsNone(scan.normalize_property_url("http://www.realtor.com/listing"))
        self.assertIsNone(scan.normalize_property_url("https://user:pass@www.realtor.com/listing"))
        self.assertIsNone(scan.normalize_property_url("https://www.realtor.com:8443/listing"))
        cleaned = scan.normalize_property_url(
            "https://www.realtor.com/realestateandhomes-detail/abc?utm=1#frag"
        )
        self.assertEqual(cleaned, "https://www.realtor.com/realestateandhomes-detail/abc")

    def test_url_rejects_empty_userinfo_and_empty_port_syntax(self):
        # An explicitly-empty userinfo ("@host") or empty port ("host:") can
        # make urlsplit's .username/.password/.port properties return None,
        # which must not be mistaken for "no forbidden syntax present."
        self.assertIsNone(scan.normalize_property_url("https://@www.realtor.com/listing"))
        self.assertIsNone(scan.normalize_property_url("https://www.realtor.com:/listing"))

    def test_webhook_url_rejects_empty_userinfo_and_empty_port_syntax(self):
        with self.assertRaises(scan.WebhookConfigError):
            scan.canonicalize_webhook_url("https://@discord.com/api/webhooks/1/token")
        with self.assertRaises(scan.WebhookConfigError):
            scan.canonicalize_webhook_url("https://discord.com:/api/webhooks/1/token")

    def test_markdown_and_control_and_at_sign_escaped(self):
        text = scan.sanitize_text("Hello *world* @user\x07 line1\nline2")
        self.assertNotIn("\x07", text)
        self.assertIn("\\*world\\*", text)
        self.assertIn("＠user", text)
        self.assertNotIn("\n", text)

    def test_utf16_truncation_never_splits_surrogate_pair(self):
        emoji = "\U0001F3E1"  # astral character, 2 UTF-16 code units
        text = emoji * 5
        truncated = scan.truncate_utf16(text, 6)
        encoded = truncated.encode("utf-16-le", errors="strict")
        self.assertEqual(len(encoded) % 2, 0)
        # Must decode cleanly with no lone surrogate.
        encoded.decode("utf-16-le")

    def test_build_payload_independently_validates_final_field_budgets(self):
        # A fault-injection probe: if truncate_utf16 were ever broken and
        # returned its input unmodified, build_payload must still catch an
        # oversized title/field/footer on its own, rather than relying
        # exclusively on the truncation helper having done its job.
        fields = scan.process_dataframe(make_df([base_row()]))[0]["123456:987654"]
        with mock.patch.object(scan, "truncate_utf16", side_effect=lambda text, limit: text):
            fields_with_long_address = dict(fields)
            fields_with_long_address["formatted_address"] = "X" * 400
            with self.assertRaises(scan.PayloadError):
                scan.build_payload(fields_with_long_address, FROZEN_NOW)

    def test_payload_construction_failure_prevents_any_post(self):
        good_fields = scan.process_dataframe(make_df([base_row()]))[0]["123456:987654"]
        bad_fields = dict(good_fields)
        bad_fields["price"] = "not-a-decimal"  # forces an exception in format_price

        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df(
                [
                    base_row(property_id="1", listing_id="1"),
                    base_row(property_id="2", listing_id="1"),
                ]
            )
            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan, "build_payload"
            ) as build:

                def side_effect(fields, observed_at):
                    if fields["property_id"] == "2":
                        raise scan.PayloadError("boom")
                    return {"embeds": [{"title": "x", "fields": [], "footer": {"text": "x"}}]}

                build.side_effect = side_effect
                with mock.patch.object(scan.requests.Session, "post") as post:
                    rc = run_main(dry_run=False)
                post.assert_not_called()
            self.assertEqual(rc, 1)

    def test_dry_run_validates_payloads_and_fails_consistently_with_real_mode(self):
        # Both dry-run branches (uninitialized baseline and initialized) must
        # exercise the same payload-validation path a real send would, so a
        # bad candidate fails the dry run too rather than reporting success
        # for something that would actually fail live.
        for initialized in (False, True):
            with StateFixture() as fx:
                fx.write(initial_state_dict(initialized=initialized, seen=[]))
                df = make_df([base_row()])
                with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                    scan, "build_payload", side_effect=scan.PayloadError("boom")
                ), mock.patch.object(scan.requests.Session, "post") as post:
                    rc = run_main(dry_run=True)
                self.assertEqual(rc, 1, msg=f"initialized={initialized}")
                post.assert_not_called()


# ---------------------------------------------------------------------------
# Group: confirmation and partial failure
# ---------------------------------------------------------------------------


class ConfirmationAndPartialFailureTests(unittest.TestCase):
    def test_confirmed_a_then_failed_b_persists_only_a(self):
        # Uses a coherent FakeClock (not a bare no-op sleep mock): with the
        # dual-clock _await_gate check, a sleep that never actually advances
        # monotonic time would fail closed on inter-message pacing *before*
        # B is ever attempted, making this test pass for the wrong reason
        # (budget exhaustion, not B's real 500). The clock must genuinely
        # advance for this to exercise what it claims to.
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df(
                [
                    base_row(property_id="1", listing_id="1"),
                    base_row(property_id="2", listing_id="1"),
                ]
            )
            responses = [FakeResponse(200, {"id": "1"}), FakeResponse(500)]
            post_calls = []
            sleep_states = []

            def fake_post(self, url, **kwargs):
                post_calls.append(json.loads(fx.read_bytes())["seen"])
                return responses.pop(0)

            clock = FakeClock()
            real_sleep = clock.sleep

            def inspect_sleep(seconds):
                sleep_states.append(json.loads(fx.read_bytes())["seen"])
                real_sleep(seconds)

            clock.sleep = inspect_sleep
            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", new=fake_post
            ), patched_clock(clock), self.assertLogs("boca_house_hunter", level="ERROR") as logs:
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 1)
            self.assertEqual(len(post_calls), 2, "expected exactly A and B to be attempted")
            # Durable state at A's POST (before it was confirmed) had nothing
            # seen yet; by B's POST, A was already durably saved.
            self.assertEqual(post_calls[0], [])
            self.assertEqual(post_calls[1], ["1:1"])
            self.assertEqual(sleep_states, [["1:1"]])
            self.assertEqual(clock.sleep_calls, [0.5])
            self.assertTrue(
                any("event=delivery_failed" in line and "http_status=500" in line for line in logs.output)
            )
            state = json.loads(fx.read_bytes())
            self.assertEqual(state["seen"], ["1:1"])

            # A second scan must retry only B: A is already seen and must
            # not be re-sent.
            responses2 = [FakeResponse(200, {"id": "2"})]

            def fake_post2(url, **kwargs):
                return responses2.pop(0)

            clock2 = FakeClock()
            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", side_effect=fake_post2
            ) as post2, patched_clock(clock2):
                rc2 = run_main(dry_run=False)
            self.assertEqual(rc2, 0)
            post2.assert_called_once()
            state2 = json.loads(fx.read_bytes())
            self.assertEqual(state2["seen"], ["1:1", "2:1"])

    def test_200_without_valid_id_and_204_do_not_mark_seen(self):
        for response in (FakeResponse(200, {"nope": "field"}), FakeResponse(204)):
            with StateFixture() as fx:
                fx.write(initial_state_dict(initialized=True, seen=[]))
                df = make_df([base_row()])
                with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                    scan.requests.Session, "post", return_value=response
                ):
                    rc = run_main(dry_run=False)
                self.assertEqual(rc, 1)
                state = json.loads(fx.read_bytes())
                self.assertEqual(state["seen"], [])

    def test_exact_session_post_arguments(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df([base_row()])
            response = FakeResponse(200, {"id": "1"})
            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", return_value=response
            ) as post:
                run_main(dry_run=False)
            _, kwargs = post.call_args
            self.assertEqual(kwargs["params"], {"wait": "true"})
            self.assertEqual(kwargs["timeout"], (5, 15))
            self.assertFalse(kwargs["allow_redirects"])

    def test_timeout_and_5xx_and_400_stop_without_marking_seen(self):
        import requests as real_requests

        for effect in (
            real_requests.exceptions.ConnectTimeout("timeout"),
            real_requests.exceptions.ConnectionError("conn"),
        ):
            with StateFixture() as fx:
                fx.write(initial_state_dict(initialized=True, seen=[]))
                df = make_df([base_row()])
                with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                    scan.requests.Session, "post", side_effect=effect
                ):
                    rc = run_main(dry_run=False)
                self.assertEqual(rc, 1)
                self.assertEqual(json.loads(fx.read_bytes())["seen"], [])

        for status in (400, 500, 503):
            with StateFixture() as fx:
                fx.write(initial_state_dict(initialized=True, seen=[]))
                df = make_df([base_row()])
                with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                    scan.requests.Session, "post", return_value=FakeResponse(status)
                ):
                    rc = run_main(dry_run=False)
                self.assertEqual(rc, 1)
                self.assertEqual(json.loads(fx.read_bytes())["seen"], [])


# ---------------------------------------------------------------------------
# Group: rate limits and budget
# ---------------------------------------------------------------------------


class RateLimitAndBudgetTests(unittest.TestCase):
    def test_429_uses_max_valid_delay_plus_quarter_second_and_retries_same_payload(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df([base_row()])
            responses = [
                FakeResponse(429, {"retry_after": 1.5}, headers={"Retry-After": "0.5"}),
                FakeResponse(200, {"id": "1"}),
            ]
            payloads_sent = []

            def fake_post(self, url, params=None, json=None, **kwargs):
                payloads_sent.append(json)
                return responses.pop(0)

            clock = FakeClock()
            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", new=fake_post
            ), patched_clock(clock) as sleep_mock:
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 0)
            # max(1.5, 0.5) + 0.25 = 1.75s raw delay, but the actual sleep is
            # to the durable, whole-second-rounded gate (12:00:02Z), i.e. 2.0s
            # from the frozen 12:00:00Z - not the raw unrounded delay.
            sleep_mock.assert_any_call(2.0)
            self.assertEqual(payloads_sent[0], payloads_sent[1])

    def test_three_attempt_bound_then_stop(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df([base_row()])
            response = FakeResponse(429, {"retry_after": 0.1})
            call_count = {"n": 0}

            def fake_post(self, url, **kwargs):
                call_count["n"] += 1
                return response

            clock = FakeClock()
            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", new=fake_post
            ), patched_clock(clock):
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 1)
            self.assertEqual(call_count["n"], scan.MAX_POST_ATTEMPTS)
            state = json.loads(fx.read_bytes())
            self.assertIsNotNone(state["discord_not_before"])

    def test_malformed_429_delay_stops_without_guessing(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df([base_row()])
            response = FakeResponse(429, {}, headers={})
            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", return_value=response
            ):
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 1)

    def test_persisted_gate_prevents_early_send_on_next_run(self):
        with StateFixture() as fx:
            future = (FROZEN_NOW.replace(year=2099)).strftime("%Y-%m-%dT%H:%M:%SZ")
            fx.write(initial_state_dict(initialized=True, seen=[], discord_not_before=future))
            df = make_df([base_row()])
            with mock.patch.object(scan, "scrape_property") as fetch, mock.patch.object(
                scan.requests.Session, "post"
            ) as post:
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 0)
            fetch.assert_not_called()
            post.assert_not_called()

    def test_last_successful_candidate_preserves_gate_without_sleeping(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df([base_row()])
            response = FakeResponse(
                200, {"id": "1"}, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset-After": "5"}
            )
            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", return_value=response
            ), mock.patch.object(scan.time, "sleep") as sleep_mock:
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 0)
            sleep_mock.assert_not_called()
            state = json.loads(fx.read_bytes())
            self.assertIsNotNone(state["discord_not_before"])

    def test_429_gate_is_durably_saved_before_sleep_and_retry_lands_on_or_after_gate(self):
        # At 12:00:00Z, a 429 body delay of 1.5 must produce a gate of
        # 12:00:02Z (round_up(now + 1.5 + 0.25)), persisted to disk BEFORE
        # the retry sleep happens, and the retry must not fire before that
        # gate (i.e. sleep >= 2.0s, not the raw 1.75s delay). Uses a coherent
        # FakeClock (sleep advances both monotonic and UTC together) so the
        # sleep call genuinely has to happen for the retry to proceed, rather
        # than a frozen clock that can't distinguish "waited" from "didn't".
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df([base_row()])
            responses = [
                FakeResponse(429, {"retry_after": 1.5}, headers={}),
                FakeResponse(200, {"id": "1"}),
            ]

            def fake_post(self, url, **kwargs):
                return responses.pop(0)

            clock = FakeClock()
            observed = []
            real_sleep = clock.sleep

            def instrumented_sleep(seconds):
                observed.append((seconds, json.loads(fx.read_bytes())["discord_not_before"]))
                real_sleep(seconds)

            with mock.patch.object(scan, "_utcnow", side_effect=clock.utcnow), mock.patch.object(
                scan.time, "monotonic", side_effect=clock.monotonic
            ), mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", new=fake_post
            ), mock.patch.object(scan.time, "sleep", side_effect=instrumented_sleep):
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 0)
            self.assertEqual(len(observed), 1)
            sleep_seconds, gate_at_sleep_time = observed[0]
            # The gate was written to disk BEFORE this (the only) sleep call.
            self.assertEqual(gate_at_sleep_time, "2026-09-05T12:00:02Z")
            self.assertGreaterEqual(sleep_seconds, 2.0)
            state = json.loads(fx.read_bytes())
            self.assertEqual(state["seen"], ["123456:987654"])

    def test_429_retry_rechecks_gate_after_backward_utc_jump_during_sleep(self):
        # Reproduces the reviewer's adversarial probe: the wall clock moves
        # backward by one second mid-sleep (e.g. an NTP correction). A
        # single up-front sleep computation would then retry one second too
        # early; _await_gate must recheck both clocks after waking and sleep
        # again if the gate isn't genuinely satisfied yet.
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df([base_row()])
            responses = [
                FakeResponse(429, {"retry_after": 1.5}, headers={}),
                FakeResponse(200, {"id": "1"}),
            ]

            def fake_post(self, url, **kwargs):
                return responses.pop(0)

            clock = FakeClock()
            jumped = {"done": False}

            def adversarial_sleep(seconds):
                clock.sleep(seconds)
                if not jumped["done"]:
                    clock.rewind_utc_only(1.0)
                    jumped["done"] = True

            with mock.patch.object(scan, "_utcnow", side_effect=clock.utcnow), mock.patch.object(
                scan.time, "monotonic", side_effect=clock.monotonic
            ), mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", new=fake_post
            ), mock.patch.object(
                scan.time, "sleep", side_effect=adversarial_sleep
            ) as sleep_mock:
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 0)
            # A single-shot sleep-then-retry design would have slept exactly
            # once and retried early; the backward jump forces a second,
            # shorter sleep before the retry is actually allowed to proceed.
            self.assertGreaterEqual(len(sleep_mock.call_args_list), 2)
            state = json.loads(fx.read_bytes())
            self.assertEqual(state["seen"], ["123456:987654"])

    def test_429_retry_honors_monotonic_deadline_despite_forward_utc_jump(self):
        # Reproduces the reviewer's other adversarial probe: the wall clock
        # jumps forward by far more than real time actually passed (e.g.
        # while writing state) -- a UTC-only check would then see the gate
        # as already satisfied and retry immediately. The independent
        # monotonic deadline must still force the real ~1.75s wait.
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df([base_row()])
            responses = [
                FakeResponse(429, {"retry_after": 1.5}, headers={}),
                FakeResponse(200, {"id": "1"}),
            ]

            def fake_post(self, url, **kwargs):
                return responses.pop(0)

            clock = FakeClock()
            jumped = {"done": False}
            real_save_state = scan.save_state

            def save_state_then_jump(state):
                real_save_state(state)
                if not jumped["done"]:
                    clock.jump_utc_only(10.0)
                    jumped["done"] = True

            with mock.patch.object(scan, "_utcnow", side_effect=clock.utcnow), mock.patch.object(
                scan.time, "monotonic", side_effect=clock.monotonic
            ), mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", new=fake_post
            ), mock.patch.object(
                scan, "save_state", side_effect=save_state_then_jump
            ), mock.patch.object(scan.time, "sleep", side_effect=clock.sleep) as sleep_mock:
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 0)
            # Despite UTC now claiming the gate is long past, a real sleep
            # honoring the monotonic side (~1.75s) still had to happen.
            slept = [call.args[0] for call in sleep_mock.call_args_list]
            self.assertTrue(slept, "expected at least one sleep honoring the monotonic deadline")
            self.assertTrue(any(s >= 1.75 - 1e-9 for s in slept))
            state = json.loads(fx.read_bytes())
            self.assertEqual(state["seen"], ["123456:987654"])

    def test_429_retry_stops_before_sleep_when_remaining_budget_insufficient(self):
        # Reproduces the reviewer's probe: with the first POST at elapsed 125
        # seconds, a retry needing ~1.75s plus the 25s reserve (26.75s total)
        # cannot fit in the 25s actually remaining (150 - 125) and must fail
        # immediately rather than sleep and retry anyway.
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df([base_row()])
            response = FakeResponse(429, {"retry_after": 1.5}, headers={})

            def fake_post(self, url, **kwargs):
                return response

            monotonic_values = iter([0.0] + [125.0] * 10)

            with mock.patch.object(scan, "_utcnow", return_value=FROZEN_NOW), mock.patch.object(
                scan, "scrape_property", return_value=df
            ), mock.patch.object(scan.requests.Session, "post", new=fake_post), mock.patch.object(
                scan.time, "monotonic", side_effect=lambda: next(monotonic_values)
            ), mock.patch.object(scan.time, "sleep") as sleep_mock:
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 1)
            sleep_mock.assert_not_called()
            # The gate is still durably saved even though the batch then fails.
            state = json.loads(fx.read_bytes())
            self.assertIsNotNone(state["discord_not_before"])

    def test_confirmed_with_unknown_exhaustion_saves_identity_then_stops_batch(self):
        # A 200 with X-RateLimit-Remaining: 0 but no valid Reset-After cannot
        # be turned into a gate: the message WAS delivered (mark it seen and
        # save), but the batch must stop rather than guess a bucket duration
        # and keep sending.
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df(
                [
                    base_row(property_id="1", listing_id="1"),
                    base_row(property_id="2", listing_id="1"),
                ]
            )
            response = FakeResponse(200, {"id": "1"}, headers={"X-RateLimit-Remaining": "0"})
            post_calls = {"n": 0}

            def fake_post(self, url, **kwargs):
                post_calls["n"] += 1
                return response

            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", new=fake_post
            ), mock.patch.object(scan.time, "sleep") as sleep_mock:
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 1)
            sleep_mock.assert_not_called()
            self.assertEqual(post_calls["n"], 1)
            state = json.loads(fx.read_bytes())
            self.assertEqual(state["seen"], ["1:1"])

    def test_confirmed_with_unrepresentable_reset_delay_still_saves_identity(self):
        # A 200 with X-RateLimit-Remaining: 0 and a syntactically valid but
        # astronomically large Reset-After (e.g. 1e20) would overflow
        # datetime construction if not guarded: the message WAS still
        # delivered and must be confirmed/saved exactly like the
        # no-reset-header case, not lose the confirmation to an unhandled
        # exception.
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df(
                [
                    base_row(property_id="1", listing_id="1"),
                    base_row(property_id="2", listing_id="1"),
                ]
            )
            response = FakeResponse(
                200,
                {"id": "1"},
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset-After": "1e20"},
            )
            post_calls = {"n": 0}

            def fake_post(self, url, **kwargs):
                post_calls["n"] += 1
                return response

            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", new=fake_post
            ), mock.patch.object(scan.time, "sleep") as sleep_mock:
                with self.assertLogs("boca_house_hunter", level="INFO") as logs:
                    rc = run_main(dry_run=False)
            self.assertEqual(rc, 1)
            sleep_mock.assert_not_called()
            self.assertEqual(post_calls["n"], 1)
            state = json.loads(fx.read_bytes())
            self.assertEqual(state["seen"], ["1:1"])
            self.assertTrue(any("event=unexpected_error" in line for line in logs.output) is False)
            self.assertTrue(any("event=rate_limit_exhaustion_unknown" in line for line in logs.output))


# ---------------------------------------------------------------------------
# Group: latch / gate recovery
# ---------------------------------------------------------------------------


class LatchAndGateRecoveryTests(unittest.TestCase):
    def test_401_403_404_latch_and_stop(self):
        for status in (401, 403, 404):
            with StateFixture() as fx:
                fx.write(initial_state_dict(initialized=True, seen=[]))
                df = make_df([base_row()])
                with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                    scan.requests.Session, "post", return_value=FakeResponse(status)
                ):
                    rc = run_main(dry_run=False)
                self.assertEqual(rc, 1)
                state = json.loads(fx.read_bytes())
                self.assertEqual(
                    state["disabled_webhook_sha256"], hashlib.sha256(CANONICAL_WEBHOOK.encode()).hexdigest()
                )

    def test_same_disabled_webhook_makes_no_external_calls(self):
        digest = hashlib.sha256(CANONICAL_WEBHOOK.encode()).hexdigest()
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[], disabled_webhook_sha256=digest))
            with mock.patch.object(scan, "scrape_property") as fetch:
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 1)
            fetch.assert_not_called()

    def test_changed_webhook_permits_recovery_without_clearing_seen(self):
        old_digest = hashlib.sha256(b"https://discord.com/api/v10/webhooks/1/oldtoken").hexdigest()
        with StateFixture() as fx:
            fx.write(
                initial_state_dict(
                    initialized=True, seen=["999:999"], disabled_webhook_sha256=old_digest
                )
            )
            df = make_df([base_row()])
            response = FakeResponse(200, {"id": "1"})
            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", return_value=response
            ):
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 0)
            state = json.loads(fx.read_bytes())
            self.assertIn("999:999", state["seen"])
            self.assertIsNone(state["disabled_webhook_sha256"])

    def test_dry_run_ignores_latch_and_gate(self):
        digest = hashlib.sha256(CANONICAL_WEBHOOK.encode()).hexdigest()
        future = "2099-01-01T00:00:00Z"
        with StateFixture() as fx:
            fx.write(
                initial_state_dict(
                    initialized=True,
                    seen=[],
                    disabled_webhook_sha256=digest,
                    discord_not_before=future,
                )
            )
            df = make_df([base_row()])
            with mock.patch.object(scan, "scrape_property", return_value=df):
                rc = run_main(dry_run=True)
            self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# Group: entry point, exceptions, and logs
# ---------------------------------------------------------------------------


class EntryPointAndLoggingTests(unittest.TestCase):
    def test_import_has_no_side_effects(self):
        with mock.patch.object(scan, "scrape_property") as fetch, mock.patch.object(
            scan.requests.Session, "post"
        ) as post:
            import importlib

            importlib.reload(scan)
            fetch.assert_not_called()
            post.assert_not_called()

    def test_invalid_dry_run_value_fails_before_network(self):
        with StateFixture():
            with mock.patch.dict("os.environ", {"DRY_RUN": "2"}), mock.patch.object(
                scan, "scrape_property"
            ) as fetch:
                rc = scan.main()
            self.assertEqual(rc, 1)
            fetch.assert_not_called()

    def test_missing_webhook_fails_before_network_even_at_baseline(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict())
            with mock.patch.object(scan, "scrape_property") as fetch:
                rc = run_main(dry_run=False, webhook=None)
            self.assertEqual(rc, 1)
            fetch.assert_not_called()

    def test_unexpected_exception_is_caught_and_logged_safely(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            with mock.patch.object(scan, "fetch_listings", side_effect=Exception("totally unexpected")):
                with self.assertLogs("boca_house_hunter", level="ERROR") as logs:
                    rc = run_main(dry_run=False)
            self.assertEqual(rc, 1)
            self.assertTrue(any("scrape_failed" in line for line in logs.output))

    def test_true_outer_safety_net_catches_unexpected_exception_with_phase(self):
        # Distinct from the scrape-specific catch above: this raises from a
        # point _main_impl does not wrap in its own try/except, so it must be
        # main()'s outermost handler that catches it, logging a phase along
        # with the exception class rather than just the class alone.
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df([base_row()])
            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan, "process_dataframe", side_effect=RuntimeError("boom")
            ):
                with self.assertLogs("boca_house_hunter", level="ERROR") as logs:
                    rc = run_main(dry_run=False)
            self.assertEqual(rc, 1)
            self.assertTrue(
                any(
                    "event=unexpected_error" in line and "phase=" in line and "RuntimeError" in line
                    for line in logs.output
                )
            )

    def test_malformed_rows_log_row_index_not_raw_identity(self):
        df = make_df([base_row(property_id="not-numeric-id-9x9x9x")])
        with self.assertLogs("boca_house_hunter", level="WARNING") as logs:
            eligible, counts = scan.process_dataframe(df)
        self.assertEqual(counts.malformed_identity, 1)
        matching = [line for line in logs.output if "event=malformed_identity_row" in line]
        self.assertEqual(len(matching), 1)
        self.assertIn("row_index=0", matching[0])
        self.assertNotIn("not-numeric-id-9x9x9x", matching[0])

    def test_baseline_creation_logs_dedicated_event_with_eligible_count(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict())
            df = make_df([base_row()])
            with mock.patch.object(scan, "scrape_property", return_value=df):
                with self.assertLogs("boca_house_hunter", level="INFO") as logs:
                    rc = run_main(dry_run=False)
            self.assertEqual(rc, 0)
            self.assertTrue(
                any(
                    "event=baseline_created" in line and "eligible_count=1" in line
                    for line in logs.output
                )
            )

    def test_failure_log_does_not_leak_webhook_token(self):
        secret_token = "super-secret-token-value"
        webhook = f"https://discord.com/api/webhooks/123456789012345678/{secret_token}"
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df([base_row()])
            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", side_effect=RuntimeError(f"leak? {webhook}")
            ):
                with self.assertLogs("boca_house_hunter", level="ERROR") as logs:
                    rc = run_main(dry_run=False, webhook=webhook)
            self.assertEqual(rc, 1)
            for line in logs.output:
                self.assertNotIn(secret_token, line)


# ---------------------------------------------------------------------------
# Literal contract checks against the spec / repo files
# ---------------------------------------------------------------------------


class LiteralContractTests(unittest.TestCase):
    def _spec_text(self) -> str:
        return SPEC_PATH.read_text(encoding="utf-8")

    def _extract_fenced_block(self, anchor: str, lang: str) -> str:
        spec = self._spec_text()
        idx = spec.index(anchor)
        fence_start = spec.index(f"```{lang}", idx)
        body_start = spec.index("\n", fence_start) + 1
        fence_end = spec.index("```", body_start)
        return spec[body_start:fence_end]

    def test_requirements_txt_matches_spec_block(self):
        expected = self._extract_fenced_block("Use exactly these direct requirements", "text")
        actual = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertEqual(actual.replace("\r\n", "\n"), expected)

    def test_workflow_yaml_matches_spec_block_after_newline_normalization(self):
        expected = self._extract_fenced_block("## 8. Exact GitHub Actions workflow", "yaml")
        actual = (REPO_ROOT / ".github" / "workflows" / "scan.yml").read_text(encoding="utf-8")
        self.assertEqual(actual.replace("\r\n", "\n").rstrip("\n"), expected.rstrip("\n"))

    def test_gitignore_has_required_entries_and_tracks_seen_json(self):
        text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        for entry in (".venv/", "__pycache__/", "*.py[cod]", ".env", "seen.json.tmp"):
            self.assertIn(entry, text)
        self.assertNotIn("seen.json\n", text.replace("seen.json.tmp", ""))

    def test_initial_schema_round_trips_through_load_state(self):
        """Validates the §5 initial schema itself via a harness fixture.

        Deliberately does NOT read the repository's live seen.json: that file
        is expected to evolve after a real deployment initializes it, and a
        recurring test asserting it stays byte-identical to the uninitialized
        schema would fail forever after the first successful baseline commit.
        The one-time check that the *shipped* seen.json matches this schema
        belongs to the plan's pre-commissioning verification command, not to
        this recurring suite.
        """
        with StateFixture() as fx:
            fx.write(initial_state_dict())
            loaded = scan.load_state()
            self.assertEqual(loaded, initial_state_dict())


# ---------------------------------------------------------------------------
# Offline CLI dry-run entry point tests (exact names required by the plan)
# ---------------------------------------------------------------------------


class OfflineCliDryRunTests(unittest.TestCase):
    def _run_copied_script(self, seen_state: dict, rows=None, fail_second_payload=False):
        tmpdir = tempfile.TemporaryDirectory()
        try:
            script_copy = Path(tmpdir.name) / "scan.py"
            shutil.copyfile(REPO_ROOT / "scan.py", script_copy)
            self.assertEqual(script_copy.read_bytes(), (REPO_ROOT / "scan.py").read_bytes())
            initial_bytes = (json.dumps(seen_state, indent=2) + "\n").encode("utf-8")
            (Path(tmpdir.name) / "seen.json").write_bytes(initial_bytes)

            original_cwd = Path.cwd()
            other_cwd = tempfile.mkdtemp()
            import os as _os

            env_backup = dict(_os.environ)
            _os.environ["DRY_RUN"] = "1"
            _os.environ.pop("DISCORD_WEBHOOK_URL", None)

            df = make_df(rows if rows is not None else [base_row(), base_row(property_id="999", hoa_fee=None)])

            payload_attempts = []

            def fetch_fixture(**kwargs):
                # Install a fault/observation seam in the executing copy's
                # globals, not in imported scan (which would test nothing).
                # The copied file remains byte-identical to production.
                frame = inspect.currentframe().f_back
                try:
                    while frame.f_globals.get("__file__") != str(script_copy):
                        frame = frame.f_back
                    namespace = frame.f_globals
                    build = namespace["build_payload"]

                    def observed_build(fields, observed_at):
                        payload_attempts.append(fields["property_id"])
                        if fail_second_payload and len(payload_attempts) == 2:
                            raise namespace["PayloadError"]("injected later-candidate failure")
                        return build(fields, observed_at)

                    namespace["build_payload"] = observed_build
                finally:
                    del frame
                return df

            write_attempts = []
            real_open = open

            def read_only_open(file, mode="r", *args, **kwargs):
                if any(flag in mode for flag in "wax+"):
                    write_attempts.append("write")
                    raise AssertionError("dry run must never open a file for writing")
                return real_open(file, mode, *args, **kwargs)

            def blocked_sleep(*args, **kwargs):
                raise AssertionError("dry run must never sleep")

            try:
                _os.chdir(other_cwd)
                with mock.patch("homeharvest.scrape_property", side_effect=fetch_fixture) as fetch, mock.patch(
                    "requests.Session.post", side_effect=AssertionError("dry run must never POST")
                ) as post, mock.patch("time.sleep", side_effect=blocked_sleep) as sleep, mock.patch(
                    "builtins.open", side_effect=read_only_open
                ), mock.patch.object(io, "open", side_effect=read_only_open), mock.patch(
                    "os.replace", side_effect=AssertionError("dry run must never replace state")
                ) as replace, self.assertLogs(
                    "boca_house_hunter", level="INFO"
                ) as logs:
                    try:
                        runpy.run_path(str(script_copy), run_name="__main__")
                        exit_code = 0
                    except SystemExit as exc:
                        exit_code = exc.code or 0
                fetch.assert_called_once_with(**FETCH_KWARGS)
                post.assert_not_called()
                sleep.assert_not_called()
                replace.assert_not_called()
                self.assertEqual(write_attempts, [])
                self.assertFalse((Path(tmpdir.name) / "seen.json.tmp").exists())
                if fail_second_payload:
                    self.assertEqual(payload_attempts, ["1", "2"])
            finally:
                _os.chdir(original_cwd)
                _os.environ.clear()
                _os.environ.update(env_backup)
                shutil.rmtree(other_cwd, ignore_errors=True)

            after_bytes = (Path(tmpdir.name) / "seen.json").read_bytes()
            return exit_code, initial_bytes, after_bytes, logs.output
        finally:
            tmpdir.cleanup()

    def test_offline_cli_dry_run_baseline(self):
        # Fixture is one qualifying row (base_row) plus one unknown-HOA row
        # (property_id="999"), so the baseline candidate count is 1.
        exit_code, before, after, logs = self._run_copied_script(initial_state_dict())
        self.assertEqual(exit_code, 0)
        self.assertEqual(before, after)
        summary_lines = [line for line in logs if "event=scan_summary" in line]
        self.assertEqual(len(summary_lines), 1)
        self.assertIn("candidate=1", summary_lines[0])
        assert_summary(self, logs, total_fetched=2, hoa_unknown=1, eligible=1, candidate=1)

    def test_offline_cli_dry_run_initialized(self):
        # Fetched rows contain BOTH an already-seen eligible pair (so
        # already_seen reflects a genuine eligible/seen overlap, not just
        # unrelated seen history) and one genuinely new eligible pair, so
        # this exercises a real would-send candidate rather than a
        # permanently-zero count.
        exit_code, before, after, logs = self._run_copied_script(
            initial_state_dict(initialized=True, seen=["555555:111111", "888:1"]),
            rows=[base_row(), base_row(property_id="555555", listing_id="111111")],
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(before, after)
        summary_lines = [line for line in logs if "event=scan_summary" in line]
        self.assertEqual(len(summary_lines), 1)
        self.assertIn("candidate=1", summary_lines[0])
        self.assertIn("already_seen=1", summary_lines[0])
        self.assertIn("confirmed=0", summary_lines[0])
        self.assertIn("unsent=1", summary_lines[0])
        assert_summary(self, logs, total_fetched=2, eligible=2, already_seen=1, candidate=1, unsent=1)

    def test_dry_run_cli_entrypoint_ignores_disabled_digest_and_future_gate(self):
        # Named to avoid matching the "-k offline_cli_dry_run" filter used by
        # plan step 11, which expects exactly the two exactly-named tests
        # above to run under that filter - this is supplementary coverage
        # through the same real entry point, not a required-name test.
        # Spec section 6: dry runs ignore both the disabled-webhook latch and
        # a future discord_not_before gate, and require no webhook at all -
        # they still fully evaluate what a real run would do.
        future = "2099-01-01T00:00:00Z"
        exit_code, before, after, logs = self._run_copied_script(
            initial_state_dict(
                initialized=True,
                seen=["555555:111111", "888:1"],
                disabled_webhook_sha256="a" * 64,
                discord_not_before=future,
            ),
            rows=[base_row(), base_row(property_id="555555", listing_id="111111")],
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(before, after)
        summary_lines = [line for line in logs if "event=scan_summary" in line]
        self.assertEqual(len(summary_lines), 1)
        self.assertIn("candidate=1", summary_lines[0])
        assert_summary(self, logs, total_fetched=2, eligible=2, already_seen=1, candidate=1, unsent=1)

    def test_copied_cli_later_payload_failure_has_no_effects(self):
        exit_code, before, after, logs = self._run_copied_script(
            initial_state_dict(initialized=True),
            rows=[base_row(property_id="1"), base_row(property_id="2")],
            fail_second_payload=True,
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(before, after)
        self.assertTrue(any("event=payload_invalid" in line for line in logs))
        assert_summary(self, logs, total_fetched=2, eligible=2, candidate=2, unsent=2)


class RequiredTimingTests(unittest.TestCase):
    def test_post_reserve_boundary(self):
        for fetch_seconds, expected_posts in ((125, 1), (125.001, 0)):
            with self.subTest(fetch_seconds=fetch_seconds), StateFixture() as fx:
                fx.write(initial_state_dict(initialized=True))
                clock = FakeClock(start_monotonic=0)

                def fetch(**kwargs):
                    clock.sleep(fetch_seconds)
                    return make_df([base_row()])

                with patched_clock(clock) as sleep, mock.patch.object(
                    scan, "scrape_property", side_effect=fetch
                ), mock.patch.object(scan.requests.Session, "post", return_value=FakeResponse(200, {"id": "1"})) as post:
                    rc = run_main(False)
                self.assertEqual(post.call_count, expected_posts)
                self.assertEqual(rc, 0 if expected_posts else 1)
                sleep.assert_not_called()
                self.assertEqual(scan.load_state()["seen"], ["123456:987654"] if expected_posts else [])

    def test_sleep_reserve_boundary_and_oversleep(self):
        for fetch_seconds, overrun, expected_posts, expected_sleeps in (
            (123, 0, 2, 1), (123.001, 0, 1, 0), (123, 0.1, 1, 1),
        ):
            with self.subTest(fetch_seconds=fetch_seconds, overrun=overrun), StateFixture() as fx:
                fx.write(initial_state_dict(initialized=True))
                clock = FakeClock(start_monotonic=0)

                def fetch(**kwargs):
                    # Simulate elapsed fetch time without changing UTC's whole-second phase.
                    clock._mono += fetch_seconds
                    return make_df([base_row()])

                attempts = []

                def post(*args, **kwargs):
                    attempts.append(clock.monotonic())
                    self.assertGreaterEqual(150 - attempts[-1], 25)
                    if len(attempts) == 1:
                        return FakeResponse(429, {"retry_after": 1.5})
                    self.assertEqual(scan.load_state()["discord_not_before"], "2026-09-05T12:00:02Z")
                    self.assertGreaterEqual(clock.utcnow(), FROZEN_NOW + timedelta(seconds=2))
                    return FakeResponse(200, {"id": "1"})

                def sleep(seconds):
                    self.assertEqual(scan.load_state()["discord_not_before"], "2026-09-05T12:00:02Z")
                    clock.sleep(seconds + overrun)

                with patched_clock(clock), mock.patch.object(scan.time, "sleep", side_effect=sleep) as sleeper, mock.patch.object(
                    scan, "scrape_property", side_effect=fetch
                ), mock.patch.object(scan.requests.Session, "post", side_effect=post):
                    rc = run_main(False)
                self.assertEqual(len(attempts), expected_posts)
                self.assertEqual(sleeper.call_count, expected_sleeps)
                self.assertEqual(rc, 0 if expected_posts == 2 else 1)
                if rc:
                    self.assertEqual(scan.load_state()["seen"], [])
                    self.assertEqual(scan.load_state()["discord_not_before"], "2026-09-05T12:00:02Z")

    def test_both_clocks_and_disk_at_retry_or_exhausted_success_next_post(self):
        for success in (False, True):
            for movement in ("backward_during_sleep", "forward_during_save"):
                with self.subTest(success=success, movement=movement), StateFixture() as fx:
                    fx.write(initial_state_dict(initialized=True))
                    clock = FakeClock(start_monotonic=0)
                    rows = [base_row(property_id="1", listing_id="1")]
                    if success:
                        rows.append(base_row(property_id="2", listing_id="1"))
                    attempts = []
                    save = scan.save_state

                    def save_and_jump(state):
                        save(state)
                        if movement == "forward_during_save":
                            clock.jump_utc_only(10)

                    def sleep(seconds):
                        state = scan.load_state()
                        self.assertEqual(state["seen"], ["1:1"] if success else [])
                        self.assertEqual(state["discord_not_before"], "2026-09-05T12:00:02Z")
                        clock.sleep(seconds)
                        if movement == "backward_during_sleep" and len(clock.sleep_calls) == 1:
                            clock.rewind_utc_only(1)

                    def post(*args, **kwargs):
                        attempts.append(kwargs["json"])
                        if len(attempts) == 1:
                            return (FakeResponse(200, {"id": "1"}, {
                                "X-RateLimit-Remaining": "0", "X-RateLimit-Reset-After": "1.5"
                            }) if success else FakeResponse(429, {"retry_after": 1.5}))
                        self.assertGreaterEqual(clock.monotonic(), 1.75)
                        self.assertGreaterEqual(clock.utcnow(), FROZEN_NOW + timedelta(seconds=2))
                        state = scan.load_state()
                        self.assertEqual(state["seen"], ["1:1"] if success else [])
                        self.assertEqual(state["discord_not_before"], "2026-09-05T12:00:02Z")
                        return FakeResponse(200, {"id": "2"})

                    with patched_clock(clock), mock.patch.object(scan.time, "sleep", side_effect=sleep), mock.patch.object(
                        scan, "save_state", side_effect=save_and_jump
                    ), mock.patch.object(scan, "scrape_property", return_value=make_df(rows)), mock.patch.object(
                        scan.requests.Session, "post", side_effect=post
                    ):
                        self.assertEqual(run_main(False), 0)
                    self.assertEqual(len(attempts), 2)
                    if not success:
                        self.assertEqual(attempts[0], attempts[1])

    def test_response_created_long_gates_suppress_next_run(self):
        for delay in (31, 360):
            for success in (False, True):
                with self.subTest(delay=delay, success=success), StateFixture() as fx:
                    fx.write(initial_state_dict(initialized=True))
                    response = (FakeResponse(200, {"id": "1"}, {
                        "X-RateLimit-Remaining": "0", "X-RateLimit-Reset-After": str(delay)
                    }) if success else FakeResponse(429, {"retry_after": delay}))
                    with patched_clock(FakeClock()) as sleep, mock.patch.object(
                        scan, "scrape_property", return_value=make_df([
                            base_row(property_id="1", listing_id="1"), base_row(property_id="2", listing_id="1")
                        ])
                    ), mock.patch.object(scan.requests.Session, "post", return_value=response) as post:
                        self.assertEqual(run_main(False), 1)
                    post.assert_called_once()
                    sleep.assert_not_called()
                    state = scan.load_state()
                    self.assertEqual(state["seen"], ["1:1"] if success else [])
                    self.assertEqual(state["discord_not_before"],
                                     (FROZEN_NOW + timedelta(seconds=delay + 1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
                    before = fx.read_bytes()
                    with patched_clock(FakeClock()) as sleep, mock.patch.object(scan, "scrape_property") as fetch, mock.patch.object(
                        scan.requests.Session, "post"
                    ) as post, mock.patch.object(scan, "save_state") as save:
                        self.assertEqual(run_main(False), 0)
                    for effect in (fetch, post, sleep, save):
                        effect.assert_not_called()
                    self.assertEqual(fx.read_bytes(), before)


class RequiredAccountingTests(unittest.TestCase):
    def test_accepted_integer_and_leading_zero_ids_survive_save_and_reload(self):
        for property_id in (123456, np.int64(123456), 10**63, "000123"):
            with self.subTest(property_id=property_id), StateFixture() as fx:
                fx.write(initial_state_dict())
                df = make_df([base_row()]).astype(object)
                df.at[0, "property_id"] = property_id
                with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                    scan.requests.Session, "post"
                ) as post, mock.patch.object(scan, "save_state", wraps=scan.save_state) as save:
                    self.assertEqual(run_main(False), 0)
                post.assert_not_called()
                save.assert_called_once()
                self.assertEqual(scan.load_state()["seen"], [f"{property_id}:987654"])

    def test_oversized_integer_mixed_dataframe_and_repeated_index_labels(self):
        for repeated_index in (False, True):
            with self.subTest(repeated_index=repeated_index), StateFixture() as fx:
                fx.write(initial_state_dict(initialized=True))
                df = make_df([base_row(property_id="1"), base_row(property_id="2")]).astype(object)
                df.at[0, "property_id"] = 10**5000
                if repeated_index:
                    df.index = [7, 7]
                with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                    scan.requests.Session, "post", return_value=FakeResponse(200, {"id": "1"})
                ) as post, self.assertLogs("boca_house_hunter", level="INFO") as logs:
                    self.assertEqual(run_main(False), 0)
                post.assert_called_once()
                self.assertEqual(scan.load_state()["seen"], ["2:987654"])
                assert_summary(self, logs.output, total_fetched=2, malformed_identity=1,
                               eligible=1, candidate=1, confirmed=1)
                self.assertFalse(any("unexpected_error" in line for line in logs.output))

    def test_payload_failure_summaries_in_all_modes(self):
        for initialized in (False, True):
            for dry_run in (False, True):
                with self.subTest(initialized=initialized, dry_run=dry_run), StateFixture() as fx:
                    fx.write(initial_state_dict(initialized=initialized))
                    before = fx.read_bytes()
                    with mock.patch.object(scan, "scrape_property", return_value=make_df([
                        base_row(property_id="1"), base_row(property_id="2")
                    ])), mock.patch.object(scan, "build_payload", side_effect=scan.PayloadError("injected")), mock.patch.object(
                        scan.requests.Session, "post"
                    ) as post, mock.patch.object(scan, "save_state") as save, self.assertLogs("boca_house_hunter", level="INFO") as logs:
                        self.assertEqual(run_main(dry_run), 1)
                    post.assert_not_called()
                    save.assert_not_called()
                    self.assertEqual(fx.read_bytes(), before)
                    assert_summary(self, logs.output, total_fetched=2, eligible=2, candidate=2,
                                   unsent=2 if initialized else 0)

    def test_confirmation_counts_survive_later_post_gate_save_and_unexpected_save_failures(self):
        for failure in ("later_post", "later_gate_save", "unexpected_confirmation_save"):
            with self.subTest(failure=failure), StateFixture() as fx:
                fx.write(initial_state_dict(initialized=True))
                responses = [FakeResponse(200, {"id": "1"}),
                             RuntimeError("injected") if failure == "later_post" else FakeResponse(429, {"retry_after": 1.5})]
                save = scan.save_state
                saves = []

                def failing_save(state):
                    saves.append(state)
                    if failure == "unexpected_confirmation_save":
                        raise RuntimeError("injected")
                    if len(saves) == 2:
                        raise OSError("injected")
                    save(state)

                with patched_clock(FakeClock()) as sleep, mock.patch.object(scan, "scrape_property", return_value=make_df([
                    base_row(property_id="1", listing_id="1"), base_row(property_id="2", listing_id="1")
                ])), mock.patch.object(scan.requests.Session, "post", side_effect=responses) as post, mock.patch.object(
                    scan, "save_state", side_effect=failing_save
                ), self.assertLogs("boca_house_hunter", level="INFO") as logs:
                    self.assertEqual(run_main(False), 1)
                self.assertEqual(post.call_count, 1 if failure == "unexpected_confirmation_save" else 2)
                self.assertEqual(sleep.call_count, 0 if failure == "unexpected_confirmation_save" else 1)
                self.assertEqual(scan.load_state()["seen"], [] if failure == "unexpected_confirmation_save" else ["1:1"])
                assert_summary(self, logs.output, total_fetched=2, eligible=2, candidate=2, confirmed=1, unsent=1)

    def test_no_candidate_recovery_save_failure_counts(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=["123456:987654"], disabled_webhook_sha256="a" * 64))
            before = fx.read_bytes()
            with mock.patch.object(scan, "scrape_property", return_value=make_df([base_row()])), mock.patch.object(
                scan, "save_state", side_effect=OSError("injected")
            ) as save, mock.patch.object(scan.requests.Session, "post") as post, self.assertLogs("boca_house_hunter", level="INFO") as logs:
                self.assertEqual(run_main(False), 1)
            save.assert_called_once()
            post.assert_not_called()
            self.assertEqual(fx.read_bytes(), before)
            assert_summary(self, logs.output, total_fetched=1, eligible=1, already_seen=1)

    def test_early_failure_reports_unobserved_counts(self):
        with StateFixture() as fx:
            fx.state_path.write_text("{}", encoding="utf-8")
            with mock.patch.object(scan, "scrape_property") as fetch, mock.patch.object(
                scan.requests.Session, "post"
            ) as post, mock.patch.object(scan, "save_state") as save, self.assertLogs("boca_house_hunter", level="INFO") as logs:
                self.assertEqual(run_main(False), 1)
            for effect in (fetch, post, save):
                effect.assert_not_called()
            assert_summary(self, logs.output, observed=False)

    def test_duplicate_totals_and_seen_overlap_exclude_absent_history(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=["1:1", "8:1", "9:1"]))
            rows = [base_row(property_id="1", listing_id="1")] * 2 + [
                base_row(property_id="2", listing_id="1"), base_row(property_id="2", listing_id="1", hoa_fee=20),
                base_row(property_id="3", listing_id="1"),
            ]
            with mock.patch.object(scan, "scrape_property", return_value=make_df(rows)), self.assertLogs("boca_house_hunter", level="INFO") as logs:
                self.assertEqual(run_main(True), 0)
            assert_summary(self, logs.output, total_fetched=5, duplicate_group=2, conflicting_duplicate=1,
                           eligible=2, already_seen=1, candidate=1, unsent=1)


class RequiredPersistenceTests(unittest.TestCase):
    def test_actual_write_flush_fsync_close_replace_order(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict())
            events = []
            real_open, real_fsync, real_replace = open, scan.os.fsync, scan.os.replace

            class RecordingFile:
                def __enter__(self):
                    return self

                def write(self, text):
                    events.append("write")
                    return self.handle.write(text)

                def flush(self):
                    events.append("flush")
                    return self.handle.flush()

                def fileno(self):
                    return self.handle.fileno()

                def __exit__(self, *exc):
                    self.handle.close()
                    events.append("close")

            def recording_open(*args, **kwargs):
                self.assertEqual(args, (fx.tmp_path, "w"))
                self.assertEqual(kwargs, dict(encoding="utf-8", newline="\n"))
                handle = RecordingFile()
                handle.handle = real_open(*args, **kwargs)
                return handle

            def fsync(fd):
                events.append("fsync")
                real_fsync(fd)

            def replace(source, target):
                events.append("replace")
                self.assertEqual((source, target), (fx.tmp_path, fx.state_path))
                real_replace(source, target)

            with mock.patch("builtins.open", side_effect=recording_open), mock.patch.object(
                scan.os, "fsync", side_effect=fsync
            ), mock.patch.object(scan.os, "replace", side_effect=replace):
                scan.save_state(initial_state_dict(initialized=True, seen=["1:1"]))
            self.assertEqual(events, ["write", "flush", "fsync", "close", "replace"])
            self.assertEqual(scan.load_state()["seen"], ["1:1"])
            self.assertFalse(fx.tmp_path.exists())

    def test_persistence_failures_preserve_bytes_and_stop_before_sleep_or_next_post(self):
        for failure in ("serialization", "write", "flush", "fsync", "replace"):
            for response_kind in ("confirmed", "rate_limited"):
                with self.subTest(failure=failure, response_kind=response_kind), StateFixture() as fx, ExitStack() as stack:
                    fx.write(initial_state_dict(initialized=True))
                    before = fx.read_bytes()
                    real_open = open

                    @contextmanager
                    def failing_file(*args, **kwargs):
                        with real_open(*args, **kwargs) as handle:
                            proxy = mock.Mock(wraps=handle)
                            getattr(proxy, failure).side_effect = OSError("injected")
                            yield proxy

                    if failure == "serialization":
                        stack.enter_context(mock.patch.object(scan.json, "dumps", side_effect=ValueError("injected")))
                    elif failure in ("write", "flush"):
                        stack.enter_context(mock.patch("builtins.open", side_effect=failing_file))
                    else:
                        stack.enter_context(mock.patch.object(scan.os, failure, side_effect=OSError("injected")))
                    response = FakeResponse(200, {"id": "1"}) if response_kind == "confirmed" else FakeResponse(429, {"retry_after": 1.5})
                    with patched_clock(FakeClock()) as sleep, mock.patch.object(scan, "scrape_property", return_value=make_df([
                        base_row(property_id="1"), base_row(property_id="2")
                    ])), mock.patch.object(scan.requests.Session, "post", return_value=response) as post, self.assertLogs(
                        "boca_house_hunter", level="INFO"
                    ) as logs:
                        self.assertEqual(run_main(False), 1)
                    post.assert_called_once()
                    sleep.assert_not_called()
                    self.assertEqual(fx.read_bytes(), before)
                    self.assertTrue(any("event=state_write_failed" in line for line in logs.output))
                    assert_summary(self, logs.output, total_fetched=2, eligible=2, candidate=2,
                                   confirmed=int(response_kind == "confirmed"), unsent=1 if response_kind == "confirmed" else 2)

    def test_recovery_ordering_and_exactly_one_healthy_save(self):
        for scenario in ("changed_secret_future_gate", "expired_gate_failed_fetch", "no_candidates", "baseline"):
            with self.subTest(scenario=scenario), StateFixture() as fx:
                baseline = scenario == "baseline"
                fx.write(initial_state_dict(
                    initialized=not baseline, seen=[] if baseline else ["123456:987654"],
                    disabled_webhook_sha256="a" * 64,
                    discord_not_before="2099-01-01T00:00:00Z" if scenario == "changed_secret_future_gate" else "2020-01-01T00:00:00Z",
                ))
                before = fx.read_bytes()
                with patched_clock(FakeClock()) as sleep, mock.patch.object(scan, "scrape_property", return_value=make_df([base_row()])) as fetch, mock.patch.object(
                    scan.requests.Session, "post"
                ) as post, mock.patch.object(scan, "save_state", wraps=scan.save_state) as save:
                    if scenario == "expired_gate_failed_fetch":
                        fetch.side_effect = RuntimeError("injected")
                    self.assertEqual(run_main(False), 1 if scenario == "expired_gate_failed_fetch" else 0)
                self.assertEqual(fetch.call_count, 0 if scenario == "changed_secret_future_gate" else 1)
                post.assert_not_called()
                sleep.assert_not_called()
                healthy = scenario in ("no_candidates", "baseline")
                self.assertEqual(save.call_count, int(healthy))
                if healthy:
                    self.assertEqual(scan.load_state(), initial_state_dict(initialized=True, seen=["123456:987654"]))
                else:
                    self.assertEqual(fx.read_bytes(), before)
                self.assertFalse(fx.tmp_path.exists())


class RequiredBoundaryTests(unittest.TestCase):
    def test_confirmation_shapes_ids_statuses_and_read_timeout(self):
        responses = [FakeResponse(200, body) for body in (
            [], [dict(id="1")], "1", 1, True, {}, {"id": None}, {"id": 1},
            {"id": True}, {"id": ""}, {"id": " 1"}, {"id": "1.0"}, {"id": "١"},
        )]
        responses += [FakeResponse(200, raise_json=True)]
        responses += [FakeResponse(status, {"id": "1"}) for status in (201, 202, 204, 206, 301, 302, 307, 308)]
        responses += [scan.requests.ReadTimeout("injected")]
        for case, response in enumerate(responses):
            with self.subTest(case=case), StateFixture() as fx:
                fx.write(initial_state_dict(initialized=True))
                before = fx.read_bytes()
                with mock.patch.object(scan, "scrape_property", return_value=make_df([base_row()])), mock.patch.object(
                    scan.requests.Session, "post", side_effect=[response]
                ) as post, mock.patch.object(scan.time, "sleep") as sleep, mock.patch.object(scan, "save_state") as save:
                    self.assertEqual(run_main(False), 1)
                post.assert_called_once()
                self.assertFalse(post.call_args.kwargs["allow_redirects"])
                sleep.assert_not_called()
                save.assert_not_called()
                self.assertEqual(fx.read_bytes(), before)

    def test_exact_full_payload_and_post_fetch_observation_time(self):
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True))
            clock = FakeClock()

            def fetch(**kwargs):
                clock.sleep(7)
                return make_df([base_row()])

            with patched_clock(clock), mock.patch.object(scan, "scrape_property", side_effect=fetch), mock.patch.object(
                scan.requests.Session, "post", return_value=FakeResponse(200, {"id": "1"})
            ) as post:
                self.assertEqual(run_main(False), 0)
            self.assertEqual(post.call_args.kwargs["json"], {
                "username": "Boca House Hunter", "allowed_mentions": {"parse": []},
                "embeds": [{
                    "title": "New match: 123 Main St, Boca Raton, FL 33432",
                    "url": base_row()["property_url"], "color": 3066993,
                    "fields": [
                        {"name": "Price", "value": "$400,000", "inline": True},
                        {"name": "Size", "value": "2,000 sq ft", "inline": True},
                        {"name": "Beds", "value": "3", "inline": True},
                        {"name": "Baths", "value": "2 full / 1 half", "inline": True},
                        {"name": "HOA fee", "value": "$0 reported; association status unverified", "inline": False},
                        {"name": "Listed", "value": "2026-09-01 (source)", "inline": True},
                    ],
                    "footer": {"text": "Realtor.com via HomeHarvest | 123456:987654"},
                    "timestamp": "2026-09-05T12:00:07Z",
                }],
            })

    def test_absent_optional_columns_through_delivery(self):
        optional = ("formatted_address", "full_street_line", "zip_code", "beds", "full_baths", "half_baths", "list_date")
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True))
            df = make_df([base_row()]).drop(columns=list(optional))
            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", return_value=FakeResponse(200, {"id": "1"})
            ) as post:
                self.assertEqual(run_main(False), 0)
            embed = post.call_args.kwargs["json"]["embeds"][0]
            self.assertEqual(embed["title"], "New match: Boca Raton, FL — property 123456")
            self.assertEqual([field["value"] for field in embed["fields"]], [
                "$400,000", "2,000 sq ft", "Unknown", "Unknown full / Unknown half",
                "$0 reported; association status unverified", "Unknown",
            ])

    def test_actual_payload_numeric_limits_and_rounding(self):
        for exponent in (26, 40, 43, 44, 70):
            with self.subTest(exponent=exponent):
                fields = scan.process_dataframe(make_df([base_row(
                    sqft=Decimal(10) ** exponent, beds=Decimal(10) ** exponent,
                    list_price="400000.005",
                )]))[0]["123456:987654"]
                embed = scan.build_payload(fields, FROZEN_NOW)["embeds"][0]
                values = {field["name"]: field["value"] for field in embed["fields"]}
                size = f"{10**exponent:,} sq ft"
                beds = str(10**exponent)
                self.assertEqual(values["Price"], "$400,000.01")
                self.assertEqual(values["Size"], size if len(size) <= 64 else "Unknown")
                self.assertEqual(values["Beds"], beds if len(beds) <= 64 else "Unknown")
                if exponent == 43:
                    self.assertEqual(len(values["Size"]), 64)

    def test_actual_payload_rejects_field_footer_and_aggregate_overflow(self):
        fields = scan.process_dataframe(make_df([base_row()]))[0]["123456:987654"]
        for message in ("field value", "footer"):
            # Corrupt just one truncation output; the independent final
            # validation must reject the actual composed payload.
            limit = scan.FIELD_VALUE_LIMIT if message == "field value" else scan.FOOTER_LIMIT
            original = scan.truncate_utf16

            def corrupt(text, requested_limit):
                return "x" * (requested_limit + 1) if requested_limit == limit else original(text, requested_limit)

            with self.subTest(message=message), mock.patch.object(scan, "truncate_utf16", side_effect=corrupt):
                with self.assertRaisesRegex(scan.PayloadError, message):
                    scan.build_payload(fields, FROZEN_NOW)
        # Reach aggregate validation while staying within individual limits.
        # Temporarily widened construction budgets make >6000 text reachable.
        with mock.patch.object(scan, "FIELD_VALUE_LIMIT", 1024), mock.patch.object(
            scan, "truncate_utf16", side_effect=lambda text, limit: "x" * limit
        ):
            with self.assertRaisesRegex(scan.PayloadError, "aggregate"):
                scan.build_payload(fields, FROZEN_NOW)

    def test_missing_scalars_numpy_booleans_and_contingent(self):
        cases = [
            ("hoa_fee", pd.NA, "hoa_unknown"), ("hoa_fee", pd.NaT, "hoa_unknown"),
            ("hoa_fee", np.bool_(False), "hoa_unknown"),
            ("list_price", pd.NA, "malformed_required_field"),
            ("sqft", np.bool_(True), "malformed_required_field"),
            ("status", pd.NaT, "malformed_required_field"),
            ("status", "CONTINGENT", "status_mismatch"),
            ("status", "PENDING", "status_mismatch"),
            ("property_id", np.bool_(True), "malformed_identity"),
            ("listing_id", pd.NA, "malformed_identity"),
            ("property_id", "١٢٣", "malformed_identity"),
            ("property_id", "１２３", "malformed_identity"),
        ]
        for key, value, reason in cases:
            with self.subTest(key=key, reason=reason):
                df = make_df([base_row()]).astype(object)
                df.at[0, key] = value
                eligible, counts = scan.process_dataframe(df)
                self.assertEqual(eligible, {})
                self.assertEqual(getattr(counts, reason), 1)

    def test_unknown_hoa_status_conflicts_and_display_tuple_ties_in_both_orders(self):
        for changes in ({"hoa_fee": pd.NA}, {"status": "CONTINGENT"}):
            for reverse in (False, True):
                rows = [base_row(), base_row(**changes)]
                eligible, counts = scan.process_dataframe(make_df(rows[::-1] if reverse else rows))
                self.assertEqual(eligible, {})
                self.assertEqual((counts.duplicate_group, counts.conflicting_duplicate), (1, 1))
        # Same address: each successive display component must break ties.
        for key, small, large in (("beds", 2, 3), ("full_baths", 1, 2), ("half_baths", 0, 1),
                                  ("list_date", "2026-08-01", "2026-09-01")):
            for reverse in (False, True):
                with self.subTest(key=key, reverse=reverse):
                    rows = [base_row(**{key: small}), base_row(**{key: large})]
                    eligible, counts = scan.process_dataframe(make_df(rows[::-1] if reverse else rows))
                    self.assertEqual(eligible["123456:987654"][key], small)
                    self.assertEqual(counts.duplicate_group, 1)

    def test_invalid_state_types_and_timestamps_fail_before_effects(self):
        states = [[], None, True, "state"]
        states += [initial_state_dict(**{key: value}) for key, value in (
            ("version", "1"), ("initialized", 1), ("seen", {}), ("seen", [1]),
            ("disabled_webhook_sha256", 1), ("discord_not_before", 1),
            ("discord_not_before", "2026-02-30T12:00:00Z"),
            ("discord_not_before", "2026-09-05T24:00:00Z"),
            ("discord_not_before", "2026-09-05T12:00:00+00:00"),
            ("discord_not_before", "2026-9-05T12:00:00Z"),
            ("discord_not_before", "2026-09-05T12:00:00.000Z"),
            ("discord_not_before", "2026-09-05T12:00:00Z\n"),
        )]
        for state in states:
            with self.subTest(state=state), StateFixture() as fx:
                fx.write(state)
                before = fx.read_bytes()
                with mock.patch.object(scan, "scrape_property") as fetch, mock.patch.object(
                    scan.requests.Session, "post"
                ) as post, mock.patch.object(scan, "save_state") as save:
                    self.assertEqual(run_main(False), 1)
                for effect in (fetch, post, save):
                    effect.assert_not_called()
                self.assertEqual(fx.read_bytes(), before)

    def test_property_and_webhook_url_authority_tables(self):
        for authority in ("realtor.com", "www.realtor.com", "WWW.REALTOR.COM"):
            url = scan.normalize_property_url(f"https://{authority}/listing?tracking=1#photo")
            self.assertEqual(url, f"https://{authority.lower()}/listing")
        for authority in ("evil.com", "realtor.com.evil.com", "user@realtor.com", "@realtor.com",
                          "realtor.com:443", "realtor.com:", "realtor.com.", "[::1]"):
            self.assertIsNone(scan.normalize_property_url(f"https://{authority}/listing"), authority)
        for url in ("http://realtor.com/listing", "https://realtor.com", "https://realtor.com/" + "x" * 2048):
            self.assertIsNone(scan.normalize_property_url(url))
        for authority in ("discord.com", "DISCORD.COM"):
            for version in ("", "/v10"):
                self.assertEqual(scan.canonicalize_webhook_url(
                    f"https://{authority}/api{version}/webhooks/123456789012345678/abcDEF-token_123"
                ), CANONICAL_WEBHOOK)
        invalid = [f"https://{authority}/api/webhooks/123/token" for authority in (
            "discordapp.com", "discord.com.evil.com", "user@discord.com", "@discord.com",
            "discord.com:443", "discord.com:", "discord.com.", "[::1]",
        )]
        invalid += [VALID_WEBHOOK + suffix for suffix in ("?wait=true", "#fragment", "/")]
        invalid += [VALID_WEBHOOK.replace("https:", "http:"), VALID_WEBHOOK.replace("/api/", "/api/v9/")]
        for url in invalid:
            with self.subTest(url=url), StateFixture() as fx:
                fx.write(initial_state_dict())
                with mock.patch.object(scan, "scrape_property") as fetch, mock.patch.object(scan.requests.Session, "post") as post:
                    self.assertEqual(run_main(False, webhook=url), 1)
                fetch.assert_not_called()
                post.assert_not_called()


class HarnessIsolationTests(unittest.TestCase):
    def test_guard_escapes_scanner_and_records_even_if_caller_catches_it(self):
        # Isolated recorder: these deliberately invoked guards are the sole
        # expected attempts and must not consume the suite's real audit log.
        attempts = []
        with mock.patch(f"{__name__}._network_attempts", attempts), StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True))
            with mock.patch.object(scan, "scrape_property", side_effect=lambda **kwargs: socket.socket()):
                with self.assertRaises(UnexpectedNetworkAttempt):
                    run_main(False)
            self.assertEqual(len(attempts), 1)
            with mock.patch.object(scan, "scrape_property", return_value=make_df([base_row()])):
                with self.assertRaises(UnexpectedNetworkAttempt):
                    run_main(False)
            self.assertEqual(len(attempts), 2)
        self.assertEqual(_network_attempts, [])


if __name__ == "__main__":
    unittest.main()
