"""Record today's asking and true prices into price_history, one row per home per fetch date. $0.

    python -m tools.snapshot_prices --date 2026-10-28    # after load_records for that date
    python -m tools.snapshot_prices --csv web/data/price_history.csv
    python -m tools.snapshot_prices --communities     # every extracted date into community_history

homes is rebuilt on every load, so history is keyed by home_key (builder + community page + address or lot),
which stays the same from day to day. Running it twice for one date replaces that date's rows. A home whose
page was carried from an earlier day (not fetched or not extracted today) is recorded with carried = 1.

community_history keeps what each community advertises (lowest price, offers), homes or no homes. Only offers with an
amount or a rate are compared from day to day: banners with neither come and go with how a page reads.
"""
import argparse
import csv
import json
import re
from pathlib import Path

from tools import db
from tools.eval_extraction import norm_address
from tools.extract_listing import OUT as EXTRACT
from tools.load_records import RAW, plan_price, read_pages

COLUMNS = ("snapshot_date", "home_key", "builder", "community", "city", "county_fips", "address", "lot", "beds",
           "baths", "sqft", "move_in_status", "asking", "was_price", "true_low", "true_high", "market_rate",
           "source_url", "carried")


def home_key(builder_id: int, community_url: str, address: str | None, lot: str | None) -> str:
    where = norm_address(address) if address else f"lot {str(lot).strip().lower()}"
    return f"{builder_id}|{community_url}|{where}"


def carried_pages(date: str) -> list[str]:
    """Pages whose homes that day come from an earlier extraction that today's page didn't confirm: carried
    (not fetched, or not extracted), as opposed to reused because the page's figures were unchanged."""
    routes = EXTRACT / date / "routes.json"
    return [r["page"] for r in json.loads(routes.read_text()) if r.get("carried_from") and not r.get("unchanged")] \
        if routes.exists() else []


def snapshot(conn, date: str, carried: list[str] | None = None) -> int:
    carried = set(carried_pages(date) if carried is None else carried)
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


def advertised_price(record: dict) -> int | None:
    """The lowest plan price on the page, else the page's own 'from' price."""
    prices = [p for pl in record.get("plans") or [] if (p := plan_price(pl.get("from_price")))]
    return min(prices) if prices else plan_price((record.get("community") or {}).get("price_from"))


def plan_prices(record: dict) -> dict[str, int]:
    return {pl["name"]: p for pl in record.get("plans") or [] if pl.get("name") and (p := plan_price(pl.get("from_price")))}


def plan_key(name: str) -> str:
    """'Plan 1 (Apex)' and 'Plan 1 Apex' are one plan, spelled two ways on different days."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def plan_timeline(rows: list[tuple]) -> dict[str, dict]:
    """rows: (date, {plan name: price}), oldest first. plan_key -> first and latest date read, and each price change."""
    out = {}
    for day, prices in rows:
        for name, price in (prices or {}).items():
            t = out.setdefault(plan_key(name), {"as_of": day, "first": day, "changes": [], "price": price})
            if price != t["price"]:
                t["changes"].append({"date": day, "was": t["price"], "now": price})
            t["as_of"], t["price"] = day, price
    return {k: {x: t[x] for x in ("as_of", "first", "changes")} for k, t in out.items()}


def lowest_still_advertised(read: int | None, previous: int | None, page: str) -> int | None:
    """A plan price we failed to read today isn't a rise: the old price stands while the page still shows it."""
    if previous and (read is None or read > previous) and (f"{previous:,}" in page or str(previous) in page):
        return previous
    return read


def snapshot_communities(conn, date: str, raw: Path = RAW) -> int:
    carried = set(carried_pages(date))
    rows = []
    for p in read_pages(date):
        if p["kind"] != "community":
            continue
        last = conn.execute("SELECT from_price FROM community_history WHERE community_url = ? AND snapshot_date < ? "
                            "AND carried = 0 AND from_price IS NOT NULL ORDER BY snapshot_date DESC LIMIT 1",
                            (p["url"], date)).fetchone()
        file = raw / date / f"{p['page']}.html"
        page = file.read_text(errors="ignore") if file.exists() else ""
        offers = [{k: i.get(k) for k in ("label", "kind", "amount_usd", "amount_is_up_to", "rate", "apr", "expires")}
                  for i in p["record"].get("incentives") or [] if i.get("scope") != "home"]
        rows.append((date, p["url"], lowest_still_advertised(advertised_price(p["record"]), last[0] if last else None, page),
                     json.dumps(offers), int(p["page"] in carried), json.dumps(plan_prices(p["record"]))))
    with conn:
        conn.execute("DELETE FROM community_history WHERE snapshot_date = ?", (date,))
        conn.executemany("INSERT OR REPLACE INTO community_history (snapshot_date, community_url, from_price, incentives, "
                         "carried, plans) VALUES (?,?,?,?,?,?)", rows)
    return len(rows)


def community_changes(rows: list[tuple]) -> dict:
    """rows: (date, from_price, offers), oldest first, carried days left out. An offer is new from the first day it was
    seen, if that's after the first day offers were read, and ended after the last, if that's before the latest; a day
    it was missed in between changes nothing. offers None = this copy can't say (fetch_wayback.capture_row)."""
    if not rows:
        return {}
    seen, moves, series, price = {}, [], [], None
    for day, from_price, offers in rows:
        if from_price and price and from_price != price:
            moves.append({"date": day, "was": price, "now": from_price})
        price = from_price or price
        if from_price:
            series.append([day, from_price])
        for i in offers or []:
            if i.get("amount_usd") or i.get("rate"):
                key = (i["kind"], float(i.get("amount_usd") or 0), i.get("rate"))
                seen.setdefault(key, {"first": day, "label": i["label"]})["last"] = day
    first, latest = rows[0][0], rows[-1][0]
    offers_from = next((r[0] for r in rows if r[2] is not None), first)
    return {"tracked_since": first, "last_checked": latest, "advertised_from": price, "price_changes": moves,
            "incentives": rows[-1][2] or [],
            "incentives_added": [{"date": s["first"], "label": s["label"]} for s in seen.values() if s["first"] > offers_from],
            "incentives_ended": [{"last_seen": s["last"], "label": s["label"]} for s in seen.values() if s["last"] < latest],
            "series": series,
            "offers_seen": [{"label": s["label"], "first_seen": s["first"], "last_seen": s["last"], "active": s["last"] == latest}
                            for s in seen.values()]}


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
    ap.add_argument("--communities", action="store_true")
    a = ap.parse_args()
    conn = db.connect()
    if a.date:
        print(f"{snapshot(conn, a.date)} homes recorded for {a.date}")
    if a.communities:
        for day in sorted(d.name for d in EXTRACT.iterdir() if (d / "routes.json").exists()):
            print(f"{snapshot_communities(conn, day)} communities recorded for {day}")
    if a.csv:
        print(f"{write_csv(conn, a.csv)} rows -> {a.csv}")
