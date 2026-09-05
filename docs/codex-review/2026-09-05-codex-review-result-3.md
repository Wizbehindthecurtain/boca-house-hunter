# Boca House Hunter second fix-pass review

Date: 2026-09-05. **Overall verdict: NEEDS CHANGES.** Remaining findings: **R2, R5, R9, R10**. R6, R7, and R11 are functionally resolved; R1, R3, R4, and R8 remain closed functionally. This is offline review evidence, not live commissioning evidence.

Reviewed `677cd375a2af59661e49829567329fa4a168755e` on local `master`, using `git show --stat 677cd37`, the actual `bd48075..677cd37` diffs, current source and tests, the round-2 report, canonical spec, and implementation-plan handback checklist. HEAD is the reviewed commit. The patch changes only `README.md`, `scan.py`, and `tests/test_scan.py` (781 insertions, 258 deletions); `bd48075` separately committed the round-2 review. The working tree was clean at entry.

Only this new report was written. No implementation file, spec, plan, prior review, or shipped state was edited. No installation, live scrape, Discord request, remote Git operation, Actions dispatch, secret change, or commissioning action was performed. Independent probes ran from Python stdin with synthetic DataFrames, mocked transport, blocked sockets, controlled clocks, and temporary state. They are review evidence, not committed regression tests.

## 1. Findings at a glance

| Finding | Ruling | Evidence / remaining issue |
|---|---|---|
| R2 — Rate handling, clocks, budgets | **Partially fixed** | Both-clock gating and overflow confirmation preservation now work. A sleep overrun can still authorize a POST with less than the required 25-second reserve. See §3.1. |
| R5 — Oversized identity rejection | **Partially fixed** | The helper catches integer-to-string `ValueError`, and ordinary/64-digit integer IDs save and reload correctly. A real object-dtype DataFrame containing the oversized integer still aborts in `iterrows()` before reaching that helper. See §3.2. |
| R6 — Within-budget numeric formatting | **Fixed correctly** | Explicit quantization context fixes the precision defect. Actual payload probes render `1e26`, `1e40`, and the 64-character `1e43` size correctly; `1e44` and `1e70` become `Unknown`. Required recurring full-payload boundary coverage remains under R10. |
| R7 — Sanitized fallback and payload validation | **Fixed correctly** | Final whitespace cleanup fixes the control-only fallback case. The builder now independently validates actual title, field-value, footer, and aggregate budgets. Boundary/failure coverage remains under R10. |
| R9 — Complete accurate summaries | **Partially fixed** | Eligible/seen overlap, healthy baseline/dry-run counts, fetched row counts on shape rejection, confirmation-save failure accounting, and the outer summary handler improve correctly. Other reached failure paths still lose known counts; duplicate-group accounting excludes conflicting groups. See §3.3. |
| R10 — Behavioral acceptance suite | **Still insufficient** | 114 tests pass, including the new targeted regressions, but a pre-existing partial-success test now passes without attempting B. Several specifically required behavioral checks remain absent. See §4 for the bounded remaining work and why it matters. |
| R11 — Operator instructions | **Fixed correctly** | README now explains silent partial-source results, same-secret latch repair, in-memory versus persisted clearing, preservation of long gates across runs/secret changes, setup-time writer exclusion, and scheduled state-push verification. |

“Fixed correctly” closes the code/document defect; it does not claim that every separately required acceptance test has been committed.

## 2. Verification performed

The existing `.\.venv\Scripts\python.exe` reports **Python 3.12.10, Windows AMD64**. The separate `py -3.12` launcher lookup again returned **“No suitable Python runtime found.”** The working venv ran the prescribed checks; no dependency acquisition was needed. Windows results do not establish Ubuntu runner behavior.

| Check | Result |
|---|---|
| Python 3.12 version assertion | Passed, 3.12.10. |
| `python -m pip check` | `No broken requirements found.` |
| Exact installed metadata assertions from plan step 11 | Passed: HomeHarvest VCS commit `8a6ac96db419b56a18d295935217649039bcdd0a`; Requests `2.32.4`. |
| `python -m unittest discover -s tests -v` | **114 tests, OK**, no skipped/expected-failure cases. A repeat captured the complete result after the initial verbose output was truncated: 0.604 seconds. |
| `python -m unittest discover -s tests -p test_scan.py -k offline_cli_dry_run -v` | **2 tests, OK**, 0.069 seconds; both required named tests ran. |
| Exact no-argument `scan.load_state()` initial-schema assertion | Passed. |
| Shipped state bytes versus canonical §5 JSON block | Exact match, including trailing newline. SHA-256: `8fbd7a55044984d56de4e49c4be3f6efc40583c3079793eca64ca181bf7772d2`. |
| Preserved documents | Original design, amendment, spec, plan, and both prior reviews have identical Git blobs between the compared commits; working contents match after newline normalization. |
| `git diff --check`; `git diff bd48075..677cd37 --check` | Passed. |
| Working tree before report creation | Clean. Git's warning about the inaccessible user-level ignore file did not block repository inspection. |

Independent positive probes also established:

- For both a 429 retry and A-success/B-next pacing, a backward UTC jump during sleep led to another wait. The subsequent POST occurred at monotonic +3 seconds and UTC `12:00:02Z`, satisfying the durable `12:00:02Z` gate. A forward UTC jump during state saving still required 1.75 seconds of monotonic elapsed time. These probes inspected both clocks and disk at the subsequent POST, not merely sleep arguments.
- A confirmed response with exhausted remaining count and reset-after `1e20` saved A, returned 1, and made no further POST or sleep. A 429 with the same unrepresentable delay stopped without marking an identity seen. Neither escaped through `unexpected_error`.
- Actual fake 429 responses requesting 31 and 360 seconds produced durable `12:00:32Z` and `12:06:01Z` gates, respectively, and failed without sleeping. The following run returned 0 without fetch, POST, sleep, or write, preserving those bytes.
- Injected serialization, fsync, and replacement failures after confirmation each stopped after one POST, with no sleep and unchanged durable state.
- Changed-secret/future-gate recovery made no external call or write; expired-gate/failed-scrape recovery preserved disk; healthy changed-secret no-candidate and baseline recovery each saved exactly once and cleared the recovery fields.

## 3. Remaining correctness defects

Locations below refer to `677cd37`. P1 denotes a core behavior blocker; P2 denotes another required correctness/conformance correction.

### 3.1 R2 — P1: Recheck the POST reserve after waiting

**Locations:** `scan.py:1063` (`_await_gate`), `scan.py:1324` (reserve check), `scan.py:1342` (POST). **Contract:** spec §7: “Before each POST require at least 25 seconds remaining.”

The reserve check is performed before `_await_gate()`. That helper checks `wait + 25` before a sleep, but returns `True` once the clocks satisfy the gate without checking the remaining budget again. The caller then immediately POSTs. Real sleep can exceed its requested duration; the pre-sleep estimate does not establish the reserve at the subsequent POST.

Independent end-to-end reproduction, using a coherent advancing clock:

1. Start the run at elapsed 0 and make the synthetic fetch consume 123 seconds.
2. The first POST at elapsed 123 receives 429 with `retry_after=1.5`. The padded, upward-rounded UTC gate requires a 2-second wait. There are exactly 27 seconds left, so the pre-sleep check permits it.
3. Let the requested 2-second sleep consume 2.1 seconds.
4. Both rate gates have passed, so the scanner makes the retry at **elapsed 125.1**, with **24.9 seconds remaining**, accepts its confirmation, and exits **0**.

Observed POST times were `[123.0, 125.1]`. This is a reserve violation, not a failure of the repaired rate-limit clocks. A larger scheduling overrun can make the breach larger.

**Required correction:** after the final wait/gate check and immediately before every POST, recompute the remaining budget and stop with exit 1 if it is below 25 seconds. Preserve the durable gate and leave unsent identities unseen. Retain the sleep-plus-25 precheck as well. Add regression tests for oversleep and the exact accepted/rejected POST and sleep-reserve boundaries.

The six-iteration bound itself fails closed: it cannot authorize an early POST when a gate remains pending. The backward/forward UTC fixes are real, and `_safe_not_before()` correctly closes the reported confirmation-loss arithmetic boundary. Those corrections should remain.

### 3.2 R5 — P2: DataFrame iteration can fail before identity rejection

**Locations:** `scan.py:169` (corrected helper), `scan.py:522` (`df.iterrows()`); `tests/test_scan.py:431` (oversized-integer regression). **Contract:** spec §4 and the round-2 correction: malformed identity is a warned/counted row rejection, without aborting other rows.

The new helper and direct object-dtype Series regression work. However, `process_dataframe()` still obtains its rows through pandas `iterrows()`. In the installed pandas, that iterator constructs a Series and performs inference that can raise `OverflowError: int too large to convert to float` before `_normalize_row()` is called.

Reproduction with an already-existing, shape-valid DataFrame:

```python
df = make_df([
    base_row(property_id="1"),
    base_row(property_id="2"),
]).astype(object)
df.at[0, "property_id"] = 10**5000
# Use this df as the mocked scrape result for an initialized real-mode run.
```

The run returns **1**, makes zero POSTs, and logs `unexpected_error error_class=OverflowError phase=normalize`. The summary reports `total_fetched=2` but `malformed_identity=None`; the otherwise valid second row is never processed. Durable state remains unchanged. This is not a DataFrame-construction failure in the probe: assignment succeeds, and the exception occurs in the scanner's iteration of that DataFrame.

**Required correction:** make row traversal preserve object scalars without invoking unsafe pandas inference before normalization, while retaining safe row-index warnings and existing duplicate semantics. Do not catch this only around the whole scan and discard the valid rows. Commit a full-DataFrame mixed bad/good-row test that asserts one malformed identity, one retained eligible identity, and no scan-wide exception. Keep the helper conversion guard and accepted-ID save/reload checks.

### 3.3 R9 — P2: Known accounting is still lost on several exits

**Locations:** `scan.py:1235`, `1269`, `1279`, `1292`, `1355`, `1388`, `1430`; duplicate accounting at `scan.py:536` and `556`. **Contract:** spec §7 final-summary requirements.

The added outer handler now emits a summary and a useful stage such as `normalize` or `delivery`. However, delivery totals remain local to the loop and are copied into the summary only on selected returns. Payload/recovery branches also initialize known counts too late.

Independent observed failures:

| Scenario | Actual summary | Required known accounting |
|---|---|---|
| Baseline payload construction fails after finding two eligible identities | `eligible=2 already_seen=None candidate=None confirmed=None unsent=None` | Candidate and baseline accounting are already known: `candidate=2 already_seen=0 confirmed=0`; use the same explicit baseline unsent convention as the healthy baseline. |
| Initialized real or dry-run payload construction fails with two candidates | `candidate=2 confirmed=None unsent=None` | `confirmed=0 unsent=2`. |
| A confirms and saves; B's POST raises an unexpected `RuntimeError` | Disk contains A; summary says `candidate=2 confirmed=None unsent=None`, phase `delivery` | `confirmed=1 unsent=1`. |
| A confirms and saves; B returns 429; saving B's gate fails | Disk contains A; summary says `candidate=2 confirmed=None unsent=None` | `confirmed=1 unsent=1`, plus the existing persistence failure event. |
| Healthy zero-candidate recovery fails to save the cleared latch | `candidate=0 confirmed=None unsent=None` | `confirmed=0 unsent=0`. |

The specific new test for a failed save immediately after A's confirmation passes correctly: it reports `confirmed=1 unsent=1`. That correction does not cover the paths above. In particular, incrementing a local `confirmed` variable before checking a boolean save result is insufficient if finalization reads a different, stale summary dictionary.

**Required correction:** initialize observable candidate/delivery counts before fallible payload/recovery work, and keep confirmation accounting available to every finalization path as deliveries occur. A shared context or a finalizer that derives the totals from current delivery state can do this; individual return-site patches have already left omissions. Continue distinguishing remote confirmation from failed persistence. Keep `None` only where the relevant stage was not observed.

The new `duplicate_group` counter also increments only after a duplicate group passes conflict checks. Two rows sharing an identity but disagreeing on HOA produce **`duplicate_group=0 conflicting_duplicate=1`**. Spec §7 requests duplicate groups as well as rejection-by-reason counts. Count each usable-identity group with multiple rows once before deciding whether it conflicts; preserve `conflicting_duplicate` as the rejected subset. An agreeing-only statistic can be separately named if desired. A mixed fixture with an agreeing group, a conflicting group, and a singleton should establish the meaning explicitly.

## 4. R10 — Coverage judgment and exact remaining work

**Current coverage is not sufficient to accept the fixes or declare readiness for live commissioning.** This judgment is based on an observed false-positive test, reproduced code defects, and specific mandatory observations in the original plan. It is not a demand for every Cartesian combination of every malformed input.

### 4.1 P1 coverage defect: the partial-success test no longer reaches B

At `tests/test_scan.py:1173`, `test_confirmed_a_then_failed_b_persists_only_a` mocks sleep without advancing monotonic time. With the new `_await_gate()` loop, it exhausts the six iterations waiting for A's 0.5-second pacing deadline and exits before B's POST. Its assertions only require exit 1 and A in state, so it passes for the wrong reason.

I ran that existing test unchanged while wrapping `post_once`: **the test passed, but `post_once` ran once**. Its failure event was `budget_exhausted_before_sleep identity=2:1`; the supplied B=500 response was never consumed.

**Required:** use the advancing clock, require exactly A and B attempts and B's intended failure event, inspect durable A at sleep and at B's POST, then run another scan and assert that only B is retried. This is the plan's ordinary partial-delivery acceptance case, not an optional expansion.

### 4.2 Load-bearing recurring checks still required

These can be compact table-driven tests. The following observations are necessary; a high test count or helper-only assertion is not a substitute.

| Area | Remaining committed observations | Why it blocks acceptance |
|---|---|---|
| Timing and budgets | Regression for §3.1; exact 25-second POST and sleep-plus-25 boundaries; both clocks and disk inspected at subsequent POSTs; exhausted-success pacing under clock movement; gate-save failure stops before sleep/retry; actual 31-/360-second response gates suppress a subsequent run. | The current tests missed a real reserve violation. Ordinary success and 429 take different state/deadline paths; directly preloading a future state fixture does not test gate creation. |
| Identity and accounting | Full DataFrame bad/good oversized integer case; complete summary values for the §3.3 scenarios and one early failure; duplicate-group total including conflicts; a seen history containing identities absent from the fetch. | Direct Series/helper tests bypass the iterator failure, and checking only selected healthy counts missed known delivery facts on failure. The revised CLI overlap fixture alone would also pass the old `len(seen_set)` implementation. |
| Atomic persistence and recovery | Assert actual flush → fsync → replace order; serialization and fsync/write failures preserve durable bytes and prevent later actions; changed-secret plus future gate; expired gate plus failed scrape; healthy no-candidate/baseline recovery saves exactly once. | Call counts do not prove write ordering. Recovery ordering determines whether a run makes an unauthorized early call or persists an in-memory clearing after a failed scrape. Independent probes pass, but these explicitly required recurring tests remain absent. |
| Copied CLI entry point (`tests/test_scan.py:1796`) | Keep the repaired fetched seen/new fixture; repeat it with latch/future gate; assert exact fetch kwargs and count, complete expected counts, no attempted POST, no sleep, no state write/temp creation, unchanged bytes; add a later-candidate payload failure through the copied script with exit 1 and no effects. | Unchanged bytes cannot detect an identical rewrite. The current copied-script tests do not assert transport or write attempt counts; the imported-module payload test does not exercise the copied entry point. |
| Confirmation and payload boundaries | Representative invalid confirmation JSON shapes/IDs, other 2xx, redirects, read timeout; actual full payload shape/values, absent optional columns, post-fetch observation time, numeric limits and field/footer validation failures. | These are distinct response/serialization branches and explicit handback requirements. The new title fault injection and numeric helper tests do not establish the full payload contract. |
| Harness isolation | Record unexpected transport/socket attempts and assert none occurred outside intended fake transport; make that assertion survive scanner exception handling. | The module socket guard raises `AssertionError`, which scanner `except Exception` paths can catch. A test expecting exit 1 can therefore pass after an unintended network attempt. Blocking actual sockets is useful but does not by itself make the attempt fail acceptance. |

The original checklist also explicitly requires representative scalar/state/URL cases still absent: pandas `NA`/`NaT` and numpy booleans, contingent status, ASCII versus non-ASCII IDs, contradictory unknown/status duplicates in both input orders, equal-address display-tuple tie-breaking, wrong state root/field types and impossible/noncanonical UTC, and both URL authority acceptance/rejection tables. These are **P2 acceptance-coverage gaps**, not additional proven production bugs. Add one behavioral case per specified equivalence class, including through the public lifecycle where side effects matter. There is no need to multiply every invalid authority syntax by every unrelated response/status/recovery combination.

My positive probes narrow uncertainty about the implementation but do not replace the plan's mandatory recurring tests. The clock fixture is a useful improvement; all tests involving successful multi-message pacing must actually use it (or an equivalent clock) and prove that the intended later action occurred.

## 5. Original handback checklist recheck

Numbers follow the plan's 23 bullets. A behavior can be correct while its bullet still fails because the bullet explicitly requires missing behavioral tests. “PASS” here is local evidence only.

| # | Item | Ruling / evidence |
|---|---|---|
| 1 | Allowed implementation paths; preserved documents | **PASS.** Three allowed implementation files changed; round-2 report separately committed; preserved documents checked. This report is the only new review output. |
| 2 | Exact direct and installed dependency pins | **PASS.** Literal tests, installed metadata, and pip check pass. |
| 3 | Import-safe single module, script-relative loader, Python 3.12/unittest | **PASS.** Venv 3.12.10 and no-argument loader verified; copied entry tests run. Launcher limitation recorded. |
| 4 | Exact fetch and whole-result rejection | **PASS for code/literal contract.** Fetch ordering and shape validation remain intact. Complete side-effect assertions still belong to R10. |
| 5 | Scalar eligibility and prescribed cases | **FAIL — coverage.** Required missing-scalar/contingent cases remain absent. No new ordinary eligibility defect established. |
| 6 | IDs and pre-filter duplicate resolution | **FAIL — correctness.** R5 DataFrame traversal aborts on oversized integer; representative duplicate tests also remain incomplete. |
| 7 | Exact initial state and strict validation | **PASS for code/state; coverage incomplete.** Initial bytes exact; strict loader unchanged. Remaining type/timestamp cases are listed in R10. |
| 8 | Atomic persistence and fatal failures | **FAIL — coverage.** Source order and independent failure probes pass; required recurring ordering/serialization/fsync tests remain missing. |
| 9 | Baseline and deduplication lifecycle | **PASS for existing behavior.** Silent baseline and ordinary suppression/relisting behavior remain intact. Partial-delivery proof is assessed separately below. |
| 10 | Whole-batch validation and exact payload | **PASS for corrected code; coverage incomplete.** Independent individual budgets now exist; required full-shape/observation cases remain under R10. |
| 11 | URL/formatting/UTF-16/budget behavioral tests | **FAIL — coverage.** R6/R7 are functionally fixed, but the required actual-payload boundary cases and representative URL tables remain incomplete. |
| 12 | Exact POST, strict confirmation, durable A/B partial success | **FAIL — coverage.** Ordinary implementation remains consistent with the contract, but the A/B test never reaches B and next-scan retry proof is absent. |
| 13 | Durable rate limits, both clocks, pacing and budget boundaries | **FAIL — correctness and coverage.** Clock/overflow fixes verified; post-sleep reserve violation remains, with important missing regressions. |
| 14 | Failure/latch/recovery/gate ordering | **PASS for independently probed behavior; coverage incomplete.** Required recurring recovery combinations remain under R10. |
| 15 | Entry flags, budget, error outcomes and complete safe summaries | **FAIL — correctness.** R2 reserve and R9 reached-stage accounting defects remain. |
| 16 | Literal workflow contract | **PASS.** Unchanged workflow literal test passes, including pinned actions and prescribed persistence logic. |
| 17 | Full offline suite and realistic CLI cases | **FAIL — behavioral acceptance.** 114/114 and 2/2 pass; shipped baseline unchanged. False-positive partial-success test and required CLI assertions remain unresolved. |
| 18 | Accurate README and commissioning/recovery guidance | **PASS.** R11 residuals addressed. No operational success claimed. |
| 19 | No excluded application/storage/search infrastructure | **PASS.** None added. |
| 20 | No excluded alerts/history/retry framework/bot features | **PASS.** None added. |
| 21 | No excluded hosting/polling/fallback or unsupported promises | **PASS.** Source completeness limitation now stated accurately. |
| 22 | Local hash/branch/check/dependency evidence; no deployment claim | **PASS.** Recorded above, including launcher limitation. |
| 23 | Actual runner/source/baseline/delivery/scheduled-state evidence | **OUTSTANDING — LIVE ONLY.** No live activity performed or claimed. |

## 6. Overall verdict

**NEEDS CHANGES. Not ready for live commissioning.** Exactly these findings remain open, ordered by correctness versus coverage:

1. **R2 — P1 correctness:** enforce the 25-second POST reserve after waiting, including sleep overruns.
2. **R5 — P2 correctness:** reject/count an oversized integer through actual DataFrame traversal without aborting valid rows.
3. **R9 — P2 correctness:** preserve known candidate/confirmation/unsent accounting on every reached failure path and count all duplicate groups.
4. **R10 — P1 coverage defect plus P2 acceptance gaps:** repair the partial-success test that never reaches B, commit regressions for the remaining bugs, and complete the specific behavioral observations in §4. Exhaustive cross-products are unnecessary; those checks are load-bearing or explicitly required by the original handback contract.

**R6, R7, R11 are closed functionally in this pass; R1, R3, R4, R8 remain closed functionally.** After the remaining local fixes and acceptance checks pass, actual runner/source comparison, silent durable baseline, controlled real delivery and repeat suppression, and observed scheduled state pushes remain a separate commissioning gate.
