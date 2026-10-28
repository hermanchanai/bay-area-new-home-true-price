# Workflow: price incentives

**Objective:** turn each home's incentives into a dollar range and a true-price range.

**Inputs:** `homes` + `incentives` rows (from extraction), the latest `market_rates` row,
`config/assumptions.yaml`, the county `conforming_limit_1unit` in `config/counties.yaml`.

**Steps**
1. `python -m tools.fetch_market_rate`, which stores the newest FRED MORTGAGE30US week. Note the date; it is printed
   next to every number.
2. For each home: `tools.price_incentives.true_price(price, incentives, rate, Assumptions(...))`.
   Incentives inherit from community and builder scope unless the fine print limits them.
3. Write `incentive_values` and `true_prices`.

**Rules (all unit-tested):**
- Rate offers: low = builder cost, high = payment-equivalent cut.
- Buydowns and ARMs: only the promo period counts.
- "Up to" amounts: low = $0.
- Closing credits that are "up to" are capped at `closing_cost_pct` of the price.
- One `pool_id` = one choice: worth its best guaranteed value up to its best possible value (the buyer
  can always take the sure offer). Fixed 2026-10-27; it had used the low end of the biggest-ceiling option.
- A promo at or above market is worth $0 (flagged).

**Edge cases**
- `first_year_rate` with no stated note rate: assumed to be a 1-0 buydown, flagged `assumed_1-0`. If the fine print
  gives a note rate, set `note_rate`.
- Missing rate or amount: valued at $0, flagged `missing_input`; never silently dropped.
- FRED stalls on a custom User-Agent; the fetcher uses the default one.

**Loading and pricing every home (Day 4):** `python -m tools.load_records` loads the routed extraction
(communities, plans, homes, incentives), prices every home and writes `eval/reports/<date>__true_prices.md`
(gate: no home with a price and an offer lacks a true-price range). Rules, each pinned by a test:
- A home on several pages is kept once: its single-home page, else the most specific community page.
  Region-wide listing pages never add homes.
- A home gets its own offers and its page's community offers. Builder offers pages only fill in a
  rate-less banner's terms (Taylor Morrison's "CONV 7/6 ARM"); they are never stacked.
- Offers don't stack unless the fine print says they combine (plan §6.7): a home's offers are one
  choice by default. A cut matching was-price minus price is already in the asking price ($0).
- Offers limited to a buyer group (teachers, nurses, police, military, "Nation's Heroes") are shown,
  not counted. "Qualified buyers", "FHA" and "select homes" are open to everyone and count.
- Plans keep their floor-plan reads (`layout_json`) across reloads.
First run 2026-10-27 (market 7.32%, FRED 2026-10-22): 462 homes, 456 priced, 403 with an offer;
average discount 3.6% guaranteed to 6.0% at most. San Joaquin's conforming limit is still null in
`config/counties.yaml`, so its loans never get the jumbo spread (confirm on FHFA).
