# CLAUDE.md — Bay Area New-Home True Price

Hackathon entry (Meta Global AI Developer Hackathon, Indie track, "Build for Your World"; building
period after the 2026-10-26 kickoff to 2026-11-04 9 PM ET). For every production-builder community
in Contra Costa, Alameda, Santa Clara, San Mateo, Solano, Sonoma, Napa and San Joaquin counties: move-in-ready homes with addresses,
floor plans, incentives priced in dollars, a true-price range, and, in the counties we pulled, assessor records
for earlier homes. The build plan is kept outside the repo.

## Rules that bind this repo
- **Built during the event only.** No code copied from any other repo. Pre-event research (the
  2026-10-26 probes) is cited, not copied.
- **Runtime AI is Meta models only** (Muse Spark via the Meta Model API; Muse Spark Open via
  Baseten/Modal). Claude may write the code; the product never calls a non-Meta model.
- **Public data only**: builders' own public pages, county assessor open data, FRED. No employer
  data, no employer branding anywhere (names, copy, commits).
- **Data rights**: commit derived facts plus a source link only. Raw builder HTML, photos and
  floor-plan images stay in gitignored `.tmp/` / `data/raw/`. Earlier homes ship as county records
  only, per home: the assessed value as published and a recorded sale price; never an estimate we derive
  (Herman, 2026-11-03; replaces "community aggregates, never a price per address").
- **Politeness**: honour robots.txt, ~1 request / 2 s per host, never work around a CAPTCHA,
  bot wall or login. Blocked builders are skipped and logged.
- Repo is PRIVATE until the submission flip (target Nov 3), which needs Herman's OK and a secret scan.
- Bug fixes and pricing math are red-test-first.

## Layout (WAT)
- `workflows/` SOPs — read before running a tool.
- `tools/` deterministic scripts, run as modules: `.venv/bin/python -m tools.<name>`.
- `config/` counties, builders, assumptions (prices, model ids and limits live here, not in code).
- `migrations/` SQLite schema, applied by `tools/db.py`. DB at `data/trueprice.db` (gitignored).
- `tests/` pytest; `tests/fixtures/` small hand-made snippets only, never real builder pages.
- `.tmp/` disposable.

## Tests
`.venv/bin/python -m pytest -q`
