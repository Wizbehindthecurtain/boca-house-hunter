# Boca House Hunter — Design

## Purpose

Personal tool to catch new single-family home listings in Boca Raton, FL
matching a fixed set of criteria as fast as possible, and push an alert
to Discord the moment one appears.

## Background

Real MLS API access (RETS/RESO Web API) is gated behind MLS board
membership — only licensed agents/brokers or vendor-technology partners
sponsored by one can get credentials. There is no self-serve path for an
individual buyer, and vendors like SimplyRETS require the caller to
already hold MLS credentials before they'll connect. The fastest
realistic alternative without a broker relationship is Realtor.com,
which is operated by the National Association of Realtors and fed
directly by MLS boards nationwide — at or near MLS speed in most
markets, missing only MLS-internal fields (private agent remarks, raw
status history).

[HomeHarvest](https://github.com/ZacharyHampton/HomeHarvest) is an
open-source Python library that scrapes Realtor.com and returns
MLS-shaped fields (price, beds/baths, sqft, hoa_fee, status, etc.) with
native filter parameters — no API key, no scraping logic to write
ourselves.

## Search Criteria (fixed, hardcoded)

| Filter | Value |
|---|---|
| Location | Boca Raton, FL |
| Listing type | `for_sale` |
| Property type | `single_family` |
| Min sqft | 1700 |
| Price range | $250,000–$650,000 |
| HOA | Excluded — any result with a non-zero `hoa_fee` is dropped post-fetch |

These live as constants in `scan.py`. Not building a config file or
CLI flags for them — single fixed search, YAGNI.

## Architecture

One Python script (`scan.py`), triggered on a schedule by GitHub
Actions. No server, no database, no web UI.

```
GitHub Actions cron (every 15 min)
        │
        ▼
   scan.py
        │
        ├─ 1. scrape_property(...) via HomeHarvest → current matching listings
        ├─ 2. drop listings with non-zero hoa_fee
        ├─ 3. load seen.json → diff property_id against current results
        ├─ 4. for each new listing → POST Discord embed via webhook
        └─ 5. write updated seen.json → git commit + push
```

### Why poll-and-diff instead of time-windowed fetch

HomeHarvest supports `past_hours` to fetch only recently-listed
properties, but a straight current-state diff against `seen.json` is
simpler and more robust: it doesn't depend on `list_date` being
accurate or timezone-consistent, and it naturally re-alerts if a
property is delisted and relisted later (a useful signal, not a bug).
Result set size for one city with these filters is small enough
(expected low hundreds at most) that fetching the full current match
set every run is cheap.

## Components

**`scan.py`** — the entire tool. Responsibilities:
- Call `scrape_property()` with the fixed criteria above
- Filter out non-zero `hoa_fee` rows
- Load `seen.json` (a JSON array of `property_id` strings); if missing, treat as empty
- Compute new listings = current results not in `seen.json`
- For each new listing, POST a Discord embed (address, price, beds/full_baths, sqft, `property_url`, `list_date`)
- Append newly-seen `property_id`s to `seen.json` and write it back
- Exit 0 whether or not new listings were found

**`seen.json`** — flat JSON array of `property_id` strings already
alerted on. Committed to the repo by the workflow after each run. No
pruning/expiry logic — file stays small for a single-city search over
a personal house-hunt timeframe (months, not years).

**`.github/workflows/scan.yml`** — GitHub Actions workflow:
- `schedule: cron: '*/15 * * * *'`
- `workflow_dispatch:` trigger too, for manual runs while testing
- Steps: checkout (with a PAT or default `GITHUB_TOKEN` that has write
  permission), set up Python, `pip install homeharvest`, run
  `scan.py`, then commit + push `seen.json` if it changed
- `DISCORD_WEBHOOK_URL` read from a GitHub Actions repository secret

## Error Handling

HomeHarvest's Realtor.com access is reverse-engineered and can raise
`AuthenticationError` or other request failures. `scan.py` wraps the
scrape call in a try/except: on failure, log the error to stdout (visible
in the Actions run log) and exit non-zero **without** touching
`seen.json` or posting to Discord. No retry/backoff logic — the next
scheduled run 15 minutes later is the retry. No failure alerting to
Discord — checking Actions run history is sufficient for a personal
tool; adding failure notifications is unnecessary complexity for a
single-user script.

## Explicitly Out of Scope

- No database, no web UI, no browsable listing archive
- No price-drop / status-change tracking on previously-seen listings
- No multi-area or multi-search support
- No email/SMS channel — Discord only
- No retry/backoff or failure alerting beyond Actions' own run log

## Repo

New private GitHub repo: `Wizbehindthecurtain/boca-house-hunter`,
kept separate from other projects.

## Testing

- Manual local run of `scan.py` against the live Realtor.com scrape
  (via HomeHarvest) with a pre-seeded `seen.json` to confirm filtering
  and Discord posting work end-to-end
- Manual `workflow_dispatch` run in GitHub Actions to confirm the
  scheduled path (checkout → run → commit-back) works before relying
  on the cron trigger
- No unit test suite planned — the logic is a thin, mostly I/O-bound
  script (fetch → filter → diff → post); correctness is verified by
  running it, not by mocking HomeHarvest/Discord
