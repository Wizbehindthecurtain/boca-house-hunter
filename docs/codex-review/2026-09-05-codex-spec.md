# Boca House Hunter — independent implementation spec

Date: 2026-09-05. Status: proposed implementation contract, independently researched; not a claim of a successful live deployment.

## 1. Decision and service promise

Build one Python scanner, one GitHub Actions workflow, one committed JSON state file, and one Discord incoming webhook. Query the full current matching inventory every **five minutes**, at UTC minutes `2,7,12,...,57`. Use a **public repository and a standard Ubuntu runner** for recurring hosting with no charge. No existing repository visibility, branch, settings, or secrets are changed by this document.

This changes the original private-repository requirement deliberately: private GitHub Free includes 2,000 runner minutes/month, and each job rounds up to a minute. A 15-minute schedule requires 2,880 jobs in 30 days, or at least 2,880 billable minutes before installation overhead; five minutes requires 8,640. Other private workflows share the allowance. A private repository is therefore incompatible with this recurring cadence and a guaranteed zero hosting bill on that allowance. Public standard runners are free; larger runners are not. [G4–G6]

Public deployment exposes the search criteria, source, Actions logs, and listing identifiers in state. Keep the Discord webhook secret. Publishing the existing repository requires the owner's separate decision; this spec is not authorization to publish it. If privacy is mandatory, do not deploy this public-hosting plan unchanged: the useful alternative is the same scanner on an already available, always-on computer. Do not silently substitute a private paid workflow or promise continuous operation after its free quota expires.

The product promise is **best-effort notification of first-observed qualifying listing identities after initial baseline**. It is not an MLS event subscription, a guarantee of municipal-boundary coverage, a guarantee that an association does not exist, or an immediate-listing SLA. Approximate latency is:

`broker/MLS → Realtor publication delay + next successful poll delay + runner/startup/scrape time + Discord delivery time`.

Only the last three terms are partly under this project's control. With perfectly punctual five-minute polls, polling alone contributes 0–5 minutes, averaging 2.5 minutes for uniformly timed arrivals. GitHub delays, dropped jobs, scraper failures, missing fields, and upstream omissions remove that upper bound. Listings that appear and disappear entirely between successful polls can be missed.

## 2. Research findings that constrain the design

### HomeHarvest: inspected code, releases, and issue discussions

The inspected default branch is `master`, at commit `8a6ac96db419b56a18d295935217649039bcdd0a`, dated 2025-12-26. Its package version and latest returned GitHub release are **0.8.18**. The repository was not archived when inspected, but no newer source commit was returned. This establishes the inspected version, not ongoing operational health. [H1–H3]

* `scrape_property()` accepts `property_type` as a **list**, and the actual numeric filter names are `sqft_min`, `price_min`, and `price_max`. There is **no `hoa_fee` argument**. There is no API key or official Realtor integration contract. The current scraper sends reverse-engineered GraphQL requests to `https://www.realtor.com/frontdoor/graphql`. [H1, H4]
* Price, size, and property type are added to the server query. `past_hours` combines a broader day query with client filtering; update-time filtering is also client-side. Neither is a reliable event cursor. Pagination is in pages of 200, with an exposed limit capped at 10,000 and support for `offset` and `parallel`. Do not fetch only a small sorted prefix. [H1, H4]
* The current base scraper forcibly sets `extra_property_data = False`, even if the caller requests it. The base search already requests `hoa { fee }`. Pandas output exposes `hoa_fee`, `style`, `list_price`, `full_baths`, `property_id`, and top-level `listing_id`; these are not the same as a `property_type` output column or an MLS board's listing number. [H5–H7]
* HOA absence becomes `None`/pandas missing data, not evidence of zero. A reported zero fee also cannot prove that no association exists; optional dues and incorrectly populated listings are possible. This tool can enforce **reported zero HOA fee**, not independently establish legal association status. [H6]
* Reliability problems are concrete. Issue #144 reported 403s on DigitalOcean, home networking, and purchased proxies. In #149, maintainer fixes were followed repeatedly by fresh 403 reports, sometimes within hours. A December 23 comment identified extra-detail requests as a failure point. These issues are now closed; closure does not establish that this query works from a current GitHub runner. [H8, H9]
* Issue #139 reported sort failures in 0.7.0. The inspected version has additional sort handling, so that historical issue is not evidence that the identical bug remains. Issue #159 reports suspect sold-listing dates; it is not a test of current for-sale freshness. Both discourage treating README examples or dates as a delivery guarantee. [H10, H11]
* The current HTTP request has **no explicit timeout** and does not generally call `raise_for_status()`. Some malformed/missing GraphQL data becomes an empty result instead of an exception. Location lookup has internal retries; a wrapper that does not retry does not mean the library never retries. A nonempty result also lacks a public completeness assertion: a later page can disappear silently. The safeguards below reduce damage without pretending to detect every partial response. [H4]

The original claim that Realtor.com is operated by NAR is inaccurate: the operator is Move, a News Corp subsidiary, under a relationship with NAR. MLS-fed listings do not by themselves establish a measurable Boca publication SLA. Operator correction is background knowledge, not an independently retrieved corporate-source finding in this review; neither operator identity nor a claimed freshness advantage is a dependency of the implementation.

### GitHub Actions

GitHub documents **five minutes as the shortest supported schedule**, not as a minimum reliable interval. No interval has an on-time guarantee. Loads are especially high at the start of an hour, and queued runs can be dropped. Offset the cron, but do not claim that offsetting prevents delays. Scheduled workflows must exist on and run from the default branch. Public schedules can be disabled after 60 days without repository activity. [G1, G2]

A fixed workflow-level concurrency group serializes scheduled and manual runs before checkout. With the default queue behavior, only one run waits; newer arrivals replace pending runs. Do not depend on dispatch ordering. `cancel-in-progress: false` protects a running send/state transaction from cancellation by a newer run. It cannot protect against manual cancellation or runner loss. [G3]

`contents: write` plus the repository's `GITHUB_TOKEN` is sufficient for allowed ordinary pushes. Branch rules may still deny a push. A token push does not recursively trigger a normal `push` workflow; a PAT is unnecessary. Git state persistence and a Discord POST are two independent operations and cannot provide exactly-once delivery. [G7]

### Discord

Execute the incoming webhook with `wait=true`: the API waits for confirmation and returns the created message. With the default `wait=false`, a message that is not saved may not produce an error. [D1]

There may be up to 10 embeds/message, but use **one**. Limits include title 256 characters, description 4,096, 25 fields, field name 256, field value 1,024, footer 2,048, author name 256, and 6,000 combined embed text characters across a message. Ordinary content is limited to 2,000. [D1, D2]

Rate limits are dynamic and per route/resource as well as global. Do not encode “five messages per two seconds” as a protocol guarantee. Observe `X-RateLimit-Remaining`, `X-RateLimit-Reset-After`, and 429 `Retry-After`/JSON `retry_after` in seconds, including fractional values. Unauthenticated requests also face IP-based global limits; a shared runner is not exempt. Discord specifically says not to keep using a webhook that returns 404. [D3]

### Alternatives considered

| Approach | Advantage for this goal | Decision |
|---|---|---|
| Public Actions + HomeHarvest at five minutes | Normal CPython runtime; no server; no recurring runner charge; faster target than the original 15 minutes | Implement this bounded hosted design, conditional on accepting public visibility and passing a live runner check. |
| An existing always-on PC/NAS, native scheduler at one minute | Removes Actions scheduling/startup delay; preserves privacy; a residential IP may behave differently | Meaningfully better if that hardware and continuous availability already exist. Availability is not established here. Electricity, sleep, connectivity, and maintenance remain costs/risks. Do not provision hardware or build a second deployment path now. |
| Realtor/Redfin/Zillow saved-search notifications; an agent's MLS auto-email | Provider-side notification can beat any poll; no scraper maintenance for the user | Sensible independent coverage. Current timing, exact no-HOA filtering, and this user's access were not verified. It does not directly satisfy Discord delivery; do not build mailbox parsing. Agent-assisted alerts do not require giving the buyer MLS API credentials. |
| Cloudflare Workers Free with one-minute cron | More frequent native schedule, private code/state possible, no Actions startup | A credible redesign, not a drop-in host. Official Python docs support asynchronous HTTP clients only; this HomeHarvest version uses synchronous `requests` and thread pools. Free cron has 10 ms CPU/invocation. Porting fetching/parsing plus adding persistent storage would need separate validation and maintenance. Do not build that port for v1. [C1–C3] |
| External cron calling GitHub dispatch | Can avoid GitHub's cron event scheduler | Still queues a GitHub job, still consumes private minutes, adds a credential/service, and does not solve source failures. Do not add it. |
| Free VM/trial or a paid data/proxy API | Potentially supports continuous polling | No independently verified perpetual-free, available, maintenance-free option or fresher authorized data feed was established. A trial is not a zero-cost long-term hosting plan. Do not make it a dependency. |

There is no researched basis for calling the chosen system the universally fastest free approach. It is the simplest verified runtime fit for the existing library with a faster free hosted target. If a live Actions scrape fails consistently, the implementation is **not operational**; changing schedulers or adding blind retries does not fix blocked source access.

## 3. Exact implementation file layout

The following is the future implementation layout. This review adds only this document, not these implementation files. The checkout currently has the original spec and Git metadata; no README was found despite the task's description.

```text
.github/workflows/scan.yml
.gitignore
README.md
requirements.txt
scan.py
seen.json
tests/test_scan.py
docs/superpowers/specs/2026-09-05-boca-house-hunter-design.md  # preserve
docs/codex-review/2026-09-05-codex-spec.md                    # this document
```

Use Python 3.12 on Ubuntu. No package scaffolding or separate application modules. Put pure normalization, state, payload, and delivery helpers in `scan.py`; importing it must cause no I/O. `tests/test_scan.py` uses standard-library `unittest` and `unittest.mock`, with synthetic rows and a fake HTTP transport. No live requests in tests.

Use exactly these direct requirements; the Git pin ties behavior to the code actually reviewed instead of assuming PyPI and `master` match:

```text
homeharvest @ git+https://github.com/ZacharyHampton/HomeHarvest.git@8a6ac96db419b56a18d295935217649039bcdd0a
requests==2.32.4
```

HomeHarvest brings pandas, pydantic, and tenacity. Its transitive ranges are not a fully reproducible lock; run the prescribed tests on every install and upgrade dependencies only through a deliberate reviewed change. Do not silently fall back to another version if installation fails. No Poetry, Docker, or dependency-update bot is required.

`.gitignore` contains `.venv/`, `__pycache__/`, `*.py[cod]`, `.env`, and `seen.json.tmp`, one per line. It must not ignore `seen.json`. Do not commit scraped DataFrames, CSVs, webhook URLs, or response bodies.

## 4. Fetch and eligibility contract

Make this one library call per run. Do not add an outer scrape retry, an HOA argument, a time window, or a small limit:

```python
from homeharvest import scrape_property

properties = scrape_property(
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
```

All omitted arguments retain the inspected version's defaults. `mls_only=False` avoids unnecessarily excluding for-sale-by-owner records. Sequential pagination avoids a burst of simultaneous page requests; there is no date-based early termination because no date filter is passed. Fetch all pages before sending.

Reject the entire scan before any state mutation or delivery if the return is not a pandas DataFrame, is empty, reaches 10,000 rows, or lacks any of these columns:

```text
property_id, listing_id, status, style, city, state,
list_price, sqft, hoa_fee, property_url
```

An empty unfiltered response is **indeterminate**, not “no new listings”: genuine zero inventory and a swallowed upstream failure cannot be distinguished with this public call. Log `scan_indeterminate_empty`, exit 1, retain state, and try again at the next scheduled run. A nonempty valid response with **zero eligible rows** is a successful scan and can initialize an empty baseline. At the cap, log `scan_result_cap`, exit 1, and require review; do not build geographic sharding.

Apply all filters again locally. Normalize a scalar missing value with `pandas.isna()` before string or numeric conversion; reject non-scalars. Do not use Python truthiness on pandas missing values and do not use `fillna(0)`.

Each eligible normalized row must satisfy all of the following:

1. `property_id` and top-level `listing_id` are trimmed strings of 1–64 ASCII digits, or nonnegative integer scalars converted to that form. Reject booleans, floating-point identifiers, zero-only IDs, missing IDs, and other formats. Preserve leading zeros in strings. Never substitute the MLS listing number, URL, address, or `list_date` for a missing ID.
2. Trim and uppercase `status`, `style`, and `state`; require `FOR_SALE`, `SINGLE_FAMILY`, and `FL`. Trim/casefold `city` and require `boca raton`. This is a Realtor city/address-label definition, including postal Boca addresses if returned; no municipal polygon/geocoding is implied.
3. `list_price` and `sqft` are finite, nonboolean numbers, or trimmed plain numeric strings accepted by `decimal.Decimal` (no currency symbols or commas). Require `250000 <= price <= 650000` and `sqft >= 1700`. Do not reject a valid boundary or round a value into range.
4. `hoa_fee` is a finite nonboolean number/plain numeric string and equals exactly zero. Positive and negative fees are rejected. Missing/null/NA/NaN/blank/unparseable/infinite fees are **unknown and excluded**, never converted to zero. No keyword inference from descriptions. An unknown fee remains eligible for reconsideration on subsequent scans if a value later arrives.
5. `property_url`, converted from its scalar value to text, is at most 2,048 characters, has scheme `https`, hostname exactly `realtor.com` or `www.realtor.com`, no credentials or explicit port, and a nonempty path. Remove query and fragment for the displayed URL. Reject an invalid URL; do not invent a link.

Log skip counts by reason; for malformed IDs log the row index, never its raw contents. Ordinary out-of-range rows are not errors. Unknown HOA rows are counted separately, so a scan with all HOA values unknown cannot look like affirmative no-HOA coverage. Other malformed required values produce warnings and no alert or seen entry for that row.

Optional display columns are `formatted_address`, `full_street_line`, `zip_code`, `beds`, `full_baths`, `half_baths`, and `list_date`. Use nonempty sanitized `formatted_address` first; otherwise, if `full_street_line` exists, join that line and `Boca Raton, FL <zip_code>` with `, `, omitting a missing ZIP. If neither address column has text, use `Boca Raton, FL — property <property_id>`. Do not reject an otherwise eligible listing for missing bedrooms, bathrooms, address line, or date. Numeric display values must be finite and nonnegative; otherwise show `Unknown`. Parse `list_date` with `pandas.to_datetime(value, errors="coerce", utc=True)` after rejecting numeric/non-scalar input; show its UTC date as `YYYY-MM-DD (source)`, without claiming an intraday MLS timestamp. Invalid/missing dates show `Unknown`. Dates never decide eligibility or deduplication.

Group rows by the normalized identity before eligibility evaluation. If duplicate rows disagree on any required normalized eligibility field or URL, skip that identity for this scan and log `conflicting_duplicate`. This includes a qualifying row paired with a contradictory HOA/status row. If required fields agree, retain one row; choose the lexicographically smallest sanitized address, then sanitized display tuple `(beds, full_baths, half_baths, list_date)` to break ties. Identical duplicates yield one candidate. A row without usable identity cannot join a group and is skipped.

## 5. State, startup, and deduplication

Ship this exact initial `seen.json`:

```json
{
  "version": 1,
  "initialized": false,
  "seen": [],
  "disabled_webhook_sha256": null,
  "discord_not_before": null
}
```

`seen` is a sorted, unique JSON array of strings in the format `<property_id>:<listing_id>`, applying the digit constraints above. `initialized` is a JSON boolean. `disabled_webhook_sha256` is null or a lowercase 64-character hexadecimal digest of the canonical webhook URL defined below. `discord_not_before` is null or a valid UTC timestamp strictly formatted `YYYY-MM-DDTHH:MM:SSZ`; it preserves a server-requested rate-limit delay across runs. The root must contain exactly these five keys; reject duplicate JSON object keys, NaN/Infinity JSON constants, extra keys, wrong types, duplicate seen entries, unsupported versions, or malformed identities. Require `seen=[]` when `initialized=false`.

Missing state, unreadable state, or invalid state is fatal: log `state_invalid` with an error class/reason, exit 1, do not scrape, post, overwrite, or regenerate it. A missing file is not a new installation. Recovery is to restore a known-good file from Git; do not clear state to silence an error.

On the first **healthy** real scan (`initialized=false`), put all currently eligible identities into `seen`, set `initialized=true`, write once, and **send nothing**. Log `baseline_created` and the eligible count. This avoids treating today's existing inventory as newly listed or flooding the webhook. A dry run never initializes state. Existing inventory can be inspected in the source's saved search; this project does not generate a listing archive.

For initialized scans:

1. `candidates = eligible_identity_set - seen_set`, sorted lexicographically by identity. Validate/build every candidate payload before the first POST.
2. Send candidates sequentially. Only confirmed Discord delivery adds that identity to `seen`. Immediately save the full state atomically after **each** confirmation, before sleeping or considering another listing.
3. A failure on candidate B after confirmation of A retains A's local state, leaves B and subsequent candidates unseen, stops sending, and exits 1. The workflow must attempt to persist A even though the scan failed.
4. Never remove an identity when it disappears from search. Never refresh state merely to store a scan timestamp. No-change scans make no commit.

New `listing_id` for the same `property_id` can produce a relisting alert. Reappearance with the same pair does not. A source ID change can also yield another alert; do not invent a heuristic to distinguish it. A previously out-of-range or unknown-HOA listing becoming eligible can generate its first alert, even if its original list date is old. Previously alerted identities never get price/status-change alerts. Use “New match,” not “Just listed,” in Discord.

Atomic save: write UTF-8 JSON with `indent=2`, `allow_nan=False`, keys in the schema order above, sorted `seen`, and one trailing newline to `seen.json.tmp` in the same directory. Flush and `os.fsync()` the temporary file, then `os.replace()` it onto `seen.json`. Only write when logical state changes. Never modify state in place. Treat write errors as fatal; stop before further POSTs. Ignore/remove a leftover temporary file only when a later real save replaces it; never use it for recovery.

Delivery semantics are **duplicate-preferring best effort**, not exactly-once. Discord may accept a message just before a timeout, malformed confirmation, process termination, or failed Git push. The next scan may send it again because durable state has not advanced. Marking seen before sending would instead lose alerts and is forbidden. Retrying next time also depends on the listing remaining observable and qualifying: there is no durable outbox of vanished listings in v1.

## 6. Exact Discord contract

Use an incoming webhook in a regular Discord text channel. No bot token, Gateway connection, forum channel, thread, or SDK. The owner sets channel/mobile notifications to **All Messages**; `allowed_mentions` below deliberately does not ping a role or everyone. DND, mute settings, and mobile OS settings can still prevent a visible push.

Read `DISCORD_WEBHOOK_URL` from the environment only. In real mode it is required even for baseline creation; fail before scraping if absent or invalid. Accept only an HTTPS URL on `discord.com`, without credentials, explicit port, query, or fragment, with path matching `/api(?:/v10)?/webhooks/([0-9]+)/([A-Za-z0-9._-]+)`. Canonicalize to `https://discord.com/api/v10/webhooks/<id>/<token>`. Compute the SHA-256 digest of this canonical URL for the disabled-webhook check; never log the URL, token, or digest.

Before scraping in real mode, if this digest equals `disabled_webhook_sha256`, log `webhook_disabled`, exit 1, and make no external calls. If the stored digest differs, clear the latch in memory; save that change only after a healthy scan, a confirmed send, or a newly latched permanent failure. Replacing the secret thus permits recovery without discarding `seen`.

Next, if UTC now is earlier than `discord_not_before`, log `webhook_backoff` and that timestamp, exit 0 without scraping, sending, sleeping, or changing state. An expired timestamp is cleared in memory and saved on the next healthy scan or delivery-state write. Dry runs ignore this gate and the disabled-webhook latch. Keep a future gate even if the webhook secret changes; waiting is conservative and avoids needing a second secret fingerprint.

Send with a single `requests.Session`, no retry adapters, no authorization header, redirects disabled:

```python
response = session.post(
    canonical_webhook_url,
    params={"wait": "true"},
    json=payload,
    timeout=(5, 15),
    allow_redirects=False,
)
```

Use this exact JSON shape, with strings substituted by the formatting rules below. Omit no field shown here; add no photos or source descriptions:

```json
{
  "username": "Boca House Hunter",
  "allowed_mentions": {"parse": []},
  "embeds": [
    {
      "title": "New match: <address>",
      "url": "<validated Realtor URL>",
      "color": 3066993,
      "fields": [
        {"name": "Price", "value": "<USD price>", "inline": true},
        {"name": "Size", "value": "<sqft> sq ft", "inline": true},
        {"name": "Beds", "value": "<beds or Unknown>", "inline": true},
        {"name": "Baths", "value": "<full or Unknown> full / <half or Unknown> half", "inline": true},
        {"name": "HOA fee", "value": "$0 reported; association status unverified", "inline": false},
        {"name": "Listed", "value": "<YYYY-MM-DD (source) or Unknown>", "inline": true}
      ],
      "footer": {"text": "Realtor.com via HomeHarvest | <property_id>:<listing_id>"},
      "timestamp": "<observation UTC timestamp>"
    }
  ]
}
```

Capture the observation timestamp after the scrape returns; format `datetime.now(timezone.utc)` at seconds precision as `YYYY-MM-DDTHH:MM:SSZ`. It is observation time, not source list time. Reuse the identical payload on a 429 retry.

Price is `$` plus comma-separated decimal notation, with zero decimals for integral prices and two decimals otherwise (decimal `ROUND_HALF_UP` for display only). Size uses comma separators and up to two decimal places with trailing decimal zeros removed. Beds/bath values must be integers or display `Unknown`. For any computed numeric display string exceeding 64 characters, use `Unknown` rather than scientific notation. Formatting never changes the underlying eligibility comparison.

Sanitize source-derived text: collapse whitespace, remove control characters, replace `@` with fullwidth `＠`, and escape Markdown characters `\\`, backtick, `*`, `_`, `~`, `|`, `[`, `]`, `<`, `>` with a backslash. Format numeric/date fields from validated values. Truncate the final escaped title to 240 characters and optional source address components before joining to 200 characters each. All field values are limited to 512 and the footer to 200; truncate to `limit-3` plus `...` if needed. Count conservatively using UTF-16 code units and never split a surrogate pair. These project budgets stay well below Discord's aggregate limit. Validate the actual payload against both the project budgets and the documented 6,000-character total before sending; a construction failure stops the whole scan before the first POST.

**Confirmation:** success is HTTP 200 plus a JSON object with a nonempty digit-string `id` for the created message. A 204 is not accepted with this `wait=true` contract. No other 2xx, malformed JSON, missing message ID, redirect, or non-2xx is success. The ID need not be stored: identity in the embed footer makes occasional duplicates recognizable.

**Rate handling:** the first candidate sends immediately after the persisted gate above permits it. After each successful message, add its identity and save state immediately, then before another POST wait at least 0.5 seconds. If `X-RateLimit-Remaining` parses as zero, also wait `X-RateLimit-Reset-After + 0.25` seconds; use the larger wait. Values must be finite nonnegative seconds. With a valid exhausted-bucket delay, set `discord_not_before` to the next whole UTC second at or after `now + delay` in the same atomic save as the confirmed identity, even if this was the last candidate. Otherwise clear an expired gate in that save. If an exhausted-bucket header lacks a valid reset delay, preserve the confirmed identity, stop the batch, and exit 1 rather than guessing the bucket duration.

On HTTP 429, take the maximum valid nonnegative numeric seconds found in the `Retry-After` header and JSON `retry_after`, add 0.25 seconds, and retry the **same** message. First atomically save `discord_not_before` using the same upward UTC-second rounding rule, without marking this identity seen. If neither delay is valid, stop with exit 1. Allow at most three POST attempts per candidate total. Before every subsequent in-run POST, including the next candidate after a success, wait until both the computed monotonic delay and any saved UTC gate have passed. A required sleep exceeding 30 seconds, or exceeding the remaining execution budget below, stops the batch with exit 1 and leaves unsent identities unseen. The persisted gate prevents a subsequent run from retrying early even if the requested delay exceeds five minutes. Never clamp a long delay down and send early. After a successful retry, update/clear the gate according to the success-header rule above. If all candidates have succeeded, retain any future gate but do not sleep just to drain the bucket; exit 0.

On 401, 403, or 404, set `disabled_webhook_sha256` to the current digest, save it with all confirmed identities, log `webhook_permanent_failure` plus HTTP status, and exit 1. Later runs make no calls with that latched webhook. Replace the webhook secret, or fix the channel and manually clear only this latch with the schedule disabled. Do not reset seen identities.

All other failures (including 400, 5xx, network/connection/read timeout, invalid confirmation, or exhausted 429 attempts) stop the batch, retain confirmed state, and exit 1. No automatic retry for ambiguous delivery errors: the next successful poll is the retry. No Discord error/test/heartbeat messages are sent.

## 7. Entry point, budgets, logging, and errors

`python scan.py` reads `DRY_RUN` (`0` if absent; only `0` or `1` accepted). No criteria flags, config file, environment-based search filters, or interactive prompts. `DRY_RUN=1` loads/validates existing state, performs the exact fetch/filter/diff/payload validation, and logs counts; it neither requires/uses a webhook nor writes any state. It reports how many messages a real initialized run would send, or how many identities would enter a first baseline. It never prints full payloads or listing data.

Resolve state relative to `Path(__file__).resolve().parent`, not the caller's directory. Start a monotonic **150-second application budget** on entry. Before each POST require at least 25 seconds remaining; before a rate-limit sleep require that sleep plus 25 seconds remaining. Exhausting the budget with unsent work is exit 1. The workflow's external 180-second GNU `timeout` is necessary because the library has no socket deadline and Requests timeouts do not bound all possible wall-clock behavior. The script need not implement subprocess supervision or monkey-patch HomeHarvest. Live local runs should use the same Ubuntu/WSL `timeout` command; a plain Windows execution does not acquire that external safety bound.

Use standard-library logging to stdout, UTC timestamp, level, and fixed event name. Include total fetched, duplicate groups, rejected-by-reason counts, eligible, already seen, candidate, confirmed, unsent, baseline-created flag, and elapsed seconds in a final summary whenever normal exception handling can run. Log candidate identity and HTTP status when relevant. Do not log webhook values, headers, exception `str()` from HTTP clients, raw response bodies, raw DataFrames, full payloads, or source descriptions. A fixed error code plus exception class and phase is sufficient to diagnose which stage failed without exposing a webhook in a Requests traceback.

Catch these boundaries explicitly:

| Boundary | Catch/recognize | Required result |
|---|---|---|
| Config/state loading | Missing/invalid env, `OSError`, JSON decode/duplicate-key errors, schema `ValueError`/`TypeError` | Exit 1 before scrape; no writes or sends. |
| Entire HomeHarvest call | `Exception`, including `AuthenticationError`, Requests errors, tenacity `RetryError`, parsing/validation errors | Log `scrape_failed` and class; exit 1; no state change or send. Do not catch only `AuthenticationError`. |
| Fetch shape/cap/empty | Explicit validation failures | Exit 1; no state change or send. |
| Row normalization | Expected scalar conversion/type/date/URL errors | Count and warn; skip affected identity; no alert/seen entry. |
| Payload construction | Validation/type/format failure | Exit 1 before any POST. |
| Discord | `requests.RequestException`, JSON errors, all response cases specified above | Stop batch; preserve confirmed state; exit 1 unless all candidates confirmed. |
| Atomic write | `OSError` or serialization failure | Log `state_write_failed`; stop further sends; exit 1. Prior remote delivery may duplicate later. |
| Unexpected application bug | Outer `except Exception` logs class and phase only | Exit 1; do not reset state, continue the batch, or mark unsent identities seen. |
| Process kill/cancel/runner loss | Not reliably catchable; no `BaseException` blanket catch | Existing atomic state may survive locally; persistence step is best effort, never guaranteed. |
| Git persistence | Checkout/add/commit/pull/rebase/push failure | Workflow fails visibly; no forced push; potential duplicate delivery next run. |

Exit 0 only for a completed dry run, deliberate `webhook_backoff` skip, healthy baseline, no-candidate scan, or fully confirmed batch whose local state writes succeeded. A no-candidate healthy scan also saves any cleared expired gate/latch once if necessary. A later Git failure still makes the workflow red. No failure notification channel beyond Actions logs/run status is implemented. The owner must check run history and scheduling activity; a dropped/disabled run produces no scanner failure message.

## 8. Exact GitHub Actions workflow

Deployment branch is **`main`**, which must be the repository default. The existing local branch is `master`; any rename/publication belongs to implementation/deployment, not this review. Use only the following triggers. Manual runs default to dry run. The public/default-branch job guard prevents accidentally running this high-frequency hosted plan against a private repository or a selected feature branch.

```yaml
name: Scan Boca listings

on:
  schedule:
    - cron: '2-59/5 * * * *'
  workflow_dispatch:
    inputs:
      dry_run:
        description: 'Fetch and validate without Discord or state writes'
        type: boolean
        required: true
        default: true

permissions:
  contents: write

concurrency:
  group: boca-house-hunter-state
  cancel-in-progress: false

defaults:
  run:
    shell: bash

env:
  PYTHONUNBUFFERED: '1'
  DRY_RUN: ${{ github.event_name == 'workflow_dispatch' && inputs.dry_run && '1' || '0' }}

jobs:
  scan:
    if: ${{ github.ref == 'refs/heads/main' && github.event.repository.private == false }}
    runs-on: ubuntu-latest
    timeout-minutes: 8
    steps:
      - name: Check out latest state and code
        uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # inspected v4
        with:
          ref: main
          fetch-depth: 0
          persist-credentials: true

      - name: Set up Python
        uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # inspected v5
        with:
          python-version: '3.12'
          cache: pip
          cache-dependency-path: requirements.txt

      - name: Install dependencies
        timeout-minutes: 2
        run: python -m pip --disable-pip-version-check install -r requirements.txt

      - name: Check behavior without network calls
        timeout-minutes: 1
        run: python -m unittest discover -s tests -v

      - name: Scan and deliver
        id: scan
        timeout-minutes: 4
        env:
          DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
        run: timeout --signal=TERM --kill-after=10s 180s python scan.py

      - name: Persist baseline and confirmed deliveries
        if: ${{ always() && env.DRY_RUN == '0' && (steps.scan.outcome == 'success' || steps.scan.outcome == 'failure' || steps.scan.outcome == 'cancelled') }}
        timeout-minutes: 1
        run: |
          set -euo pipefail
          python -c 'import scan; scan.load_state()'
          git add -- seen.json
          if git diff --cached --quiet; then
            exit 0
          fi
          git config user.name 'github-actions[bot]'
          git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
          git commit -m 'Update listing alert state'
          for attempt in 1 2 3; do
            if git push origin HEAD:main; then
              exit 0
            fi
            if ! git pull --rebase origin main; then
              git rebase --abort || true
              echo '::error::State push/rebase failed; restore durable state before relying on deduplication.'
              exit 1
            fi
            sleep "$attempt"
          done
          echo '::error::State push failed after three attempts; confirmed messages may repeat.'
          exit 1
```

`scan.load_state()` is the pure state-loading/validation function specified above; it performs no scrape, POST, or mutation. Its default path is the script-adjacent `seen.json`. No `continue-on-error`: a failed scan stays failed even when state persistence succeeds. `always()` attempts to retain partial successes, but job timeout/cancellation/runner loss can still prevent that step from completing.

Full checkout occurs after acquiring the workflow concurrency lock and uses the latest `main`, not the original event SHA. This matters for queued/re-run workflows: they must not resend from stale state. The small rebase loop handles an unrelated human code commit during the scan. It is not a state merge algorithm; a state conflict stops for review. Never force-push or automatically choose one side of a state conflict.

Before enabling the schedule, repository settings must permit the default token to push to `main`; this dedicated personal repository must have no rule that requires a PR for its state commits. Do not introduce a PAT or weaken unrelated repositories' branch rules. No other workflow or local scheduled process may send to this webhook and write this state concurrently. Disable the schedule and wait for its active run to finish before manual state repair. Human code changes should not modify `seen.json`.

Do not add `push`, `pull_request`, `repository_dispatch`, or periodic keepalive commits. `GITHUB_TOKEN` avoids push recursion anyway. Keep pip cache at the included/default limit; add no artifacts, custom images, larger runners, or paid storage. Check Actions run history daily during the house hunt and ensure the schedule is enabled; review/reactivate it before relying on it after an extended idle period. Do not assume an occasional bot state commit prevents the 60-day inactivity shutdown.

## 9. Required verification and setup sequence

The implementation is accepted only after these behavioral tests pass. These are small tests of alert correctness, not mocks that merely assert one function calls another:

* Inclusive price/sqft boundaries; wrong city/state/type; pending/contingent; below/above limits.
* HOA `0`, `0.0`, and `"0"` qualify; null, pandas NA/NaN, blank, nonnumeric, boolean false, infinity, negative, and positive fees do not. Missing required column fails the scan; absent optional display fields do not.
* Baseline creates state with zero POSTs; repeat scan sends nothing; new pair sends once; same property with new listing ID can send; disappearing/reappearing same pair cannot; missing IDs do not become `"nan"` identities; conflicting duplicates do not alert.
* Unknown-HOA becoming explicit zero can first qualify. Price movement for an already seen identity produces no new message.
* Corrupt/missing state fails without network or replacement; empty scrape fails without clearing or initializing; nonempty data with no eligible records is valid; result cap fails. Dry run never sends or writes.
* A confirmed A followed by failed B persists only A; next scan retries B. A 200 without an ID and a 204 do not mark seen. An atomic-write failure stops further delivery.
* 429 honors fractional header/body delays and the three-attempt bound; long delays persist across runs and later runs do not send early; malformed limits stop; 400/5xx/timeout stop without marking seen; 401/403/404 latch disables future use; changing the webhook permits recovery without clearing seen.
* Payload limits, non-ASCII/long address truncation, invalid URL rejection, unknown optional fields, no mentions, and absence of webhook tokens in captured failure logs.

Test temporary state belongs in the test harness's temporary directory. Do not send synthetic test messages to the real channel. Freeze the clock and replace sleep/HTTP in tests to verify behavior quickly.

Setup order:

1. Implement the listed files and tests; preserve both specs. Run the offline tests.
2. Resolve the public-visibility decision before publication. Configure `main`, a regular text-channel webhook secret, write permission for state pushes, and channel/mobile notification settings. Keep the scheduled workflow disabled while checking setup.
3. Enable it for commissioning and immediately dispatch with `dry_run=true`. Inspect the exact pinned version's output counts from a GitHub runner. Manually compare current Realtor search results and several source records, including a reported zero HOA fee and an unknown/positive fee. Do not accept an empty response or all-unknown HOA data as evidence the no-HOA requirement is working. If no explicit zero records exist, record that coverage remains unproven rather than loosening the filter.
4. Dispatch with `dry_run=false` while `initialized=false`. Verify a baseline commit and zero Discord messages. A schedule may perform that same baseline first; concurrency keeps these equivalent.
5. To verify real delivery, disable the schedule, wait for active runs to finish, remove exactly one still-qualifying identity from initialized state, and commit that intentional edit. Re-enable and run one real dispatch. Verify the expected single real listing message and a durable state commit. Repeat the dispatch and verify zero additional messages. Do not clear the entire baseline.
6. Leave the schedule enabled and inspect several actual scheduled runs, their observed start gaps, durations, and state pushes. Document only measured observations; do not infer an SLA from a few successful samples. Confirm no private-runner or paid-storage configuration is active.

No live HomeHarvest request from GitHub Actions or Discord delivery was executed during this review, and no dependencies were installed. Those commissioning checks remain required before the system can be described as working.

## 10. Explicitly do not build

* No UI, API server, database, listing archive, CSV exports, queues, durable outbox, or separate state branch.
* No multi-city searches, configurable criteria, user accounts, saved-search management, maps, geocoding, municipal-boundary polygons, or property enrichment.
* No broker/MLS/RETS/RESO integration, proxy purchases/rotation, CAPTCHA handling, scraping-framework fork, browser automation, or alternate-portal scraper.
* No price-drop messages, repeated same-identity status messages, historical market analysis, dedup expiry/pruning, or inferred relist dates.
* No automatic dependency upgrades, HomeHarvest monkey patches, generalized retry framework, automatic state-conflict merge, or exactly-once claim.
* No Discord bot, photos, multiple embeds, role pings, commands, threads, error alerts, heartbeats, email, SMS, or email-to-Discord parsing.
* No Docker, Terraform, serverless port, cloud database, extra scheduler, paid hosting, free-trial dependency, or idle runner loop to poll faster than Actions cron permits.
* No hidden fallback from unknown HOA to zero. No assertion of “no association” from the fee alone. No claim of complete MLS coverage or near-instant publication without measurements.

## 11. Evidence and access limits

Research was conducted on 2026-09-05 through the connected GitHub reader against public upstream repositories, including official documentation source repositories. The local Firecrawl CLI was installed but unauthenticated. A direct PowerShell GitHub request failed with a network connection error; the requested escalation returned `Rejected("approval request failed")`. The GitHub connector successfully supplied the evidence below. No sandbox changes or extra credentials were made. Direct provider websites, PyPI package contents, local/runner scrape behavior, provider saved-search notification timing, and the corporate operator reference were not independently fetched/tested. Do not interpret these sources as a live service-health test.

HomeHarvest source links are pinned to the inspected commit. GitHub/Discord/Cloudflare documentation links point to their official source repositories as read on the research date; those branches can change later.

* **H1:** [Public call signature and parameter conversion](https://github.com/ZacharyHampton/HomeHarvest/blob/8a6ac96db419b56a18d295935217649039bcdd0a/homeharvest/__init__.py).
* **H2:** [Pinned package metadata](https://github.com/ZacharyHampton/HomeHarvest/blob/8a6ac96db419b56a18d295935217649039bcdd0a/pyproject.toml) and [inspected commit](https://github.com/ZacharyHampton/HomeHarvest/commit/8a6ac96db419b56a18d295935217649039bcdd0a).
* **H3:** [Release v0.8.18](https://github.com/ZacharyHampton/HomeHarvest/releases/tag/v0.8.18); [repository metadata](https://api.github.com/repos/ZacharyHampton/HomeHarvest).
* **H4:** [Realtor requests, filters, empty paths, and pagination](https://github.com/ZacharyHampton/HomeHarvest/blob/8a6ac96db419b56a18d295935217649039bcdd0a/homeharvest/core/scrapers/realtor/__init__.py).
* **H5:** [Extra data forced off](https://github.com/ZacharyHampton/HomeHarvest/blob/8a6ac96db419b56a18d295935217649039bcdd0a/homeharvest/core/scrapers/__init__.py).
* **H6:** [HOA/identity processing](https://github.com/ZacharyHampton/HomeHarvest/blob/8a6ac96db419b56a18d295935217649039bcdd0a/homeharvest/core/scrapers/realtor/processors.py) and [base queries](https://github.com/ZacharyHampton/HomeHarvest/blob/8a6ac96db419b56a18d295935217649039bcdd0a/homeharvest/core/scrapers/realtor/queries.py).
* **H7:** [Pandas output mapping](https://github.com/ZacharyHampton/HomeHarvest/blob/8a6ac96db419b56a18d295935217649039bcdd0a/homeharvest/utils.py) and [models](https://github.com/ZacharyHampton/HomeHarvest/blob/8a6ac96db419b56a18d295935217649039bcdd0a/homeharvest/core/scrapers/models.py).
* **H8:** [Issue #144 and discussion](https://github.com/ZacharyHampton/HomeHarvest/issues/144).
* **H9:** [Issue #149 and repeated failure/fix discussion](https://github.com/ZacharyHampton/HomeHarvest/issues/149).
* **H10:** [Issue #139: historical sort/pagination report](https://github.com/ZacharyHampton/HomeHarvest/issues/139).
* **H11:** [Issue #159: sold-date report](https://github.com/ZacharyHampton/HomeHarvest/issues/159).
* **G1:** [Scheduled events/default branch/inactivity](https://github.com/github/docs/blob/main/content/actions/reference/workflows-and-actions/events-that-trigger-workflows.md#schedule) and [five-minute minimum](https://github.com/github/docs/blob/main/data/reusables/repositories/actions-scheduled-workflow-example.md).
* **G2:** [Official delay/drop warning](https://github.com/github/docs/blob/main/data/reusables/actions/schedule-delay.md).
* **G3:** [Concurrency behavior](https://github.com/github/docs/blob/main/data/reusables/actions/actions-group-concurrency.md).
* **G4:** [Actions billing and free public standard runners](https://github.com/github/docs/blob/main/content/billing/concepts/product-billing/github-actions.md).
* **G5:** [Included plan quotas](https://github.com/github/docs/blob/main/data/reusables/billing/actions-included-quotas.md).
* **G6:** [Per-job minute rounding](https://github.com/github/docs/blob/main/content/actions/how-tos/monitor-workflows/view-job-execution-time.md).
* **G7:** [Token-trigger behavior](https://github.com/github/docs/blob/main/data/reusables/actions/actions-do-not-trigger-workflows.md).
* **G8:** Inspected action tag targets: [checkout v4](https://api.github.com/repos/actions/checkout/git/ref/tags/v4), [setup-python v5](https://api.github.com/repos/actions/setup-python/git/ref/tags/v5). The YAML pins their returned commit SHAs rather than moving tags.
* **D1:** [Execute Webhook and wait semantics](https://github.com/discord/discord-api-docs/blob/main/developers/resources/webhook.mdx).
* **D2:** [Embed/message limits](https://github.com/discord/discord-api-docs/blob/main/developers/resources/message.mdx).
* **D3:** [Dynamic rate limits, retry headers, invalid requests, and webhook 404](https://github.com/discord/discord-api-docs/blob/main/developers/topics/rate-limits.mdx).
* **C1:** [Workers cron, including every-minute expressions](https://github.com/cloudflare/cloudflare-docs/blob/production/src/content/docs/workers/configuration/cron-triggers.mdx).
* **C2:** [Python package/HTTP client support](https://github.com/cloudflare/cloudflare-docs/blob/production/src/content/docs/workers/languages/python/packages/index.mdx).
* **C3:** [Workers Free CPU/cron limits](https://github.com/cloudflare/cloudflare-docs/blob/production/src/content/docs/workers/platform/limits.mdx).

## Disagreements with the existing spec

1. **Five-minute offset polling instead of `*/15`.** Five minutes is GitHub's documented minimum and better matches the user's speed goal. Fifteen minutes is not inherently reliable either. The new promise explicitly includes delays, dropped runs, and upstream publication latency instead of “the moment one appears.”
2. **Public hosted repository instead of assuming private hosting stays free.** Even one billed minute per 15-minute run exceeds GitHub Free's monthly private allowance. Public standard runners solve the recurring runner bill at a real privacy cost. This is a proposed deployment requirement, not permission to change visibility; an existing always-on computer is the better private/faster alternative if available.
3. **Source verification instead of calling Realtor NAR-operated or guaranteeing near-MLS speed.** The operator claim is inaccurate, and neither library metadata nor MLS-shaped fields establish publication latency. The spec distinguishes verified code, user issue reports, background knowledge, and unperformed live checks.
4. **Exact pinned call with extras off and local revalidation.** The original leaves the signature/version implicit. The current library has no HOA search parameter, forcibly disables extras, has internal retries, and has unbounded HTTP waits. Pin the inspected source, name actual arguments/output fields, avoid time windows, and bound the process externally.
5. **Explicit zero HOA only; missing means unknown.** “Drop nonzero” is underspecified for pandas NA and risks treating missing data as no HOA. This spec knowingly sacrifices some recall to avoid unknown-fee alerts and labels zero as reported, not proof that no association exists.
6. **Silent explicit baseline and strict state validation.** Missing state must not silently reset alert history and send the whole inventory. The shipped uninitialized state distinguishes first setup from corruption. Baseline is intentional and visible in logs.
7. **Property-plus-listing identity instead of only `property_id`.** The original append-only property list cannot “naturally re-alert” a relisting as claimed. A new source listing ID can distinguish a new listing episode without purging absent properties; same-pair reappearance remains suppressed. Missing listing identity is skipped, not guessed.
8. **Confirmed delivery and per-message atomic saves.** Unsent or failed deliveries must not be marked seen. `wait=true` plus a returned message ID is the success contract; partial success is preserved even when the run fails. The unavoidable send-versus-Git crash window is documented rather than implying exactly-once behavior.
9. **Bounded Discord rate handling and a permanent-failure latch.** A blanket “no retry/backoff” policy ignores Discord's protocol, especially during a backlog. Only 429 has bounded in-run retries; a saved not-before timestamp honors longer server delays across runs, and 401/403/404 prevent repeated use of a broken webhook. General scrape/delivery failures wait for a later scan rather than adding a retry subsystem.
10. **Serialized runs, latest-branch checkout, explicit write permission, and checked push recovery.** The original commit-back sketch does not cover overlapping manual/scheduled runs, stale event state, partial-send failures, push rejection, or branch protection. The specified YAML addresses normal races without PATs, forced pushes, or automatic state conflict resolution.
11. **Empty and capped results fail conservatively.** Current code can convert malformed upstream results into an empty dataset, so exceptions alone are insufficient. Never initialize or clear state from such a scan, and reject a result that visibly reaches the cap. Undetectable partial responses or truncation remain an acknowledged limit.
12. **Small offline behavioral tests plus live commissioning instead of live-only testing.** HOA null handling, deduplication, partial failures, 429s, and corrupt state directly affect missed/duplicate alerts and are difficult to validate safely through a live scrape alone. Tests cover those outcomes without building a broad test framework or sending synthetic Discord messages.
