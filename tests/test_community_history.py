"""What each community advertises, day by day: the lowest price on its page and its offers, homes or no homes."""
import json

from tests.test_agent_tools import conn as _conn
from tools.export_site_data import summarize
from tools.geocode_communities import to_place
from tools.load_records import plan_price
from tools.snapshot_prices import advertised_price, community_changes, lowest_still_advertised

conn = _conn
CASH = {"kind": "flat_cash", "amount_usd": 250000, "rate": None, "label": "Up to $250K in FLEX Cash"}
RATE = {"kind": "perm_rate", "amount_usd": None, "rate": 4.99, "label": "National Sales Event: 4.99%"}
BANNER = {"kind": "unquantified", "amount_usd": None, "rate": None, "label": "Limited-Time Deals"}


def test_builder_placeholder_price_is_no_price():
    assert plan_price(555555) is None                   # Lennar's stand-in on 40 plans in 6 communities
    assert plan_price(1249990) == 1249990
    assert plan_price(None) is None


def test_advertised_price_is_the_lowest_plan_else_the_community_from_price():
    plans = [{"from_price": 1449990}, {"from_price": 1249990}, {"from_price": None}]
    assert advertised_price({"community": {"price_from": 999}, "plans": plans}) == 1249990
    assert advertised_price({"community": {"price_from": 735000}, "plans": []}) == 735000
    assert advertised_price({"community": {"price_from": 555555}, "plans": [{"from_price": 555555}]}) is None
    assert advertised_price({"community": {}}) is None


def test_a_plan_price_we_failed_to_read_is_not_a_price_rise():
    page = "<li>Plan 1C $999,000</li>"
    assert lowest_still_advertised(1599995, 999000, page) == 999000     # The Station: read dropped, page unchanged
    assert lowest_still_advertised(None, 999000, page) == 999000
    assert lowest_still_advertised(1168000, 1148000, "Plan 5 $1,168,000") == 1168000   # the cheap plan really left
    assert lowest_still_advertised(641990, 661990, "was $661,990 now $641,990") == 641990   # a lower price stands
    assert lowest_still_advertised(641990, None, "") == 641990


def test_changes_report_price_moves_and_offers_that_came_and_went():
    rows = [("2026-10-27", 661990, [CASH, BANNER]),
            ("2026-10-28", None, [CASH]),                    # price not read that day: not a change
            ("2026-10-30", 641990, [BANNER]),                # the banner flickers; only quantified offers are tracked
            ("2026-11-03", 641990, [RATE])]
    out = community_changes(rows)
    assert out["tracked_since"] == "2026-10-27" and out["last_checked"] == "2026-11-03"
    assert out["advertised_from"] == 641990
    # since: the last reading before the change, so a gap between readings isn't presented as the day it happened
    assert out["price_changes"] == [{"date": "2026-10-30", "was": 661990, "now": 641990, "since": "2026-10-27"}]
    assert out["incentives_added"] == [{"date": "2026-11-03", "label": RATE["label"]}]
    assert out["incentives_ended"] == [{"last_seen": "2026-10-28", "label": CASH["label"]}]
    assert [i["label"] for i in out["incentives"]] == [RATE["label"]]


def test_an_offer_missed_for_a_day_has_not_ended():
    out = community_changes([("2026-10-27", 1, [CASH]), ("2026-10-28", 1, []), ("2026-10-29", 1, [{**CASH, "amount_usd": 250000.0}])])
    assert out["incentives_added"] == [] and out["incentives_ended"] == []


def test_no_history_is_no_claim():
    assert community_changes([]) == {}


def _add(conn, cid, status, lat=None):
    conn.execute("INSERT INTO communities (id, builder_id, name, city, county_fips, lat, url, status) "
                 "VALUES (?, 1, ?, 'San Bruno', '06081', ?, ?, ?)", (cid, f"C{cid}", lat, f"https://b.example/{cid}", status))
    conn.execute("INSERT INTO plans (community_id, name, beds_max) VALUES (?, 'Plan 1', 4)", (cid,))


def test_every_community_still_selling_gets_a_pin_homes_or_not(conn):
    _add(conn, 81, "selling")
    _add(conn, 82, "coming_soon")
    _add(conn, 83, "sold_out")
    _add(conn, 84, "selling", lat=37.6)
    ids = {r["id"] for r in to_place(conn)}
    assert {81, 82} <= ids and not {83, 84} & ids


def test_the_site_gets_each_communitys_advertised_price_and_changes(conn):
    _add(conn, 81, "selling")
    conn.execute("INSERT INTO community_history (snapshot_date, community_url, from_price, incentives) VALUES "
                 "('2026-11-03', 'https://b.example/81', 1249990, ?)", (json.dumps([RATE]),))
    s = {c["id"]: c for c in summarize(conn, "2026-11-03")}
    assert s[81]["advertised_from"] == 1249990 and s[81]["tracked_since"] == "2026-11-03"
    assert s[81]["plan_beds_max"] == 4 and s[81]["incentives"][0]["label"] == RATE["label"]


def test_a_community_page_gets_the_daily_series_and_every_offer_with_its_dates():
    out = community_changes([("2026-09-01", 700, [CASH]), ("2026-10-27", None, [CASH, BANNER]), ("2026-11-03", 650, [RATE])])
    assert out["series"] == [["2026-09-01", 700], ["2026-11-03", 650]]
    assert out["offers_seen"] == [{"label": CASH["label"], "first_seen": "2026-09-01", "last_seen": "2026-10-27", "active": False},
                                  {"label": RATE["label"], "first_seen": "2026-11-03", "last_seen": "2026-11-03", "active": True}]


def test_the_series_goes_on_the_community_page_not_in_the_site_file(conn, tmp_path):
    from tools.export_site_data import community_detail, export
    _add(conn, 81, "selling")
    conn.execute("INSERT INTO community_history (snapshot_date, community_url, from_price, incentives) VALUES "
                 "('2026-11-03', 'https://b.example/81', 1249990, '[]')")
    s = {c["id"]: c for c in summarize(conn, "2026-11-03")}
    assert "series" not in s[81] and "offers_seen" not in s[81]
    assert community_detail(conn, 81, "2026-11-03")["series"] == [["2026-11-03", 1249990]]
    meta = export(conn, tmp_path / "web", tmp_path / "site.db", today="2026-11-03")
    assert meta["assumptions"]["unadvertised_incentive_pct"] == [0, 3]
    assert meta["assumptions"]["rate_pct_per_point"] == 0.25 and meta["assumptions"]["arm_par_spread_pct"] == 0.5


def test_each_plan_keeps_its_price_by_date_whatever_the_spelling():
    from tools.snapshot_prices import plan_prices, plan_timeline
    rec = {"plans": [{"name": "Plan 1 (Apex)", "from_price": 900}, {"name": "Lennar stub", "from_price": 555555}, {"name": "No price"}]}
    assert plan_prices(rec) == {"Plan 1 (Apex)": 900}
    rows = [("2026-10-27", {"Plan 1 (Apex)": 900}), ("2026-10-28", {}), ("2026-10-30", {"Plan 1 Apex": 900}),
            ("2026-11-03", {"Plan 1 Apex": 850, "Plan 2": 1000})]
    out = plan_timeline(rows)
    assert out["plan1apex"] == {"as_of": "2026-11-03", "first": "2026-10-27",
                                "changes": [{"date": "2026-11-03", "was": 900, "now": 850, "since": "2026-10-30"}]}
    assert out["plan2"] == {"as_of": "2026-11-03", "first": "2026-11-03", "changes": []}


def test_a_home_only_offer_goes_under_its_floor_plan_not_with_the_community_offers(conn):
    from tools.export_site_data import community_detail
    conn.execute("INSERT INTO community_history (snapshot_date, community_url, from_price, incentives, plans) VALUES "
                 "('2026-10-27', 'https://b.example/oak', 900, '[]', ?), ('2026-11-03', 'https://b.example/oak', 850, '[]', ?)",
                 (json.dumps({"Plan 1": 900}), json.dumps({"Plan 1": 850})))
    conn.execute("INSERT INTO incentives (id, scope, scope_id, kind, amount_usd, fine_print, source_url, label) "
                 "VALUES (900, 'home', 1, 'price_cut', 80000, 'f', 'https://b.example/oak', '$80,000 in Savings')")
    d = community_detail(conn, 1, "2026-11-03")
    plan = next(p for p in d["plans"] if p["name"] == "Plan 1")
    assert plan["price_as_of"] == "2026-11-03"
    assert plan["price_changes"] == [{"date": "2026-11-03", "was": 900, "now": 850, "since": "2026-10-27"}]
    assert {"label": "$80,000 in Savings", "address": "1 Oak St"} in plan["home_offers"]
    assert all(i["scope"] == "community" for i in d["community_incentives"])


def test_a_home_with_no_plan_name_finds_its_plan_by_size_when_only_one_fits(conn):
    from tools.export_site_data import plan_of_homes
    conn.execute("INSERT INTO plans (id, community_id, name, sqft_min, sqft_max) VALUES (70, 2, 'A', 1386, 1386), (71, 2, 'B', 1500, 1500), (72, 2, 'C', 1500, 1500)")
    conn.execute("INSERT INTO homes (id, community_id, address, sqft, price_basis) VALUES (70, 2, '1 Bay', 1386, 'quick_move_in'), (71, 2, '2 Bay', 1500, 'quick_move_in'), (72, 2, '3 Bay', 999, 'quick_move_in')")
    got = plan_of_homes(conn, 2)
    assert got[70] == 70 and 71 not in got and 72 not in got     # 1,500 sq ft fits two plans: don't guess
