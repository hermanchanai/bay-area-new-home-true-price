"""build_truth parsers on small page excerpts in each builder's layout."""
import json

from tools.build_truth import pulte_truth, tm_truth, trumark_truth

PULTE_ALT_CARD = """\
Upstairs laundry
337 E Beatrice Avenue ,
Mountain House ,
CA
95391
$300,000 in savings
Save
$789,925
Was $1,089,925
00401
homesite #
Available Now
2689
sq. ft.
5
beds
3
baths
2
car garage
View Home Details
"""


def test_pulte_alt_card_layout_parses_home():
    t = pulte_truth("", PULTE_ALT_CARD)
    assert t["homes"] == [{"address": "337 E Beatrice Avenue", "price": 789925, "was_price": 1089925,
                           "beds": 5, "baths": 3.0, "sqft": 2689}]
    assert ["price_cut"] in t["incentive_kinds"]


def test_pulte_appliance_package_and_paid_hoa_are_unquantified_offers():
    text = "### Special Offers\nAppliance Package\nLimited time, special offer on select homes.\n"
    assert ["unquantified"] in pulte_truth("", text)["incentive_kinds"]
    assert ["unquantified"] in pulte_truth("", "Includes up to one year of paid HOA dues\n")["incentive_kinds"]
    assert pulte_truth("", "Nothing on offer\n")["incentive_kinds"] == []


def test_trumark_note_rate_counts_only_in_this_communitys_sections():
    nearby = "# Azure\n## Available Homes\n## Around the Area\nJasmine\nNOTE RATE 5.250%, 5.993% APR*\n"
    own = "# Jasmine\n## Available Homes\nNOTE RATE 5.250%, 5.993% APR*\n## Around the Area\n"
    assert trumark_truth("", nearby)["incentive_kinds"] == []
    assert trumark_truth("", own)["incentive_kinds"] == [["perm_rate", "unquantified"]]


def test_trumark_plan_without_from_price():
    text = ("## Floor Plans\nNow Selling!\nResidence 3\nFrom $1,090,000\n2,087 Sq. Ft.\n3 Beds\n"
            "Temporarily Sold Out!\nResidence 4\n2,164 Sq. Ft.\n3 Beds\n3 Ba, 1 Half Ba\n## Site Map\n")
    assert trumark_truth("", text)["plans"] == [
        {"name": "Residence 3", "from_price": 1090000, "sqft_min": 2087, "beds_max": 3},
        {"name": "Residence 4", "from_price": None, "sqft_min": 2164, "beds_max": 3}]


def test_trumark_savings_badge_is_a_price_cut_without_home_cards():
    text = "### Northern California\n$760,899\n$699,000\n$61K savings!\n"
    assert ["price_cut"] in trumark_truth("", text)["incentive_kinds"]


def _tm_html(*blobs):
    return "".join(f'<script data-fed-ref="x" type="application/json">{json.dumps(b)}</script>' for b in blobs)


def test_tm_arm_banner_without_rate_may_be_unquantified():
    text = '{"title":"CONV 7/6 ARM with Taylor Morrison Home Funding, Inc.*"}'
    assert tm_truth(_tm_html(), text)["incentive_kinds"] == [["arm_promo", "unquantified"]]
    with_rate = "7/6 ARM 4.25% interest rate / 5.49% APR"
    assert tm_truth(_tm_html(), with_rate)["incentive_kinds"] == [["arm_promo"]]
    fine_print = ("The advertised Annual Percentage Rate (APR) of 5.49% APR is calculated using seller incentives "
                  "and is based on a Conventional 7/6 ARM, 30-year fully amortizing term")
    assert tm_truth(_tm_html(), fine_print)["incentive_kinds"] == [["arm_promo"]]


def test_tm_page_without_floorplan_data_leaves_plans_unlabelled():
    # plans described only in prose ("The Residence 1 plan features ...") aren't parseable without a model
    assert tm_truth(_tm_html({"availableHomesTotal": 0, "homes": []}), "")["plans"] is None
    fp = {"floorplans": [{"name": "Plan A", "specs": {"maxBed": 3, "minSqFt": 1500}, "isPrice": 900000}]}
    assert tm_truth(_tm_html(fp), "")["plans"] == [
        {"name": "Plan A", "beds_max": 3, "sqft_min": 1500, "from_price": 900000}]


def _lennar_html(*homesites):
    state = {f"HomesiteType:h_{n}": h for n, h in enumerate(homesites)}
    state["PlanType:p_1"] = {"name": "Residence 1"}
    nd = {"props": {"pageProps": {"initialApolloState": state}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script>'


def test_lennar_truth_reads_homesites_on_this_page_only():
    from tools.build_truth import lennar_truth
    base = "/new-homes/california/san-francisco-bay-area/mountain-house/lakeshore/lugano"
    own = {"address": "1130 N. Salvin Court", "price": 771074, "wasPrice": 857074, "beds": 4, "baths": 3,
           "halfBaths": 1, "sqft": 1829, "status": "UNDER_CONSTRUCTION", "url": base + "/residence-1/25207519032"}
    other = {**own, "address": "9 Elsewhere Way", "url": "/new-homes/california/san-francisco-bay-area/tracy/x/y/1"}
    text = f"# SOURCE https://www.lennar.com{base}\n## PAGE TEXT\n"
    t = lennar_truth(_lennar_html(own, other), text)
    assert t["homes"] == [{"address": "1130 N. Salvin Court", "price": 771074, "was_price": 857074,
                           "beds": 4, "baths": 3.5, "sqft": 1829}]
    assert t["plans"] is None and t["incentive_kinds"] is None   # not labelled for Lennar


def test_eval_skips_unlabelled_incentive_kinds(tmp_path, monkeypatch):
    from tools import eval_extraction as ev
    truth = {"page": "lennar/p", "url": "u", "builder": "lennar", "fetch_date": "2026-10-27", "page_sha256": "x",
             "homes": [{"address": "1 A St", "price": 1, "was_price": None, "beds": 1, "baths": 1.0, "sqft": 1}],
             "plans": None, "incentive_kinds": None}
    (tmp_path / "truth").mkdir()
    (tmp_path / "truth" / "lennar__p.json").write_text(json.dumps(truth))
    out = tmp_path / "extract" / "2026-10-27" / "m" / "lennar"
    out.mkdir(parents=True)
    (out / "p.json").write_text(json.dumps({"homes": truth["homes"], "plans": [], "incentives": [{"kind": "flat_cash"}]}))
    monkeypatch.setattr(ev, "TRUTH", tmp_path / "truth")
    monkeypatch.setattr(ev, "EXTRACT", tmp_path / "extract")
    monkeypatch.setattr(ev, "REPORTS", tmp_path / "reports")
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE eval_results (page_id, field, expected, got, match, run_at, run_id, model)")
    r = ev.evaluate("m", conn=conn)
    assert r["summary"]["home_recall"] == (1, 1) and "incentive_kind_precision" not in r["summary"]
    assert r["passed"]  # unmeasured metrics (kinds not labelled) are not failures
