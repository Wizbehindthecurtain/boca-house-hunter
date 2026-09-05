# Boca House Hunter

A personal alert tool. It watches Realtor.com (via the open-source
[HomeHarvest](https://github.com/ZacharyHampton/HomeHarvest) library, no MLS
API key) for for-sale, single-family listings in Boca Raton, FL matching a
fixed set of criteria, and makes a best-effort attempt to post a Discord
message soon after a listing first qualifies — see the latency and
delivery-guarantee caveats below before treating this as instant or
guaranteed. Full behavioral contract: `docs/codex-review/2026-09-05-codex-spec.md`.
Amendment record (visibility decision, rigor decision): `docs/superpowers/specs/2026-09-05-boca-house-hunter-design-v2.md`.

## What it actually does — and does not — guarantee

- **Fixed search only**: Boca Raton, FL; for-sale; single-family; sqft ≥ 1,700;
  price $250,000–$650,000. Not configurable without editing `scan.py`.
- **"No HOA" means "reported zero HOA fee"**, nothing stronger. A listing
  with no HOA data at all is *unknown*, not assumed HOA-free — it's excluded
  from alerts until (if ever) it's re-scraped with an explicit `$0` HOA fee.
  A reported zero also does not prove no association exists; it only reflects
  what Realtor.com's data shows.
- **"Boca Raton" is a city/address label**, not a checked municipal boundary
  — whatever Realtor.com tags as that city, including any Boca-addressed
  unincorporated listings, is in scope.
- **First run creates a silent baseline.** The very first healthy scan
  records every currently-matching identity as already-seen and sends
  **zero** Discord messages — otherwise every existing listing in the city
  would look "new." After that, an identity alerts the first time it
  *becomes* eligible — that includes a genuinely new listing, but also an
  existing listing whose HOA fee was previously unknown and is later
  reported as an explicit `$0` (see the "reported zero" caveat above); it is
  not limited to inventory that is literally new to the search.
- **Dedup key is `property_id:listing_id`, not just the property.** The same
  physical property relisted under a new listing ID can alert again; the
  exact same pair reappearing after vanishing does not. A property never
  drops out of the seen-list just because it stops appearing in search
  results.
- **No price-drop or status-change alerts.** Once a listing has alerted, any
  later price or status change on it is silent.
- **Delivery is duplicate-preferring, not exactly-once — and misses are
  still possible.** A listing is never marked "seen" without an
  HTTP-confirmed Discord delivery first, so a delivery that was actually
  attempted won't be silently dropped from history: if Discord accepts a
  message but the process dies or the state commit fails before that
  success is saved, the same listing can alert again on a later run
  (a duplicate, not a miss). But this does **not** mean misses are
  impossible: a listing that appears and then disappears again entirely
  between two successful polls is never observed at all, and there is no
  durable outbox of candidates that failed to send — a run that stops
  partway through a batch leaves the *unsent* remainder to be picked up (if
  still eligible) on the next successful poll, not guaranteed. An **empty,
  non-DataFrame, or column-missing** response is treated as indeterminate
  and simply retried next run (see `scan_indeterminate_empty`) — but a
  response that comes back nonempty and shape-valid is processed as
  healthy with no way to detect that it was silently truncated or
  otherwise partial upstream; that failure mode is not caught by this
  project.
- **Best-effort latency, not an SLA.** Approximate latency is broker/MLS →
  Realtor.com publication delay + time until the next successful poll +
  runner startup/scrape time + Discord delivery time. Only the last two are
  under this project's control. GitHub's own docs say scheduled workflows
  can be delayed or dropped, especially at busy times — a 5-minute cron does
  not mean a 5-minute worst case.
- **HomeHarvest's Realtor.com access is reverse-engineered**, not an
  official API. It has documented history of intermittent 403 blocking from
  some hosting IP ranges. Whether it works reliably from a GitHub-hosted
  runner has not yet been established — see Commissioning below.
- **Repo visibility is public**, deliberately, so the 5-minute schedule stays
  within GitHub's free Actions minutes (a private repo's free quota would be
  exhausted well before that cadence). That means the search criteria,
  `seen.json` history, and Actions logs are all publicly readable. The
  Discord webhook URL is not — it lives only in a GitHub Actions secret and
  is never logged or committed.
- **The scheduled workflow can be auto-disabled after 60 days of repo
  inactivity** (a GitHub platform behavior for public schedules) — check
  Actions history periodically, especially after not touching the repo for
  a while.

## Local setup

```bash
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1      # Windows
python -m pip --disable-pip-version-check install -r requirements.txt
```

`python scan.py` always performs a real Realtor.com fetch — `DRY_RUN=1` only
skips Discord delivery and state writes, it is **not** an offline mode. The
offline, no-network test suite is `tests/test_scan.py` (see Verification
below).

Environment variables:

- `DISCORD_WEBHOOK_URL` — required for any real (`DRY_RUN=0`) run, including
  the very first baseline run. Must be a `https://discord.com/...webhooks/...`
  URL. Never printed in logs.
- `DRY_RUN` — `0` (default) or `1`. Any other value fails immediately.

The script has an internal 150-second execution budget and requires margin
before each Discord POST or rate-limit sleep; on Ubuntu/WSL the GitHub
Actions workflow additionally wraps it in a 180-second `timeout` (see
`.github/workflows/scan.yml`) because the HTTP client itself has no hard
socket deadline. A plain Windows run has neither the external `timeout`
guard nor a socket deadline beyond the script's own budget.

## Verification

Offline (no network, no real Discord/GitHub access) — must all exit 0:

```bash
python -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version"
python -m pip check
python -c "import json; from importlib.metadata import distribution, version; d = json.loads(distribution('homeharvest').read_text('direct_url.json')); assert d['vcs_info']['commit_id'] == '8a6ac96db419b56a18d295935217649039bcdd0a', d['vcs_info']['commit_id']; assert version('requests') == '2.32.4', version('requests'); print('Exact dependency pins verified')"
python -m unittest discover -s tests -v
python -m unittest discover -s tests -p test_scan.py -k offline_cli_dry_run -v
python -c "import scan; s = scan.load_state(); assert s == dict(version=1, initialized=False, seen=[], disabled_webhook_sha256=None, discord_not_before=None)"
git diff --check
git status --short
```

The `load_state()` assertion above is a **one-time pre-commissioning check**
against the freshly-scaffolded, still-uninitialized `seen.json` — it is not
part of the recurring test suite and is expected to stop matching reality
the moment a real baseline is committed. `tests/test_scan.py` validates the
same initial schema through an isolated fixture instead, precisely so the
recurring suite keeps passing after deployment.

Once dependencies are installed, the same real-fetch dry run the workflow
uses can be run locally on Ubuntu/WSL (not plain Windows, which has no
external process-kill guard) — note this still makes a real Realtor.com
request, so it is a live check, not an offline one:

```bash
DRY_RUN=1 timeout --signal=TERM --kill-after=10s 180s python scan.py
```

Live (requires an actual GitHub Actions run and a real Discord channel) —
**outstanding, not yet performed**:

1. Set the repo's default branch to `main`, add the `DISCORD_WEBHOOK_URL`
   secret, confirm the default `GITHUB_TOKEN` can push to `main`, and set the
   target Discord channel/mobile notifications to "All Messages." Confirm
   nothing else — no other workflow, script, or person — pushes to
   `seen.json` or posts to the same webhook; this project has no
   protection against a second writer, so it must be verified true before
   the first real delivery, not just spot-checked later. Keep the schedule
   disabled while doing this and while performing any of the manual repair
   steps below — a scheduled run racing a manual one against the same
   state is not something this project resolves for you.
2. Dispatch the workflow manually with `dry_run=true`. Inspect the run's
   logged counts and manually cross-check a few current Realtor.com results
   for Boca Raton against them. Specifically look for at least one listing
   with an explicit `$0` HOA fee (to confirm the filter actually lets zero
   through), one with a genuinely unknown/blank HOA field, and one with a
   positive HOA fee (to confirm both are correctly excluded). If no
   explicit-zero example exists in the current live inventory to check
   against, record HOA-filter coverage as **unproven**, not passing — do not
   loosen the filter to manufacture a positive test case.
3. Dispatch with `dry_run=false` while `seen.json` is still uninitialized.
   Confirm exactly one commit updating `seen.json`, with zero Discord
   messages sent. If the scheduled cron happens to fire and create this
   baseline first, that's equivalent — the workflow's concurrency group
   serializes runs, so check the Actions history to see which run actually
   did it rather than assuming your manual dispatch was first.
4. Disable the schedule, wait for any in-flight run to finish, remove one
   still-qualifying identity from `seen.json`, commit that, re-enable, and
   dispatch once more. Confirm exactly one real Discord message and one
   state commit; dispatch again and confirm zero further messages.
5. Re-enable the schedule and, over the following days, check Actions run
   history for actual start-time gaps, run durations, whether HomeHarvest is
   being blocked from the runner's IP range, and — separately from those —
   **whether the scheduled runs are actually producing `seen.json` commits**
   (an on-time, successful-looking run that never pushes state is not
   proof of persistence; check the commit history itself, not just the run
   status). Also confirm the repo is still on a public standard runner (no
   private/paid runner or storage got configured). If scraping is
   consistently failing, the service is not operational — this is not fixed
   by adding proxies, alternate scrapers, or relaxing the HOA filter; it
   needs review. Check Actions history periodically (daily is reasonable)
   for the life of the tool, not just during initial commissioning.

If `seen.json` state ever looks wrong (missing, corrupted, or the workflow
fails to push it), disable the schedule, wait for any active run to finish,
then restore the last known-good version from Git history rather than
deleting it — a missing or invalid state file is treated as fatal by design,
not silently reset.

## Recovering from a disabled webhook

A 401/403/404 response from Discord permanently disables further sends
against that specific webhook secret (recorded as a hash in `seen.json`,
never the secret itself) until either:

- **You replace `DISCORD_WEBHOOK_URL`** with a working one (e.g. you
  regenerated the webhook). The very next run computes a different digest,
  which no longer matches the stored one — but that clearing only happens
  **in memory** for that run; it is only *saved* back to `seen.json` once
  that same run reaches a point where it would save anyway (a healthy
  no-candidate recovery, a confirmed delivery, or a newly-latched permanent
  failure). If the run fails before then — most commonly a scrape
  failure, which happens *after* the latch check but before any of those
  save points — the on-disk digest is left exactly as it was, and the next
  run repeats the same in-memory clearing attempt.
- **You fix the same channel/webhook** (e.g. un-blocking a bot, restoring
  channel permissions) without changing the secret itself. In that case
  nothing will auto-clear the latch, since the digest never changes: you
  must manually edit `seen.json` and clear `disabled_webhook_sha256` to
  `null`, leaving `seen` and `discord_not_before` untouched.

Either way, this **never requires clearing `seen.json`'s `seen` list** —
doing so would cause every already-alerted listing to re-alert. A persisted
`discord_not_before` rate-limit gate is independent of the latch and can
extend well past the next scheduled tick (a long 429/exhausted-bucket delay
survives across runs and across a secret replacement) — do not delete or
edit it to force an earlier retry; let it expire naturally. As with any
manual state repair, disable the schedule and wait for any in-flight run to
finish first.
