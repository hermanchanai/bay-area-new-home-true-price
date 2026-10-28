"""load_records: dedupe homes across pages, attach incentives, never double-count a cut already in the price."""
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
        ("price_cut", True), ("flat_cash", False), ("arm_promo", False)]
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
