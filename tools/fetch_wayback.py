"""Older copies of builders' community pages from the Internet Archive's Wayback Machine.

    python -m tools.fetch_wayback --probe              # which community pages have captures in the window
    python -m tools.fetch_wayback --download           # the capture nearest the target date, per page
    python -m tools.fetch_wayback --read               # Muse Spark reads each copy (model spend), into community_history

Herman's OK 2026-11-03: this history predates our own daily checks (from 2026-10-27). Pages stay in gitignored
.tmp/wayback; only derived facts and a link to the capture are kept. One request per 15 s, and it stops rather than
pushes on if the Archive refuses connections or says to slow down.
"""
import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

from tools import db, llm
from tools.extract_listing import SCHEMA, messages_for
from tools.preprocess_page import preprocess
from tools.snapshot_prices import advertised_price, plan_prices

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / ".tmp" / "wayback"
CDX = "https://web.archive.org/cdx/search/cdx"
UA = {"User-Agent": "bay-area-new-home-true-price/0.1 (+https://github.com/hermanchanai/bay-area-new-home-true-price)"}
WINDOW = ("20260801", "20261026")      # before our first daily check
TARGET = "20260903"                    # two months before the first full run
PAUSE_S = 15.0
MAX_FAILS = 3                          # in a row: the Archive is refusing us, so stop and come back later
log = logging.getLogger(__name__)


def nearest(captures: list[str], target: str = TARGET) -> str | None:
    """The capture timestamp closest to the target date."""
    when = datetime.strptime(target, "%Y%m%d")
    return min(captures, key=lambda c: abs(datetime.strptime(c[:8], "%Y%m%d") - when), default=None)


def capture_url(timestamp: str, url: str) -> str:
    """The Archive's copy as the builder served it (id_ = without the Archive's toolbar and rewritten links)."""
    return f"https://web.archive.org/web/{timestamp}id_/{url}"


def capture_row(record: dict, url: str, stamp: str, page: str) -> dict:
    """One community_history row. A price counts only if the copy prints it; a copy with no priced offers says nothing
    about offers, because an old copy can lose what a page drew with scripts."""
    price = advertised_price(record)
    if price and f"{price:,}" not in page and str(price) not in page:
        price = None
    offers = [{k: i.get(k) for k in ("label", "kind", "amount_usd", "amount_is_up_to", "rate", "apr", "expires")}
              for i in record.get("incentives") or [] if i.get("scope") != "home"]
    priced = any(o["amount_usd"] or o["rate"] for o in offers)
    return {"snapshot_date": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}", "community_url": url, "from_price": price,
            "incentives": json.dumps(offers) if priced else None, "source_url": capture_url(stamp, url),
            "plans": json.dumps({n: p for n, p in plan_prices(record).items() if f"{p:,}" in page or str(p) in page})}


def read_captures(conn, budget_usd: float = 2.0) -> dict:
    """Muse Spark reads each downloaded copy, the same extractor as the daily run; rows go in dated by capture."""
    cfg, spent, rows = llm.load_config(), 0.0, 0
    model = cfg["purposes"]["extract"]["model"]
    for meta in sorted((OUT / "pages").glob("*.json")):
        m = json.loads(meta.read_text())
        html = meta.with_suffix(".html").read_text(errors="ignore")
        builder = urlparse(m["url"]).netloc.removeprefix("www.")
        r = llm.chat(messages_for(preprocess(html, m["url"]).text, builder, m["url"], "community"), purpose="extract",
                     model=model, conn=conn, cfg=cfg, response_format={"type": "json_schema", "json_schema": {"name": "listing", "schema": SCHEMA}})
        spent += r.cost_usd
        row = capture_row(llm.parse_json(r.text), m["url"], m["timestamp"], html)
        with conn:
            conn.execute("INSERT OR REPLACE INTO community_history (snapshot_date, community_url, from_price, incentives, "
                         "source_url, plans) VALUES (:snapshot_date, :community_url, :from_price, :incentives, :source_url, :plans)", row)
        rows += 1
        log.info("%s %s from %s, offers %s", m["timestamp"][:8], m["url"][-40:], row["from_price"], "yes" if row["incentives"] else "no")
        if spent > budget_usd:
            log.warning("stopping at $%.2f", spent)
            break
    return {"rows": rows, "spent": spent}


def get(address: str, **params) -> requests.Response:
    time.sleep(PAUSE_S)
    r = requests.get(address, params=params or None, headers=UA, timeout=120)
    if r.status_code == 429:
        raise SystemExit("the Archive asked us to slow down (429): stopping")
    return r


def captures(url: str) -> list[str]:
    r = get(CDX, url=url, output="json", fl="timestamp", filter="statuscode:200", collapse="digest",
            **{"from": WINDOW[0], "to": WINDOW[1]})
    r.raise_for_status()
    rows = r.json() if r.text.strip() else []
    return [row[0] for row in rows[1:]]


def pages(conn) -> list:
    return conn.execute("SELECT c.id, c.name, c.url FROM communities c WHERE c.status IS NOT 'sold_out' AND "
                        "(EXISTS (SELECT 1 FROM homes h WHERE h.community_id = c.id) OR "
                        "EXISTS (SELECT 1 FROM plans p WHERE p.community_id = c.id)) ORDER BY c.id").fetchall()


def probe(conn) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "captures.json"
    found = json.loads(path.read_text()) if path.exists() else {}
    fails = 0
    for c in pages(conn):
        if c["url"] in found:
            continue
        try:
            found[c["url"]] = captures(c["url"])
            fails = 0
        except requests.RequestException as e:
            log.warning("%s: %s", c["url"], type(e).__name__)
            fails += 1
            if fails >= MAX_FAILS:
                log.warning("stopping after %d failures in a row", fails)
                break
            continue
        path.write_text(json.dumps(found, indent=0))
        log.info("%-40s %d captures", c["name"][:40], len(found[c["url"]]))
    return found


def download(conn) -> int:
    found = json.loads((OUT / "captures.json").read_text())
    n = 0
    for c in pages(conn):
        stamp = nearest(found.get(c["url"]) or [])
        file = OUT / "pages" / f"{c['id']}.html"
        if not stamp or file.exists():
            continue
        try:
            r = get(capture_url(stamp, c["url"]))
        except requests.RequestException as e:
            log.warning("stopping: %s", type(e).__name__)
            break
        if r.status_code != 200:
            log.warning("%s %s: HTTP %s", c["name"], stamp, r.status_code)
            continue
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(r.text)
        (OUT / "pages" / f"{c['id']}.json").write_text(json.dumps({"community_id": c["id"], "url": c["url"], "timestamp": stamp}))
        n += 1
        log.info("%-40s %s %d KB", c["name"][:40], stamp, len(r.text) // 1024)
    return n


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--read", action="store_true")
    a = ap.parse_args()
    conn = db.connect()
    if a.probe:
        got = probe(conn)
        print(f"{sum(bool(v) for v in got.values())} of {len(got)} pages have captures")
    if a.download:
        print(f"{download(conn)} pages downloaded")
    if a.read:
        print(read_captures(conn))
