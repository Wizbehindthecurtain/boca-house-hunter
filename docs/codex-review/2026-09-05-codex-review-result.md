# Boca House Hunter implementation review result

Date: 2026-09-05. **Overall local verdict: NEEDS CHANGES.** Live commissioning remains outstanding and is a separate acceptance gate.

Reviewed implementation commit `e756f39b7692b4bd8cf56ab92b9f5a3a3b40c4db` on local `master`, with HEAD `87bcc55` adding the previously untracked plan. I inspected `git show e756f39 --stat` and the actual commit patches for all seven implementation files, then compared the source and tests with the canonical spec and the plan's handback checklist. The implementation commit adds exactly seven files, 2,596 lines. The subsequent commit adds only the plan. The working implementation files match the implementation commit; the original design, amendment, and canonical spec are unchanged. No implementation, spec, or plan edits were made in this review.

The existing `.venv` was reused. No installation, real HomeHarvest/Discord request, push, Actions dispatch, secret change, or commissioning action was performed. Additional review probes ran through Python stdin with synthetic data, temporary state, mocked transport, blocked sockets, and controlled clocks where relevant; no additional test/source file was created.

## 1. Handback checklist verdicts

Numbers follow the plan's 23 checklist bullets in order. A pass applies to the stated local contract, not to live operational acceptance. Findings R1–R11 below explain failures and remediation.

| # | Checklist item | Verdict | Evidence / limitation |
|---|---|---|---|
| 1 | Only seven implementation paths; preserved documents unchanged | **PASS** | Commit diff has exactly the allowed seven files. HEAD adds only the plan separately. Preserved document Git blobs and working contents were compared. The plan did not exist in the implementation parent, so its earlier untracked contents cannot be reconstructed from Git; the present review leaves its committed bytes intact. |
| 2 | Exact direct and installed dependency pins | **PASS** | Requirement text matches the spec. Installed HomeHarvest `direct_url.json` identifies `8a6ac96db419b56a18d295935217649039bcdd0a`; Requests is `2.32.4`; `pip check` passes. No extra direct dependency or fallback. |
| 3 | Single import-safe module, no-argument script-relative loader, Python 3.12/unittest | **PASS** | Source has an entry guard and no application fetch/send/write on import. No-argument loader command passed; copied-script tests exercise another working directory. Python is 3.12.10. |
| 4 | Exact fetch invocation and whole-result rejection | **PASS** | All 13 arguments match. Whole-result validation precedes processing and delivery; source and tests reject non-DataFrame, empty, cap, and missing columns. Generic scrape exceptions preserve state. |
| 5 | Scalar eligibility, boundaries, explicit-zero HOA, required behavioral coverage | **FAIL** | Ordinary inclusive/unrounded boundaries and boolean HOA exclusion work, but finite Decimal scalar inputs are rejected. Required coverage omits cases such as pandas `NA` and contingent status. R6, R10. |
| 6 | Identity constraints and duplicate resolution before filtering | **FAIL** | Integer IDs bypass the 64-digit limit; identifiable malformed duplicate rows are discarded before grouping and can leave a qualifying counterpart eligible. R4, R5. |
| 7 | Exact initial state and strict state validation | **FAIL** | Initial file bytes are exact and most validation works, but `seen=['0:000']` is accepted, contrary to the shared identity constraints. R5. |
| 8 | Atomic serialization/write order and fatal write failures | **PASS** | Source uses same-directory temp file, exact serialization/newline, flush/fsync/replace. Existing replacement-failure test passes; independent serialization and fsync failure probes each exit 1 after one POST, with no sleep/second POST and unchanged durable state. No-op scans do not write. Missing rate-limit save transitions are separately failed in item 13. |
| 9 | Baseline, repeat suppression, new pairs/relisting, reconsideration | **PASS** | Existing lifecycle tests pass and source implements these transitions, including a healthy empty eligible baseline. This scanner behavior does not cure the recurring-test blocker in R1. |
| 10 | Whole-batch payload validation, exact payload and observation timestamp | **FAIL** | Real initialized delivery builds all payloads before the first POST and uses the correct shape/time, but dry runs skip payload construction entirely. Actual-payload validation also lacks explicit project-budget checks. R3, R7. |
| 11 | URLs, optional formatting, escaping and UTF-16/text budgets tested | **FAIL** | Empty userinfo/port syntax is accepted; numeric fallback and address processing violate the contract; the suite lacks the required end-to-end boundary cases. R6–R8, R10. |
| 12 | Exact POST, strict confirmation, durable partial success before next action | **PASS** | One Session; correct parameters; no custom auth/retry adapter. Confirmation requires 200 plus ASCII digit-string ID. Independent A-success/B-failure probe observed durable A both at sleep and B's POST; the following run posted only B and retained both identities. Exhausted success-header handling is separately failed in item 13. |
| 13 | Durable/bounded rate handling, both clocks, pacing and budget tests | **FAIL** | Retries run without saving a gate or checking their budget/rounded UTC deadline; invalid exhausted-bucket reset is treated as ordinary success. Required tests are missing. R2, R10. |
| 14 | Failure responses, permanent latch and recovery/gate ordering | **PASS** | Ordinary errors stop; 401/403/404 latch; same canonical secret blocks fetching. Independent changed-secret probes verified future gate: zero fetch/POST/sleep/save; expired gate plus failed scrape: unchanged state; healthy no-candidate recovery: one save. Seen history is retained. |
| 15 | Entry flags, budget, all error outcomes and safe complete summaries | **FAIL** | Basic config and exception boundaries exist, but retries bypass the budget; final summaries are missing on failure/early exits and incomplete on success; malformed rows produce no required warnings. R2, R9. |
| 16 | Literal workflow contract | **PASS** | Workflow text matches spec after newline normalization, with terminal newline, pinned actions, guard, concurrency, checkout, permissions, tests, timeout and checked persistence loop. Live branch/token behavior is unverified. |
| 17 | Full suite plus both realistic offline CLI cases and unchanged baseline | **FAIL** | Commands report 80/80 and 2/2 passing, and baseline bytes stay unchanged. However, the initialized CLI test has zero new eligible pairs, omits the required count/clock/transport assertions and gated repeat, and the recurring suite fails as soon as state initializes. R1, R10. |
| 18 | Accurate README and complete pending commissioning/recovery instructions | **FAIL** | README acknowledges pending live access and many limitations, but contradicts latency/missed-alert limits and omits required verification/recovery/commissioning details. R11. |
| 19 | No additional UI/storage/search/integration/scraping infrastructure | **PASS** | No excluded additions in the diff. |
| 20 | No repeated alert/history/pruning/retry framework/bot/notification additions | **PASS** | No excluded implementation additions or exactly-once claim. Broken prescribed 429 handling is a conformance defect, not a new retry framework. |
| 21 | No extra hosting/polling/fallback or unsupported coverage/latency claims | **FAIL** | No extra infrastructure or unknown-to-zero fallback, but README opens with “the moment a new one appears.” R11. |
| 22 | Local hash/branch/counts/commands/dependency evidence; no deployment claim | **PASS** | Independently recorded here. `master`, implementation `e756f39`, HEAD `87bcc55`; exact environment and command results below. No local capability blocker for these commands. |
| 23 | Live runner/source/baseline/delivery/scheduled-run evidence | **BLOCKED / CAN'T VERIFY LOCALLY** | Not performed and outside this review's authorization. This blocks operational acceptance, independently of the local failures. |

### Step 11 commands actually run

I invoked the existing environment's `python` as `.\.venv\Scripts\python.exe`, equivalent to activating it. Commands were run in the plan's order; each completed successfully (exit 0):

| Command | Result |
|---|---|
| `python -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version"` | Python 3.12.10, Windows AMD64. |
| `python -m pip check` | `No broken requirements found.` |
| Plan's `importlib.metadata`/`direct_url.json` pin assertion command | `Exact dependency pins verified`; HomeHarvest VCS commit and Requests version as above. |
| `python -m unittest discover -s tests -v` | **80 tests, OK**, 0.414 seconds, no skips/expected failures. |
| `python -m unittest discover -s tests -p test_scan.py -k offline_cli_dry_run -v` | **2 tests, OK**, 0.059 seconds; baseline logs `would_baseline=1`, initialized logs **`candidate=0`**. |
| Plan's exact no-argument `scan.load_state()` initial-schema assertion | Passed. Independent byte comparison also matched the spec's initial JSON. |
| `git diff --check` | Passed. |
| `git status --short` | Clean before creating this report. Git warned that the user-level ignore file was inaccessible; repository status and commit inspection still succeeded. |

These are Windows offline results, not proof of Ubuntu runner execution. Passing the supplied tests does not satisfy missing assertions or requirements contradicted by the implementation.

## 2. Rulings on the three judgment calls

1. **Boolean HOA goes to unknown: AGREE.** Spec §4 explicitly excludes booleans from qualifying numeric fees. A boolean is not evidence of a nonzero fee; counting it as unknown is consistent with the conservative coverage reporting. Keep this choice and cover both Python and numpy booleans. This does not excuse rejecting valid Decimal zero values.
2. **Combined `malformed_required_field` counter: AGREE.** Spec §§4/7 require rejected-by-reason counts, not a separately named counter for every field. The common malformed-required reason is acceptable. Keep the distinct ordinary mismatch/range and unknown-HOA counts. However, the required warnings, row-level malformed-ID logging and complete final summaries still need implementation (R9); the accepted aggregation does not waive them.
3. **Clear stale latch/expired gate in the baseline save: AGREE.** Spec §6 already permits saving in-memory clearing after a healthy scan. A healthy baseline is such a scan, and the single initialization save is the appropriate place. A matching disabled digest must still fail first, a future gate must still prevent all external calls/writes, and a failed fetch must not save recovery changes. Current ordering follows those rules. Add explicit baseline recovery tests during the next pass.

## 3. Assessment of the claimed bug fixes

The reviewed commit introduces these files wholesale; it does not contain the implementer's intermediate pre-fix revisions. I can assess the final code and the described earlier mistakes, not independently establish the history of those intermediate edits.

1. **JSON boolean version rejection: correct and necessary.** `scan.py:550` checks `type(version) is not int or version != 1`. A comparison alone accepts `True` (and `1.0`); spec §5 and plan step 4 explicitly require strict types. The boolean-version regression test passes. Add the float-version case for completeness. Other state defects remain in R5.
2. **Atomic-write wrapper and outer safety net: correct for the tested paths and required error handling.** `scan.py:923` catches ordinary serialization/write failures, logs only the class under `state_write_failed`, and callers return 1. `main()` catches remaining `Exception` without printing its message/traceback. Replacement, fsync and serialization failure cases stop subsequent delivery. The claim that an otherwise uncaught `os.replace` exception would silently allow later POSTs is inaccurate: an uncaught exception would unwind and stop execution. The fix was needed for the specified controlled log/exit behavior and outer safety boundary. Complete summaries and phase reporting are still absent (R9), and the wrapper cannot protect an omitted pre-retry save (R2).
3. **Avoiding repeated Markdown escaping: correct and necessary for the exact payload.** `scan.py:721` sanitizes the address in `build_address_display()` and only truncates the composed title. The fixed source-label separator ` | ` in the footer is emitted literally; validated digit IDs need no escaping. The separator between property/listing IDs is `:`, not `|`. Re-escaping an already escaped address corrupts the intended representation; escaping the footer's fixed pipe violates the exact shape. Existing shape/footer tests pass. Address sanitization/truncation defects remain in R7. This fix primarily follows §6's payload contract, rather than being an exception-boundary fix in §7.

## 4. Defects and precise follow-up work

This consolidates checklist failures and additional defects found by inspecting the actual diff. Locations refer to the reviewed implementation, not to this report. P1 means a core behavior or deployment blocker; P2 means another required conformance correction. No finding below has been patched.

### R1 — P1: The recurring suite stops the service after baseline

**Location:** `tests/test_scan.py:1146`; workflow offline-test step. **Contract:** plan step 9 explicitly forbids requiring the live repository state to remain uninitialized.

`test_shipped_seen_json_matches_initial_schema` reads repository `seen.json` and compares it to the initial state. After a successful baseline commit, every subsequent workflow runs this test before scanning and fails. I reproduced the assertion failure by pointing this test's repository root to temporary valid initialized state (`seen=['1:1']`).

**Required change:** Test the initial schema using the preserved spec block and a harness-owned fixture. Keep the actual shipped-byte assertion in the one-time pre-commissioning verification, not in recurring workflow tests. Verify that the full suite can run with valid initialized repository state using an isolated checkout/copy; do not alter production history to make it pass.

### R2 — P1: Rate-limit retries are not durable or budget/gate compliant

**Locations:** `scan.py:800`, `scan.py:822`, `scan.py:839`, `scan.py:1057`. **Contract:** spec §§6–7.

`send_one()` retries internally before returning control to the state-saving caller. It computes but does not persist the gate for short 429 delays, sleeps only the unrounded delay, and checks neither the remaining application budget nor the saved UTC deadline before retrying. The main-loop reserve check applies only once per candidate. Success pacing also lacks a both-clock recheck before the next POST.

Independent frozen-clock probes observed:

- At `12:00:00Z`, 429 body delay `1.5` followed by 200: second POST occurs at `+1.75s` and disk gate is still null at both sleep and retry. Required gate is `12:00:02Z`, saved before either action, with retry no earlier than that gate.
- With the first POST at elapsed 125 seconds, the same retry occurs at 126.75 seconds (23.25 seconds remain) and the run exits 0. The required sleep-plus-25 reserve already fails at 125 seconds.
- A confirmed 200 with `X-RateLimit-Remaining: 0` and no reset delay saves A, sleeps 0.5, sends B and exits 0. Required behavior saves A, stops without B and exits 1. The parser conflates invalid/missing exhausted reset with a non-exhausted success.

**Required change:** Make every 429 transition save its rounded gate before waiting/retrying, preserve that gate if a later response fails, and stop immediately if saving fails. Enforce remaining-budget and both-clock requirements before every POST/sleep, including retries. Distinguish absent/non-exhausted headers from exhausted-with-invalid-reset; retain the confirmed identity before the latter fails. Preserve the three-attempt bound, identical payload, long-delay cross-run persistence and no final bucket-draining sleep. Add behavioral tests with advancing UTC/monotonic clocks, disk snapshots before every retry, backward wall-clock movement, exact budget boundaries, invalid success reset, and delays over 30 seconds/over five minutes.

### R3 — P1: Dry runs do not validate payloads

**Locations:** `scan.py:1012`, `scan.py:1029`, versus payload construction at `scan.py:1046`. **Contract:** spec §7 and plan steps 7/9.

Both dry-run branches return before calling `build_payload`. An otherwise eligible row with `sqft='1e100'` currently exits 0 in dry mode while real initialized mode exits 1 with `payload_invalid`. Although that formatter failure itself needs R6, it demonstrates that the commissioning dry run does not exercise the promised path.

**Required change:** Run the appropriate eligible/candidate payload-validation path before completing either dry-run mode, retaining zero webhook use, writes and sleeps. Test successful payload validation and a later candidate construction failure through the actual copied-script entry point; failure must return 1 without any side effect.

### R4 — P1: Malformed duplicates can be hidden by a qualifying row

**Locations:** `scan.py:377`, `scan.py:447`. **Contract:** spec §4 and §7 row boundary.

Rows with a usable identity but malformed required data are discarded before grouping. Pairing the base qualifying row with the same identity and `status=None`, `property_url='invalid'`, or `list_price='bad'` yields one eligible identity, `malformed_required_field=1`, and `conflicting_duplicate=0`. The malformed counterpart must not be hidden. Also, storing only `hoa_class` makes distinct normalized nonzero fees appear to agree, losing the specified required-field conflict distinction even though these particular rows remain ineligible.

**Required change:** Group every usable identity before discarding rows for required-field failures; preserve enough normalized values/invalid markers to suppress a group containing contradictory required data. Retain the actual normalized HOA value for agreement checks. Add valid-plus-malformed and zero-versus-unknown/nonzero/status duplicate tests in both input orders, and display-tuple tie tests.

### R5 — P1: Identity validation differs between fetch and state

**Locations:** `scan.py:151`, `scan.py:545`, `scan.py:595`. **Contract:** spec §§4–5.

`normalize_identity_component(10**64)` returns a 65-digit ID, while its string equivalent is rejected. The integer path never applies the length limit. Such an identity can be baselined or sent and saved, then cause the next load to reject the state. Conversely, the state loader accepts zero-only components: the probe loaded `seen=['0:000']` successfully. Invalid UTF-8 state also escapes the loader's `OSError` catch as `UnicodeDecodeError`, reaching `unexpected_error` rather than the required `state_invalid` boundary.

**Required change:** Apply identical ASCII, length and non-zero-only constraints after integer conversion and when validating stored pairs. Route decoding failures through state validation without replacement. Test 64/65-digit integers and strings, leading zeros, all-zero components on both sides of a stored pair, wrong scalar types, and invalid UTF-8 bytes. Persisted identities must always reload successfully.

### R6 — P2: Finite numeric inputs and display fallbacks are incomplete

**Locations:** `scan.py:191`, `scan.py:272`, `scan.py:318`, `scan.py:327`. **Contract:** spec §§4/6.

- `normalize_number(Decimal('0'))` returns `None`; finite Decimal price/sqft/HOA scalars are valid numbers under the spec.
- `format_size(Decimal('1e100'))` raises `InvalidOperation` while quantizing under the default Decimal context, instead of using the specified long-display `Unknown` fallback. A valid row can fail the entire payload batch.
- `format_optional_nonneg_int('1e5000')` raises `ValueError` during integer-to-string conversion before the 64-character fallback. For agreeing duplicates this can occur during tie-breaking and abort normalization too.
- `format_price(Decimal('400000.001'))` produces `$400,000`; the source value is nonintegral and requires two displayed decimals (`$400,000.00`). The implementation selects decimals based on the rounded result.

**Required change:** Accept supported finite numeric scalars without boolean/coercion loopholes; determine price decimal count from the original value; make numeric formatting and its length checks safe for large magnitudes before conversion/quantization fails. Optional invalid/oversized values must display `Unknown`. Add scalar Decimal, rounding-across-integrality and oversized numeric tests through actual payloads and duplicate processing.

### R7 — P2: Address sanitization and component budgets are wrong

**Locations:** `scan.py:287`, `scan.py:336`, `scan.py:721`. **Contract:** spec §§4/6.

The component paths slice with `[:200]`, counting Python code points and omitting the required ellipsis. A formatted address of 150 house emoji survives as a 300-UTF-16-unit component. Final title truncation limits the title but does not satisfy the separate component contract. Control characters are removed before whitespace collapse: `123\nMain\tSt` becomes `123MainSt`. The nonempty-address choice occurs before sanitization: `formatted_address='\x07'` selects an empty display instead of falling back to an available street line. The control regex covers only ASCII controls. Finally, the builder checks only aggregate Python `len`, without explicitly validating actual title/field/footer project budgets using the required counting rule.

**Required change:** Collapse whitespace without joining address words, remove remaining control characters, select the first nonempty sanitized address, and apply the shared UTF-16-aware `limit-3 + '...'` truncation to source components. Validate the actual final payload's individual and aggregate budgets. Retain the correct single escaping pass. Add full-payload tests for ASCII/astral overlong components, control-only fallback, non-ASCII controls, and all formatting/budget boundaries.

### R8 — P2: URL validators accept forbidden authority syntax

**Locations:** `scan.py:221`, `scan.py:661`. **Contract:** spec §§4/6.

Truthiness checks on parsed username/password miss explicitly empty userinfo; `parts.port is None` also accepts an explicitly present empty port. Probes accepted and canonicalized both `https://@www.realtor.com/path` and `https://www.realtor.com:/path`, plus the corresponding `discord.com/api/webhooks/1/token` authorities. These violate the no-credentials/no-explicit-port contract.

**Required change:** Reject the presence of userinfo or port syntax, including empty values, without weakening hostname/path validation. Add table-driven tests for both URL types, valid webhook equivalence, and malformed authority/query/fragment/length/path cases. No network calls are necessary.

### R9 — P2: Required logging and summaries are missing

**Locations:** `scan.py:447`, `scan.py:932`, `scan.py:991`, `scan.py:1133`. **Contract:** spec §§4/5/7.

Normalization increments counters without warnings and discards the malformed row index. `scan_summary` is defined only after processing, invoked only on selected success exits, and never consistently includes duplicate-group count, already-seen count, candidate/confirmed/unsent counts and baseline-created flag together. Ordinary delivery/write/scrape/config failures and early latch/backoff exits have no final summary even when exception handling runs normally. Baseline logs a flag on `scan_summary`, not the prescribed `baseline_created` event. The outer safety net logs class without phase.

**Required change:** Maintain run accounting from entry and emit a complete final summary on all normally handled exits, with meaningful zero/not-yet-observed accounting rather than invented successful counts. Log malformed-ID row index without raw ID contents; warn safely for malformed required values; distinguish duplicate groups from conflicting groups. Emit the baseline event and phase/class error details. Test partial success/failure and pre-fetch failures, not just the presence of one error line. The combined malformed-field counter remains acceptable.

### R10 — P1: The behavioral suite does not establish the planned acceptance gate

**Locations:** `tests/test_scan.py:904`, `tests/test_scan.py:1060`, `tests/test_scan.py:1156`, and related groups. **Contract:** plan steps 9/11 and handback checklist.

The two named CLI tests exist and copy/run the actual script from another directory, but both use one eligible row plus an unknown-HOA row. The initialized state already contains the sole eligible pair, so it never exercises a would-send candidate. They assert only exit status and state bytes, not exact fetch arguments, required counts, no sleep/temp write/transport use, or the initialized gated repeat. Their clocks/sleep are not replaced. Most suite tests also lack the required socket/HTTP guard and controlled clocks; the module docstring's claim that those are always frozen is false.

The five rate-limit tests omit save-before-retry, actual rounded deadlines, budget boundaries, invalid exhausted-success reset, both clocks, and actual creation of a long cross-run gate. Other gaps include required missing scalars, malformed contradictory duplicates, state types/zero IDs, several invalid confirmation responses, and explicit recovery combinations. A test named `test_unexpected_exception_is_caught_and_logged_safely` exercises the scrape catch instead of the outer boundary; the token test happens to exercise the outer boundary via a `RuntimeError`, but does not substitute for the full Requests/JSON failure matrix. The atomic sequence test checks calls, not flush/fsync/replace order.

**Required change:** Complete the plan's behavioral matrix, including regression tests for R1–R9, without changing expected behavior to match this implementation. Make the initialized CLI fixture contain one seen eligible pair and one new eligible pair, require would-send count 1, and repeat with latch/future gate. Block unexpected network attempts across the suite and freeze/advance clocks deliberately. Verify observable state/order/log/exit outcomes, then rerun both prescribed commands. Do not interpret the present 80 passing tests as complete coverage.

### R11 — P2: README contradicts service limits and omits required operator guidance

**Locations:** `README.md:6`, `README.md:37`, `README.md:87`, `README.md:130`. **Contract:** spec §§1/5/7–10; plan step 10.

The introduction promises a message “the moment a new one appears,” despite the later latency caveat. “Silent misses shouldn't happen” is also unsupported: partial source responses, unknown HOA and listings vanishing before a later successful retry can miss alerts; there is no durable outbox. The introductory baseline wording should describe identities first becoming eligible, not only newly appearing inventory.

**Required change:** Remove those promises and describe the actual best-effort service. Include the omitted installed-pin verification and Git checks from step 11, mark the initial-state assertion as pre-commissioning only, and provide the Ubuntu/WSL live timeout invocation. Explicitly document long persisted gates (including after secret replacement), schedule-disabled/wait-for-active-run rules for every manual repair, latch-only repair, and daily history checks. Complete commissioning instructions with unknown/positive source examples as well as explicit zero; record absent explicit-zero coverage as unproven; account for a scheduled baseline winning the race; verify actual state pushes/public standard runners/no paid storage and no competing sender. Replacing a secret does not necessarily persist latch clearing on the immediate next run because a future gate or scrape failure can prevent it.

## 5. Overall acceptance and live boundary

**NEEDS CHANGES for the local implementation.** The exact dependency pins, fetch/workflow contract, ordinary lifecycle, confirmed-send persistence and several failure boundaries are sound, and all provided verification commands execute successfully. However, R1 would stop all scanning after initialization, and the rate-limit, dry-run, duplicate, identity, formatting and acceptance-suite defects violate explicit local requirements. These are actionable corrections within the seven-file implementation scope, not reasons to redesign the service or relax the spec.

The next implementation pass should address these findings and rerun the complete local gate before another review. This review creates only this report; it does not authorize deployment or claim any live success.

After local acceptance, the plan's separate commissioning sequence is still required: configure/verify remote `main`, webhook/token/branch permissions and notifications with scheduling disabled; perform real runner dry-run/source comparison and explicit-zero coverage assessment; verify silent durable baseline; perform the controlled one-identity real delivery and repeat-suppression check; then observe actual scheduled runs, durations, gaps and state pushes on public standard runners. None of those live items is satisfied by these Windows offline results.
