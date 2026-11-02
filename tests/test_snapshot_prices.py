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


def test_a_home_carried_from_an_earlier_fetch_is_marked_so_it_is_not_a_check(conn, tmp_path):  # noqa: F811
    """Codex review 1 (chunk 2) #2: a carried home's old price was recorded as today's check. Its row is kept
    (it is still listed, so it isn't 'gone') but marked carried, in the table and the CSV."""
    with conn:
        conn.execute("UPDATE homes SET source_page = 'b/p' || id")
    snapshot(conn, "2026-10-29", carried=["b/p1"])
    rows = dict(conn.execute("SELECT address, carried FROM price_history WHERE snapshot_date = '2026-10-29'").fetchall())
    assert rows["1 Oak St"] == 1 and sum(rows.values()) == 1
    write_csv(conn, tmp_path / "h.csv")
    assert "carried" in next(csv.reader((tmp_path / "h.csv").open()))


def test_a_rerun_of_the_snapshot_cli_keeps_the_carried_marks(conn, tmp_path, monkeypatch):  # noqa: F811
    """Codex review 2 #6: `snapshot_prices --date` without the carried list marked every home fresh. With no list
    given, carried pages come from that day's routes (carried or reused-unfetched, not unchanged)."""
    import json

    import tools.snapshot_prices as sp
    monkeypatch.setattr(sp, "EXTRACT", tmp_path)
    (tmp_path / "2026-10-29").mkdir()
    (tmp_path / "2026-10-29" / "routes.json").write_text(json.dumps([
        {"page": "b/p1", "carried_from": "2026-10-28", "unfetched": True},
        {"page": "b/p2", "carried_from": "2026-10-27", "unchanged": True}, {"page": "b/p3"}]))
    with conn:
        conn.execute("UPDATE homes SET source_page = 'b/p' || id")
    snapshot(conn, "2026-10-29")
    marks = dict(conn.execute("SELECT address, carried FROM price_history WHERE snapshot_date = '2026-10-29'").fetchall())
    assert marks["1 Oak St"] == 1 and sum(marks.values()) == 1
