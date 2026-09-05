# Boca House Hunter

A personal alert tool. It watches Realtor.com (via the open-source
[HomeHarvest](https://github.com/ZacharyHampton/HomeHarvest) library, no MLS
API key) for new for-sale, single-family listings in Boca Raton, FL matching
a fixed set of criteria, and posts a Discord message the moment a new one
appears. Full behavioral contract: `docs/codex-review/2026-09-05-codex-spec.md`.
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
  records every currently-matching listing as already-seen and sends **zero**
  Discord messages — otherwise every existing listing in the city would look
  "new." Only listings that appear *after* that baseline alert.
- **Dedup key is `property_id:listing_id`, not just the property.** The same
  physical property relisted under a new listing ID can alert again; the
  exact same pair reappearing after vanishing does not. A property never
  drops out of the seen-list just because it stops appearing in search
  results.
- **No price-drop or status-change alerts.** Once a listing has alerted, any
  later price or status change on it is silent.
- **Delivery is duplicate-preferring, not exactly-once.** If Discord accepts
  a message but the process dies or the state commit fails before that
  success is saved, the same listing may alert again on a later run. A
  listing is never marked "seen" without an HTTP-confirmed Discord delivery
  first — so silent *misses* shouldn't happen, but occasional *duplicates*
  can.
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
python -m unittest discover -s tests -v
python -m unittest discover -s tests -p test_scan.py -k offline_cli_dry_run -v
python -c "import scan; s = scan.load_state(); assert s == dict(version=1, initialized=False, seen=[], disabled_webhook_sha256=None, discord_not_before=None)"
```

Live (requires an actual GitHub Actions run and a real Discord channel) —
**outstanding, not yet performed**:

1. Set the repo's default branch to `main`, add the `DISCORD_WEBHOOK_URL`
   secret, confirm the default `GITHUB_TOKEN` can push to `main`, and set the
   target Discord channel/mobile notifications to "All Messages." Keep the
   schedule disabled while doing this.
2. Dispatch the workflow manually with `dry_run=true`. Inspect the run's
   logged counts and manually cross-check a few current Realtor.com results
   for Boca Raton against them — including at least one listing with an
   explicit `$0` HOA fee if one exists, to confirm the HOA filter is actually
   discriminating and not passing everything through as "unknown."
3. Dispatch with `dry_run=false` while `seen.json` is still uninitialized.
   Confirm exactly one commit updating `seen.json`, with zero Discord
   messages sent.
4. Disable the schedule, wait for any in-flight run to finish, remove one
   still-qualifying identity from `seen.json`, commit that, re-enable, and
   dispatch once more. Confirm exactly one real Discord message and one
   state commit; dispatch again and confirm zero further messages.
5. Re-enable the schedule and, over the following days, check Actions run
   history for actual start-time gaps, run durations, and whether
   HomeHarvest is being blocked from the runner's IP range. If it's
   consistently failing to scrape, the service is not operational — this is
   not fixed by adding proxies, alternate scrapers, or relaxing the HOA
   filter; it needs review.

If `seen.json` state ever looks wrong (missing, corrupted, or the workflow
fails to push it), restore the last known-good version from Git history
rather than deleting it — a missing or invalid state file is treated as
fatal by design, not silently reset.

## Recovering from a disabled webhook

A 401/403/404 response from Discord permanently disables further sends
against that specific webhook secret (recorded as a hash in `seen.json`,
never the secret itself) until you replace `DISCORD_WEBHOOK_URL` with a
working one. Replacing the secret alone clears this automatically on the
next run — it does not require clearing `seen.json`, and doing so would
cause every already-alerted listing to re-alert.
