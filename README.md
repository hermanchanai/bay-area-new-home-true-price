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
| Builder page fetch (`tools/fetch_builder_pages.py`): Taylor Morrison, Pulte, Trumark | done, 41 communities |
| Page preprocessing (`tools/preprocess_page.py`) | done: 0.1–5 MB of HTML → 3k–31k tokens per page |
| Model client (`tools/llm.py`): cache, cost ledger, budget guard | done |
| Truth labels (`tools/build_truth.py`, no model): 43 pages, 136 homes, 81 plans | done |
| Extraction (`tools/extract_listing.py`) + eval (`tools/eval_extraction.py`) | Muse Spark contributor passes the 90% gate on 43 pages ([report](eval/reports/2026-10-27__muse-spark-1.3-contributor.md)) |
| Floor plans, parcel linking, agent, map | next |

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
