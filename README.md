# Bay Area New-Home True Price

What does a new Bay Area home actually cost once the builder's incentives are counted, and what did
earlier buyers in that community actually pay?

For every production-builder community in Contra Costa, Alameda, Santa Clara and San Joaquin
counties, this project collects move-in-ready homes with street addresses, floor plans and incentive
fine print from the builders' own sites. It turns each incentive (flat cash, closing credits,
permanent rate buydowns, 2/1 buydowns, ARM promos) into a dollar value, and shows the result
as a true-price range next to assessor-recorded sale values from earlier phases.

Built for the Meta Global AI Developer Hackathon (Indie track, "Build for Your World").

## Meta models used
- **Muse Spark Open** (via Baseten / Modal): bulk extraction of homes, plans and incentives across
  ~20 builder sites with one extractor. *(in progress)*
- **Muse Spark** (Meta Model API): escalation for hard pages, floor-plan vision, and the buyer agent
  with tools. *(in progress)*

Every model call in the product goes to a Meta model. The code itself was written with Claude Code.

## Status

| Piece | State |
|---|---|
| Schema (`migrations/001_init.sql`, `tools/db.py`) | done |
| Incentive pricing (`tools/price_incentives.py`) | done, 17 unit tests |
| Market rate (`tools/fetch_market_rate.py`, FRED MORTGAGE30US) | done |
| Assessor pulls (`tools/pull_assessor.py`): Alameda, Contra Costa, San Joaquin | done, 155k parcels + 14.5k priced Alameda sales |
| Builder page fetch (`tools/fetch_builder_pages.py`): 19 builders | done: 209 community pages + 73 single-home pages; 3 builders skipped (JS-only / bot challenge), logged |
| Page preprocessing (`tools/preprocess_page.py`) | done: 0.1–5 MB of HTML → 3k–31k tokens per page |
| Model client (`tools/llm.py`): cache, cost ledger, budget guard | done |
| Truth labels (`tools/build_truth.py`, no model): 43 pages, 136 homes, 81 plans | done |
| Extraction (`tools/extract_listing.py`) + eval (`tools/eval_extraction.py`) | Muse Spark contributor passes the 90% gate on 43 pages ([report](eval/reports/2026-10-27__muse-spark-1.3-contributor.md)) |
| Validation (`tools/validate_records.py`) + routing (contributor → standard → review) | done; 6 of 286 pages in the review queue |
| Coverage (`tools/coverage_report.py`) | Day 3 gate PASS: 463 unique homes, 100% with address or lot ([report](eval/reports/2026-10-27__coverage.md)) |
| True prices (`tools/load_records.py`): 462 homes loaded, 456 priced | Day 4 gate PASS ([report](eval/reports/2026-10-27__true_prices.md)) |
| Floor plans (`tools/read_floorplan.py`, Muse Spark vision): 107 Lennar plans | 98.7% of printed room labels read exactly ([report](eval/reports/2026-10-27__floorplans.md)) |
| Parcel linking + past sales (`tools/link_parcels.py`, no model) | Day 5 gate PASS: 27 communities show what earlier buyers paid ([report](eval/reports/2026-10-27__past_sales.md)) |
| Buyer agent (`tools/agent.py` + `tools/agent_tools.py`, Muse Spark native tool calls, 7 read-only tools) | Day 6 gate PASS: 20/20 hand-checked questions, every dollar figure traced to a tool result ([report](eval/reports/2026-10-27__agent.md)) |
| Map + web app (`web/`, `tools/server.py`): map of 101 communities, home list, community pages, agent box | Built and checked locally on desktop and a 375 px phone; not deployed yet |

## How incentives become dollars

Assumptions: 20% down, 30-year amortization, savings discounted monthly at the market rate (FRED
MORTGAGE30US; jumbo loans add a spread). Each rate offer gets two figures:

- **Payment-equivalent price cut** (buyer view): the price cut that gives the same monthly payment at the
  market rate.
- **Builder cost** (lender view): the present value of the monthly savings over the period the promo
  actually lasts. That's 30 years for a permanent buydown, 24 months for a 2/1, and the fixed period
  for an ARM.

"Up to" amounts are ranges starting at $0. Incentives the builder offers as a choice ("your way")
count once, at the biggest option. The true price is a range: asking price minus the high and low
values.

## Run

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m tools.db                  # create data/trueprice.db
.venv/bin/python -m tools.fetch_market_rate   # latest 30-yr rate
.venv/bin/python -m tools.link_parcels --spot-check   # past sales per community + live assessor checks
.venv/bin/python -m tools.agent "4 bed under $1.1M true price, ready by January, Contra Costa or San Joaquin"
.venv/bin/python -m tools.eval_agent          # 20-question agent eval -> eval/reports/<date>__agent.md
.venv/bin/python -m tools.geocode_communities # map pins for communities the assessor roll can't place yet
.venv/bin/python -m tools.export_site_data    # web/data/*.json + data/site.db (the live agent's slim DB)
.venv/bin/python -m tools.server              # site + agent API at http://localhost:8787
.venv/bin/python -m tools.build_bundle        # allowlisted deploy folder for the agent API -> .tmp/bundle/
```

## What existed before the event

Before the 2026-10-26 kickoff: research only, no code. That covered a county ranking from Census
population and permits, a roster of builders currently selling in the four counties, and a check
of which county assessors publish free parcel data. All code in this repo was written during the
building period.

## Build log

- **2026-10-26 (after kickoff):**
  - Repo scaffold, SQLite schema (13 tables), incentive pricing engine. Its unit tests were written
    first and seen failing (17 failed against a stub) before the code went in.
  - FRED market-rate fetcher. First pull: 2026-10-22, 7.32%.
- **2026-10-27:**
  - Assessor pulls from public ArcGIS layers: Alameda 80,524 parcels plus 41,603 transfers
    (14,520 with a single-parcel price), Contra Costa 61,689, San Joaquin 12,951. Every run checks
    its row count against the server's count.
  - Builder page fetcher (robots.txt read with our own User-Agent, 2 s per host): Taylor Morrison
    11 communities, Pulte 12, Trumark 18, with zero errors and zero robots refusals.
  - Muse Spark client with disk cache, cost ledger and a hard budget guard; page preprocessor;
    model-free truth labels for 43 pages; extractor with JSON-schema output; eval with a 90% gate.
  - First Muse Spark run (contributor mode, 43 pages, $0.07): home recall 99.3%, precision, price,
    beds and sq ft 100%, incentive kinds 95.7% recall / 100% precision. The first scoring failed at
    80.8% kind precision; reading the misses showed the model was right and five truth rules were
    wrong (fixed test-first, listed in `workflows/extract_listing.md`).
  - Day 3 (same day): 16 more builders; discovery now reads the city from the page itself when URLs
    don't name it, follows Lennar's master plans and Meritage's single-home pages. FCB, D.R. Horton and
    Richmond American skipped (no data without the site's JavaScript; D.R. Horton shows a bot
    challenge). Routed extraction over 286 pages: 10 escalated to Muse Spark standard, 6 left for
    review. Hold-out check on Lennar (truth parsed from its own homesite data, prompt never tuned on
    it): 151/151 homes, price/beds/baths/sq ft 100%. Meta spend to date $2.34.
  - Day 4 (same day): extraction loaded into SQLite (462 homes, 456 priced). Offers don't stack unless the
    fine print says so; cuts already in the asking price and buyer-group offers are shown, not counted.
    Average discount 3.6% guaranteed to 6.0% at most, at a 7.32% market rate. Floor plans: Muse Spark
    vision read 107 Lennar plans, 906/918 printed room labels exact, $0.09. Meta spend to date $2.44.
  - Day 5 (same day): communities linked to assessor parcels by their homes' streets, near the right
    city, in new-home book-pages, in the community's size band (San Joaquin). 27 communities show past
    sales (5 Contra Costa / Alameda, 22 San Joaquin). Alameda's 14.5k recorded prices confirmed the
    Prop 13 rule used everywhere else (value = price × 1.02 per July-1 roll since the sale, 2025-26
    roll). Most Alameda communities selling now have no closed sales yet (lots still use code 0800).
  - Day 6 (same day): buyer agent on Muse Spark with native tool calls (search homes, find/get
    community, plan, past sales, compare, re-price with the buyer's own down payment). Rankings and
    budgets use the guaranteed end of the true price, since an "up to $250K" offer may pay $0. The
    re-pricing tool reproduces all 456 stored true prices at 20% down. 20-question eval: 17/20 on
    the first run, 20/20 after adding address lookup and tightening the prompt; 5 held-out questions
    also checked correct by hand. Whole eval $0.05 in contributor mode.
  - Day 7 (Oct 28): web app. Leaflet map of 101 communities (72 placed by their assessor parcels,
    29 by the builder's own map pin or OpenStreetMap street centres; 15 with streets too new for any
    public map are listed, not pinned), a home list sorted by guaranteed true price, community pages
    (homes with each offer, incentives in dollars, earlier buyers, floor plans with rooms read by Muse
    Spark) and the agent box. Agent citations become numbered sources with the builder page and the
    date it was read. Visitors' questions run in standard mode (Meta doesn't train on them; ~$0.01 a
    question) behind per-IP limits and a $2/day, $10 total cap; the 20 hand-checked eval answers
    are served free as examples. The re-price endpoint reruns the incentive math for any down
    payment. The deployed DB holds listings, prices and community aggregates only, no parcels.
