"""snapshot_prices: one row per home per fetch date, keyed so a reload (new homes.id) continues the same series."""
import csv

from tests.test_agent_tools import conn  # noqa: F401  (fixture)
from tools.snapshot_prices import home_key, snapshot, write_csv


def test_home_key_survives_address_spelling_and_falls_back_to_the_lot():
    assert home_key(1, "https://b.example/oak", "1 Oak Street", None) == home_key(1, "https://b.example/oak", "1 Oak St", None)
    assert home_key(1, "https://b.example/oak", None, " 263 ") == "1|https://b.example/oak|lot 263"


def test_a_series_continues_across_reloads_and_a_rerun_replaces_the_day(conn, tmp_path):  # noqa: F811
    assert snapshot(conn, "2026-10-27") == 5
    with conn:                                   # next day: reload renumbers ids, one price drops, one home sells
        conn.execute("DELETE FROM true_prices")
        conn.execute("DELETE FROM incentive_values")
        conn.execute("UPDATE homes SET id = id + 100")
        conn.execute("UPDATE homes SET price = 880000 WHERE address = '1 Oak St'")
        conn.execute("DELETE FROM homes WHERE address = '7 Bay Ct'")
    assert snapshot(conn, "2026-10-28") == 4
    assert snapshot(conn, "2026-10-28") == 4     # rerun replaces, never duplicates
    series = conn.execute("SELECT snapshot_date, asking FROM price_history WHERE address = '1 Oak St' "
                          "ORDER BY snapshot_date").fetchall()
    assert [tuple(r) for r in series] == [("2026-10-27", 900000), ("2026-10-28", 880000)]
    assert conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] == 9
    first = conn.execute("SELECT true_low, true_high, market_rate, offers FROM price_history "
                         "WHERE address = '1 Oak St' AND snapshot_date = '2026-10-27'").fetchone()
    assert (first[0], first[1], first[2]) == (840000, 880000, 6.25) and '"counted": true' in first[3]
    out = tmp_path / "h.csv"
    assert write_csv(conn, out) == 9
    rows = list(csv.DictReader(out.open()))
    assert rows[0].keys() >= {"snapshot_date", "home_key", "asking", "true_low", "true_high"}
