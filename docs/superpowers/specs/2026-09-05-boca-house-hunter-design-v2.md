# Boca House Hunter — Design v2 (supersedes v1)

## Status

This supersedes `2026-09-05-boca-house-hunter-design.md`. That v1 spec is kept
for history, not for implementation — it was reviewed independently by Codex
(model `gpt-6-astra`), which caught a real correctness bug and a real
operational bug in it. Both are fixed here.

## What changed from v1, and why

Astra was given the v1 spec and told to research independently rather than
trust it, then write its own competing spec at
`docs/codex-review/2026-09-05-codex-spec.md`. **That document is now the
canonical implementation spec.** This file records the decisions that make it
authoritative and the amendment made on top of it.

### Bugs v1 had that Astra's spec fixes

1. **Private-repo Actions minutes would not have covered the cadence.**
   GitHub Free gives private repos 2,000 Actions minutes/month, and every
   scheduled run rounds up to at least 1 billable minute. v1's 15-minute cron
   is ~2,880 runs/month — over budget before setup/install time is even
   counted. v1 didn't catch this because it assumed "private repo" and
   "scheduled Actions" were compatible by default; they aren't at this
   cadence on the free tier.
2. **HOA filtering had an unhandled missing-data case.** v1 said "drop any
   listing with non-zero `hoa_fee`." HomeHarvest returns a pandas missing
   value (`NaN`/`None`) when a listing doesn't report an HOA fee at all —
   not zero. Naive truthiness or an `if not hoa_fee` check would treat a
   *missing* fee the same as an *explicit zero*, silently letting
   unknown-HOA-status listings through as if they were confirmed no-HOA.
3. **Dedup key was too coarse.** v1 deduped on `property_id` alone and
   claimed this "naturally re-alerts" a relisted property — it doesn't; an
   append-only seen-list keyed on `property_id` alone would never re-alert
   the same property even on a genuine new listing episode.
4. **Factual error, now corrected**: v1 described Realtor.com as
   "NAR-operated." It's operated by Move, Inc. (a News Corp subsidiary)
   under a data relationship with NAR. This doesn't change the design, but
   the documentation and any future claims about freshness should not repeat
   the inaccurate operator claim.

### Amendment made on top of Astra's spec

Astra's spec is written for a **public** repository (it computed that a
private repo can't sustain a 5-minute cadence on the free tier either — even
tighter than v1's 15-minute assumption — since it recommends 5-minute
polling as the faster, still-free option once public). The user was asked
directly: public-repo-for-speed vs. private-with-reduced-frequency vs.
private-and-pay vs. run-locally-on-own-PC. **Decision: make the repo
public.** This trades repo/criteria/search-history visibility (the
`seen.json` state file, Actions logs, and search parameters are all
world-readable) for zero-cost 5-minute polling. The Discord webhook URL
itself stays secret (GitHub Actions secret, never committed, never logged).
The repo `Wizbehindthecurtain/boca-house-hunter` has been switched to public
to reflect this.

### Rigor level

Astra's spec is written defensively — closer to production-system rigor
(webhook fingerprint latching, byte-exact Discord rate-limit bucket
handling, a monotonic execution budget, strict JSON schema rejection,
UTF-16-safe truncation, conflicting-duplicate-row detection, pinned
dependency commits, exact GitHub Action SHA pins) than a typical personal
script would need. The user was asked whether to right-size this down or
implement it as written, and chose **to implement it in full** rather than
descope it. Section-by-section rationale for each defensive mechanism is in
Astra's spec itself (`docs/codex-review/2026-09-05-codex-spec.md`,
particularly §4–§9) and is not repeated here.

## Canonical spec

**`docs/codex-review/2026-09-05-codex-spec.md`, sections 1–11, is the
implementation contract**, with one substitution: wherever that document
notes the public/private decision as still-open, it is now resolved as
**public**, which is already true of the shipped repo. Nothing else in that
document is amended. In particular, the implementer must follow, as written:

- §3 exact file layout and pinned `requirements.txt` (HomeHarvest pinned to
  commit `8a6ac96db419b56a18d295935217649039bcdd0a`, not a floating version)
- §4 exact `scrape_property()` call and its row-eligibility contract
  (identity validation, HOA-explicit-zero-only rule, duplicate-row conflict
  handling)
- §5 exact `seen.json` schema, atomic-write procedure, and the
  confirmed-delivery-before-seen rule
- §6 exact Discord webhook contract (URL validation/canonicalization,
  payload shape, rate-limit and permanent-failure handling)
- §7 entry point, logging, and the boundary/exception table
- §8 exact GitHub Actions workflow YAML
- §9 the required verification/commissioning sequence — this project is not
  considered done until those checks pass against a live GitHub Actions run
  and a live Discord channel, since no live scrape or delivery was performed
  during either spec's research
- §10 explicit non-goals — hold the line on these; this is still a personal
  single-search tool, not a platform
- §11 evidence links, for anyone auditing a claim later

## Self-review

- **Placeholders**: none in the canonical spec; it was already commissioned
  with explicit "not yet verified live" caveats rather than TBDs.
- **Consistency**: the public-repo decision matches the workflow guard
  already written into §8 (`github.event.repository.private == false`) —
  Astra's spec anticipated this outcome rather than assuming either answer.
- **Scope**: single implementation plan is appropriate — one script, one
  workflow, one state file, no sub-projects to decompose.
- **Ambiguity**: none identified beyond what §9 already flags as requiring
  live commissioning to resolve (whether HomeHarvest actually works
  un-blocked from a GitHub-hosted runner — a real open risk, not a spec gap).
