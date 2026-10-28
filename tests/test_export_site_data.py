"""export_site_data: the static site's JSON and the live agent's slim DB, both from the same queries."""
import json
import sqlite3

import pytest

from tests.test_agent_tools import conn as _conn
from tools.export_site_data import demo_answers, export, summarize

TODAY = "2026-10-27"
conn = _conn


def test_summary_per_community_uses_the_guaranteed_true_price(conn):
    s = {c["id"]: c for c in summarize(conn, TODAY)}
    oak = s[1]
    assert oak["homes"] == 4 and oak["min_asking"] == 900_000
    assert oak["min_true_price"] == 880_000                 # guaranteed end of the cheapest home
    # biggest sure discount: 1 Oak St, 20,000 of 900,000; "up to $300K" on 9 Oak St counts as 10,000
    assert oak["max_discount_pct"] == 2.2
    assert oak["max_discount_pct_best"] == 30.0             # 9 Oak St if the "up to" pays in full
    assert oak["has_past_sales"] and not s[2]["has_past_sales"]


def test_export_writes_site_json_detail_files_and_a_slim_db(conn, tmp_path):
    conn.execute("INSERT INTO parcels (county, apn, address, total, lat, lng, pulled_at) "
                 "VALUES ('x', '1', '1 Oak St', 900000, 37.7, -121.4, 'd')")
    out = export(conn, tmp_path / "web", tmp_path / "site.db", today=TODAY)
    site = json.loads((tmp_path / "web" / "site.json").read_text())
    assert {c["id"] for c in site["communities"]} == {1, 2}
    assert [h["home_id"] for h in site["homes"]][:2] == [1, 3]                  # cheapest guaranteed first
    assert site["meta"]["market_rate"] == 6.25 and site["meta"]["homes"] == 5
    assert {p["plan_id"]: p["community_id"] for p in site["plans"]} == {1: 1, 2: 1}
    detail = json.loads((tmp_path / "web" / "c" / "1.json").read_text())
    assert detail["past_sales"]["median_est_price"] == 870_000
    assert all("fine_print" not in i for i in detail["incentives"])             # builder text stays out
    slim = sqlite3.connect(tmp_path / "site.db")
    assert slim.execute("SELECT COUNT(*) FROM homes").fetchone()[0] == 5
    assert slim.execute("SELECT COUNT(*) FROM parcels").fetchone()[0] == 0      # per-address data never ships
    assert slim.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0
    assert out["communities"] == 2


def test_demo_answers_keep_only_answers_whose_citations_exist(conn, tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    ok = {"question": "Homes in Tracy?", "answer": "1 Oak St [H1] in Oak Grove [C1]", "score": {"pass": True}}
    stale = {"question": "Old?", "answer": "Gone [H999]", "score": {"pass": True}}
    failed = {"question": "Bad?", "answer": "[H1]", "score": {"pass": False}}
    for i, r in enumerate([ok, stale, failed]):
        (runs / f"q{i}.json").write_text(json.dumps(r))
    assert demo_answers(conn, runs) == {"homes in tracy": {"question": "Homes in Tracy?",
                                                            "answer": "1 Oak St [H1] in Oak Grove [C1]"}}


@pytest.mark.parametrize("q,key", [("  Homes in TRACY? ", "homes in tracy"), ("4 bed, under $1.1M!", "4 bed under $1.1m")])
def test_question_key_ignores_case_spacing_and_punctuation(q, key):
    from tools.export_site_data import question_key
    assert question_key(q) == key


def test_community_pages_carry_each_homes_daily_price_history(conn, tmp_path):
    from tools.snapshot_prices import snapshot
    snapshot(conn, "2026-10-27")
    with conn:
        conn.execute("UPDATE homes SET price = 880000 WHERE address = '1 Oak St'")
        conn.execute("UPDATE true_prices SET asking = 880000, true_high = 860000 WHERE home_id = 1")
    snapshot(conn, "2026-10-28")
    meta = export(conn, tmp_path / "web", tmp_path / "site.db", today=TODAY)
    assert meta["history"] == {"first": "2026-10-27", "last": "2026-10-28", "checks": 2,
                               "price_changed": 1, "gone": 0, "new": 0}
    d = json.loads((tmp_path / "web" / "c" / "1.json").read_text())
    oak = next(h for h in d["homes"] if h["address"] == "1 Oak St")
    assert oak["history"] == [["2026-10-27", 900000, 880000], ["2026-10-28", 880000, 860000]]
