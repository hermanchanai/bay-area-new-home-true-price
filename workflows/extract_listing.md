# Workflow: extract listings with Muse Spark, then score them

**Objective:** turn each saved builder page into homes (address, price, beds, baths, sq ft), floor
plans and incentives (kind, amount or rate, fine print), and measure how accurate that is against
labels built without any model.

**Run, in order**
1. `.venv/bin/python -m tools.preprocess_page` — HTML → `.tmp/pre/<date>/<builder>/*.md` ($0).
2. `.venv/bin/python -m tools.build_truth` — model-free truth → `eval/truth/*.json` ($0).
3. `.venv/bin/python -m tools.llm --ping` — one tiny call; checks the key and request format.
4. `.venv/bin/python -m tools.extract_listing --truth-pages` — Muse Spark on the eval pages.
5. `.venv/bin/python -m tools.eval_extraction --model <model id>` — scores + `eval/reports/`.
6. `.venv/bin/python -m tools.llm --spend` — spend against the cap; print it in every status update.

**Models and cost** (`config/llm.yaml`): `extract` uses `muse-spark-1.3-contributor`, the same model
in Meta's contributor mode ($0.10 / $0.20 per 1M tokens in/out, 12x cheaper than standard; Meta may
train on the request, which is acceptable because we only send public builder pages). `escalate`
uses `muse-spark-1.3` standard. The budget guard refuses any call whose worst case would push
recorded Meta spend past `cap_usd` ($120 of the $150 credit). Re-runs hit the disk cache and cost $0.

**Truth labels** (`tools/build_truth.py`): homes and plans are parsed with builder-specific rules
(Taylor Morrison's embedded JSON, Trumark's and Pulte's card layouts); every truth home's address and
price string was checked against its page text. Incentive kinds come from text rules, reviewed by
hand page by page on 2026-10-27:
- Trumark "NOTE RATE as low as 5.250% (5.993% APR)*": the page doesn't carry the asterisk's fine
  print, so either `perm_rate` or `unquantified` counts.
- "Flex cash your way" (Taylor Morrison, Pulte) is `flat_cash` or `closing_credit`.
- Pulte's "$2,000* incentive" for teachers, nurses, first responders and military is `flat_cash`.
- Pages with no listed homes (coming soon, sold out, no quick move-ins) have zero truth homes, so an
  invented home shows up as a precision miss.
- Offers / all-homes pages (`kind: extra`) are scored on incentives only.

**Gate (Day 2):** ≥ 90% on home recall and precision, price, beds, sq ft, and incentive-kind recall and
precision. Below that, read the misses in the report before changing anything: a miss can be a
truth-parser error as easily as a model error. Fix whichever is wrong, and record it below.

**Edge cases seen**
- Pulte repeats every quick move-in card (gallery + grid); the price can sit above a long
  calculator disclaimer. Taylor Morrison's visible text is empty (client-rendered): everything is in
  `data-fed-ref` JSON, and its offers' fine print is only on `/make-moves/bay-area`.
- Prices already net of a discount ("Was $1,181,537 / $956,537"): `price` is the shown price and the
  discount is a `price_cut` with `included_in_price = true`, so true-price math never subtracts it twice.
