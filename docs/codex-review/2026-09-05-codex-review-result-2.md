# Boca House Hunter fix-pass review

Date: 2026-09-05. **Overall verdict: NEEDS CHANGES.** Four findings are fixed correctly; seven remain partially fixed. This is an offline local review, not live commissioning evidence.

Reviewed commit `179fb80ba07d6f1d816b1af4e42668d73078fb67` on local `master`, using the actual `abdb4d4..179fb80` diff and current source/tests against the canonical spec, prior R1–R11 findings, and implementation-plan handback checklist. HEAD is the reviewed commit. The fix changes only `README.md`, `scan.py`, and `tests/test_scan.py` (830 insertions, 270 deletions). Commit `abdb4d4` separately adds the previous review. The working tree was clean at entry.

Only this new report was written. No implementation file, initial state, spec, plan, amendment, or previous review was edited. No installation, live scrape, Discord request, remote Git operation, Actions dispatch, secret/configuration change, or commissioning action was performed. Additional probes used Python stdin, synthetic rows, mocked transport, blocked sockets, temporary state, and controlled clocks. No probe source was added to the repository.

## 1. Explicit R1–R11 rulings

| Finding | Ruling | Result |
|---|---|---|
| R1 — Recurring tests require uninitialized repository state | **Fixed correctly** | The recurring test now loads a harness-owned fixture. I also ran all 103 tests in an isolated copy with initialized repository state; they passed without changing that state. The fixture-to-spec assertion still belongs in the coverage corrections under R10. |
| R2 — Rate-limit durability, clocks, and budgets | **Partially fixed** | Gates are now saved before retry sleeps; POST and sleep reserves are checked; missing exhausted-success reset preserves the confirmed identity before failure. But both-clock enforcement is still absent, and oversized reset arithmetic can lose an already-confirmed identity. See §3. |
| R3 — Dry-run payload validation | **Fixed correctly** | Both dry-run branches now build all relevant payloads before returning. Baseline validates eligible identities; initialized dry run validates new candidates. Payload failure returns 1 without delivery or saving. Required copied-entry-point failure coverage remains part of R10. |
| R4 — Malformed/contradictory duplicates | **Fixed correctly** | Usable identities are grouped even when required data is malformed, suppressing a valid sibling. Agreement now compares actual normalized HOA values. Independent malformed/status/HOA probes suppressed each group in both input orders. Deterministic address/display selection remains in place. |
| R5 — Identity/state consistency | **Partially fixed** | The 64/65-digit cases, zero-only stored components, and UTF-8 state boundary are corrected. Extremely long integer IDs still raise during conversion instead of being rejected at the row boundary. See §3. |
| R6 — Finite numbers and display fallbacks | **Partially fixed** | Finite Decimal inputs and original-value price decimal selection work. The reported oversized examples now return `Unknown`. However, valid size displays below the 64-character limit also return `Unknown` because Decimal context precision is mistaken for display overflow. See §3. |
| R7 — Address sanitation and payload budgets | **Partially fixed** | Whitespace words are preserved; C1 controls are handled; components use UTF-16 truncation with ellipsis; aggregate counting uses UTF-16. Whitespace left after control removal still defeats fallback, and actual individual project budgets are not validated. See §3. |
| R8 — Forbidden URL authority syntax | **Fixed correctly** | Both validators reject raw userinfo/port syntax, including empty forms. Host/path restrictions and accepted webhook canonical equivalence remain intact. Independent authority tables passed. Broader required URL tests remain under R10. |
| R9 — Safe complete logging | **Partially fixed** | Row warnings and a baseline event exist; explicit handled returns mostly emit summaries. Duplicate-group count is still absent, several known counts remain `None`, already-seen accounting counts unrelated historical identities, and outer exceptions emit no summary. See §3. |
| R10 — Behavioral acceptance suite | **Partially fixed** | 103 tests pass and a module-wide socket guard was added. The initialized CLI now has a candidate, but still lacks the prescribed fetched seen/new pair and required assertions. Timing tests do not advance both clocks and accept early POSTs. Substantial explicit matrix requirements remain absent. See §4. |
| R11 — Operator instructions | **Partially fixed** | Latency/miss promises were removed; verification commands, live timeout, source examples and much commissioning guidance were added. Partial-result detection is now overstated; latch-only recovery and explicit long-gate preservation remain missing; scheduled state-push verification is incomplete. See §3. |

“Fixed correctly” assesses the functional correction; it does not waive the separately requested committed regression coverage in R10. Passing the supplied suite does not override a contrary code/probe result.

## 2. Local verification actually performed

I reused `.\.venv\Scripts\python.exe`, equivalent to invoking Python after activating the existing environment. It reports **Python 3.12.10, Windows AMD64**. No dependency acquisition was needed.

| Command/check | Observed result |
|---|---|
| Python 3.12 version assertion from plan step 11 | Passed; version 3.12.10. |
| `python -m pip check` | `No broken requirements found.` |
| Exact plan `importlib.metadata` / `direct_url.json` assertions | `Exact dependency pins verified`: HomeHarvest VCS commit `8a6ac96db419b56a18d295935217649039bcdd0a`; Requests `2.32.4`. |
| `python -m unittest discover -s tests -v` | **103 tests, OK**, 0.546 seconds; no skipped/expected-failure cases. |
| `python -m unittest discover -s tests -p test_scan.py -k offline_cli_dry_run -v` | **2 tests, OK**, 0.062 seconds; both named entry-point tests ran. |
| Exact plan no-argument `scan.load_state()` initial-schema assertion | Passed. |
| Independent initial-state byte comparison with canonical §5 JSON block plus newline | Passed. |
| `git diff --check` | Passed. |
| `git status --short` before report creation | Clean. Git warns that the user-level ignore file is inaccessible; repository inspection succeeds. |
| Same full suite in a temporary copy with `initialized=true`, `seen=['1:1']` | **103 tests, OK**, 0.556 seconds; isolated state bytes unchanged. Original repository state also unchanged. |
| Preserved-document comparison | Original design, amendment, canonical spec, plan, and prior review have identical Git blobs between `abdb4d4` and `179fb80`; working contents match after line-ending normalization. |

The environment's separate `py -3.12 -c "import sys; print(sys.version)"` lookup returned **“No suitable Python runtime found.”** This launcher discovery limitation did not block verification: the existing venv's working Python 3.12.10 ran every prescribed check above. These Windows results do not establish Ubuntu runner behavior.

Additional successful probes included valid-plus-malformed duplicates and zero-versus-unknown/nonzero/status conflicts in both orders; pandas missing/boolean/non-scalar HOA exclusions; both URL authority validators and webhook equivalence; 31-second and 360-second 429 delays creating durable gates and preventing the next run from fetching; and serialization/fsync/replace failures stopping after one confirmed POST with no sleep or second POST and unchanged durable state.

## 3. Remaining implementation/documentation corrections

Locations refer to `179fb80`. Priorities retain the original meaning: P1 is a core behavior/acceptance blocker; P2 is another required conformance correction.

### R2 — P1: Saved gates still do not govern every subsequent POST

**Locations:** `scan.py:916` (`post_once`), `scan.py:1174`–`1227` (retry loop), `scan.py:1244`–`1256` (success pacing). **Contract:** spec §§6–7, especially “both the computed monotonic delay and any saved UTC gate.”

The retry loop checks the application budget, then immediately calls `post_once`. It has no monotonic rate-limit deadline and does not recheck the UTC gate after sleeping. Success pacing has the same missing gate recheck. Reading `time.monotonic()` for the overall budget is not equivalent to enforcing the server delay with a monotonic deadline.

Independent controlled-clock reproductions:

| Scenario | Actual result | Required result |
|---|---|---|
| At 12:00:00Z, 429 `retry_after=1.5`; during the requested 2-second sleep, UTC moves backward one second | Second POST at monotonic +2 seconds / **12:00:01Z**, while disk gate is **12:00:02Z**; exit 0 | Wait/recheck until the stored gate and monotonic deadline both pass, subject to reserves. |
| Same 429; UTC advances ten seconds during the state save, monotonic time does not advance | **No sleep**; both POSTs at monotonic +0 seconds; exit 0 | Still honor the 1.75-second monotonic delay. |
| A confirmed with remaining `0`, reset-after `1.5`; UTC moves backward one second during sleep before B | B POSTs at **12:00:01Z** despite A's durable **12:00:02Z** gate; exit 0 | Recheck both deadlines before B. |

The newly added `test_429_gate_is_durably_saved_before_sleep_and_retry_lands_on_or_after_gate` does not establish its claimed landing condition: UTC is permanently frozen, its sleep callback only records arguments/state, and it never checks time at the retry. Consequently it accepts a retry before the gate.

There is also an unchecked delay-conversion boundary: a valid 200 confirmation with headers `X-RateLimit-Remaining: 0`, `X-RateLimit-Reset-After: 1e20` raises `OverflowError` while constructing the datetime. The run exits 1 via `unexpected_error` with **no confirmed identity saved**. Rate metadata processing must not discard an already established confirmation. Handle unrepresentable/nonusable reset arithmetic through the controlled exhausted-delay failure path: save the identity and stop without another POST. Apply a controlled failure boundary to 429 gate construction as well; do not invent or clamp a long delay to authorize early delivery.

**Required correction:** carry explicit monotonic deadlines alongside the persisted UTC gate, recompute the required wait and reserves before every sleep/POST, and recheck after waking. Retain the now-correct save-before-wait behavior, three-attempt limit, identical payload, gate retention on later failure, and final-success no-sleep behavior. Add behavioral tests for clock movement, gate-save failure, exact reserve boundaries, and safe delay arithmetic.

### R5 — P2 residual: Reject oversized integers before unsafe string conversion

**Location:** `scan.py:164`. **Contract:** spec §§4/7, malformed identity is a counted row rejection.

`normalize_identity_component(10**5000)` still raises `ValueError` under the installed Python's integer-string digit limit. The new `len(text) > 64` check runs after `str(int(value))`, so it cannot reject this input safely. Its string counterpart is rejected normally. This is a remaining conversion-boundary defect, not a recurrence of the now-fixed 65-digit persistence corruption.

**Required correction:** reject out-of-range integer magnitudes before conversion, or safely catch the expected conversion failure and return invalid identity. Verify a malformed oversized integer row is warned/counted/skipped without aborting other rows; cover accepted integer IDs through save/reload too.

### R6 — P2: Decimal context errors are not the 64-character display limit

**Location:** `scan.py:356`–`365`. **Contract:** spec §§4/6.

The formatter catches `InvalidOperation` from default-context `quantize` and substitutes `Unknown`. This fixes the original crash, but silently misformats valid values whose correct text fits the allowed budget. Through `process_dataframe` and the real `build_payload`, a qualifying row with `sqft='1e26'` produces Size **`Unknown`**. Its required display is `100,000,000,000,000,000,000,000,000 sq ft` (41 characters). `1e40` also fits the 64-character budget and incorrectly returns `Unknown`.

**Required correction:** distinguish actual display overflow from insufficient Decimal context precision. Safely determine output magnitude/length and use adequate bounded precision for values whose display fits. Preserve `ROUND_HALF_UP`, up-to-two-decimal size rendering, and the unrounded eligibility value. Test actual payloads below/at/above the numeric limit, plus large optional values through agreeing-duplicate tie-breaking; merely catching quantization exceptions is insufficient.

### R7 — P2: Sanitized whitespace still defeats fallback; validation remains aggregate-only

**Locations:** `scan.py:305`–`310`, `scan.py:368`–`386`, `scan.py:837`–`846`. **Contract:** spec §§4/6.

With `formatted_address='\x07 \x07'` and `full_street_line='5 Elm St'`, `build_address_display` returns a single space. Whitespace is stripped before controls are removed; control removal leaves whitespace, and the truthy string wins over the street fallback. The same issue affects the street fallback decision and can leave whitespace-only optional components.

**Required correction:** perform final whitespace cleanup after control removal and decide address/ZIP fallback from the resulting sanitized nonempty text. Preserve the repaired newline/tab word separation, single escaping pass, and UTF-16 truncation.

The builder now counts aggregate UTF-16 units correctly, but still never explicitly checks actual title, field-value, or footer project limits. It relies exclusively on the construction helpers. A fault-injection probe making `truncate_utf16` return its input caused `build_payload` to accept a 311-unit title because the total remained below 6,000. This is evidence of the missing independent validation required by the spec, not a claim that ordinary unchanged truncation generates that title.

**Required correction:** validate final individual project limits and the documented aggregate limit before returning the payload. Add actual full-payload tests for both ordinary boundaries and validation failures; existing helper-only checks do not close this requirement.

### R9 — P2: Final accounting remains incomplete and sometimes misleading

**Locations:** `scan.py:394`–`410`, `scan.py:996`–`1031`, `scan.py:1080`–`1158`, `scan.py:1185`–`1195`, `scan.py:1271`–`1276`. **Contract:** spec §7.

Remaining concrete failures:

- Neither `ScanCounts` nor the summary contains **duplicate groups** separately from conflicting groups. Identical duplicates disappear from this required accounting.
- `already_seen = len(seen_set)` counts all historical identities, including those absent from this fetch. The initialized CLI fixture contains historical `555555:111111` and fetches only new `123456:987654`, yet asserts `already_seen=1`. The fetched eligible overlap is zero. Report the eligible/seen intersection; expose total history separately if useful.
- A healthy baseline or dry run still reports known delivery counts as `confirmed=None unsent=None`; baseline also leaves `already_seen=None`. A payload failure can leave counts unpopulated even after candidates were computed. Use meaningful counts for reached stages, retaining an explicit not-observed marker only where applicable.
- On a confirmation-state write failure, `finish(1)` runs before delivery counters are updated. The supplied replacement-failure test logs `candidate=2 confirmed=None unsent=None` despite an observed confirmation and known candidate set. Ensure the summary reflects confirmed remote delivery and remaining work, with persistence failure identified separately, including when earlier candidates were durably saved.
- A synthetic `RuntimeError` from `process_dataframe` produces **no `scan_summary`**. `finish` is local to `_main_impl`; `main` cannot finalize its accounting when catching the exception. The added `phase="_main_impl"` identifies the wrapper, not the actual failed processing/delivery/save phase.
- For an empty/capped DataFrame, the row count is available but the summary leaves `total_fetched=None` because it is populated only after successful normalization.

**Required correction:** retain accounting and current phase across the outer exception boundary, emit exactly one complete summary on every normally handled exit, populate observable counts as stages finish, and distinguish matching seen identities, duplicate groups, and conflicting groups. Add full-summary assertions for early failures, baseline, dry run, ordinary partial failure, state-write failure, and an outer exception. Keep safe row-index warnings and the dedicated baseline event.

### R11 — P2: Remaining operator inaccuracies and omissions

**Locations:** `README.md:47`–`50`, commissioning step 5 (`README.md:160`–`169`), disabled-webhook recovery (`README.md:177` onward). **Contract:** spec §§1–2/5–7/9 and plan step 10.

- The revised text says a **“partial/malformed upstream response is treated as indeterminate”**. A nonempty, shape-valid partial result is not detected as partial and is processed as healthy. State this silent-partial-result limitation explicitly; reserve the indeterminate claim for the actual empty/invalid-shape/cap checks.
- Recovery still only describes replacing the secret. Add the prescribed alternative: fix the channel/webhook, disable scheduling and wait for active runs, then clear **only `disabled_webhook_sha256`**, preserving `seen` and any future `discord_not_before`.
- Explain explicitly that long gates can extend beyond the next five-minute poll, persist across runs and secret replacement, and must not be removed to force a retry. The current paragraph mentions a gate but does not supply that operating rule.
- Distinguish clearing a changed-secret latch **in memory** from saving that clearing after a healthy scan/delivery-state transition. A scrape failure occurs after the latch check, not before it as the current example implies; it prevents persistence. A future gate permits no fetch/write and must remain intact.
- Commissioning step 5 should require checking actual **state pushes** in observed scheduled runs, alongside gaps/durations/source health. Checking that nobody else pushes and observing earlier manual state commits does not establish scheduled persistence. Move verification of no competing sender/state writer into setup before first commissioning delivery.

The newly supplied pin assertion, Git checks, pre-commissioning-only state warning, Ubuntu/WSL timeout, explicit-zero/unknown/positive examples, scheduled-baseline race explanation, schedule-disabled repair precautions, and public standard-runner/no-paid-storage checks are useful and should remain.

## 4. R10 — P1: Required committed acceptance coverage still incomplete

The acceptance gate is behavioral, not a minimum test count. The following is the concrete remaining work in `tests/test_scan.py`, in addition to regressions for §3:

1. **Copied CLI cases (`1482` onward):** fetch both one seen eligible pair and one new eligible pair in the initialized case, then repeat that same case with latch/future gate. Assert exact scrape kwargs/call count, complete safe counts, no attempted POST, no sleep, no state write or temporary-state creation, and unchanged bytes. Replace UTC and monotonic clocks as well as sleep. The current socket guard and unchanged bytes do not detect attempted HTTP that fails or an identical state rewrite. Add the requested later-candidate payload failure through the copied entry point, with exit 1 and no effects; the new `972` test mocks `build_payload` on the imported module instead.
2. **Timing (`1079`–`1260`):** use a clock model that advances UTC and monotonic time independently and inspect both clocks plus disk at every POST. Cover backward/forward wall-clock changes, 0.5-second success spacing, exhausted-success delays, exact 25-second POST and sleep-plus-25 reserve boundaries, and failed gate saves. Generate a delay over 30 seconds and one over five minutes from real fake responses, then test the subsequent run. The current long-gate test writes a future fixture directly; the new retry landing test only observes the sleep request.
3. **Eligibility/identity/duplicates:** add pandas `NA`/`NaT`, contingent status, numpy booleans, ASCII-versus-non-ASCII IDs, all required boundary variants, contradictory unknown/status/malformed duplicates in both orders, and a tie where addresses agree but the display tuple differs. Include finite Decimal rows through payload/lifecycle, not only helper assertions.
4. **State/atomic writes:** tie the harness initial fixture to preserved §5 JSON, exercise the remaining wrong root/field types and noncanonical/impossible UTC forms, and verify flush/fsync/replace **order**. The existing atomic test checks only fsync and replace call counts. Add serialization and fsync/write failure scenarios with durable bytes and no later delivery assertions; currently only replacement failure is tested.
5. **Payload/URL:** validate the exact full JSON key/value shape, optional columns all absent, post-fetch observation time, full-payload numeric/text/UTF-16 boundaries, and explicit project/aggregate failures. Complete both URL acceptance/rejection tables and canonical-equivalence behavior. The new tests mainly call individual formatting/sanitizing helpers.
6. **Confirmation/recovery:** cover malformed/nonobject JSON, invalid ID scalar/string forms, other 2xx and redirects, read timeout, and safe HTTP/JSON error logging. Verify durable A at sleep and B's POST, then a second scan retries only B. Add changed-secret plus future gate, expired gate plus failed scrape, and healthy no-candidate/baseline recovery with exact state/write effects.
7. **Accounting and isolation:** assert complete summary values and real phase for the failure/success combinations in R9. Make unexpected network attempts fail acceptance even when the scanner catches their exceptions. Remove the false module-docstring claim that clocks are “always mocked/frozen,” or make the harness actually enforce it. The source currently has one budget test with a monotonic sequence and several frozen-UTC tests, not an advancing two-clock harness.

My temporary probes supplement this review; they do not replace the specified recurring regression suite. Re-run the full suite and both prescribed CLI cases after these corrections.

## 5. Full handback checklist recheck

Numbers follow the plan's 23 bullets in order. PASS is local to the stated behavior; coverage deficiencies expressly required by a bullet can still make that bullet FAIL. Live acceptance remains separate.

| # | Checklist item | Verdict | Evidence / remaining limitation |
|---|---|---|---|
| 1 | Allowed implementation paths; preserved documents unchanged | **PASS** | Fix touches three of seven allowed files. Prior review is a separate documentation commit. Preserved blobs/content checked; this review adds only its requested report. |
| 2 | Exact direct and installed dependency pins | **PASS** | Requirements literal test and installed metadata assertions pass; `pip check` clean. |
| 3 | Import-safe single module, script-relative no-argument loader, Python 3.12/unittest | **PASS** | Entry guard, copied-script tests, successful no-argument load and existing 3.12.10 environment. Launcher limitation noted above. |
| 4 | Exact 13-argument fetch; whole-result rejection | **PASS** | Call and validation ordering unchanged; literal invocation and rejection tests pass. Broader boundary side-effect assertions remain in R10. |
| 5 | Scalar eligibility/boundaries/HOA with required behavioral cases | **FAIL** | Decimal support fixed and independent missing-value probes pass, but required committed NA/contingent/scalar matrix still incomplete. R10. |
| 6 | ID constraints and duplicate resolution before filtering | **FAIL** | R4 fixed; extreme integer conversion still aborts instead of row rejection. R5; additional required tests in R10. |
| 7 | Exact initial state and strict state validation | **PASS (behavior corrected)** | Initial bytes exact; zero-only stored IDs and UTF-8 handling repaired; strict type/schema/order/digest/time validation remains. Broader committed validation matrix is still required under R10. |
| 8 | Atomic serialization/write order and fatal write failures | **PASS** | Source order correct; independent serialization/fsync/replace failures each stop later sends and preserve durable bytes. Recurring order/failure coverage remains in R10. |
| 9 | Baseline, suppression, new pairs/relisting, reconsideration | **PASS** | Lifecycle suite passes; baseline remains silent, including zero eligible rows; recurring-state blocker R1 independently closed. |
| 10 | Whole-batch validation and exact payload/observation contract | **FAIL** | Dry-run validation now present, but actual individual project-budget validation absent. R7; observation/full-shape coverage in R10. |
| 11 | URL/optional/numeric/escaping/UTF-16/budget behavioral tests | **FAIL** | Authority checks fixed; residual formatting/fallback and required coverage failures. R6–R7/R10. |
| 12 | Exact POST, strict confirmation, durable ordinary partial success | **PASS for ordinary response paths** | Exact POST/confirmation and A-success/B-failure behavior intact; atomic-failure probes stop subsequent actions. Exhausted-reset arithmetic failure is assessed in item 13/R2. |
| 13 | Durable/bounded rate handling, both clocks, pacing/budget tests | **FAIL** | Save-before-retry and ordinary reserves repaired; reproduced early sends, unchecked reset arithmetic, and missing behavioral cases. R2/R10. |
| 14 | Failure stop/latch/recovery/gate ordering | **PASS (ordinary paths)** | Matching latch precedes gate/fetch; changed secret preserves future gate; clearing remains in memory until permitted saves. Required recovery-combination tests still under R10. |
| 15 | Entry flags/budget/error outcomes/safe complete summaries | **FAIL** | R2 and R9 remain; strict ordinary config boundaries retained. |
| 16 | Literal workflow contract | **PASS** | Unchanged workflow matches canonical YAML; pinned actions, main/public guard, concurrency, tests, timeout and checked persistence loop intact. |
| 17 | Full suite and both realistic offline CLI cases; unchanged baseline | **FAIL** | 103/103 and 2/2 pass; initialized isolated suite passes and baseline unchanged. Required CLI fixture/assertions and behavioral acceptance matrix still incomplete. R10. |
| 18 | Accurate README and complete commissioning/recovery guidance | **FAIL** | Many additions correct, but partial-source claim and recovery/commissioning omissions remain. R11. |
| 19 | No excluded UI/storage/search/integration/scraper infrastructure | **PASS** | No such additions. |
| 20 | No excluded alerts/history/retry framework/bot/notifications | **PASS** | Changes remain within prescribed scanner behavior; no excluded product additions. |
| 21 | No extra hosting/polling/fallback or unsupported instant/coverage promise | **PASS** | Instant/no-miss promises removed; no excluded hosting or unknown-to-zero fallback. Incorrect partial-result detection statement is separately failed in item 18. |
| 22 | Local hash/branch/counts/commands/dependency evidence; no deployment claim | **PASS** | Recorded in this report, including launcher limitation and working venv. |
| 23 | Real runner/source/baseline/delivery/scheduled-run evidence | **BLOCKED / NOT PERFORMED** | No live activity authorized/performed in this review. Remains a separate operational acceptance gate. |

## 6. Overall verdict

**NEEDS CHANGES.** Exactly these findings remain open: **R2, R5, R6, R7, R9, R10, R11**. Correct both-clock rate handling and confirmation preservation on reset-conversion failure; safe oversized-ID rejection; valid within-budget numeric rendering; sanitized fallback and individual payload validation; complete accurate final accounting; the specified behavioral suite; and the remaining README recovery/source/commissioning instructions. **R1, R3, R4, R8 are closed functionally.**

This implementation is **not yet ready for live commissioning**. After the local corrections and acceptance suite pass, the separate plan sequence still requires actual runner/source comparison and explicit-zero coverage assessment, silent durable baseline, controlled one-identity real delivery plus repeat suppression, and observed scheduled runs/state pushes on public standard runners. None of that live evidence is supplied by this review.
