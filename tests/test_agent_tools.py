"""agent_tools: ready dates, home search filters, re-pricing with the buyer's own down payment, past sales."""
import json

import pytest

from tools import db
from tools.agent_tools import call, ready_month, reprice_home, search_homes

TODAY = "2026-10-27"


def test_ready_month_reads_every_format_builders_print():
    assert ready_month("2026-12-07", "under_construction", TODAY) == "2026-12"
    assert ready_month("2026-08-09T07:00:00+00:00", "now", TODAY) == "2026-10"     # past date + ready now = now
    assert ready_month("1/31/2027", "under_construction", TODAY) == "2027-01"
    assert ready_month("10/30/2026 12:00:00 AM", "now", TODAY) == "2026-10"
    assert ready_month("February 2027", "month", TODAY) == "2027-02"
    assert ready_month("Nov 2026", "under_construction", TODAY) == "2026-11"
    assert ready_month("Dec/Jan", "month", TODAY) == "2027-01"                     # the later month counts
    assert ready_month("Nov/Dec", "month", TODAY) == "2026-12"
    assert ready_month("Dec. Move In.", "month", TODAY) == "2026-12"               # no year: next one
    assert ready_month("Apr.", "month", TODAY) == "2027-04"
    assert ready_month(None, "now", TODAY) == "2026-10"
    assert ready_month(None, "under_construction", TODAY) is None
    assert ready_month("Spring", "month", TODAY) is None


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    c.execute("INSERT INTO builders (id, name, site) VALUES (1, 'B', 'https://b.example')")
    c.executemany("INSERT INTO communities (id, builder_id, name, city, county_fips, url, product, fetched_at) "
                  "VALUES (?, 1, ?, ?, ?, ?, ?, '2026-10-27')",
                  [(1, "Oak Grove", "Tracy", "06077", "https://b.example/oak", "SFD"),
                   (2, "Bay Lofts", "Dublin, CA", "06001", "https://b.example/lofts", "TH")])
    c.execute("UPDATE communities SET age_restricted = 1 WHERE id = 2")
    layout = {"downstairs_bedroom": True, "primary_floor": 2, "loft": True, "flex_room": False, "garage_bays": 2}
    c.execute("INSERT INTO plans (id, community_id, name, beds_min, beds_max, sqft_min, layout_json) "
              "VALUES (1, 1, 'Plan 1', 4, 4, 2100, ?)", (json.dumps(layout),))
    c.execute("INSERT INTO plans (id, community_id, name, beds_min, beds_max, sqft_min) VALUES (2, 1, 'Plan 2', 5, 5, 2600)")
    homes = [  # id, community, plan, address, price, status, ready, beds
        (1, 1, 1, "1 Oak St", 900_000, "now", None, 4),
        (2, 1, 2, "3 Oak St", 1_000_000, "under_construction", "Feb 2027", 5),
        (3, 1, 1, "5 Oak St", 950_000, "under_construction", "2026-12-15", 4),
        (4, 2, None, "7 Bay Ct", 1_200_000, "now", None, 3),
        (5, 1, 2, "9 Oak St", 1_000_000, "now", None, 5)]
    c.executemany("INSERT INTO homes (id, community_id, plan_id, address, price, price_basis, move_in_status, "
                  "ready_date, beds, baths, sqft, url, fetched_at) VALUES (?,?,?,?,?,'quick_move_in',?,?,?,3,2000,"
                  "'https://b.example/h','2026-10-27')", homes)
    c.executemany("INSERT INTO true_prices VALUES (?,?,?,?,?,?,'2026-10-27')",
                  [(1, 900_000, 20_000, 60_000, 840_000, 880_000), (2, 1_000_000, 0, 0, 1_000_000, 1_000_000),
                   (3, 950_000, 10_000, 10_000, 940_000, 940_000), (4, 1_200_000, 0, 0, 1_200_000, 1_200_000),
                   (5, 1_000_000, 10_000, 300_000, 700_000, 990_000)])   # "up to $300K": $0 guaranteed
    c.execute("INSERT INTO market_rates VALUES ('MORTGAGE30US', '2026-10-22', 6.25, '2026-10-27')")
    c.execute("INSERT INTO incentives (id, scope, scope_id, kind, rate, structure, fine_print, source_url, label) "
              "VALUES (1, 'community', 1, 'perm_rate', 4.99, '30yr fixed', 'fp', 'https://b.example/oak', '4.99% fixed')")
    c.execute("INSERT INTO incentive_values VALUES (1, 1, 6.25, '2026-10-22', 20000, 60000, 'm', '{}', '[]')")
    c.execute("INSERT INTO past_sales VALUES (1, 12, 870000, 820000, 905000, '2024-01 to 2025-09', 'value_backed_out', 12)")
    return c


def addresses(rows):
    return [r["address"] for r in rows["homes"]]


def test_search_ranks_by_the_guaranteed_true_price_and_filters_county_beds_and_ready_by(conn):
    # an "up to" offer may pay $0, so ranking and budgets use the guaranteed end (true_price_high)
    assert addresses(search_homes(conn, today=TODAY)) == ["1 Oak St", "5 Oak St", "9 Oak St", "3 Oak St", "7 Bay Ct"]
    assert addresses(search_homes(conn, county="San Joaquin", min_beds=4, ready_by="2027-01", today=TODAY)) == [
        "1 Oak St", "5 Oak St", "9 Oak St"]
    assert addresses(search_homes(conn, county="alameda", today=TODAY)) == ["7 Bay Ct"]
    assert addresses(search_homes(conn, max_true_price=900_000, today=TODAY)) == ["1 Oak St"]


def test_search_city_ignores_a_state_suffix_and_age_restricted_filters(conn):
    assert addresses(search_homes(conn, city="dublin", today=TODAY)) == ["7 Bay Ct"]
    assert addresses(search_homes(conn, age_restricted=True, today=TODAY)) == ["7 Bay Ct"]
    assert "7 Bay Ct" not in addresses(search_homes(conn, age_restricted=False, today=TODAY))


def test_search_downstairs_bedroom_only_knows_plans_read_from_their_images(conn):
    got = search_homes(conn, downstairs_bedroom=True, today=TODAY)
    assert addresses(got) == ["1 Oak St", "5 Oak St"]
    assert got["note"].startswith("Only homes whose floor plan image was read")


def test_search_results_carry_ids_sources_and_precomputed_discounts(conn):
    h = search_homes(conn, today=TODAY)["homes"][0]
    assert (h["home_id"], h["community_id"], h["true_price_low"], h["true_price_high"]) == (1, 1, 840_000, 880_000)
    assert (h["discount_low"], h["discount_high"], h["discount_pct_high"]) == (20_000, 60_000, 6.7)
    assert h["source_url"] == "https://b.example/h" and h["fetched"] == "2026-10-27"


def test_reprice_reruns_the_math_with_the_buyers_down_payment(conn):
    at20 = reprice_home(conn, 1, down_payment_pct=20)
    at10 = reprice_home(conn, 1, down_payment_pct=10)
    assert at20["asking"] == at10["asking"] == 900_000
    # bigger loan: the builder's cost of the rate cut (PV of savings) grows; the payment-equivalent cut is a
    # ratio of payments, so it doesn't move
    assert at10["incentive_value_low"] > at20["incentive_value_low"]
    assert at10["incentive_value_high"] == at20["incentive_value_high"]
    assert at10["assumptions"]["down_payment_pct"] == 10 and at10["market_rate"] == 6.25
    assert at10["true_price_low"] == 900_000 - at10["incentive_value_high"]


def test_call_dispatches_and_reports_bad_arguments_instead_of_raising(conn):
    assert call(conn, "past_sales", {"community_id": 1})["median_est_price"] == 870_000
    assert call(conn, "past_sales", {"community_id": 2})["past_sales"] is None
    assert "error" in call(conn, "search_homes", {"county": "Napa"})
    assert "error" in call(conn, "no_such_tool", {})
    assert "error" in call(conn, "get_community", {"community_id": 999})


def test_agent_loop_runs_tools_and_returns_the_final_answer(conn):
    from tools.agent import run
    from tools.llm import Result
    replies = [Result("", {}, 0.001, False, "m", "tool_calls", [
                   {"id": "a", "type": "function", "function": {"name": "past_sales", "arguments": '{"community_id": 1}'}},
                   {"id": "b", "type": "function", "function": {"name": "nope", "arguments": "not json"}}]),
               Result("Median $870k [C1]", {}, 0.002, False, "m", "stop")]
    sent = []

    def fake_chat(messages, **kw):
        sent.append([dict(m) for m in messages])
        return replies.pop(0)
    r = run("what did buyers pay at Oak Grove?", conn=conn, chat=fake_chat)
    assert r["answer"] == "Median $870k [C1]" and r["turns"] == 2 and r["cost_usd"] == pytest.approx(0.003)
    assert [t["tool"] for t in r["trace"]] == ["past_sales", "nope"]
    assert r["trace"][0]["result"]["median_est_price"] == 870_000 and "error" in r["trace"][1]["result"]
    tool_msgs = [m for m in sent[1] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["a", "b"]                 # one reply per call, in order


def test_reprice_does_not_stack_offers_unless_the_fine_print_says_they_combine(conn):
    # Plan §6.7 (and load_records.pricing_set): offers are one choice by default; the biggest counts.
    # Lilac at Oakwood Trails: "up to $250K FLEX cash" + a 4.99% ARM -> $467,215 to $660,506, not stacked.
    conn.execute("INSERT INTO incentives (id, scope, scope_id, kind, amount_usd, amount_is_up_to, fine_print, "
                 "source_url, label) VALUES (2, 'home', 5, 'flat_cash', 300000, 1, 'Up to $300K', 'u', 'flex')")
    conn.execute("INSERT INTO incentive_values VALUES (1, 5, 6.25, '2026-10-22', 1, 1, 'm', '{}', '[]')")
    conn.execute("INSERT INTO incentive_values VALUES (2, 5, 6.25, '2026-10-22', 0, 300000, 'm', '{}', '[]')")
    r = reprice_home(conn, 5, down_payment_pct=20)
    rate_cut = next(o for o in r["incentives"] if o["label"] == "4.99% fixed")
    assert r["incentive_value_high"] == 300_000                       # the bigger offer, not the sum
    assert r["incentive_value_low"] == rate_cut["value_low"]          # guaranteed floor: the rate cut
    assert r["true_price_low"] == 700_000
    conn.execute("UPDATE incentives SET fine_print = 'Up to $300K. May be combined with other offers.' WHERE id = 2")
    assert reprice_home(conn, 5, down_payment_pct=20)["incentive_value_high"] > 300_000   # stacks when it says so


def test_search_finds_a_home_by_street_address(conn):
    assert addresses(search_homes(conn, address="9 oak street", today=TODAY)) == ["9 Oak St"]   # suffix spelled out
    assert addresses(search_homes(conn, address="7 Bay Court, Dublin", today=TODAY)) == ["7 Bay Ct"]
    assert addresses(search_homes(conn, address="99 Nowhere Rd", today=TODAY)) == []


def test_ready_month_takes_the_year_written_next_to_the_last_month():
    """Codex review 1 (chunk 4) #7: 'Dec 2026 / Jan 2027' read as 2026-01, so a January 2027 home passed a
    'ready by December' filter."""
    from tools.agent_tools import ready_month
    assert ready_month("Dec 2026 / Jan 2027", "month", "2026-10-30") == "2027-01"
