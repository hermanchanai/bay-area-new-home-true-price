# Bay Area New-Home True Price

What does a new Bay Area home actually cost once the builder's incentives are counted, and what did
earlier buyers in that community actually pay?

For every production-builder community in Contra Costa, Alameda, Santa Clara and San Joaquin
counties, this project collects move-in-ready homes with street addresses, floor plans and incentive
fine print from the builders' own sites. It turns each incentive (flat cash, closing credits,
permanent rate buydowns, 2/1 buydowns, ARM promos) into a dollar value, and shows the result
as a true-price range next to assessor-recorded sale values from earlier phases.

Built for the Meta Global AI Developer Hackathon (Indie track, "Build for Your World").

**Live: https://bay-area-true-price.netlify.app**: map, community pages and an agent you can ask
("cheapest 4-bed in Contra Costa after incentives, ready now?").

## In numbers (data read 2026-11-02; refreshed every morning)

- 456 move-in-ready homes in 118 communities from 15 builders; every home has a street address or lot.
- 445 homes priced: incentives cut the asking price by 3.8% guaranteed, up to 4.3% at most, at a 7.24%
  market rate. Savings a builder already shows in the price are never counted twice.
- 27 communities show what earlier buyers paid, from county assessor records.
- Extraction accuracy on 43 hand-checked pages: home recall 99.3%; price, beds and sq ft 100%.
  Hold-out builder (never tuned on): 151/151 homes.
- Agent: 20/20 hand-checked questions on both Muse Spark Open (the live model) and Muse Spark, scored on
  frozen 10-30 data; every dollar figure traced to a database row.
- Total Meta model spend for the whole build, daily price checks included: about $4.

## Meta models used

All on **Muse Spark 1.3** through the Meta Model API:

| Job | Mode | Calls | Cost |
|---|---|---:|---:|
| Extract homes, plans and incentives from ~290 builder pages, one extractor for every site | contributor | 645 | $1.62 |
| Re-extract pages that fail validation | standard | 28 | $0.73 |
| Read room labels off floor-plan images (vision) | contributor | 432 | $0.10 |
| Buyer agent with 7 read-only database tools (native tool calls), eval runs | contributor | 173 | $0.05 |

Contributor mode lets Meta train on the request and is about 12× cheaper. It only ever sees public
builder pages and my own test questions.

**Muse Spark Open** (open weights, on Fireworks) ran the same 43-page extraction eval and also passes
the 90% gate ([report](eval/reports/2026-10-27__accounts_fireworks_models_muse-spark-open__taylor_morrison-pulte-trumark.md),
$1.04). Against Muse Spark contributor on the same pages ([report](eval/reports/2026-10-27__muse-spark-1.3-contributor__taylor_morrison-pulte-trumark.md)):

| Field | Muse Spark (contributor) | Muse Spark Open |
|---|---:|---:|
| Home recall / price / beds / sq ft | 99.3% / 100% / 100% / 100% | 99.3% / 100% / 100% / 100% |
| Baths | 99.3% | 98.5% |
| Incentive kinds: recall / precision | 95.7% / 100% | 96.7% / 97.5% |
| Plan beds | 100% | 95.1% |
| Pages failed | 0 of 43 | 1 of 43 (output ran on to the 32k-token limit) |

The buyer agent on Muse Spark Open passes the same 20 hand-checked questions, 20/20, with every dollar
figure traced to a tool result ([report](eval/reports/2026-10-30__agent__agent_open.md);
`python -m tools.eval_agent --purpose agent_open`). The eval runs on a frozen copy of the 10-30 data, so the
daily refresh can't move its answers; `python -m tools.derive_agent_truth --freeze` rebuilds the key from
newer data with SQL written apart from the agent's tools. **Since 2026-10-28 the live site's agent runs on Muse Spark
Open** via Fireworks, which doesn't store prompts for open models; `/api/health` names the model.
Visitor questions are capped at $2/day.

Every model call in the product goes to a Meta model. The code itself was written with Claude Code.

## How it works

```
builder sites ──fetch (robots.txt, 2 s/host)──► preprocess (embedded JSON + visible text)
        ──► Muse Spark extract (JSON schema) ──► validate (ranges, quotes must match the page)
        ──fail──► Muse Spark standard ──fail──► review queue
        ──► SQLite: homes, plans, incentives ──► incentive pricing (FRED rate) ──► true price range
county assessor layers (ArcGIS) ──► link parcels by street ──► past-sale estimates per community
SQLite ──► export ──► static site (Netlify)       SQLite ──► agent API (Flask on Railway, /api proxy)
```

## Daily price check

Every morning `python -m tools.daily_refresh` (a launchd job on the build machine) fetches every builder again,
re-extracts only pages whose prices, amounts or rates changed (pages change other text daily: office hours,
tracking tokens), reprices, records each home's asking and true price in `price_history`, and redeploys.
Meta spend is capped at $1 a day; a page that misses extraction keeps its last one, as does a builder whose site
fails that morning (dropped connections are retried first), and a day that loads under 70% of the previous day's
homes is not published. Each home keeps a permanent id, so links and cited answers
stay on the same home. The site shows each home's price history, and the whole series is a CSV:
https://bay-area-true-price.netlify.app/data/price_history.csv

## Data and rules

- Public data only: builders' own public pages, county assessor open data, FRED.
- Only derived facts and a source link are stored. Raw pages, photos and floor-plan images never
  leave the build machine, and incentive fine print is linked, not copied.
- Earlier sales are shown as community medians, never a price per address.
- robots.txt honoured. Builders behind a bot challenge or JavaScript-only data were skipped and logged.

## Limits

- Listings and incentives are re-read every morning at 05:30 Pacific; a builder can change either
  in between. A page that could not be fetched keeps its last reading for up to 3 days, marked as carried.
- Santa Clara has no free assessor values, so no past sales there. Most Alameda communities selling now
  have no closed sales on the roll yet.
- "Up to" incentives may pay $0, so rankings use the guaranteed end of the range.
- Not financial advice. The builder's own page is the source of truth; every figure links to it.

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
| Map + web app (`web/`, `tools/server.py`): map of 101 communities, home list, community pages, agent box | **Live: https://bay-area-true-price.netlify.app** (site on Netlify; agent API on Railway behind Netlify's `/api` proxy). Checked on desktop and a 375 px phone; live answers verified against the DB |

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
.venv/bin/python -m tools.daily_refresh --no-deploy   # the daily price check, without publishing
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
    Deployed the same day: Netlify for the static site, Railway for the agent API (the Meta key is a
    Railway variable, never a file). A live question on the deployed site cost $0.007.
  - Day 8 (Oct 28, same day): secret scan of the whole history (gitleaks: no leaks), README for
    public readers. Muse Spark Open (open weights) wired on Fireworks and Baseten: the 43-page
    extraction eval passes the gate ($1.04) and the buyer agent scores 20/20 ($0.14), so the live
    site's agent now runs on Muse Spark Open.
    Pricing fix: a home that already shows savings (Pulte's "$220,000 in Savings" at The Square) has had
    the builder's "up to $250K FLEX Cash" applied, so that offer is no longer subtracted again; closing-date
    guarantees and offers naming a buyer group in their label aren't counted either. Best-case prices
    changed on 80 homes (average best-case discount 6.0% -> 4.1%); guaranteed prices changed on none.
    Agent eval re-derived for the two affected questions, 20/20.
    Daily price check built and scheduled; first daily run (10-28): 319 pages, 93 with changed figures
    re-extracted for $0.68, 2 asking prices changed, 3 homes listed and 4 gone. Homes now keep permanent ids
    (a reload had moved 38 of 40 example-answer citations onto other homes).
- **2026-10-29 / 10-30:** The 10-29 morning run stopped when one builder's site timed out (the laptop slept
    through the fetch). Now a dropped connection is retried, a builder that fails keeps its last prices, and
    the run keeps the machine awake; 10-29 republished with 18 pages carried over. 10-30: 457 homes, 2 price
    changes, 9 sold or withdrawn, 5 new; Pulte ended its up-to-$250K FLEX Cash offer. The agent eval moved
    to a frozen copy of the data with a committed key builder: Muse Spark Open 20/20, Muse Spark 19/20
    (one figure it made up was caught by the trace check).
