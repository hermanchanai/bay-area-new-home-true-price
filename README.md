# Bay Area New-Home True Price

What does a new Bay Area home actually cost once the builder's incentives are counted, and what do the
county's records say about the homes already sold in that community?

For production-builder communities in eight counties in and around the Bay Area (Contra Costa, Alameda, Santa Clara,
San Mateo, Solano, Sonoma, Napa and San Joaquin; San Francisco and Marin showed none on the builders we read), this project collects
move-in-ready homes with street addresses or lots, floor plans and incentive
fine print from the builders' own sites. It turns each incentive (flat cash, closing credits,
permanent rate buydowns, 2/1 buydowns, ARM promos) into a dollar value, and shows the result
as a true-price range next to the county assessor's records for homes sold in earlier phases.

Built for the Meta Global AI Developer Hackathon (Indie track, "Build for Your World").

**Live: https://bay-area-true-price.netlify.app**: map, community pages and an agent you can ask
("cheapest 4-bed in Contra Costa after incentives, ready now?").

## In numbers (data read 2026-11-04; refreshed every morning)

- 581 move-in-ready homes in 146 communities from 17 builders; every home has a street address or lot. Another 83 communities
  (20 of them coming soon) show floor plans with nothing ready to move into, and are listed but not counted. Sold-out ones are left off.
- 570 homes priced: incentives cut the asking price by 2.7% with "up to" offers at $0, 2.9% with them in full, at a 7.24%
  market rate. Savings a builder already shows in the price are never counted twice.
- 37 communities show 453 earlier homes' county records (assessed value as published, no estimates); 27 of them
  have enough for a median.
- Extraction accuracy on 43 hand-checked pages: home recall 99.3%; price, beds and sq ft 100%.
  Hold-out builder (never tuned on): 151/151 homes.
- Agent, on frozen 11-03 data: Muse Spark 20/20 hand-checked questions, Muse Spark Open (the live model, on Baseten) 20/20
  ([report](eval/reports/2026-11-04__agent__agent_live.md)). It scored 18/20 until a review caught "biggest discount" ranking by
  percentage; the 20/20 comes after that fix, so it isn't a blind score. The other miss, a citation to a home id that doesn't exist,
  didn't recur, which may be chance. Fireworks' 18/20 for the same model predates the fix and wasn't rerun
  ([report](eval/reports/2026-11-04__agent__agent_open.md)).
- Total Meta model spend for the whole build, daily price checks included: about $11.

## Meta models used

Spend from the build's own ledger (`llm_calls`, repeat calls served from cache cost $0):

| Job | Model and mode | Calls | Cost |
|---|---|---:|---:|
| Extract homes, plans and incentives from ~340 builder pages, one extractor for every site, plus daily re-reads of changed pages | Muse Spark 1.3, contributor | 1,141 | $5.04 |
| Re-extract pages that fail validation | Muse Spark 1.3, standard | 43 | $1.93 |
| Extraction eval on open weights | Muse Spark Open | 43 | $1.04 |
| Buyer agent with 7 read-only database tools (native tool calls), eval runs | Muse Spark Open | 588 | $1.35 |
| Same agent, eval runs | Muse Spark 1.3, contributor | 797 | $0.16 |
| Read room labels off floor-plan images (vision) | Muse Spark 1.3, contributor | 432 | $0.10 |

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

The buyer agent on Muse Spark Open passes all 20 of the same hand-checked questions, after the ranking fix above, with every dollar
figure traced to a tool result ([report](eval/reports/2026-11-04__agent__agent_live.md);
`python -m tools.eval_agent --purpose agent_live`). The site shows the tool calls behind every answer. The eval runs on a frozen copy of the 11-03 data, so the
daily refresh can't move its answers; `python -m tools.derive_agent_truth --freeze` rebuilds the key from
newer data with SQL written apart from the agent's tools. **Since 2026-10-28 the live site's agent runs on Muse Spark
Open**, on Baseten since 2026-11-04, which doesn't store model inputs or outputs by default; `/api/health` names the model.
It moved from Fireworks that morning, when a one-word call took 133 seconds there and new questions timed out.
Visitor questions are capped at $2/day.

Every model call in the product goes to a Meta model. The code itself was written with Claude Code.

## How it works

```
builder sites ──fetch (robots.txt, 2 s/host)──► preprocess (embedded JSON + visible text)
        ──► Muse Spark extract (JSON schema) ──► validate (ranges, quotes must match the page)
        ──fail──► Muse Spark standard ──fail──► review queue
        ──► SQLite: homes, plans, incentives ──► incentive pricing (FRED rate) ──► true price range
county assessor layers (ArcGIS) ──► link parcels by street ──► each home's assessor record + community median
SQLite ──► export ──► static site (Netlify)       SQLite ──► agent API (Flask on Railway, /api proxy)
```

## Daily price check

Every morning `python -m tools.daily_refresh` (a launchd job on the build machine) fetches every builder again,
re-extracts only pages whose prices, amounts or rates changed (pages change other text daily: office hours,
tracking tokens), reprices, records each home's asking and true price in `price_history`, and redeploys.
Model spend is capped at $10 a day on the Meta API; a page that misses extraction keeps its last one, as does a builder whose site
fails that morning (dropped connections are retried first), and a day that loads under 70% of the previous day's
homes is not published. Each home keeps a permanent id, so links and cited answers
stay on the same home. The site shows each home's price history, and the whole series is a CSV:
https://bay-area-true-price.netlify.app/data/price_history.csv

## Data and rules

- Public data only: builders' own public pages, county assessor open data, FRED.
- Only derived facts and a source link are stored. Raw pages, photos and floor-plan images never
  leave the build machine, and incentive fine print is linked, not copied.
- Earlier homes show their county records as published: the assessed value (land + improvements) once it has
  been reset to the last sale. Nothing is estimated. A value
  under 60% of its community's median per sq ft is withheld as possibly part-built, and the page says so (7 today).
- robots.txt honoured. Builders behind a bot challenge or JavaScript-only data were skipped and logged.

## Limits

- Listings and incentives are re-read every morning at 05:30 Pacific; a builder can change either
  in between. A page that could not be fetched keeps its last reading for up to 3 days, marked as carried.
- County records cover Alameda, Contra Costa and San Joaquin only. Santa Clara has no free assessor values, and San Mateo,
  Solano, Sonoma and Napa haven't been pulled. Sonoma's builders list no move-in-ready homes today, so it shows communities only.
- Some builders aren't covered: FCB, D.R. Horton and Richmond American need JavaScript or show a bot challenge, and a few small
  builders (Landsea, Integral and others) weren't added. Most Alameda communities selling now
  have no closed sales on the roll yet, and Alameda's transfer list ends 3/2025.
- An assessed value on a fully assessed new home is usually the sale price, but it's the county's figure: we have no recorded sale prices today. San Joaquin publishes no sale dates, so its homes count once built
  before the roll's lien date; a builder still owning a finished home would show its construction value.
- "Up to" incentives may pay $0, so rankings count them at $0.
- A lower rate is valued at what it would cost to buy: one point (1% of the loan) per 0.25% below the no-points rate. That rate
  is the market rate for a fixed loan and an estimated 0.50% lower for an ARM, because no free daily ARM rate exists; published gaps
  in 2026 ran 0.31 to 0.83. On a $1,074,533 loan at 4.99%, that range moves the value between $61,033 and $83,384.
- Every figure is at 20% down. A financing offer may not apply at every down payment, so the site doesn't pretend to rework it.
- The 0–3% "worth asking" note on community pages is our estimate of what builders may give beyond what they advertise. It isn't
  from any builder and isn't counted in any price.
- Home history in `price_history.csv` before 2026-11-03 used the earlier rate method (payment savings), so compare asking prices across those dates, not true prices.
- A rate offer is only treated as fixed when the builder says it's fixed. Meritage's terms page confirms its
  4.99% is a 7/6 ARM, so it's valued that way. Trumark's 5.25% (5.993% APR) names no loan on any page we read, so it's
  marked "term not stated" and valued like an ARM; that may be too low. KB's 4.99% is an FHA 5/1 ARM and its terms page gives
  no conventional rate, so it's valued like any loan at 20% down; that leaves out FHA mortgage insurance and may be too high. True prices in the history before 11-03 used
  the older reading of these offers.
- Not financial advice. The builder's own page is the source of truth; every figure links to it.

## Status

Each piece when it passed its gate, with the counts of that day. Current figures are at the top.

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
| Parcel linking + county records (`tools/link_parcels.py`, no model) | Day 5 gate PASS: 27 communities show earlier homes' assessor records ([report](eval/reports/2026-10-27__past_sales.md)) |
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

A rate with no loan named isn't assumed to be a 30-year fixed (see Limits). "Up to" amounts are ranges starting at $0. Incentives the builder offers as a choice ("your way")
count once, at the biggest option. The true price is a range: asking price minus the high and low
values.

## Run

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m tools.db                  # create data/trueprice.db
.venv/bin/python -m tools.fetch_market_rate   # latest 30-yr rate
.venv/bin/python -m tools.link_parcels --spot-check   # assessor records per community + live assessor checks
.venv/bin/python -m tools.agent "4 bed under $1.1M true price, ready by January, Contra Costa or San Joaquin"
.venv/bin/python -m tools.eval_agent          # 20-question agent eval -> eval/reports/<date>__agent.md
.venv/bin/python -m tools.geocode_communities # map pins for communities the assessor roll can't place yet
.venv/bin/python -m tools.export_site_data    # web/data/*.json + data/site.db (the live agent's slim DB)
.venv/bin/python -m tools.server              # site + agent API at http://localhost:8787
.venv/bin/python -m tools.build_bundle        # allowlisted deploy folder for the agent API -> .tmp/bundle/
.venv/bin/python -m tools.daily_refresh --no-deploy   # the daily price check, without publishing
```

## Licence

Code: [MIT](LICENSE). Listings, incentives and county records belong to their publishers; follow the source links.

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
- **2026-11-03:** Repo made public after a history scrub (parcel hand checks and test parcels removed). Past
    sales switched from estimates (assessed value with Prop 13 trending backed out) to the records as the
    county publishes them: each earlier home's assessed value, sold date and size, plus Alameda's recorded
    price where there is one; communities show the median assessed value.
    The same day, checking why the average discount had jumped to 6.0%: Meritage's new "rates starting at 4.99% (5.909% APR)"
    banner had been read as a 30-year rate on 69 homes, but its terms page says 7/6 ARM (5.99% for the 30-year fixed).
    Woodside's 4.999% is an FHA loan (5.375% conventional). Known loans now come from a hand-checked terms file, and a
    rate well under its APR with no loan named counts for 5 years; the average is now 4.3%, up to 5.0%. Values that look
    part-built are withheld. Communities with under 5 records list them with no median. The site shows the agent's tool calls.
    Eval re-frozen on the corrected 11-03 data, so the example answers carry the corrected prices: Muse Spark 20/20, Muse Spark Open 18/20.
    Later that day: added San Mateo, Solano, Sonoma and Napa with FHFA's 2026 loan limits (and San Joaquin's, blank until now), K. Hovnanian
    and Brookfield's East Bay page, a list of communities with no move-in-ready homes, and a Google base map. 532 homes, Muse Spark 20/20,
    Muse Spark Open 19/20 on the re-frozen data.
    Last changes that evening: each home shows its sum (asking minus offer = true price). After a live answer called all 10 San Mateo homes one
    community because it saw only 5, the search tool reports every match by community; `compare` now returns the differences so the model adds nothing up.
    A Codex review found sold-out communities in the no-homes list; they're gone and coming-soon ones are labelled. KB's FHA ARM is disclosed under Limits.
    Same frozen data: Muse Spark 20/20, Muse Spark Open 19/20, with a different miss from the earlier run.
    14 communities with homes had no map pin although their builders' pages say where they are; the geocoder now reads those records. 137 of 138 are on the map; Woodside Estates' page gives no location.
    Every community now gets a pin, not just those with a priced home: 215 of 219 communities still selling or coming soon (hollow pin = floor plans only). Each shows its lowest advertised price and how that and its priced offers have moved, from `community_history`, backfilled to Oct 27 from saved readings (Nov 3 for the four newest counties). Older prices aren't on record. Lennar's 555,555 placeholder on 40 plans no longer shows as a price.
    Rebuilt around what no single visit to a builder's site shows. Each community has its own page: advertised price over time, every priced offer with first and last seen dates, then county records of earlier homes. The list is by community and starting price, the map has one color, and every figure is at 20% down. A lower rate is now valued at the points it would cost to buy, which cut the average discount from 4.1% to 2.8%. Fixed along the way: "7/6 ARM, 2-1" was read as 7 and 6 points off the rate (19 offers).
    867 of 1,005 floor plans now link to their own page on the builder's site, read from the community page with no model calls; a plan with no certain link shows none. Meritage, SummerHill, Signature and Ponderosa print no plan links, and old plan spellings the latest reading no longer lists are now removed each day. One sampled link per builder opened (16 of 16 returned 200).
    Each floor plan's price now carries its date and is recorded daily (backfilled to Oct 27 for what the saved readings show), so a plan's price history builds from here. Offers tied to one home sit under that home's floor plan; the incentives section is community-wide only.
    Internet Archive copies of 56 of 219 community pages, from Aug 2 to Oct 21, were read with the same extractor into `community_history` ($0.20), so those communities' history starts earlier than our Oct 27 first check. A price counts only if the copy prints it, a copy with no priced offers says nothing about offers, and a move between readings far apart shows as a range of dates. Lennar, Trumark and Tri Pointe are mostly not archived.
    Added Seeno Homes (7 communities listed, 30 homes) and K. Hovnanian's San Joaquin communities (Manteca, Lathrop, Lodi; 15 homes) from a city-by-city coverage search, on Herman's say. 581 homes, 225 of 229 communities on the map. Seeno's site states no ready dates, so its homes show no "ready now".
    The agent now says once that an assessed value on a fully assessed new home is usually the sale price. Same frozen data, no re-rolls: Muse Spark 20/20, Muse Spark Open 18/20.
- **2026-11-04:** New questions on the live site were timing out: Fireworks took 133 seconds on a one-word call, past Netlify's roughly 28-second proxy limit. The live agent moved to Muse Spark Open on Baseten, where fresh questions answered in about 3 seconds. Same frozen data, one run, no re-rolls: 18/20, with different misses from the Fireworks run.
  A review of the live site found three faults, fixed the same day. "Biggest discount" ranked by percentage, so the agent missed the three largest dollar savings in San Joaquin; search now has a dollar sort with "up to" offers at $0. San Joaquin's county records have no sale dates but read as "earlier sales"; they now say years built, and the eval fails an answer that says otherwise. Rate offers now show their rate on the community page. Evals after the fixes: Muse Spark 20/20, Muse Spark Open on Baseten 20/20.
