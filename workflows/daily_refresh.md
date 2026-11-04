# Workflow: daily refresh

**Objective:** re-read every builder each morning, reprice, record each home's price history and publish.

**Run:** `com.hermanchan.true-price-daily` runs `python -m tools.daily_refresh` at 05:30 Pacific (launchd, Mac).
By hand: `.venv/bin/python -m tools.daily_refresh --no-deploy` (no publish). Log: `~/Library/Logs/true-price-daily.log`.

**Steps** (in `tools/daily_refresh.py`): market rate; fetch builders; preprocess; reuse last extraction for pages whose
prices, amounts and rates are unchanged; extract the rest; carry forward what couldn't be read; `load_records`; stop if
under 70% of the previous day's homes; geocode; snapshot; export; deploy Netlify, then the agent API on Railway.

**Limits:** model spend $2 a day (`config/llm.yaml`). A carried page keeps its reading for up to 3 days.

**After a run**
- The site and API update themselves. `web/data/` in the repo does not: commit it by hand to keep the public copy current.
- Read `eval/reports/<date>__true_prices.md`. **A jump in the average discount means a new offer: open its terms.**
  That is how the Meritage ARM was caught on 2026-11-03. New rate offers with no loan named show as "term not stated";
  add the builder's real terms to `config/rate_terms.yaml` when its terms page has them.

- `community_history` records each community's lowest advertised price and page offers daily, homes or no homes
  (backfill: `python -m tools.snapshot_prices --communities`, from saved extractions). A rise is recorded only once the old
  price has left the page; only offers with an amount or rate are compared.

**Not run daily:** assessor pulls and `link_parcels`, so county records change only when those are rerun.
