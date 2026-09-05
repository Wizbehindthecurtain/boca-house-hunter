# Boca House Hunter round-4 implementation and local acceptance

Date: 2026-09-05. **Verdict: READY FOR LOCAL ACCEPTANCE**. Live commissioning remains outstanding and outside this round.

Starting checkout: local `master`, commit `9f8cf7cb31f7ceba5cdaa8a95ba7e85fe4b0c080`; clean working tree. I read `git show 9f8cf7c`, the round-3 findings, canonical spec, and implementation plan. This round implements fixes directly. The user's instruction to leave work unstaged and uncommitted supersedes the plan's commit step. No commit, push, live scrape, Discord request, Actions activity, dependency installation, or remote configuration was performed.

## 1. Verification of Claude's fix commit

| Finding | Verification and outcome |
|---|---|
| R2 | The additional reserve check immediately after the final gate wait and before `post_once` correctly enforces the 25-second reserve after oversleep. Retained unchanged. New tests accept exactly 25 seconds, reject 24.999 seconds, accept sleep plus exactly 25 seconds, reject insufficient sleep reserve, and stop the reproduced 2.1-second oversleep at elapsed 125.1 without a second POST. |
| R5 | Scalar `.at[]` access fixes the reported oversized-integer coercion for ordinary unique index labels. The actual mixed DataFrame with `10**5000` now rejects one row and delivers the valid sibling. However, repeated index labels make `.at[]` return a Series, silently rejecting valid rows. Fixed by positional `.iat[]` access, retaining the original index for warnings. |
| R9 | Counts initialized before baseline/real/dry payload construction and no-candidate recovery now remain accurate on the round-3 failure paths. Immediate summary updates after counter changes preserve A when B raises or B's rate-gate save fails. Counting duplicate groups before conflicts correctly includes both agreeing and conflicting groups. Retained these changes. One residual gap remained: the counter itself changed only after `save_current()`. An unexpected save exception could still report zero remote confirmations. Moved the confirmation count and summary update to the start of the confirmed branch, before fallible local work. |
| R10 | The repaired A-confirmed/B-500 test uses a coherent clock, requires both attempts and the intended 500 event, observes durable A at B's POST, and retries only B next scan. It is correct. Added durable-state inspection at the intervening sleep and the exact 0.5-second pacing assertion. |

Two targeted regressions were also executed against the exact original `9f8cf7c:scan.py` source, loaded into an isolated in-memory module without changing the working file. The repeated-index subcase failed because the valid sibling was never POSTed; the unexpected-confirmation-save subcase failed because the summary reported `confirmed=0 unsent=2` instead of `confirmed=1 unsent=1`. The original unique-index oversized case and ordinary later-failure cases passed. These two intentional failures establish that the new tests distinguish the residual defects; they are separate from the final passing working-tree verification.

## 2. Changes made in this round

Only `scan.py`, `tests/test_scan.py`, and this new report changed.

- `scan.py`: preserve scalar row access even with repeated DataFrame index labels; record remote confirmation before any fallible persistence/deadline work.
- `tests/test_scan.py`: add bounded behavioral regressions and strengthen the offline harness and copied entry-point checks.
- No validation rule, dependency pin, workflow, shipped state, spec, plan, prior review, or operator guidance was relaxed or edited.

The network harness now guards Requests request/send and socket construction/connection/DNS boundaries. An unexpected attempt records a credential-free marker and raises a dedicated `BaseException` subclass that the scanner's ordinary exception handlers cannot swallow. Module teardown independently rejects any recorded attempts even if an intervening library catches that exception. A self-test uses a separate recorder to deliberately exercise both scrape-side sockets and unmocked delivery, proving that the scanner cannot turn either into an ordinary exit-1 success for a negative test. Production exception handling remains unchanged.

## 3. Required recurring coverage completed

These are the load-bearing observations from round 3 §4.2, plus representative explicitly required equivalence classes listed immediately below that table. They exercise different control-flow, persistence, or serialization boundaries. No Cartesian product of unrelated malformed inputs was added.

| Area | Added or strengthened observations | Why required |
|---|---|---|
| Timing and budgets | Exact POST reserve and sleep-plus-reserve acceptance/rejection; oversleep regression; disk and both clocks observed at the next POST for 429 and exhausted-success responses under backward UTC movement during sleep and forward movement during saving; identical retry payload; failed rate-gate saves stop before sleep/retry; actual 31-/360-second 429 and exhausted-success gates persist and suppress the next run without fetch, POST, sleep, or write; A saved before ordinary 0.5-second pacing. | Prevents early delivery and false positives that only inspect requested sleep duration or preload a gate instead of creating it. |
| Identity and accounting | Mixed oversized/good DataFrame through real-mode lifecycle, including repeated index labels; ordinary/numpy/64-digit integers and leading-zero string IDs survive baseline save/reload; every contract summary count checked for baseline/real/dry payload failures, later unexpected POST, later gate-save failure, unexpected confirmation-save failure, no-candidate recovery-save failure, and an early state failure; mixed agreeing/conflicting/singleton groups; fetched seen overlap with unrelated absent history. | Protects row rejection without losing valid listings and preserves known facts on every reported failure path. |
| Atomic persistence and recovery | Actual write → flush → fsync → close → replace order using a real temporary file and real fsync/replace; serialization, write, flush, fsync, and replace failures preserve durable bytes and prevent subsequent sleep/POST for both confirmations and 429 gates; changed secret plus future gate; expired gate plus failed fetch; healthy no-candidate and baseline recovery each save exactly once. | Proves durability ordering and that failed work never persists recovery clearing or authorizes later delivery. |
| Copied CLI entry point | Both required named tests still execute a byte-identical copied script under another cwd with no webhook. Exact single fetch kwargs, complete summary counts, zero POST/sleep/replace/write-open attempts, no temp creation, and unchanged bytes are checked. Initialized fixture includes fetched seen/new pairs plus absent history; the same fixture is repeated with a latch and future gate. A later-candidate payload failure actually reaches the second builder in the executing copy, exits 1, and has no effects. | Byte equality alone misses identical rewrites; imported-module tests alone miss entry-point behavior. The failure seam replaces the executing copy's builder through the fake fetch callback, without altering copied source. |
| Confirmation and payload boundaries | Invalid JSON shapes and IDs, malformed JSON, representative other 2xx and redirect statuses, and read timeout through the delivery lifecycle; exact full payload equality at POST; post-fetch observation timestamp; absent optional columns; actual large-number payload values at/over the 64-character size boundary and decimal rounding; independent field/footer/aggregate rejection. Existing title/UTF-16/escaping coverage retained. | Distinguishes response-classification branches and validates actual output rather than only helper strings. Aggregate fault injection widens construction budgets only inside its test to reach the unchanged 6000-character validation branch. |
| Harness isolation | Guarded Requests/socket/DNS boundaries, persistent attempt record, uncatchable-by-Exception signal, teardown assertion, and isolated positive self-test of the guard. | An unintended real transport attempt can no longer hide inside an expected scanner failure. |
| Representative checklist classes | pandas NA/NaT, numpy booleans, pending/contingent, non-ASCII IDs; unknown-HOA and status-conflicting duplicates in both orders; each equal-address display-tuple tie-break; invalid state roots/field types and impossible/noncanonical UTC timestamps through startup; property and webhook authority acceptance/rejection tables and invalid webhook startup effects. | Closes the expressly required scalar/state/URL observations without multiplying them across unrelated response/recovery combinations. |

## 4. Final local verification

The existing `.venv\\Scripts\\python.exe` is used directly, as requested; the known failing `py -3.12` launcher lookup is unnecessary. Windows Python verification does not establish Ubuntu runner operation. Full command output follows, including expected scanner error events generated by negative tests; those events are distinct from unittest failures.

**All eight prescribed commands exited 0. Full suite: 139 tests, OK (0 failures/errors, no skips or expected failures). Filtered suite: both required named tests, 2 tests, OK.** The suite grew from 114 to 139 tests, with additional table-driven subcases and strengthened existing tests.

```powershell
.\.venv\Scripts\python.exe -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version"
```

```text
(no output)
Exit code: 0
```

```powershell
.\.venv\Scripts\python.exe -m pip check
```

```text
No broken requirements found.
Exit code: 0
```

```powershell
.\.venv\Scripts\python.exe -c "import json; from importlib.metadata import distribution, version; d = json.loads(distribution('homeharvest').read_text('direct_url.json')); assert d['vcs_info']['commit_id'] == '8a6ac96db419b56a18d295935217649039bcdd0a', d['vcs_info']['commit_id']; assert version('requests') == '2.32.4', version('requests'); print('Exact dependency pins verified')"
```

```text
Exact dependency pins verified
Exit code: 0
```

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

```text
test_flush_fsync_replace_sequence_used (test_scan.AtomicWriteTests.test_flush_fsync_replace_sequence_used) ... ok
test_noop_scan_does_not_write (test_scan.AtomicWriteTests.test_noop_scan_does_not_write) ... ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=1 candidate=0 confirmed=0 unsent=0 baseline_created=False
ok
test_save_state_exact_formatting (test_scan.AtomicWriteTests.test_save_state_exact_formatting) ... ok
test_write_failure_right_after_confirmation_still_reports_it_as_confirmed (test_scan.AtomicWriteTests.test_write_failure_right_after_confirmation_still_reports_it_as_confirmed) ... ok
test_write_failure_stops_further_sends_and_logs_state_write_failed (test_scan.AtomicWriteTests.test_write_failure_stops_further_sends_and_logs_state_write_failed) ... ts=2026-09-05T07:35:53Z level=ERROR event=state_write_failed error_class=OSError
ts=2026-09-05T07:35:53Z level=INFO event=delivered identity=1:1 http_status=200
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=2 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=2 already_seen=0 candidate=2 confirmed=1 unsent=1 baseline_created=False
ok
test_200_without_valid_id_and_204_do_not_mark_seen (test_scan.ConfirmationAndPartialFailureTests.test_200_without_valid_id_and_204_do_not_mark_seen) ... ts=2026-09-05T07:35:53Z level=ERROR event=delivery_failed identity=123456:987654 http_status=200
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:53Z level=ERROR event=delivery_failed identity=123456:987654 http_status=204
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ok
test_confirmed_a_then_failed_b_persists_only_a (test_scan.ConfirmationAndPartialFailureTests.test_confirmed_a_then_failed_b_persists_only_a) ... ts=2026-09-05T12:00:00Z level=INFO event=delivered identity=2:1 http_status=200
ts=2026-09-05T12:00:00Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=2 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=2 already_seen=1 candidate=1 confirmed=1 unsent=0 baseline_created=False
ok
test_exact_session_post_arguments (test_scan.ConfirmationAndPartialFailureTests.test_exact_session_post_arguments) ... ts=2026-09-05T07:35:53Z level=INFO event=delivered identity=123456:987654 http_status=200
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=1 unsent=0 baseline_created=False
ok
test_timeout_and_5xx_and_400_stop_without_marking_seen (test_scan.ConfirmationAndPartialFailureTests.test_timeout_and_5xx_and_400_stop_without_marking_seen) ... ts=2026-09-05T07:35:53Z level=ERROR event=delivery_failed identity=123456:987654 http_status=None
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:53Z level=ERROR event=delivery_failed identity=123456:987654 http_status=None
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:53Z level=ERROR event=delivery_failed identity=123456:987654 http_status=400
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:53Z level=ERROR event=delivery_failed identity=123456:987654 http_status=500
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.015 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:53Z level=ERROR event=delivery_failed identity=123456:987654 http_status=503
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ok
test_baseline_creation_logs_dedicated_event_with_eligible_count (test_scan.EntryPointAndLoggingTests.test_baseline_creation_logs_dedicated_event_with_eligible_count) ... ok
test_failure_log_does_not_leak_webhook_token (test_scan.EntryPointAndLoggingTests.test_failure_log_does_not_leak_webhook_token) ... ok
test_import_has_no_side_effects (test_scan.EntryPointAndLoggingTests.test_import_has_no_side_effects) ... ok
test_invalid_dry_run_value_fails_before_network (test_scan.EntryPointAndLoggingTests.test_invalid_dry_run_value_fails_before_network) ... ts=2026-09-05T07:35:53Z level=ERROR event=config_invalid reason=invalid DRY_RUN value: '2'
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ok
test_malformed_rows_log_row_index_not_raw_identity (test_scan.EntryPointAndLoggingTests.test_malformed_rows_log_row_index_not_raw_identity) ... ok
test_missing_webhook_fails_before_network_even_at_baseline (test_scan.EntryPointAndLoggingTests.test_missing_webhook_fails_before_network_even_at_baseline) ... ts=2026-09-05T07:35:53Z level=ERROR event=webhook_config_invalid reason=DISCORD_WEBHOOK_URL is not set
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ok
test_true_outer_safety_net_catches_unexpected_exception_with_phase (test_scan.EntryPointAndLoggingTests.test_true_outer_safety_net_catches_unexpected_exception_with_phase) ... ok
test_unexpected_exception_is_caught_and_logged_safely (test_scan.EntryPointAndLoggingTests.test_unexpected_exception_is_caught_and_logged_safely) ... ok
test_fetch_listings_uses_exact_keywords (test_scan.FetchAndRequiredFieldsTests.test_fetch_listings_uses_exact_keywords) ... ok
test_nonempty_zero_eligible_is_healthy_and_produces_no_eligible (test_scan.FetchAndRequiredFieldsTests.test_nonempty_zero_eligible_is_healthy_and_produces_no_eligible) ... ok
test_rejects_each_missing_required_column (test_scan.FetchAndRequiredFieldsTests.test_rejects_each_missing_required_column) ... ok
test_rejects_empty_dataframe (test_scan.FetchAndRequiredFieldsTests.test_rejects_empty_dataframe) ... ok
test_rejects_non_dataframe (test_scan.FetchAndRequiredFieldsTests.test_rejects_non_dataframe) ... ok
test_rejects_result_cap (test_scan.FetchAndRequiredFieldsTests.test_rejects_result_cap) ... ok
test_scrape_authentication_error_is_not_specially_narrowed (test_scan.FetchAndRequiredFieldsTests.test_scrape_authentication_error_is_not_specially_narrowed) ... ts=2026-09-05T07:35:53Z level=ERROR event=scrape_failed error_class=FakeAuthError
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ok
test_scrape_generic_exception_is_caught_and_state_untouched (test_scan.FetchAndRequiredFieldsTests.test_scrape_generic_exception_is_caught_and_state_untouched) ... ts=2026-09-05T07:35:53Z level=ERROR event=scrape_failed error_class=RuntimeError
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ok
test_total_fetched_is_reported_even_when_shape_validation_rejects_the_result (test_scan.FetchAndRequiredFieldsTests.test_total_fetched_is_reported_even_when_shape_validation_rejects_the_result) ... ok
test_guard_escapes_scanner_and_records_even_if_caller_catches_it (test_scan.HarnessIsolationTests.test_guard_escapes_scanner_and_records_even_if_caller_catches_it) ... ok
test_agreeing_duplicates_have_deterministic_tie_break_regardless_of_order (test_scan.IdentityAndDuplicateTests.test_agreeing_duplicates_have_deterministic_tie_break_regardless_of_order) ... ok
test_conflicting_duplicates_suppress_entire_identity (test_scan.IdentityAndDuplicateTests.test_conflicting_duplicates_suppress_entire_identity) ... ok
test_distinct_nonzero_hoa_fees_are_a_real_disagreement (test_scan.IdentityAndDuplicateTests.test_distinct_nonzero_hoa_fees_are_a_real_disagreement) ... ok
test_extremely_oversized_integer_rejected_without_raising (test_scan.IdentityAndDuplicateTests.test_extremely_oversized_integer_rejected_without_raising) ... ok
test_identical_duplicates_yield_one_result (test_scan.IdentityAndDuplicateTests.test_identical_duplicates_yield_one_result) ... ok
test_integer_identity_enforces_same_64_digit_limit_as_string (test_scan.IdentityAndDuplicateTests.test_integer_identity_enforces_same_64_digit_limit_as_string) ... ok
test_leading_zeros_preserved (test_scan.IdentityAndDuplicateTests.test_leading_zeros_preserved) ... ok
test_lone_malformed_required_row_is_not_double_counted_as_conflicting (test_scan.IdentityAndDuplicateTests.test_lone_malformed_required_row_is_not_double_counted_as_conflicting) ... ts=2026-09-05T07:35:53Z level=WARNING event=malformed_required_field_row row_index=0
ok
test_lone_row_is_not_counted_as_a_duplicate_group (test_scan.IdentityAndDuplicateTests.test_lone_row_is_not_counted_as_a_duplicate_group) ... ok
test_malformed_identities_rejected_without_nan_identity (test_scan.IdentityAndDuplicateTests.test_malformed_identities_rejected_without_nan_identity) ... ts=2026-09-05T07:35:53Z level=WARNING event=malformed_identity_row row_index=0
ts=2026-09-05T07:35:53Z level=WARNING event=malformed_identity_row row_index=0
ts=2026-09-05T07:35:53Z level=WARNING event=malformed_identity_row row_index=0
ts=2026-09-05T07:35:53Z level=WARNING event=malformed_identity_row row_index=0
ts=2026-09-05T07:35:53Z level=WARNING event=malformed_identity_row row_index=0
ts=2026-09-05T07:35:53Z level=WARNING event=malformed_identity_row row_index=0
ts=2026-09-05T07:35:53Z level=WARNING event=malformed_identity_row row_index=0
ts=2026-09-05T07:35:53Z level=WARNING event=malformed_identity_row row_index=0
ok
test_malformed_required_field_sibling_suppresses_qualifying_duplicate (test_scan.IdentityAndDuplicateTests.test_malformed_required_field_sibling_suppresses_qualifying_duplicate) ... ts=2026-09-05T07:35:53Z level=WARNING event=malformed_required_field_row row_index=1
ts=2026-09-05T07:35:53Z level=WARNING event=malformed_required_field_row row_index=1
ts=2026-09-05T07:35:53Z level=WARNING event=malformed_required_field_row row_index=1
ok
test_valid_integer_scalars_accepted (test_scan.IdentityAndDuplicateTests.test_valid_integer_scalars_accepted) ... ok
test_401_403_404_latch_and_stop (test_scan.LatchAndGateRecoveryTests.test_401_403_404_latch_and_stop) ... ts=2026-09-05T07:35:53Z level=ERROR event=webhook_permanent_failure http_status=401
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.016 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:53Z level=ERROR event=webhook_permanent_failure http_status=403
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:53Z level=ERROR event=webhook_permanent_failure http_status=404
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.016 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ok
test_changed_webhook_permits_recovery_without_clearing_seen (test_scan.LatchAndGateRecoveryTests.test_changed_webhook_permits_recovery_without_clearing_seen) ... ts=2026-09-05T07:35:53Z level=INFO event=delivered identity=123456:987654 http_status=200
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=1 unsent=0 baseline_created=False
ok
test_dry_run_ignores_latch_and_gate (test_scan.LatchAndGateRecoveryTests.test_dry_run_ignores_latch_and_gate) ... ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ok
test_same_disabled_webhook_makes_no_external_calls (test_scan.LatchAndGateRecoveryTests.test_same_disabled_webhook_makes_no_external_calls) ... ts=2026-09-05T07:35:53Z level=INFO event=webhook_disabled
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ok
test_already_seen_price_change_never_alerts (test_scan.LifecycleTests.test_already_seen_price_change_never_alerts) ... ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=1 candidate=0 confirmed=0 unsent=0 baseline_created=False
ok
test_baseline_creates_state_with_zero_posts (test_scan.LifecycleTests.test_baseline_creates_state_with_zero_posts) ... ts=2026-09-05T07:35:53Z level=INFO event=baseline_created eligible_count=1
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.016 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=0 baseline_created=True
ok
test_baseline_with_zero_eligible_rows_is_healthy (test_scan.LifecycleTests.test_baseline_with_zero_eligible_rows_is_healthy) ... ts=2026-09-05T07:35:53Z level=INFO event=baseline_created eligible_count=0
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=1 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=0 already_seen=0 candidate=0 confirmed=0 unsent=0 baseline_created=True
ok
test_candidate_order_is_lexicographic (test_scan.LifecycleTests.test_candidate_order_is_lexicographic) ... ts=2026-09-05T12:00:00Z level=INFO event=delivered identity=1:1 http_status=200
ts=2026-09-05T12:00:00Z level=INFO event=delivered identity=2:1 http_status=200
ts=2026-09-05T12:00:00Z level=INFO event=scan_summary elapsed_seconds=0.5 total_fetched=2 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=2 already_seen=0 candidate=2 confirmed=2 unsent=0 baseline_created=False
ok
test_dry_run_never_initializes_state (test_scan.LifecycleTests.test_dry_run_never_initializes_state) ... ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=0 baseline_created=False
ok
test_new_pair_sends_once (test_scan.LifecycleTests.test_new_pair_sends_once) ... ts=2026-09-05T07:35:53Z level=INFO event=delivered identity=123456:987654 http_status=200
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=1 unsent=0 baseline_created=False
ok
test_repeat_scan_sends_and_writes_nothing (test_scan.LifecycleTests.test_repeat_scan_sends_and_writes_nothing) ... ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=1 candidate=0 confirmed=0 unsent=0 baseline_created=False
ok
test_same_pair_disappear_reappear_stays_suppressed (test_scan.LifecycleTests.test_same_pair_disappear_reappear_stays_suppressed) ... ts=2026-09-05T07:35:53Z level=ERROR event=delivery_failed identity=999999:987654 http_status=<MagicMock name='post().status_code' id='2173507544912'>
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.016 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=1 candidate=0 confirmed=0 unsent=0 baseline_created=False
ok
test_same_property_new_listing_id_may_send (test_scan.LifecycleTests.test_same_property_new_listing_id_may_send) ... ts=2026-09-05T07:35:53Z level=INFO event=delivered identity=123456:222222 http_status=200
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.015 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=1 unsent=0 baseline_created=False
ok
test_unknown_hoa_becoming_zero_can_first_alert (test_scan.LifecycleTests.test_unknown_hoa_becoming_zero_can_first_alert) ... ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=1 hoa_nonzero=0 eligible=0 already_seen=0 candidate=0 confirmed=0 unsent=0 baseline_created=False
ts=2026-09-05T07:35:53Z level=INFO event=delivered identity=123456:987654 http_status=200
ts=2026-09-05T07:35:53Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=1 unsent=0 baseline_created=False
ok
test_gitignore_has_required_entries_and_tracks_seen_json (test_scan.LiteralContractTests.test_gitignore_has_required_entries_and_tracks_seen_json) ... ok
test_initial_schema_round_trips_through_load_state (test_scan.LiteralContractTests.test_initial_schema_round_trips_through_load_state)
Validates the �5 initial schema itself via a harness fixture. ... ok
test_requirements_txt_matches_spec_block (test_scan.LiteralContractTests.test_requirements_txt_matches_spec_block) ... ok
test_workflow_yaml_matches_spec_block_after_newline_normalization (test_scan.LiteralContractTests.test_workflow_yaml_matches_spec_block_after_newline_normalization) ... ok
test_copied_cli_later_payload_failure_has_no_effects (test_scan.OfflineCliDryRunTests.test_copied_cli_later_payload_failure_has_no_effects) ... ok
test_dry_run_cli_entrypoint_ignores_disabled_digest_and_future_gate (test_scan.OfflineCliDryRunTests.test_dry_run_cli_entrypoint_ignores_disabled_digest_and_future_gate) ... ok
test_offline_cli_dry_run_baseline (test_scan.OfflineCliDryRunTests.test_offline_cli_dry_run_baseline) ... ok
test_offline_cli_dry_run_initialized (test_scan.OfflineCliDryRunTests.test_offline_cli_dry_run_initialized) ... ok
test_address_component_is_utf16_truncated_not_sliced_by_code_point (test_scan.PayloadTests.test_address_component_is_utf16_truncated_not_sliced_by_code_point) ... ok
test_address_control_chars_do_not_fuse_adjacent_words (test_scan.PayloadTests.test_address_control_chars_do_not_fuse_adjacent_words) ... ok
test_address_fallback_precedence (test_scan.PayloadTests.test_address_fallback_precedence) ... ok
test_address_falls_back_when_control_removal_leaves_only_whitespace (test_scan.PayloadTests.test_address_falls_back_when_control_removal_leaves_only_whitespace) ... ok
test_address_falls_back_when_primary_source_sanitizes_to_empty (test_scan.PayloadTests.test_address_falls_back_when_primary_source_sanitizes_to_empty) ... ok
test_beds_baths_integer_or_unknown (test_scan.PayloadTests.test_beds_baths_integer_or_unknown) ... ok
test_build_payload_independently_validates_final_field_budgets (test_scan.PayloadTests.test_build_payload_independently_validates_final_field_budgets) ... ok
test_dry_run_validates_payloads_and_fails_consistently_with_real_mode (test_scan.PayloadTests.test_dry_run_validates_payloads_and_fails_consistently_with_real_mode) ... ts=2026-09-05T07:35:54Z level=ERROR event=payload_invalid reason=boom
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=0 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=payload_invalid reason=boom
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ok
test_exact_payload_shape (test_scan.PayloadTests.test_exact_payload_shape) ... ok
test_invalid_url_rejected_and_query_fragment_stripped (test_scan.PayloadTests.test_invalid_url_rejected_and_query_fragment_stripped) ... ok
test_markdown_and_control_and_at_sign_escaped (test_scan.PayloadTests.test_markdown_and_control_and_at_sign_escaped) ... ok
test_non_ascii_control_characters_are_removed (test_scan.PayloadTests.test_non_ascii_control_characters_are_removed) ... ok
test_oversized_numeric_values_fall_back_to_unknown_not_an_exception (test_scan.PayloadTests.test_oversized_numeric_values_fall_back_to_unknown_not_an_exception) ... ok
test_payload_construction_failure_prevents_any_post (test_scan.PayloadTests.test_payload_construction_failure_prevents_any_post) ... ts=2026-09-05T07:35:54Z level=ERROR event=payload_invalid reason=boom
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=2 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=2 already_seen=0 candidate=2 confirmed=0 unsent=2 baseline_created=False
ok
test_price_and_size_formatting (test_scan.PayloadTests.test_price_and_size_formatting) ... ok
test_price_decimal_count_follows_original_value_not_rounded_result (test_scan.PayloadTests.test_price_decimal_count_follows_original_value_not_rounded_result) ... ok
test_source_date_and_invalid_date_rejection (test_scan.PayloadTests.test_source_date_and_invalid_date_rejection) ... ok
test_url_rejects_empty_userinfo_and_empty_port_syntax (test_scan.PayloadTests.test_url_rejects_empty_userinfo_and_empty_port_syntax) ... ok
test_utf16_truncation_never_splits_surrogate_pair (test_scan.PayloadTests.test_utf16_truncation_never_splits_surrogate_pair) ... ok
test_webhook_url_rejects_empty_userinfo_and_empty_port_syntax (test_scan.PayloadTests.test_webhook_url_rejects_empty_userinfo_and_empty_port_syntax) ... ok
test_429_gate_is_durably_saved_before_sleep_and_retry_lands_on_or_after_gate (test_scan.RateLimitAndBudgetTests.test_429_gate_is_durably_saved_before_sleep_and_retry_lands_on_or_after_gate) ... ts=2026-09-05T12:00:02Z level=INFO event=delivered identity=123456:987654 http_status=200
ts=2026-09-05T12:00:02Z level=INFO event=scan_summary elapsed_seconds=2.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=1 unsent=0 baseline_created=False
ok
test_429_retry_honors_monotonic_deadline_despite_forward_utc_jump (test_scan.RateLimitAndBudgetTests.test_429_retry_honors_monotonic_deadline_despite_forward_utc_jump) ... ts=2026-09-05T12:00:11Z level=INFO event=delivered identity=123456:987654 http_status=200
ts=2026-09-05T12:00:11Z level=INFO event=scan_summary elapsed_seconds=1.75 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=1 unsent=0 baseline_created=False
ok
test_429_retry_rechecks_gate_after_backward_utc_jump_during_sleep (test_scan.RateLimitAndBudgetTests.test_429_retry_rechecks_gate_after_backward_utc_jump_during_sleep) ... ts=2026-09-05T12:00:02Z level=INFO event=delivered identity=123456:987654 http_status=200
ts=2026-09-05T12:00:02Z level=INFO event=scan_summary elapsed_seconds=3.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=1 unsent=0 baseline_created=False
ok
test_429_retry_stops_before_sleep_when_remaining_budget_insufficient (test_scan.RateLimitAndBudgetTests.test_429_retry_stops_before_sleep_when_remaining_budget_insufficient) ... ts=2026-09-05T12:00:00Z level=ERROR event=budget_exhausted_before_sleep identity=123456:987654
ts=2026-09-05T12:00:00Z level=INFO event=scan_summary elapsed_seconds=125.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ok
test_429_uses_max_valid_delay_plus_quarter_second_and_retries_same_payload (test_scan.RateLimitAndBudgetTests.test_429_uses_max_valid_delay_plus_quarter_second_and_retries_same_payload) ... ts=2026-09-05T12:00:02Z level=INFO event=delivered identity=123456:987654 http_status=200
ts=2026-09-05T12:00:02Z level=INFO event=scan_summary elapsed_seconds=2.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=1 unsent=0 baseline_created=False
ok
test_confirmed_with_unknown_exhaustion_saves_identity_then_stops_batch (test_scan.RateLimitAndBudgetTests.test_confirmed_with_unknown_exhaustion_saves_identity_then_stops_batch) ... ts=2026-09-05T07:35:54Z level=INFO event=delivered identity=1:1 http_status=200
ts=2026-09-05T07:35:54Z level=ERROR event=rate_limit_exhaustion_unknown identity=1:1
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=2 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=2 already_seen=0 candidate=2 confirmed=1 unsent=1 baseline_created=False
ok
test_confirmed_with_unrepresentable_reset_delay_still_saves_identity (test_scan.RateLimitAndBudgetTests.test_confirmed_with_unrepresentable_reset_delay_still_saves_identity) ... ok
test_last_successful_candidate_preserves_gate_without_sleeping (test_scan.RateLimitAndBudgetTests.test_last_successful_candidate_preserves_gate_without_sleeping) ... ts=2026-09-05T07:35:54Z level=INFO event=delivered identity=123456:987654 http_status=200
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=1 unsent=0 baseline_created=False
ok
test_malformed_429_delay_stops_without_guessing (test_scan.RateLimitAndBudgetTests.test_malformed_429_delay_stops_without_guessing) ... ts=2026-09-05T07:35:54Z level=ERROR event=rate_limit_invalid_delay identity=123456:987654
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ok
test_persisted_gate_prevents_early_send_on_next_run (test_scan.RateLimitAndBudgetTests.test_persisted_gate_prevents_early_send_on_next_run) ... ts=2026-09-05T07:35:54Z level=INFO event=webhook_backoff not_before=2099-09-05T12:00:00Z
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ok
test_three_attempt_bound_then_stop (test_scan.RateLimitAndBudgetTests.test_three_attempt_bound_then_stop) ... ts=2026-09-05T12:00:02Z level=ERROR event=rate_limited_exhausted identity=123456:987654
ts=2026-09-05T12:00:02Z level=INFO event=scan_summary elapsed_seconds=2.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ok
test_accepted_integer_and_leading_zero_ids_survive_save_and_reload (test_scan.RequiredAccountingTests.test_accepted_integer_and_leading_zero_ids_survive_save_and_reload) ... ts=2026-09-05T07:35:54Z level=INFO event=baseline_created eligible_count=1
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=0 baseline_created=True
ts=2026-09-05T07:35:54Z level=INFO event=baseline_created eligible_count=1
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.016 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=0 baseline_created=True
ts=2026-09-05T07:35:54Z level=INFO event=baseline_created eligible_count=1
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=0 baseline_created=True
ts=2026-09-05T07:35:54Z level=INFO event=baseline_created eligible_count=1
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=0 baseline_created=True
ok
test_confirmation_counts_survive_later_post_gate_save_and_unexpected_save_failures (test_scan.RequiredAccountingTests.test_confirmation_counts_survive_later_post_gate_save_and_unexpected_save_failures) ... ok
test_duplicate_totals_and_seen_overlap_exclude_absent_history (test_scan.RequiredAccountingTests.test_duplicate_totals_and_seen_overlap_exclude_absent_history) ... ok
test_early_failure_reports_unobserved_counts (test_scan.RequiredAccountingTests.test_early_failure_reports_unobserved_counts) ... ok
test_no_candidate_recovery_save_failure_counts (test_scan.RequiredAccountingTests.test_no_candidate_recovery_save_failure_counts) ... ok
test_oversized_integer_mixed_dataframe_and_repeated_index_labels (test_scan.RequiredAccountingTests.test_oversized_integer_mixed_dataframe_and_repeated_index_labels) ... ok
test_payload_failure_summaries_in_all_modes (test_scan.RequiredAccountingTests.test_payload_failure_summaries_in_all_modes) ... ok
test_absent_optional_columns_through_delivery (test_scan.RequiredBoundaryTests.test_absent_optional_columns_through_delivery) ... ts=2026-09-05T07:35:54Z level=INFO event=delivered identity=123456:987654 http_status=200
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=1 unsent=0 baseline_created=False
ok
test_actual_payload_numeric_limits_and_rounding (test_scan.RequiredBoundaryTests.test_actual_payload_numeric_limits_and_rounding) ... ok
test_actual_payload_rejects_field_footer_and_aggregate_overflow (test_scan.RequiredBoundaryTests.test_actual_payload_rejects_field_footer_and_aggregate_overflow) ... ok
test_confirmation_shapes_ids_statuses_and_read_timeout (test_scan.RequiredBoundaryTests.test_confirmation_shapes_ids_statuses_and_read_timeout) ... ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=200
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=200
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=200
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=200
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=200
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=200
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=200
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=200
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=200
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=200
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=200
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=200
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=200
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=200
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=201
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.015 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=202
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=204
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=206
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=301
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=302
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.016 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=307
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=308
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.015 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=delivery_failed identity=123456:987654 http_status=None
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ok
test_exact_full_payload_and_post_fetch_observation_time (test_scan.RequiredBoundaryTests.test_exact_full_payload_and_post_fetch_observation_time) ... ts=2026-09-05T12:00:07Z level=INFO event=delivered identity=123456:987654 http_status=200
ts=2026-09-05T12:00:07Z level=INFO event=scan_summary elapsed_seconds=7.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=1 unsent=0 baseline_created=False
ok
test_invalid_state_types_and_timestamps_fail_before_effects (test_scan.RequiredBoundaryTests.test_invalid_state_types_and_timestamps_fail_before_effects) ... ts=2026-09-05T07:35:54Z level=ERROR event=state_invalid reason=StateError
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=state_invalid reason=StateError
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=state_invalid reason=StateError
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=state_invalid reason=StateError
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=state_invalid reason=StateError
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=state_invalid reason=StateError
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=state_invalid reason=StateError
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=state_invalid reason=StateError
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=state_invalid reason=StateError
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=state_invalid reason=StateError
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=state_invalid reason=StateError
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=state_invalid reason=StateError
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=state_invalid reason=StateError
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=state_invalid reason=StateError
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=state_invalid reason=StateError
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=state_invalid reason=StateError
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ok
test_missing_scalars_numpy_booleans_and_contingent (test_scan.RequiredBoundaryTests.test_missing_scalars_numpy_booleans_and_contingent) ... ts=2026-09-05T07:35:54Z level=WARNING event=malformed_required_field_row row_index=0
ts=2026-09-05T07:35:54Z level=WARNING event=malformed_required_field_row row_index=0
ts=2026-09-05T07:35:54Z level=WARNING event=malformed_required_field_row row_index=0
ts=2026-09-05T07:35:54Z level=WARNING event=malformed_identity_row row_index=0
ts=2026-09-05T07:35:54Z level=WARNING event=malformed_identity_row row_index=0
ts=2026-09-05T07:35:54Z level=WARNING event=malformed_identity_row row_index=0
ts=2026-09-05T07:35:54Z level=WARNING event=malformed_identity_row row_index=0
ok
test_property_and_webhook_url_authority_tables (test_scan.RequiredBoundaryTests.test_property_and_webhook_url_authority_tables) ... ts=2026-09-05T07:35:54Z level=ERROR event=webhook_config_invalid reason=webhook URL must be discord.com
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=webhook_config_invalid reason=webhook URL must be discord.com
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=webhook_config_invalid reason=webhook URL must not contain credentials or an explicit port
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=webhook_config_invalid reason=webhook URL must not contain credentials or an explicit port
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=webhook_config_invalid reason=webhook URL must not contain credentials or an explicit port
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=webhook_config_invalid reason=webhook URL must not contain credentials or an explicit port
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=webhook_config_invalid reason=webhook URL must be discord.com
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=webhook_config_invalid reason=webhook URL must be discord.com
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=webhook_config_invalid reason=webhook URL must not contain query/fragment
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=webhook_config_invalid reason=webhook URL must not contain query/fragment
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=webhook_config_invalid reason=webhook URL path is not a valid webhook path
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=webhook_config_invalid reason=webhook URL must be https
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T07:35:54Z level=ERROR event=webhook_config_invalid reason=webhook URL path is not a valid webhook path
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ok
test_unknown_hoa_status_conflicts_and_display_tuple_ties_in_both_orders (test_scan.RequiredBoundaryTests.test_unknown_hoa_status_conflicts_and_display_tuple_ties_in_both_orders) ... ok
test_actual_write_flush_fsync_close_replace_order (test_scan.RequiredPersistenceTests.test_actual_write_flush_fsync_close_replace_order) ... ok
test_persistence_failures_preserve_bytes_and_stop_before_sleep_or_next_post (test_scan.RequiredPersistenceTests.test_persistence_failures_preserve_bytes_and_stop_before_sleep_or_next_post) ... ok
test_recovery_ordering_and_exactly_one_healthy_save (test_scan.RequiredPersistenceTests.test_recovery_ordering_and_exactly_one_healthy_save) ... ts=2026-09-05T12:00:00Z level=INFO event=webhook_backoff not_before=2099-01-01T00:00:00Z
ts=2026-09-05T12:00:00Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T12:00:00Z level=ERROR event=scrape_failed error_class=RuntimeError
ts=2026-09-05T12:00:00Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T12:00:00Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=1 candidate=0 confirmed=0 unsent=0 baseline_created=False
ts=2026-09-05T12:00:00Z level=INFO event=baseline_created eligible_count=1
ts=2026-09-05T12:00:00Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=0 baseline_created=True
ok
test_both_clocks_and_disk_at_retry_or_exhausted_success_next_post (test_scan.RequiredTimingTests.test_both_clocks_and_disk_at_retry_or_exhausted_success_next_post) ... ts=2026-09-05T12:00:02Z level=INFO event=delivered identity=1:1 http_status=200
ts=2026-09-05T12:00:02Z level=INFO event=scan_summary elapsed_seconds=3.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=1 unsent=0 baseline_created=False
ts=2026-09-05T12:00:21Z level=INFO event=delivered identity=1:1 http_status=200
ts=2026-09-05T12:00:21Z level=INFO event=scan_summary elapsed_seconds=1.75 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=1 unsent=0 baseline_created=False
ts=2026-09-05T12:00:00Z level=INFO event=delivered identity=1:1 http_status=200
ts=2026-09-05T12:00:02Z level=INFO event=delivered identity=2:1 http_status=200
ts=2026-09-05T12:00:02Z level=INFO event=scan_summary elapsed_seconds=3.0 total_fetched=2 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=2 already_seen=0 candidate=2 confirmed=2 unsent=0 baseline_created=False
ts=2026-09-05T12:00:10Z level=INFO event=delivered identity=1:1 http_status=200
ts=2026-09-05T12:00:21Z level=INFO event=delivered identity=2:1 http_status=200
ts=2026-09-05T12:00:21Z level=INFO event=scan_summary elapsed_seconds=1.75 total_fetched=2 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=2 already_seen=0 candidate=2 confirmed=2 unsent=0 baseline_created=False
ok
test_post_reserve_boundary (test_scan.RequiredTimingTests.test_post_reserve_boundary) ... ts=2026-09-05T12:02:05Z level=INFO event=delivered identity=123456:987654 http_status=200
ts=2026-09-05T12:02:05Z level=INFO event=scan_summary elapsed_seconds=125 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=1 unsent=0 baseline_created=False
ts=2026-09-05T12:02:05Z level=ERROR event=budget_exhausted candidate=123456:987654
ts=2026-09-05T12:02:05Z level=INFO event=scan_summary elapsed_seconds=125.001 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ok
test_response_created_long_gates_suppress_next_run (test_scan.RequiredTimingTests.test_response_created_long_gates_suppress_next_run) ... ts=2026-09-05T12:00:00Z level=ERROR event=budget_exhausted_before_sleep identity=1:1
ts=2026-09-05T12:00:00Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=2 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=2 already_seen=0 candidate=2 confirmed=0 unsent=2 baseline_created=False
ts=2026-09-05T12:00:00Z level=INFO event=webhook_backoff not_before=2026-09-05T12:00:32Z
ts=2026-09-05T12:00:00Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T12:00:00Z level=INFO event=delivered identity=1:1 http_status=200
ts=2026-09-05T12:00:00Z level=ERROR event=budget_exhausted_before_sleep identity=2:1
ts=2026-09-05T12:00:00Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=2 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=2 already_seen=0 candidate=2 confirmed=1 unsent=1 baseline_created=False
ts=2026-09-05T12:00:00Z level=INFO event=webhook_backoff not_before=2026-09-05T12:00:32Z
ts=2026-09-05T12:00:00Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T12:00:00Z level=ERROR event=budget_exhausted_before_sleep identity=1:1
ts=2026-09-05T12:00:00Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=2 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=2 already_seen=0 candidate=2 confirmed=0 unsent=2 baseline_created=False
ts=2026-09-05T12:00:00Z level=INFO event=webhook_backoff not_before=2026-09-05T12:06:01Z
ts=2026-09-05T12:00:00Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ts=2026-09-05T12:00:00Z level=INFO event=delivered identity=1:1 http_status=200
ts=2026-09-05T12:00:00Z level=ERROR event=budget_exhausted_before_sleep identity=2:1
ts=2026-09-05T12:00:00Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=2 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=2 already_seen=0 candidate=2 confirmed=1 unsent=1 baseline_created=False
ts=2026-09-05T12:00:00Z level=INFO event=webhook_backoff not_before=2026-09-05T12:06:01Z
ts=2026-09-05T12:00:00Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ok
test_sleep_reserve_boundary_and_oversleep (test_scan.RequiredTimingTests.test_sleep_reserve_boundary_and_oversleep) ... ts=2026-09-05T12:00:02Z level=INFO event=delivered identity=123456:987654 http_status=200
ts=2026-09-05T12:00:02Z level=INFO event=scan_summary elapsed_seconds=125.0 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=1 unsent=0 baseline_created=False
ts=2026-09-05T12:00:00Z level=ERROR event=budget_exhausted_before_sleep identity=123456:987654
ts=2026-09-05T12:00:00Z level=INFO event=scan_summary elapsed_seconds=123.001 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ts=2026-09-05T12:00:02Z level=ERROR event=budget_exhausted candidate=123456:987654
ts=2026-09-05T12:00:02Z level=INFO event=scan_summary elapsed_seconds=125.1 total_fetched=1 malformed_identity=0 malformed_required_field=0 duplicate_group=0 conflicting_duplicate=0 status_mismatch=0 style_mismatch=0 state_mismatch=0 city_mismatch=0 price_out_of_range=0 sqft_out_of_range=0 hoa_unknown=0 hoa_nonzero=0 eligible=1 already_seen=0 candidate=1 confirmed=0 unsent=1 baseline_created=False
ok
test_finite_decimal_scalar_inputs_are_valid_numbers (test_scan.ScalarEligibilityTests.test_finite_decimal_scalar_inputs_are_valid_numbers) ... ok
test_genuinely_oversized_values_still_display_unknown (test_scan.ScalarEligibilityTests.test_genuinely_oversized_values_still_display_unknown) ... ok
test_hoa_classification_table (test_scan.ScalarEligibilityTests.test_hoa_classification_table) ... ok
test_inclusive_price_and_sqft_boundaries (test_scan.ScalarEligibilityTests.test_inclusive_price_and_sqft_boundaries) ... ok
test_just_outside_boundaries_excluded_without_rounding (test_scan.ScalarEligibilityTests.test_just_outside_boundaries_excluded_without_rounding) ... ok
test_large_but_within_budget_values_display_correctly_not_unknown (test_scan.ScalarEligibilityTests.test_large_but_within_budget_values_display_correctly_not_unknown) ... ok
test_non_scalar_and_boolean_price_cannot_bypass_checks (test_scan.ScalarEligibilityTests.test_non_scalar_and_boolean_price_cannot_bypass_checks) ... ts=2026-09-05T07:35:54Z level=WARNING event=malformed_required_field_row row_index=0
ts=2026-09-05T07:35:54Z level=WARNING event=malformed_required_field_row row_index=1
ok
test_whitespace_and_case_are_normalized (test_scan.ScalarEligibilityTests.test_whitespace_and_case_are_normalized) ... ok
test_wrong_city_state_style_status_excluded (test_scan.ScalarEligibilityTests.test_wrong_city_state_style_status_excluded) ... ok
test_boolean_version_is_rejected_not_treated_as_one (test_scan.StateIntegrityTests.test_boolean_version_is_rejected_not_treated_as_one) ... ok
test_duplicate_json_keys_rejected (test_scan.StateIntegrityTests.test_duplicate_json_keys_rejected) ... ok
test_extra_and_missing_keys_rejected (test_scan.StateIntegrityTests.test_extra_and_missing_keys_rejected) ... ok
test_forbidden_json_constants_rejected (test_scan.StateIntegrityTests.test_forbidden_json_constants_rejected) ... ok
test_invalid_utf8_state_is_state_invalid_not_unexpected (test_scan.StateIntegrityTests.test_invalid_utf8_state_is_state_invalid_not_unexpected) ... ok
test_leftover_temp_file_never_used_as_recovery (test_scan.StateIntegrityTests.test_leftover_temp_file_never_used_as_recovery) ... ok
test_malformed_digest_and_timestamp_rejected (test_scan.StateIntegrityTests.test_malformed_digest_and_timestamp_rejected) ... ok
test_malformed_json_rejected (test_scan.StateIntegrityTests.test_malformed_json_rejected) ... ok
test_missing_state_file_is_fatal_before_fetch (test_scan.StateIntegrityTests.test_missing_state_file_is_fatal_before_fetch) ... ts=2026-09-05T07:35:54Z level=ERROR event=state_invalid reason=StateError
ts=2026-09-05T07:35:54Z level=INFO event=scan_summary elapsed_seconds=0.0 total_fetched=None malformed_identity=None malformed_required_field=None duplicate_group=None conflicting_duplicate=None status_mismatch=None style_mismatch=None state_mismatch=None city_mismatch=None price_out_of_range=None sqft_out_of_range=None hoa_unknown=None hoa_nonzero=None eligible=None already_seen=None candidate=None confirmed=None unsent=None baseline_created=False
ok
test_nonempty_uninitialized_seen_rejected (test_scan.StateIntegrityTests.test_nonempty_uninitialized_seen_rejected) ... ok
test_script_relative_lookup_works_from_another_cwd (test_scan.StateIntegrityTests.test_script_relative_lookup_works_from_another_cwd) ... ok
test_unreadable_state_directory_in_place_of_file (test_scan.StateIntegrityTests.test_unreadable_state_directory_in_place_of_file) ... ok
test_unsorted_duplicate_and_malformed_seen_values_rejected (test_scan.StateIntegrityTests.test_unsorted_duplicate_and_malformed_seen_values_rejected) ... ok
test_unsupported_version_rejected (test_scan.StateIntegrityTests.test_unsupported_version_rejected) ... ok
test_zero_only_seen_components_rejected (test_scan.StateIntegrityTests.test_zero_only_seen_components_rejected) ... ok

----------------------------------------------------------------------
Ran 139 tests in 1.300s

OK
Exit code: 0
```

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_scan.py -k offline_cli_dry_run -v
```

```text
test_offline_cli_dry_run_baseline (test_scan.OfflineCliDryRunTests.test_offline_cli_dry_run_baseline) ... ok
test_offline_cli_dry_run_initialized (test_scan.OfflineCliDryRunTests.test_offline_cli_dry_run_initialized) ... ok

----------------------------------------------------------------------
Ran 2 tests in 0.076s

OK
Exit code: 0
```

```powershell
.\.venv\Scripts\python.exe -c "import scan; s = scan.load_state(); assert s == dict(version=1, initialized=False, seen=[], disabled_webhook_sha256=None, discord_not_before=None), 'Shipped state must remain the initial baseline schema'"
```

```text
(no output)
Exit code: 0
```

```powershell
git diff --check
```

```text
(no output)
Exit code: 0
```

```powershell
git status --short
```

```text
warning: unable to access 'C:\Users\jacks/.config/git/ignore': Permission denied
warning: unable to access 'C:\Users\jacks/.config/git/ignore': Permission denied
 M scan.py
 M tests/test_scan.py
?? docs/codex-review/2026-09-05-codex-review-result-4.md
Exit code: 0
```

Supplemental environment, scope, and preservation assertions:

```powershell
@'
import hashlib, platform, re, subprocess, sys
from pathlib import Path
from importlib.metadata import distribution, version

print('Python:', sys.version.replace('\n', ' '))
print('Platform:', platform.platform())
print('Executable:', sys.executable)
print('HomeHarvest:', version('homeharvest'))
print('HomeHarvest direct_url.json:', distribution('homeharvest').read_text('direct_url.json'))
print('Requests:', version('requests'))
print('Branch:', subprocess.check_output(['git', 'branch', '--show-current'], text=True).strip())
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
assert head == '9f8cf7cb31f7ceba5cdaa8a95ba7e85fe4b0c080', head
print('Unchanged HEAD:', head)
assert not subprocess.check_output(['git', 'diff', '--cached', '--name-only']).strip()
print('Staging area: empty')
spec = Path('docs/codex-review/2026-09-05-codex-spec.md').read_text(encoding='utf-8')
section = spec.split('## 5. State, startup, and deduplication', 1)[1]
expected = re.search(r'```json\n(.*?)\n```', section, re.S).group(1).encode('utf-8') + b'\n'
baseline = Path('seen.json').read_bytes()
assert baseline == expected == subprocess.check_output(['git', 'show', 'HEAD:seen.json'])
print('Shipped baseline: exact canonical JSON bytes and HEAD match')
print('Shipped baseline SHA-256:', hashlib.sha256(baseline).hexdigest())
preserved = subprocess.check_output(['git', 'ls-files', 'docs'], text=True).splitlines()
for name in preserved:
    current = Path(name).read_bytes().replace(b'\r\n', b'\n')
    original = subprocess.check_output(['git', 'show', 'HEAD:' + name]).replace(b'\r\n', b'\n')
    assert current == original, name
print('Preserved tracked documents:', len(preserved), 'unchanged after newline normalization')
changed = set(subprocess.check_output(['git', 'diff', '--name-only'], text=True).splitlines())
assert changed == {'scan.py', 'tests/test_scan.py'}, changed
print('Implementation changes restricted to scan.py and tests/test_scan.py')
'@ | .\.venv\Scripts\python.exe -
```

```text
warning: unable to access 'C:\Users\jacks/.config/git/ignore': Permission denied
Python: 3.12.10 (tags/v3.12.10:0cc8128, Apr  8 2025, 12:21:36) [MSC v.1943 64 bit (AMD64)]
Platform: Windows-10-10.0.19045-SP0
Executable: C:\Users\jacks\boca-house-hunter\.venv\Scripts\python.exe
HomeHarvest: 0.8.18
HomeHarvest direct_url.json: {"url": "https://github.com/ZacharyHampton/HomeHarvest.git", "vcs_info": {"commit_id": "8a6ac96db419b56a18d295935217649039bcdd0a", "requested_revision": "8a6ac96db419b56a18d295935217649039bcdd0a", "vcs": "git"}}
Requests: 2.32.4
Branch: master
Unchanged HEAD: 9f8cf7cb31f7ceba5cdaa8a95ba7e85fe4b0c080
Staging area: empty
Shipped baseline: exact canonical JSON bytes and HEAD match
Shipped baseline SHA-256: 8fbd7a55044984d56de4e49c4be3f6efc40583c3079793eca64ca181bf7772d2
Preserved tracked documents: 7 unchanged after newline normalization
Implementation changes restricted to scan.py and tests/test_scan.py
Exit code: 0
```

Git's existing warning about the inaccessible user-level ignore file did not prevent any check. No Git configuration was changed. The final report-only update was followed by another successful `git diff --check`; HEAD and the empty staging area remained unchanged.

## 5. Acceptance boundary

**Ready for human diff review and local acceptance; changes are unstaged and uncommitted.** No remaining local correctness or required coverage blocker was found in this round.

**Live commissioning is outstanding.** Actual runner/source comparison, explicit-zero coverage assessment, silent durable baseline, controlled real one-identity delivery and repeat suppression, and observed scheduled runs/state pushes remain the separate spec §9 gate. No operational or deployment success is claimed.

