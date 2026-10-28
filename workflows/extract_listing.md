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

**First run, 2026-10-27** (`muse-spark-1.3-contributor`, 43 pages, $0.07, about 3 minutes with 4 workers)
The first scoring failed the gate on incentive-kind precision (80.8%). Every disagreement was checked
against the page text; five were truth-rule errors, fixed test-first in `tests/test_build_truth.py`:
- Pulte's "Appliance Package" (washer, dryer, fridge) and "paid HOA dues" offers had no truth rule;
  both are `unquantified`.
- Pulte Cypress uses a second card layout ("337 E Beatrice Avenue ," / "Mountain House ," / "CA" /
  ZIP, lowercase labels); the parser skipped its 3 homes, which are real ($300K "Was" cuts).
- Trumark's "NOTE RATE" matched on cards for *other* communities under "Around the Area"; offer rules
  now read only the page's own sections. Its all-homes page has "$61K savings!" badges with no home
  cards, now a `price_cut`.
- Trumark "Temporarily Sold Out" plans have no "From $" line; plans now anchor on the sq ft line.
- Taylor Morrison community pages name "CONV 7/6 ARM" in a banner with no rate; by our own rules that
  is `arm_promo` or `unquantified`. Pages with no floorplan JSON (plans only in prose) are unlabelled.
After the fixes: gate PASS (home recall 99.3%, everything else ≥ 95.7%). Remaining misses are the
model's: it skipped the rate-less ARM banner on 4 TM pages, missed one Trumark home (1310 Parkcrest
Circle, a repeated card) and read one Pulte 3.5-bath home as 3.0.
Caution: these truth fixes were found *because* the model disagreed, so this set now flatters the
model a little. Judge Day 3's new builders on fresh truth before trusting the 100% figures.

**Routed run (Day 3).** `python -m tools.extract_listing --builder <key>` (no `--purpose`) runs
`route()`: contributor mode, then `validate_records`; any error or confidence < 0.7 re-runs on Muse
Spark standard; still failing → `needs_review` in `.tmp/extract/<date>/routes.json` (never dropped).
`python -m tools.coverage_report` checks the Day 3 gates and lists the review queue.
Validator rules learned from real pages (each pinned by a test):
- Quotes stitched from a card's separate lines pass when every token is close together on the page
  (a Pulte badge sits ~70 tokens above its address); an invented number still fails.
- Incentives over 25% of the price warn; over 50% fail. Price cuts already in the shown price are
  judged against the original price (Century: was $1,236,615, now $779,000).
- A home's quote drifting is a warning; its address and price must be on the page (errors).
Preprocessor: JSON assigned to JavaScript variables is kept (KB's `var LocalQMIs = [...]`); without it
KB community pages showed only "Move-in Ready (2)" counts.
Hold-out (`--builder lennar`): truth from Lennar's own homesite records, built after the prompt was
frozen: 42 pages, 151/151 homes, every field 100%.
Review queue 2026-10-27, all correct calls: Century's "3.875% … adjusted for the first 5 years" (ARM
terms not stated, 3 pages); SummerHill 28FIFTY, where the model built "2859 Josephine Lane" from photo
file names; three low-confidence pages (Ponderosa, Tri Pointe River Islands).
Zero-home builders checked by hand: Kiper's Echo and The Rose are rentals; Brookfield Rosewood is sold
out; City Ventures and Signature list plans only.

**Floor plans (Day 4):** `python -m tools.read_floorplan` reads Lennar's per-floor SVG floor plans
(rendered to PNG in `.tmp/floorplans/` with macOS `qlmanage`, never committed; re-runs reuse that day's
SVGs) with Muse Spark vision, one call per plan, and writes `plans.layout_json` (rooms, downstairs
bedroom, primary-suite floor, loft, flex room, garage bays). Truth = the SVG's own text labels, paired
with dimensions by position. 2026-10-27: 107 plans, 906/918 dimensioned labels read exactly (98.7%),
$0.09. The plan's 60-110% area check assumed every room is dimensioned; Lennar dimensions only
bedrooms, living rooms and garages (~50% of sq ft), so the area ratio is an outlier flag (35-75%). It
caught the model returning one floor twice, now deduplicated. Other builders' floor plans (PDFs,
images inside JS viewers) are not read yet.
