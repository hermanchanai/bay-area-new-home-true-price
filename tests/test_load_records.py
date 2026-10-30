"""load_records: dedupe homes across pages, attach incentives, never double-count a cut already in the price."""
import json

import tools.load_records as lr
from tools.load_records import assign_homes, fill_from_offers, home_incentives


def page(name, kind, url, homes=(), incentives=(), community="C"):
    return {"page": name, "builder": "b", "kind": kind, "url": url,
            "record": {"community": {"name": community}, "homes": list(homes), "incentives": list(incentives)}}


def h(address, price=1_000_000, was=None):
    return {"address": address, "lot": None, "price": price, "was_price": was, "beds": 4, "baths": 3, "sqft": 2000}


def inc(kind, scope="community", **kw):
    return {"kind": kind, "scope": scope, "home_address": None, "label": kind, "amount_usd": None,
            "amount_is_up_to": False, "included_in_price": False, "rate": None, "apr": None, "structure": None,
            "lender_condition": None, "eligibility": None, "pool_id": None, "expires": None, "fine_print": None,
            "evidence": "e", **kw}


def test_home_on_master_and_sub_page_goes_to_the_sub_page():
    master = page("b/master", "community", "https://x.com/ca/tracy/tracy-hills", [h("1 A St")])
    sub = page("b/sub", "community", "https://x.com/ca/tracy/tracy-hills/boulder", [h("1 A Street")])
    allhomes = page("b/all", "extra", "https://x.com/all-homes", [h("1 A St"), h("9 Orphan Way")])
    got = assign_homes([master, sub, allhomes])
    assert [(g["page"]["page"], g["home"]["address"]) for g in got] == [("b/sub", "1 A Street")]


def test_single_home_page_attaches_to_its_community_by_name():
    comm = page("b/alder", "community", "https://x.com/state/ca/bay-area/alder-grove", community="Alder Grove")
    one = page("b/2875", "home", "https://x.com/state/ca/bay-area/2875-dawson-creek-st",
               [h("2875 Dawson Creek St")], community="Alder Grove at Manteca")
    stray = page("b/99", "home", "https://x.com/state/ca/bay-area/99-x", [h("99 X St")], community="Nowhere")
    got = assign_homes([comm, one, stray])
    assert [(g["page"]["page"], g["community_page"]["page"]) for g in got] == [("b/2875", "b/alder")]


def test_home_incentives_take_its_own_and_community_offers_and_skip_cuts_already_in_price():
    home = h("1 A St", price=789_925, was=1_089_925)
    p = page("b/c", "community", "https://x.com/c", [home], [
        inc("price_cut", "home", home_address="1 A Street", amount_usd=300_000),    # model forgot included_in_price
        inc("price_cut", "home", home_address="2 B St", amount_usd=50_000),         # someone else's
        inc("flat_cash", amount_usd=250_000, amount_is_up_to=True, pool_id="p1"),
        inc("arm_promo", rate=4.99, structure="7/6 ARM", pool_id="p1"),
    ])
    got = home_incentives(home, p, p)
    assert [(i["kind"], i["included_in_price"]) for i in got] == [
        ("price_cut", True), ("flat_cash", True), ("arm_promo", False)]   # savings shown: up-to cash applied
    assert {i["pool_id"] for i in got if i["pool_id"]} == {"b/c:p1"}   # pools never collide across pages


def test_fill_from_offers_completes_a_rate_less_banner_only():
    banner = inc("arm_promo", label="CONV 7/6 ARM", structure="7/6 ARM")
    offers = [inc("arm_promo", rate=4.25, apr=5.49, structure="7/6 ARM", fine_print="fine print")]
    filled = fill_from_offers([banner, inc("flat_cash", amount_usd=10_000)], offers)
    assert (filled[0]["rate"], filled[0]["apr"], filled[0]["fine_print"]) == (4.25, 5.49, "fine print")
    assert filled[0]["filled_from_offers"] and len(filled) == 2          # offers are never added on their own
    other = fill_from_offers([inc("arm_promo", structure="5/1 ARM")], offers)
    assert other[0]["rate"] is None                                       # different loan: not filled


def test_offers_do_not_stack_unless_the_fine_print_says_so():
    from tools.load_records import pricing_set
    cash = inc("flat_cash", amount_usd=250_000, amount_is_up_to=True)
    arm = inc("arm_promo", rate=4.99, structure="7/6 ARM")
    hero = inc("flat_cash", amount_usd=2_000, eligibility="teachers, nurses, first responders, military")
    cut = {**inc("price_cut", amount_usd=25_000), "included_in_price": True}
    counted, excluded = pricing_set([cash, arm, hero, cut], "h1")
    assert [i["kind"] for i in counted] == ["flat_cash", "arm_promo"]
    assert {i["pool_id"] for i in counted} == {"h1:one-offer"}            # one choice: the biggest counts
    assert [(i["kind"], r) for i, r in excluded] == [("flat_cash", "eligibility_limited"),
                                                     ("price_cut", "in_asking_price")]
    combo = inc("flat_cash", amount_usd=10_000, fine_print="May be combined with the rate offer.")
    counted, _ = pricing_set([combo, arm], "h1")
    assert counted[0]["pool_id"] is None                                  # stated as combinable: stacks
    never = inc("flat_cash", amount_usd=10_000, fine_print="Cannot be combined with any other offer.")
    assert pricing_set([never, arm], "h1")[0][0]["pool_id"] == "h1:one-offer"


def test_only_offers_limited_to_a_buyer_group_are_left_out():
    from tools.load_records import pricing_set
    general = [inc("perm_rate", rate=4.99, eligibility="Qualified buyers"),
               inc("temp_buydown", rate=4.99, structure="2-1", eligibility="new buyers only on select quick move-in homes"),
               inc("arm_promo", rate=4.99, structure="5/1 ARM", eligibility="FHA")]
    group = [inc("closing_credit", amount_usd=2_000, eligibility="Nation's Heroes"),
             inc("flat_cash", amount_usd=2_000, eligibility="active teachers, nurses, EMS, police, firefighters & military")]
    counted, excluded = pricing_set(general + group, "h1")
    assert [i["kind"] for i in counted] == ["perm_rate", "temp_buydown", "arm_promo"]
    assert [r for _, r in excluded] == ["eligibility_limited", "eligibility_limited"]


def test_savings_already_shown_in_the_price_are_the_up_to_cash_offer_applied():
    # Pulte The Square, 400 Mexico Terrace Unit 9 (2026-10-27): "$220,000 in Savings", $1,090,586, was $1,310,586,
    # next to "Up to $250K in FLEX Cash". The $220K is the FLEX Cash already applied: never subtract it again.
    from tools.load_records import pricing_set
    home = h("400 Mexico Terrace, Unit 9", price=1_090_586, was=1_310_586)
    flex = inc("flat_cash", amount_usd=250_000, amount_is_up_to=True, label="Up to $250K in FLEX Cash")
    arm = inc("arm_promo", rate=4.99, structure="7/6 ARM")
    heroes = inc("flat_cash", amount_usd=2_000, label="$2,000 Hometown Heroes incentive")
    p = page("b/c", "community", "https://x.com/c", [home], [flex, arm, heroes])
    got = home_incentives(home, p, p)
    assert [(i["label"], i["included_in_price"]) for i in got] == [
        ("Up to $250K in FLEX Cash", True), ("arm_promo", False), ("$2,000 Hometown Heroes incentive", False)]
    counted, excluded = pricing_set(got, "h1")
    assert [i["kind"] for i in counted] == ["arm_promo"]                  # the rate offer still counts
    assert [(i["label"], r) for i, r in excluded] == [("Up to $250K in FLEX Cash", "in_asking_price"),
                                                      ("$2,000 Hometown Heroes incentive", "eligibility_limited")]
    full = h("1 A St", price=1_000_000)                                  # no savings shown: the cash offer counts
    assert home_incentives(full, page("b/d", "community", "https://x.com/d", [full], [flex]),
                           page("b/d", "community", "https://x.com/d"))[0]["included_in_price"] is False


def test_a_closing_date_guarantee_is_not_a_discount():
    from tools.load_records import pricing_set
    guarantee = inc("flat_cash", amount_usd=5_000, amount_is_up_to=True,
                    label="Closing-Ready Guarantee - reimburse up to $5,000 if not ready")
    counted, excluded = pricing_set([guarantee, inc("perm_rate", rate=5.25)], "h1")
    assert [i["kind"] for i in counted] == ["perm_rate"]
    assert [r for _, r in excluded] == ["conditional"]


def test_a_home_keeps_its_id_across_daily_reloads():
    # [H<id>] citations in saved answers and #h/<id> links must keep pointing at the same home after a reload in
    # which other homes sold or appeared (2026-10-28: 38 of 40 example-answer citations moved to other homes).
    from tools import db
    from tools.load_records import stable_home_id
    conn = db.connect(":memory:")
    conn.execute("INSERT INTO builders (id, name, site) VALUES (1, 'B', 'https://b.example')")
    conn.execute("INSERT INTO communities (id, builder_id, name, url) VALUES (7, 1, 'Oak', 'https://b.example/oak')")
    first = [stable_home_id(conn, 7, a, None) for a in ("1 Oak St", "3 Oak St", "5 Oak St")]
    assert first == [1, 2, 3]
    conn.execute("DELETE FROM homes")                                  # next day's reload: 3 Oak St sold, one new
    assert stable_home_id(conn, 7, "5 Oak Street", None) == 3          # same home, spelled differently
    assert stable_home_id(conn, 7, "9 Oak St", None) == 4              # new home: a new id, never a retired one
    assert stable_home_id(conn, 7, "1 Oak St", None) == 1
    assert stable_home_id(conn, 7, None, "Lot 12") == 5


def test_a_page_carried_without_a_fetch_today_reads_its_page_record_from_the_day_it_was_fetched(tmp_path, monkeypatch):
    """Shea's pages weren't fetched on 10-29 (below floor) but carry 10-28's extraction; the loader takes
    the url / kind / fetched_at from 10-28's manifest instead of failing on a missing key."""
    raw, ex = tmp_path / "raw", tmp_path / "extract"
    monkeypatch.setattr(lr, "RAW", raw)
    monkeypatch.setattr(lr, "EXTRACT", ex)
    (raw / "2026-10-28" / "shea").mkdir(parents=True)
    (raw / "2026-10-28" / "shea" / "manifest.json").write_text(json.dumps(
        [{"file": "a.html", "status": 200, "kind": "community", "url": "https://shea/a", "fetched_at": "2026-10-28T12:00:00+00:00"}]))
    (raw / "2026-10-29" / "shea").mkdir(parents=True)
    (raw / "2026-10-29" / "shea" / "manifest.json").write_text(json.dumps(
        [{"file": None, "status": 0, "kind": "start", "url": "https://shea/start"}]))
    (ex / "2026-10-29" / "m" / "shea").mkdir(parents=True)
    (ex / "2026-10-29" / "m" / "shea" / "a.json").write_text(json.dumps({"community": {"name": "A"}, "homes": []}))
    (ex / "2026-10-29" / "routes.json").write_text(json.dumps(
        [{"page": "shea/a", "model": "m", "needs_review": False, "carried_from": "2026-10-28", "unfetched": True}]))
    [p] = lr.read_pages("2026-10-29")
    assert (p["url"], p["kind"], p["manifest"]["fetched_at"][:10]) == ("https://shea/a", "community", "2026-10-28")
