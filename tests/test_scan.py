"""Offline behavioral suite for scan.py.

No live network calls are made anywhere in this file. HTTP, sleep, and wall
clock are always mocked/frozen. See docs/codex-review/2026-09-05-codex-spec.md
for the contract these tests verify against.
"""

from __future__ import annotations

import hashlib
import json
import math
import runpy
import shutil
import socket
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

import pandas as pd

import scan

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "docs" / "codex-review" / "2026-09-05-codex-spec.md"
FROZEN_NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)

_socket_guard_patcher = None


def _blocked_socket(*args, **kwargs):
    raise AssertionError("no test in this suite may open a real socket")


def setUpModule():
    # Suite-wide defense in depth: every test already mocks scrape_property
    # and requests.Session.post directly, but this ensures nothing in this
    # file can silently fall through to a real network call.
    global _socket_guard_patcher
    _socket_guard_patcher = mock.patch("socket.socket", side_effect=_blocked_socket)
    _socket_guard_patcher.start()


def tearDownModule():
    _socket_guard_patcher.stop()


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


VALID_WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/abcDEF-token_123"
CANONICAL_WEBHOOK = "https://discord.com/api/v10/webhooks/123456789012345678/abcDEF-token_123"


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

            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", new=fake_post
            ), mock.patch.object(scan.time, "sleep"):
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 0)
            self.assertEqual(
                sent_order,
                [
                    "Realtor.com via HomeHarvest | 1:1",
                    "Realtor.com via HomeHarvest | 2:1",
                ],
            )


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
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df(
                [
                    base_row(property_id="1", listing_id="1"),
                    base_row(property_id="2", listing_id="1"),
                ]
            )
            responses = [FakeResponse(200, {"id": "1"}), FakeResponse(500)]

            def fake_post(self, url, **kwargs):
                return responses.pop(0)

            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", new=fake_post
            ), mock.patch.object(scan.time, "sleep"):
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 1)
            state = json.loads(fx.read_bytes())
            self.assertEqual(state["seen"], ["1:1"])

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

            with mock.patch.object(scan, "_utcnow", return_value=FROZEN_NOW), mock.patch.object(
                scan, "scrape_property", return_value=df
            ), mock.patch.object(scan.requests.Session, "post", new=fake_post), mock.patch.object(
                scan.time, "sleep"
            ) as sleep_mock:
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

            with mock.patch.object(scan, "scrape_property", return_value=df), mock.patch.object(
                scan.requests.Session, "post", new=fake_post
            ), mock.patch.object(scan.time, "sleep"):
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
        # Reproduces the reviewer's frozen-clock probe: at 12:00:00Z, a 429
        # body delay of 1.5 must produce a gate of 12:00:02Z (round_up(now +
        # 1.5 + 0.25)), persisted to disk BEFORE the retry sleep happens, and
        # the retry must not fire before that gate (i.e. sleep >= 2.0s, not
        # the raw 1.75s delay).
        with StateFixture() as fx:
            fx.write(initial_state_dict(initialized=True, seen=[]))
            df = make_df([base_row()])
            responses = [
                FakeResponse(429, {"retry_after": 1.5}, headers={}),
                FakeResponse(200, {"id": "1"}),
            ]

            def fake_post(self, url, **kwargs):
                return responses.pop(0)

            observed = []

            def fake_sleep(seconds):
                observed.append((seconds, json.loads(fx.read_bytes())["discord_not_before"]))

            with mock.patch.object(scan, "_utcnow", return_value=FROZEN_NOW), mock.patch.object(
                scan, "scrape_property", return_value=df
            ), mock.patch.object(scan.requests.Session, "post", new=fake_post), mock.patch.object(
                scan.time, "sleep", side_effect=fake_sleep
            ):
                rc = run_main(dry_run=False)
            self.assertEqual(rc, 0)
            self.assertEqual(len(observed), 1)
            sleep_seconds, gate_at_sleep_time = observed[0]
            self.assertEqual(gate_at_sleep_time, "2026-09-05T12:00:02Z")
            self.assertGreaterEqual(sleep_seconds, 2.0)

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
    def _run_copied_script(self, seen_state: dict, rows=None):
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

            def blocked_socket(*args, **kwargs):
                raise OSError("network disabled in offline dry-run test")

            def blocked_sleep(*args, **kwargs):
                raise AssertionError("dry run must never sleep")

            try:
                _os.chdir(other_cwd)
                with mock.patch("homeharvest.scrape_property", return_value=df), mock.patch.object(
                    socket, "socket", side_effect=blocked_socket
                ), mock.patch("time.sleep", side_effect=blocked_sleep), self.assertLogs(
                    "boca_house_hunter", level="INFO"
                ) as logs:
                    try:
                        runpy.run_path(str(script_copy), run_name="__main__")
                        exit_code = 0
                    except SystemExit as exc:
                        exit_code = exc.code or 0
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

    def test_offline_cli_dry_run_initialized(self):
        # One already-seen pair (not present in the fetched rows at all) plus
        # one genuinely new eligible pair, so this exercises a real would-send
        # candidate rather than a permanently-zero count.
        exit_code, before, after, logs = self._run_copied_script(
            initial_state_dict(initialized=True, seen=["555555:111111"]),
            rows=[base_row()],
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(before, after)
        summary_lines = [line for line in logs if "event=scan_summary" in line]
        self.assertEqual(len(summary_lines), 1)
        self.assertIn("candidate=1", summary_lines[0])
        self.assertIn("already_seen=1", summary_lines[0])

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
                seen=[],
                disabled_webhook_sha256="a" * 64,
                discord_not_before=future,
            ),
            rows=[base_row()],
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(before, after)
        summary_lines = [line for line in logs if "event=scan_summary" in line]
        self.assertEqual(len(summary_lines), 1)
        self.assertIn("candidate=1", summary_lines[0])


if __name__ == "__main__":
    unittest.main()
