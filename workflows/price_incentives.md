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
- One `pool_id` = one choice; the biggest option counts.
- A promo at or above market is worth $0 (flagged).

**Edge cases**
- `first_year_rate` with no stated note rate: assumed to be a 1-0 buydown, flagged `assumed_1-0`. If the fine print
  gives a note rate, set `note_rate`.
- Missing rate or amount: valued at $0, flagged `missing_input`; never silently dropped.
- FRED stalls on a custom User-Agent; the fetcher uses the default one.
