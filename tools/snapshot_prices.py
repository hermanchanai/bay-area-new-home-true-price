"""Record today's asking and true prices into price_history, one row per home per fetch date. $0.

    python -m tools.snapshot_prices --date 2026-10-28    # after load_records for that date
    python -m tools.snapshot_prices --csv web/data/price_history.csv

homes is rebuilt on every load, so history is keyed by home_key (builder + community page + address or lot),
which stays the same from day to day. Running it twice for one date replaces that date's rows. A home whose
page was carried from an earlier day (not fetched or not extracted today) is recorded with carried = 1.
"""
import argparse
import csv
import json
from pathlib import Path

from tools import db
from tools.eval_extraction import norm_address

COLUMNS = ("snapshot_date", "home_key", "builder", "community", "city", "county_fips", "address", "lot", "beds",
           "baths", "sqft", "move_in_status", "asking", "was_price", "true_low", "true_high", "market_rate",
           "source_url", "carried")


def home_key(builder_id: int, community_url: str, address: str | None, lot: str | None) -> str:
    where = norm_address(address) if address else f"lot {str(lot).strip().lower()}"
    return f"{builder_id}|{community_url}|{where}"


def snapshot(conn, date: str, carried: list[str] = ()) -> int:
    carried = set(carried)
    rate = conn.execute("SELECT rate FROM market_rates ORDER BY as_of DESC LIMIT 1").fetchone()
    rows = []
    for h in conn.execute(
            "SELECT h.id, h.address, h.lot, h.beds, h.baths, h.sqft, h.move_in_status, h.price, h.was_price, h.url, "
            "h.source_page, c.builder_id, c.url curl, c.name community, c.city, c.county_fips, b.name builder, "
            "t.true_low, t.true_high FROM homes h JOIN communities c ON c.id = h.community_id "
            "JOIN builders b ON b.id = c.builder_id LEFT JOIN true_prices t ON t.home_id = h.id "
            "WHERE h.address IS NOT NULL OR h.lot IS NOT NULL"):
        offers = [{"label": o["label"], "kind": o["kind"], "value_low": o["value_low"], "value_high": o["value_high"],
                   "counted": "not_counted" not in (o["flags"] or "")}
                  for o in conn.execute("SELECT i.label, i.kind, v.value_low, v.value_high, v.flags FROM incentive_values v "
                                        "JOIN incentives i ON i.id = v.incentive_id WHERE v.home_id = ?", (h["id"],))]
        rows.append((date, home_key(h["builder_id"], h["curl"], h["address"], h["lot"]), h["builder"], h["community"],
                     h["city"], h["county_fips"], h["address"], h["lot"], h["beds"], h["baths"], h["sqft"],
                     h["move_in_status"], h["price"], h["was_price"], h["true_low"], h["true_high"],
                     rate[0] if rate else None, json.dumps(offers), h["url"], int(h["source_page"] in carried)))
    with conn:
        conn.execute("DELETE FROM price_history WHERE snapshot_date = ?", (date,))
        conn.executemany("INSERT OR REPLACE INTO price_history (snapshot_date, home_key, builder, community, city, "
                         "county_fips, address, lot, beds, baths, sqft, move_in_status, asking, was_price, true_low, "
                         "true_high, market_rate, offers, source_url, carried) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         rows)
    return len(rows)


def write_csv(conn, path: Path) -> int:
    rows = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM price_history ORDER BY home_key, snapshot_date").fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerows(tuple(r) for r in rows)
    return len(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--csv", type=Path)
    a = ap.parse_args()
    conn = db.connect()
    if a.date:
        print(f"{snapshot(conn, a.date)} homes recorded for {a.date}")
    if a.csv:
        print(f"{write_csv(conn, a.csv)} rows -> {a.csv}")
