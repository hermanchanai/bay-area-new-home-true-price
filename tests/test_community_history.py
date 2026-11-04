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
    assert out["price_changes"] == [{"date": "2026-10-30", "was": 661990, "now": 641990}]
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
