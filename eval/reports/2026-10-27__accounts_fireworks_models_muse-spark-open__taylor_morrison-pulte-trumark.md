# Extraction eval: accounts/fireworks/models/muse-spark-open on pages fetched 2026-10-27 (taylor_morrison, pulte, trumark)

Run `dbbc81d4eccd`, 42 pages scored against model-free truth (`tools/build_truth.py`). 1 truth pages had no extraction.

**Gate (every metric ≥ 90%): PASS**

| Metric | Correct | Of | Score |
|---|---:|---:|---:|
| home_baths | 133 | 135 | 98.5% |
| home_beds (gate) | 135 | 135 | 100.0% |
| home_precision (gate) | 135 | 135 | 100.0% |
| home_price (gate) | 135 | 135 | 100.0% |
| home_recall (gate) | 135 | 136 | 99.3% |
| home_sqft (gate) | 135 | 135 | 100.0% |
| incentive_kind_precision (gate) | 77 | 79 | 97.5% |
| incentive_kind_recall (gate) | 89 | 92 | 96.7% |
| plan_beds_max | 77 | 81 | 95.1% |
| plan_from_price | 65 | 65 | 100.0% |
| plan_recall | 81 | 81 | 100.0% |
| plan_sqft_min | 81 | 81 | 100.0% |

## Misses by page

- **pulte/homes__california__bay-area__manteca__lilac-at-oakwood-trails-211184**
  - homes.baths: expected 3.5, got 3 (3348 andean way)
  - homes.baths: expected 3.5, got 3 (1520 torrey dr)
- **taylor_morrison/ca__bay-area__manteca__sage-at-oakwood-trails**
  - incentive kind missed: arm_promo|unquantified
- **taylor_morrison/ca__bay-area__mountain-house__silverleaf-at-lakeshore**
  - incentive kind missed: arm_promo|unquantified
- **taylor_morrison/ca__bay-area__newark__the-emery**
  - incentive kind missed: arm_promo|unquantified
- **trumark/new-homes__ca__dublin__azure-at-francis-ranch**
  - plans.beds_max: expected 4, got None (residence1)
  - plans.beds_max: expected 4, got None (residence2)
  - plans.beds_max: expected 4, got None (residence3)
  - plans.beds_max: expected 4, got None (residence3x)
  - incentive kind not on page: unquantified
- **trumark/new-homes__ca__manteca__dawn-at-the-collective-55**
  - plans.extra_plan: expected None, got 'residence1'
  - plans.extra_plan: expected None, got 'residence2'
  - plans.extra_plan: expected None, got 'residence3'
- **trumark/new-homes__ca__manteca__origin-at-the-collective-55**
  - incentive kind not on page: unquantified
- **trumark/new-homes__ca__manteca__vida-at-the-collective-55**
  - homes.missing: expected '1310 parkcrest cir', got None
