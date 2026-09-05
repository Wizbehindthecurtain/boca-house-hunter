# Boca House Hunter — ordered implementation plan

Date: 2026-09-05. Audience: the separate coding agent implementing this repository. This is the final implementation procedure; it does not assert that implementation or live commissioning has passed.

## Authority, scope, and stop rule

[The canonical spec](2026-09-05-codex-spec.md), **sections 1–11**, is the sole source of truth for behavior. Read it in full before editing. [The v2 amendment record](../superpowers/specs/2026-09-05-boca-house-hunter-design-v2.md) resolves only the visibility decision: `Wizbehindthecurtain/boca-house-hunter` is already public. It also records the user's decision to implement the canonical spec at full rigor. Do not ask to choose visibility or reduce rigor again. The original v1 design is historical and must not supply implementation behavior.

This plan orders work and makes acceptance checks explicit. It does not amend the spec. Preserve every specified function signature and invocation, library argument and default, JSON/YAML shape, validation rule, error-table outcome, and section 10 exclusion. In particular, `scan.load_state()` must work with no arguments, validate script-adjacent state, and neither scrape, send, nor mutate anything. The spec does not enumerate signatures for every internal helper; this plan does not invent additional public interfaces. Keep internal decomposition inside `scan.py` and do not turn implementation details into new behavior or configuration.

If an unspecified case requires a behavioral decision, or any requirement cannot be implemented or verified as written, **stop and flag the exact spec section, the unresolved case, and its effect on acceptance**. Do not supply a guessed policy, relaxed validation, alternate dependency, fallback service, placeholder, or silent omission. If this plan appears to conflict with the spec, flag the conflict instead of choosing a behavior. Environmental failure is a blocked check, never a pass.

Complete the numbered steps in order. A step's done condition is a gate for the next step. Steps 2–7 are checked by source review when written and must subsequently pass the behavioral suite in steps 9 and 11; source review alone is not final acceptance.

The only implementation files to create or modify are:

```text
.github/workflows/scan.yml
.gitignore
README.md
requirements.txt
scan.py
seen.json
tests/test_scan.py
```

Preserve the original design, v2 amendment, canonical spec, and this plan byte-for-byte. No additional tracked source, configuration, fixture, report, dependency, or infrastructure files. Synthetic fixtures belong inside `tests/test_scan.py`; temporary test state belongs in a harness-owned temporary directory. An ignored `.venv/` and normal ignored interpreter caches are local execution artifacts, not deliverables. This document's creation is documentation-only; the author of this plan must not perform the implementation steps.

## 1. Inspect the checkout and scaffold the exact files

**Files touched:** `requirements.txt`, `.gitignore`, `seen.json`. Read all four preserved documents and existing repository instructions; inspect Git status and current branch without modifying unrelated work.

1. Record the starting branch, commit, and existing changes for the handback. Do not overwrite or stage another contributor's changes. Local inspection when this plan was written found `master`; the deployment target remains `main`. Do not assume the remote default branch has been configured. Remote settings and any required branch transition belong to the commissioning handoff below.
2. Copy the two direct requirement lines from spec §3 exactly, including HomeHarvest commit `8a6ac96db419b56a18d295935217649039bcdd0a` and `requests==2.32.4`. Do not add test dependencies, lockfile tooling, or alternate versions.
3. Write the five `.gitignore` entries specified in §3, one per line. Keep `seen.json` tracked.
4. Copy the initial JSON in §5 exactly: `version: 1`, `initialized: false`, empty `seen`, and null latch/gate, in the specified key order. Use two-space indentation and a trailing newline. Never populate repository state through local testing.

**Done when:** the three files match §§3 and 5, no additional scaffold exists, the source documents are unchanged, and existing work is accounted for. Repository publication is already resolved; actual deployment remains unverified.

## 2. Implement scalar normalization, duplicate grouping, and eligibility

**Files touched:** `scan.py` only.

1. Make the module safe to import without application I/O. Keep all application helpers in this one module (§3).
2. Implement §4 scalar checks before conversion, using `pandas.isna()` for missing scalars and rejecting non-scalars. Never evaluate pandas missing values for truthiness or replace them with zero.
3. Implement both identity components exactly: trimmed 1–64 ASCII digit strings or nonnegative integer scalars, excluding booleans, floating-point values, zero-only IDs, and invalid/missing formats. Preserve string leading zeros. Use the top-level `listing_id`; never manufacture a substitute.
4. Normalize every required eligibility value and URL, group by usable identity, and resolve duplicate conflicts **before eligibility filtering**. A qualifying row must not hide a contradictory row. Skip conflicting identities with `conflicting_duplicate`. For agreeing duplicates use the specified smallest sanitized address, then sanitized `(beds, full_baths, half_baths, list_date)` tuple. Do not let input row order choose an alert.
5. Apply exact local status/style/city/state requirements and inclusive price/sqft thresholds. Use finite nonboolean numeric values/plain Decimal-compatible strings. Compare before display rounding. Enforce explicit numeric zero HOA only; count unknown HOA separately.
6. Enforce §4 Realtor URL length, scheme, exact hostname, credentials/port prohibition, and nonempty path; remove query and fragment for display. Implement the optional address fallbacks, numeric display eligibility, and exact `pandas.to_datetime(value, errors="coerce", utc=True)` call after rejecting numeric/non-scalar dates. Optional omissions must not disqualify a valid listing.
7. Count skips by reason; malformed IDs expose only their row index. Distinguish ordinary out-of-range rows from malformed values requiring warnings.

**Done when:** every §4 eligibility and duplicate rule has a source implementation, source dates cannot affect identity/eligibility, and no invalid row can produce an alert or seen entry. Steps 9 and 11 must prove boundaries, pandas missing values, contradictory duplicates, and deterministic tie-breaking.

## 3. Implement the one fetch and whole-result validation

**Files touched:** `scan.py` only.

1. Copy the `scrape_property()` invocation from §4 verbatim, including all 13 keyword arguments. Preserve `property_type=["single_family"]`, `mls_only=False`, `extra_property_data=False`, `return_type="pandas"`, `limit=10000`, `offset=0`, and `parallel=False`. Omitted arguments retain pinned-library defaults.
2. Complete the fetch before considering delivery. Add no outer retry, time window, custom page loop, sorting prefix, HOA argument, or alternative search.
3. Reject non-DataFrames, empty responses, responses reaching the cap, and missing required columns before any write or POST. Use `scan_indeterminate_empty` and `scan_result_cap` for their specified cases. A valid nonempty response with zero eligible rows remains healthy.
4. Catch `Exception` around the entire library call, log `scrape_failed` with its class, and preserve state. Do not narrow this to `AuthenticationError` or expose exception messages.
5. Capture observation UTC time after the fetch returns, as required by §6.

**Done when:** the call matches §4 literally, fetch failure/shape checks match the §7 error table, and no failed or indeterminate fetch can initialize state or send. Tests must distinguish empty inventory response from a healthy response containing no eligible rows.

## 4. Implement strict state loading and atomic persistence

**Files touched:** `scan.py` only. Leave the shipped `seen.json` unchanged.

1. Provide the no-argument `scan.load_state()` interface required by §8. Resolve its default file relative to `Path(__file__).resolve().parent`, regardless of working directory.
2. Enforce every §5 JSON invariant: exactly five root keys; no duplicate object keys or NaN/Infinity constants; exact version and types; boolean `initialized`; sorted, unique, valid pair strings; empty seen when uninitialized; null or lowercase 64-character hex digest; null or valid, strictly formatted UTC gate. Do not allow Python's boolean/integer equivalence to bypass JSON type checks.
3. Treat missing, unreadable, malformed, or invalid state as `state_invalid`, exit 1 before network, and never replace or regenerate it. Do not recover from `seen.json.tmp`.
4. Implement writes exactly as §5: logical changes only; UTF-8, `indent=2`, `allow_nan=False`, schema key order, sorted seen, one trailing newline; same-directory `seen.json.tmp`; flush, `os.fsync()`, then `os.replace()`. Do not mutate state in place. A leftover temp file is only displaced by a subsequent real save, never consumed as state.
5. Keep state append-only for identities. Build the lexicographically sorted eligible-minus-seen set. First healthy real scan initializes all eligible identities once with zero messages, including the valid empty baseline case. Dry run cannot initialize.
6. Provide atomic state transitions for each confirmed identity and for latch/gate changes specified in §6. Preserve a previously confirmed A if B fails. Write failure is fatal and prevents later POSTs.

**Done when:** §§5, 7, and 8 loading/writing contracts are present, state cannot silently reset, no-op scans cannot write merely a scan timestamp, and delivery can durably save each local success independently. Behavioral proof must use temporary files, not repository state.

## 5. Implement webhook validation and exact payload construction

**Files touched:** `scan.py` only.

1. Read the webhook only from `DISCORD_WEBHOOK_URL` in real mode. Apply the exact §6 host/path/URL constraints and canonicalization to the v10 URL. Hash only the canonical URL. Never log the URL, token, or digest. Dry run must not require or use this setting.
2. Copy §6's payload shape exactly, including username, empty allowed-mentions parse list, one embed, color, six fields in their shown order and inline values, footer, and observation timestamp. No top-level content, additional fields, photos, or source descriptions. Use `New match`, the required HOA qualification, and the full identity in the footer.
3. Implement the exact price/size/bed/bath/date formatting and 64-character numeric-string fallback. Price display rounding uses decimal `ROUND_HALF_UP`; eligibility remains unrounded.
4. Sanitize source text exactly as §6 requires. Apply component/title/field/footer budgets after the specified sanitization/escaping stages. Count UTF-16 code units and never split a surrogate pair; use `limit-3` plus `...` when truncating. Validate actual payloads against project budgets and Discord's documented aggregate limit.
5. Build and validate **every candidate** payload before the first POST. A bad later payload must prevent even the first candidate from being sent.

**Done when:** the §6 payload and URL contracts are implemented without added fields or relaxed limits, optional missing data remains displayable, and payload failure has the whole-batch pre-send outcome in §7.

## 6. Implement sequential Discord delivery, persisted gates, and latching

**Files touched:** `scan.py` only.

1. Use one `requests.Session`, no retry adapters or authorization header, and the exact §6 `session.post()` arguments: canonical URL, `params={"wait": "true"}`, `json=payload`, `timeout=(5, 15)`, `allow_redirects=False`.
2. Accept only HTTP 200 with a JSON object containing a nonempty digit-string message `id`. Treat 204, every other 2xx, redirects, malformed JSON, and missing/invalid IDs as failure. Add an identity only after confirmation; save before another POST or sleep.
3. After success, enforce the 0.5-second minimum before another POST. When remaining parses as zero, use the larger of this minimum and a valid reset-after delay plus 0.25. Persist a valid exhausted-bucket gate, rounded upward to the next whole UTC second, **in the same save as the confirmed identity**, including after the last candidate. Invalid exhausted-bucket reset delay must preserve the confirmed identity and then fail the batch.
4. On 429, take the maximum valid nonnegative finite header/body delay, add 0.25, and save the upward-rounded UTC gate before retrying without marking the candidate seen. Reuse the identical payload. Permit at most three POST attempts per candidate total. Invalid delays fail; do not guess or clamp.
5. Before every subsequent POST honor both the monotonic delay and saved UTC gate. A sleep over 30 seconds, or one that would violate the remaining-budget requirement, fails with unsent identities unseen and the persisted gate intact. Do not sleep merely to drain a bucket once all candidates succeeded.
6. On 401/403/404 save the current canonical URL's digest with confirmed state, log `webhook_permanent_failure` and status, and stop. Other failures stop without automatic retry, except the prescribed 429 handling. Catch Requests/JSON errors without leaking their text or response bodies.
7. Implement next-run checks in §6's order: matching disabled digest exits 1 without external calls; changed digest clears the latch only in memory until a permitted save; future UTC gate exits 0 without scrape/send/sleep/write, even with a changed secret; expired gate clears in memory until a permitted save. Dry runs ignore both gates. Failed scraping must not persist in-memory recovery changes.

**Done when:** every §6 response path maps to the specified seen/latch/gate changes and §7 exit outcome. No identity is marked seen before confirmation, no requested delay can be shortened, and no unconfirmed message gets a generic retry.

## 7. Wire the entry point, execution budget, and safe logging

**Files touched:** `scan.py` only.

1. Wire `python scan.py` under an import-safe entry guard. Accept only `DRY_RUN` absent/`0`/`1`, defaulting to `0`. Add no CLI flags, prompts, criteria environment variables, or configuration file.
2. Start the monotonic 150-second application budget on entry. Load/validate config and state before scrape; in real mode validate the webhook even for baseline and apply latch/gate checks. Fetch, normalize/group/filter, calculate differences, and validate candidate payloads before delivery. Route healthy baseline, no-candidate, dry-run, and delivery outcomes exactly as §§5–7 specify.
3. Require at least 25 seconds remaining before each POST and required sleep plus 25 seconds before sleeping. Unsent work when the budget expires fails. Do not implement process supervision or patch upstream socket behavior; §8 supplies the external timeout.
4. Dry run performs the real fetch/filter/diff/payload-validation path, logs would-send or would-baseline counts, and never touches webhook transport or writes state. A healthy real no-candidate run saves cleared latch/expired gate once if needed, otherwise does not write.
5. Implement stdout logging with UTC timestamp, level, fixed event name, safe phase/class details, and every §7 final-summary counter whenever normal exception handling can run. Log identities/status only as allowed. No raw data, payload, headers, response bodies, webhook fingerprint, HTTP exception string, or unsafe traceback.
6. Review each of §7's ten error-table rows individually against the code, including outer unexpected `Exception`, serialization/write errors, and uncatchable process loss. Never catch `BaseException` as a blanket recovery mechanism. Exit 0 only for the five success categories enumerated in §7.

**Done when:** entry behavior and error outcomes match §7, import has no scanner side effects, and no exception handler can silently continue delivery, regenerate state, or report success after a required failure.

## 8. Copy the exact GitHub Actions workflow

**Files touched:** `.github/workflows/scan.yml` only.

Copy the complete YAML fenced block in spec §8 verbatim. Do not reconstruct an equivalent workflow. Preserve line content, expressions, action SHA pins, step order, Bash script, and commands. A terminal newline is required; platform line-ending normalization is acceptable.

Review the copied workflow for its existing required effects: only offset five-minute schedule and manual dispatch; manual dry-run default; public `main` guard; workflow concurrency before checkout; latest `main` and full history; `contents: write`; Python 3.12 on standard Ubuntu; pinned setup/checkout actions; pip cache and bounded installation; offline tests; 180-second GNU timeout; persistence after success/failure/cancelled real scan; `scan.load_state()` before staging; no-change exit; only `seen.json` staged; specified bot identity; checked three-attempt push/rebase sequence; visible conflict/failure. Do not remove the external timeout because the application has a budget.

**Done when:** newline-normalized text equals §8's YAML block, with no added triggers, `continue-on-error`, forced pushes, PATs, state-conflict resolution, keepalive commits, or extra workflow files. This is static verification, not a claim that branch rules permit a live push (§§8–9).

## 9. Write the complete offline behavioral suite

**Files touched:** `tests/test_scan.py` only. Fix defects in `scan.py` if a test demonstrates a spec mismatch; after such a fix rerun the affected checks and the final full suite. Do not change the spec or expected behavior to accommodate the implementation.

Use standard-library `unittest` and `unittest.mock`, synthetic pandas DataFrames, temporary state, fake HTTP, frozen UTC/monotonic clocks, and replaced sleep. Real socket/HTTP attempts must fail the harness. Assertions must cover returned outcomes, bytes/state transitions, attempted deliveries, timing, and safe logs, rather than merely asserting calls between internal helpers. Fixed literal external-call assertions supplement behavioral tests because those invocations are contractual.

The following groups are mandatory; together they implement §9's eight behavioral bullets and the detailed contracts those bullets reference:

| Group | Required observations |
|---|---|
| Fetch and required fields (§§4, 7, 9) | Assert all exact scrape keywords and one fetch on the healthy path. Reject non-DataFrame, empty, cap, and each missing required column without writes/POSTs. Accept nonempty zero-eligible data, including an empty first baseline. Inject library exceptions beyond authentication errors; verify failure and unchanged state. Optional columns may all be absent. |
| Scalar eligibility (§§4, 9) | Inclusive 250000/650000 price and 1700 sqft; just-outside values cannot round into range. Wrong city/state/style; pending/contingent; normalization of allowed whitespace/case. HOA numeric `0`, `0.0`, and string `"0"` qualify; null, NA, NaN, blank, nonnumeric, false, infinities, negative and positive fees do not. Non-scalars and booleans cannot bypass required numeric checks. Unknown HOA has a distinct count. |
| Identity and duplicate handling (§§4–5, 9) | Leading zeros preserved; valid integer scalars accepted; missing/NA, floats, booleans, zero-only, non-ASCII digits, overlength and malformed IDs rejected without `nan` identities or raw-ID logs. Identical duplicates yield one result. HOA/status/other required-field or URL disagreements suppress the entire identity before eligibility. Reverse row order to prove deterministic address/display tie-breaking. |
| Lifecycle (§§5, 9) | Baseline saves once with zero POSTs; repeated scan sends/writes nothing; new pair sends once; same property/new listing ID may send; same pair disappearing/reappearing remains suppressed. Unknown HOA becoming zero can first alert. Already-seen price/status movement never alerts. Candidate order is lexicographic. No pruning or timestamp-only saves. |
| State integrity (§§5, 7, 9) | Missing/unreadable/corrupt state fails before fetch without replacement. Exercise duplicate JSON keys, forbidden constants, extra/missing keys, wrong types including booleans masquerading as numbers, unsupported versions, unsorted/duplicate/malformed seen values, nonempty uninitialized seen, malformed digest and invalid/noncanonical UTC timestamps. Script-relative lookup works from another working directory. A leftover temp file is never loaded as recovery. |
| Atomic writes (§§5–7, 9) | Exact encoding/indent/key order/sorted seen/trailing newline; flush and fsync before replace; no writes on unchanged logical state. Inject serialization, write/fsync, and replace failure. A prior confirmed send can duplicate later, but no subsequent POST occurs after save failure; the target file is not partially rewritten. |
| Payloads (§§4, 6, 9) | Exact key shape, field order/inline flags, one embed, no mentions, correct identity footer and post-fetch observation timestamp. Price rounding, trimmed size decimals, integer-only beds/baths, 64-character numeric fallback, address precedence/fallback, missing optionals, UTC source date and numeric/invalid date rejection. Malformed/disallowed URLs rejected. Query/fragment removed from valid Realtor display URLs. Long text, Markdown, controls, `@`, and astral Unicode obey UTF-16 budgets without splitting pairs. Check aggregate budget. A construction failure on a later candidate prevents every POST. |
| Confirmation and partial failure (§§5–7, 9) | Confirmed A then failed B saves only A and leaves B/later entries unseen; the next healthy scan retries B. Verify a snapshot of durable A before any sleep/next POST. Reject 200 without valid string ID, malformed JSON, nonobject JSON, 204, other 2xx, redirects, 400, 5xx, and Requests connection/read timeout without inappropriate marking/retries. Assert exact POST arguments and a single Session. |
| Rate limits and budget (§§6–7, 9) | Fractional header/body 429 delays use the maximum valid value plus 0.25, identical retry payload, upward UTC rounding, save-before-retry, and three total attempts maximum. Invalid/negative/nonfinite delays do not authorize an early POST. Success enforces 0.5 minimum and exhausted-bucket reset delay plus 0.25; identity and gate share one save. Invalid exhausted reset preserves confirmed identity then fails. Long delays over 30 seconds or insufficient remaining budget stop with unseen work; a delay beyond five minutes survives the next run. Honor both clocks before subsequent POSTs; test 25-second POST and sleep-plus-25 budget boundaries. Last successful candidate preserves future gate without sleeping. |
| Latch/gate recovery (§§6–7, 9) | Each of 401/403/404 latches and stops. Repeating the same canonical secret makes no external calls. Equivalent accepted URL forms yield the same latch behavior. A changed secret permits recovery without clearing seen, but a future gate still prevents fetch/send/sleep/write. Expired gate/changed latch are not saved on failed scrape; healthy no-candidate scan saves their clearing once. Dry run ignores both gates and does not mutate them. |
| Entry, exceptions, and logs (§§3, 7, 9) | Import never loads state, fetches, sends, or writes. Invalid DRY_RUN and missing/invalid real webhook fail before network, including baseline. Cover expected row errors, outer unexpected exceptions, and all locally testable §7 error rows. Final counters and exit results match outcomes. Captured failures containing a synthetic token in exception text do not reveal that token, URL, digest, headers, bodies, payloads, or source data. |

Add a literal contract check in this same test file that reads preserved spec code blocks and compares `requirements.txt` and newline-normalized workflow text to their source blocks. Verify `.gitignore`'s required entries. Do not add a YAML parser dependency. Test the initial-state fixture against §5, but **do not require the repository's live `seen.json` to stay uninitialized in recurring workflow tests**; state is expected to evolve after deployment.

Also include exactly named test methods `test_offline_cli_dry_run_baseline` and `test_offline_cli_dry_run_initialized`. These supply the separately runnable local dry-run verification:

1. Each method copies `scan.py` into a `TemporaryDirectory`, writes synthetic valid `seen.json` beside that copy, and executes the copied file with `runpy.run_path(..., run_name="__main__")`. This exercises the actual script entry guard and script-relative path resolution; do not replace the entry point, filtering, diff, payload, or state loader with mocks.
2. Set `DRY_RUN=1`, remove the webhook environment setting, patch the imported HomeHarvest call to return synthetic data, replace time/sleep, and make HTTP/socket use fail. Run from a different temporary working directory and restore environment/cwd afterward. The copied script must remain byte-identical to the implementation under test.
3. Baseline case: use the uninitialized schema and a nonempty fixture with exactly one eligible identity plus an unknown-HOA row. Require successful completion and would-baseline count 1, with no POST, sleep, state write, or temp-state creation.
4. Initialized case: use one already-seen eligible pair and one new eligible pair; require would-send count 1 and no state change. Repeat this case with a valid stored disabled digest and future gate to prove dry run ignores them without a webhook.
5. Compare state bytes before/after, assert the exact scrape invocation, and assert safe summary counts. Catch the script's `SystemExit` if used and require code 0; normal completion is also exit 0. No full payloads or listing data may be logged.

**Done when:** all groups and both entry-point dry-run methods exist with behavioral assertions and fully isolated test state. No test depends on live listings, credentials, real sleeps, current wall time, or a production state reset. The required run-and-pass gate follows in step 11.

## 10. Write the operating and commissioning instructions

**Files touched:** `README.md` only.

Document the fixed search, reported-zero-HOA meaning, city/address-label boundary, silent baseline, pair deduplication/relist behavior, unknown-HOA reconsideration, duplicate-preferring delivery, and absence of price/status alerts (§§1–6). Explain potential silent partial source results, upstream delays, dropped/delayed schedules, the 60-day inactivity risk, and why zero hosting charge requires public standard runners. Do not claim live success or an SLA.

Include the exact local commands in step 11 and the complete live commissioning sequence below. State that plain `DRY_RUN=1 python scan.py` still queries HomeHarvest and is not offline. Explain the 150-second application budget and Ubuntu/WSL external timeout; plain Windows does not provide that GNU timeout bound (§7).

Document `DISCORD_WEBHOOK_URL`, `DRY_RUN`, main/default-branch and token-write prerequisites, All Messages/mobile notification setup, daily Actions history checks, safe restoration of corrupt state from Git, disabled-webhook recovery, preserved long rate-limit gates, and push-failure duplicate risk. State that manual repair requires disabling the schedule and waiting for active runs; do not clear all seen history. Link the canonical spec and amendment without editing them. Mark live commissioning as outstanding until actual evidence exists (§§8–11).

**Done when:** README gives an operator the §9 setup order and failure recovery without inventing a new deployment path, hiding source-access uncertainty, or exposing a webhook. The public visibility choice is presented as resolved, while live operational acceptance remains pending.

## 11. Run and pass local verification

**Files touched:** none intentionally. Tests may create only their own temporary files and normal ignored interpreter caches; dependency setup may use `.venv/`. If a check fails, fix only the responsible allowed implementation file and rerun affected checks plus the full suite. Do not alter repository `seen.json` to make a test pass.

Run from the repository root using Python **3.12** with the exact requirements installed. On Windows, activate an existing correctly provisioned Python 3.12 environment, or prepare one with:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip --disable-pip-version-check install -r requirements.txt
```

On Ubuntu/WSL, equivalent preparation is:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip --disable-pip-version-check install -r requirements.txt
```

Dependency acquisition can need network access; it is separate from offline verification. Use existing exact installed dependencies if available. If installation is blocked, report the failed command and block local acceptance. Do not replace HomeHarvest with a fake installed package, change its pin, or count a stubbed dependency installation as verification. Request environment access through the available approval mechanism when required. Do not claim Windows testing proves Ubuntu runner operation.

With that environment active, run these commands in order; every command must exit 0:

```text
python -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version"
python -m pip check
python -c "import json; from importlib.metadata import distribution, version; d = json.loads(distribution('homeharvest').read_text('direct_url.json')); assert d['vcs_info']['commit_id'] == '8a6ac96db419b56a18d295935217649039bcdd0a', d['vcs_info']['commit_id']; assert version('requests') == '2.32.4', version('requests'); print('Exact dependency pins verified')"
python -m unittest discover -s tests -v
python -m unittest discover -s tests -p test_scan.py -k offline_cli_dry_run -v
python -c "import scan; s = scan.load_state(); assert s == dict(version=1, initialized=False, seen=[], disabled_webhook_sha256=None, discord_not_before=None), 'Shipped state must remain the initial baseline schema'"
git diff --check
git status --short
```

The full suite must report `OK` with no skipped or expected-failure acceptance cases. The separate `-k offline_cli_dry_run` invocation must run **both named tests**, not zero tests, and report `OK`. It is the required realistic offline dry-run invocation: it executes the script entry point with synthetic fetch data and blocked delivery. The `load_state()` command proves the workflow's exact no-argument loader interface works and the checked-in installation state remains initial; it is a pre-commissioning check only, not a recurring test requirement.

The dependency command checks HomeHarvest's installed `direct_url.json` VCS commit, rather than trusting its package version alone, and checks Requests version `2.32.4`. Record its result in the handback, without adding a dependency/report file.

Review status and compare against the starting checkout: only the seven allowed implementation paths may be new or changed by this implementation, preserved docs must be unchanged, and repository state must match the initial spec bytes. Passing tests do not authorize committing unrelated changes. Repeat the literal workflow/requirements check through the suite after any edits to those files.

Do **not** run a real scrape or POST as part of this offline gate. The later live Ubuntu/WSL dry-run command, when access exists, is:

```bash
DRY_RUN=1 timeout --signal=TERM --kill-after=10s 180s python scan.py
```

That command requires live source access, is not a substitute for the mocked tests, and cannot establish Discord or GitHub Actions health. The decisive hosted checks are the §9 commissioning dispatches below.

**Done when:** the exact environment and commands above pass, both dry-run entry tests execute, literal contracts match, preserved files and initial state remain intact, and results are ready to report. Without dependencies or another necessary local capability, hand back a blocked check and do not label the implementation locally verified.

## 12. Review the diff and commit the verified implementation

**Files touched:** no new source changes; stage only the seven implementation paths. A local commit changes Git metadata. Do not push, enable Actions, configure secrets, alter remote settings, or commission the service during this local-only task.

Run:

```text
git add -- .github/workflows/scan.yml .gitignore README.md requirements.txt scan.py seen.json tests/test_scan.py
git diff --cached --check
git diff --cached --stat
git diff --cached --name-only
git diff --cached
```

Inspect the complete staged diff, including new files which ordinary unstaged `git diff` would omit. Require precisely the allowed implementation changes, no unrelated staged work, and no section 10 additions. Verify the handback checklist below. If another contributor's changes are staged, stop and report the conflict rather than committing or silently unstaging them.

After the diff and all local checks pass, create the local commit:

```text
git commit -m "Implement Boca House Hunter canonical spec"
git status --short
git log -1 --oneline
```

Do not bypass hooks, fabricate a commit result, or configure an invented human Git identity. If repository permissions, identity, or hooks block committing, report that exact blocker and the verified diff status.

**Done when:** the local implementation commit exists, its hash and branch are recorded, all local verification outcomes are reported, and no unrelated files were included (§§3, 9, 10). The handback must explicitly distinguish **locally verified implementation** from **live commissioning outstanding**. Include exact command outcomes/test counts, environment and dependency pin evidence, unchanged-spec/state confirmation, and any blocker. No additional tracked report is required.

## Live commissioning boundary and required handoff

These actions implement spec §9 setup steps 2–6 and require real GitHub Actions/Discord access. They remain mandatory for operational acceptance, but the local implementer must neither simulate their completion nor weaken tests to conceal their absence. Record them as outstanding in the handback and README. The owner or later agent with access must execute them in this order:

1. Treat public visibility as already resolved by the amendment. Configure/verify deployment branch `main` as the remote default, regular text-channel webhook secret, `GITHUB_TOKEN` write permission and branch rules that permit state pushes, and channel/mobile All Messages settings. Keep the schedule disabled while checking setup. No competing scheduled process may use this webhook/state. Perform any necessary branch transition/publication in this deployment phase; do not assume a local `master` commit has already met this requirement.
2. Enable commissioning and immediately dispatch `dry_run=true`. From the actual GitHub runner, inspect pinned-scraper counts and manually compare current Realtor results and source records, including explicit-zero and unknown/positive HOA examples. Empty responses or all-unknown HOA do not prove coverage. If no explicit-zero records exist, record coverage as unproven without relaxing the filter.
3. Dispatch `dry_run=false` with uninitialized state and verify a durable baseline commit with zero Discord messages. An intervening scheduled baseline is equivalent because the workflow serializes runs; inspect which run performed it.
4. Disable the schedule and wait for active runs to finish. Remove exactly one still-qualifying identity from initialized state, commit the intentional edit, re-enable, and run one real dispatch. Verify exactly the expected real listing message and durable state commit. Repeat the dispatch and verify zero additional messages. Never clear the whole baseline or send synthetic channel test messages.
5. Leave the schedule enabled and inspect several actual scheduled runs, observed start gaps, durations, and state pushes. Record only measured results. Verify public standard runners and no paid/private-runner/storage configuration; check history daily and review scheduling activity after idle periods. Do not infer an SLA, unblocked future scraping, or exactly-once delivery from these samples.

Consistent live scraper failure means the service is not operational. Stop for review; do not add proxies, another scheduler, alternate scraping, or relaxed HOA rules. Successful offline checks alone never close these commissioning items (§§1–2, 9–11).

## Handback checklist for your own review

The plan author will use this checklist against the implementation diff, test source/results, and later commissioning evidence. Each unchecked local item blocks local acceptance; unchecked live items block operational acceptance.

- [ ] Diff contains only `.github/workflows/scan.yml`, `.gitignore`, `README.md`, `requirements.txt`, `scan.py`, `seen.json`, and `tests/test_scan.py`; original design, amendment, canonical spec, and this plan are unchanged (§3).
- [ ] Direct requirement lines match §3 byte-for-byte apart from line endings; installed HomeHarvest VCS commit and Requests version are verified. No floating pin, extra dependency, lockfile framework, automatic upgrade, or fallback version appears.
- [ ] One import-safe `scan.py`; `scan.load_state()` works without arguments and reads script-adjacent state without scrape/send/write; Python 3.12 and standard-library unittest are used (§§3, 8).
- [ ] Exactly the §4 fetch call appears, with all 13 keyword arguments and no extra filter/retry/window/page loop. Full result precedes POSTs; non-DataFrame, empty, cap, and missing-column tests fail safely.
- [ ] Eligibility tests cover exact inclusive price/sqft boundaries, wrong city/state/type, pending/contingent, pandas missing scalars/non-scalars, and unrounded comparisons. Only reported explicit zero HOA qualifies; null/NA/NaN/blank/text/false/infinite/negative/positive cases are excluded and unknown counts remain visible (§§4, 9).
- [ ] IDs enforce ASCII digits/length/integer/zero-only/boolean/float rules with leading-zero preservation. Duplicate conflicts are detected before filtering, contradictory HOA/status rows suppress the identity, and agreeing duplicates have deterministic tie-breaking (§4).
- [ ] Initial `seen.json` has exactly the five §5 values and prescribed formatting. Tests reject malformed state, duplicate keys/entries, constants, wrong types/version, unsorted identities, malformed digest/timestamp, and nonempty uninitialized history. No state reset or temp-file recovery exists.
- [ ] Atomic writes use the exact temp path, UTF-8 serialization options, schema order, sorted identities, newline, flush/fsync/replace sequence. No-op scans do not write; serialization/fsync/replace failures prevent subsequent delivery (§§5, 7).
- [ ] Baseline writes once with zero POSTs, including nonempty zero-eligible data. Repeat scans are silent; new pairs and new listing IDs may alert; same-pair reappearance and seen price changes do not. Unknown HOA becoming zero can first qualify (§§5, 9).
- [ ] All candidate payloads validate before any POST; a later invalid payload prevents earlier delivery. Exact one-embed shape, field order, inline values, no mentions, `New match`, HOA disclaimer, footer identity, and observation/source-date distinction match §6.
- [ ] URL acceptance/canonicalization, optional address/date/numeric fallbacks, Decimal display rounding, numeric length cap, escaping, long/non-ASCII address handling, UTF-16-safe truncation, and project/Discord text budgets have behavioral tests (§§4, 6, 9).
- [ ] Exact Session POST parameters, no redirects/auth/retry adapters, and only 200 plus a valid digit-string message ID confirms delivery. 204/malformed confirmation fail. A-confirmed/B-failed preserves only A; next scan retries B; saving A precedes sleep/next POST (§§5–6, 9).
- [ ] Fractional 429 max-delay-plus-0.25, three-attempt bound, identical retry payload, upward-rounded durable UTC gate, both-clock waiting, 0.5 success pacing, exhausted-bucket handling, last-candidate gate preservation without sleep, malformed limits, long cross-run delays, and budget boundaries are tested (§§6–7, 9).
- [ ] 400/5xx/timeouts stop without seen/retry; 401/403/404 latch; same canonical secret blocks external calls; changed secret preserves seen and any future gate; failed scrape does not save in-memory recovery; healthy no-candidate recovery saves once (§§6–7).
- [ ] Strict DRY_RUN values, missing/invalid real webhook even at baseline, 150-second budget, 25-second POST reserve, sleep-plus-25 reserve, exact §7 error-table outcomes, safe stdout UTC summaries, and failure exit codes are present. No blanket `BaseException` recovery or leaked HTTP exception text/token/digest exists.
- [ ] Workflow text matches §8's YAML exactly after newline normalization, including both SHA pins, cron, public/main guard, concurrency, latest-main checkout, permissions, timeout, tests, partial-state persistence, and checked push/rebase loop. No extra triggers, forced push, PAT, conflict-side selection, artifacts, or `continue-on-error` appears.
- [ ] Full offline suite passed with no skipped/expected-failure acceptance cases. Separate `-k offline_cli_dry_run` invocation ran both named entry-point tests successfully with mocked fetch, blocked HTTP, unchanged temporary state, correct counts, and no webhook requirement. Repository baseline bytes remained unchanged (§§7, 9).
- [ ] README reflects resolved public visibility, reported-zero limits, best-effort latency, silent baseline, duplicate risk, live-access limits, schedule monitoring, safe state/latch recovery, and the complete pending commissioning order. It does not claim operational success or guaranteed publication latency (§§1–2, 8–11).
- [ ] No UI/API server/database/archive/CSV/queue/outbox/separate state branch; no multi-city/configurable search/accounts/maps/geocoding/polygons/enrichment; no MLS/RETS/RESO integration/proxy/CAPTCHA/browser/alternate scraper appears (§10).
- [ ] No price-drop or repeated status alerts/history/pruning/inferred relist dates; no HomeHarvest patch/general retry framework/automatic conflict merge/exactly-once claim; no Discord bot/photos/multiple embeds/pings/commands/threads/error/test/heartbeat notifications/email/SMS/parser appears (§10).
- [ ] No Docker/Terraform/serverless/cloud database/extra scheduler/paid hosting/trial dependency/idle polling loop; no unknown-HOA-to-zero fallback, claim of no association, complete MLS coverage, or unmeasured near-instant publication appears (§10).
- [ ] Local commit hash, branch, test counts, command outcomes, dependency evidence, and any blocker are supplied; unrelated work is excluded. No remote deployment is misrepresented as part of local completion.
- [ ] **Live acceptance only:** evidence exists for real runner dry-run/source comparison, explicit-zero coverage assessment, silent durable baseline, controlled one-identity real delivery plus repeat suppression, and observed scheduled runs/state pushes. Until then, commissioning is explicitly outstanding (§9).
