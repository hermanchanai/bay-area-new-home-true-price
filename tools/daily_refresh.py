"""Daily price check: fetch every builder, re-extract what changed, record dated prices, redeploy.

    python -m tools.daily_refresh                 # today, then deploy the site (Netlify) and agent API (Railway)
    python -m tools.daily_refresh --no-deploy     # everything up to the export; nothing published
    python -m tools.daily_refresh --skip-fetch    # rerun today from the pages already fetched

Run by launchd every morning at 05:30 (~/Library/LaunchAgents/com.hermanchan.true-price-daily.plist; log in
~/Library/Logs/true-price-daily.log and .tmp/logs/daily_<date>.log). The fetch takes ~15 minutes. Steps:
  1. market rate (FRED)         2. fetch all builders        3. preprocess
  4. reuse: a page whose prices, amounts and rates are the same as at its last extraction reuses that
     extraction ($0). Pages change text every day (office hours, tracking tokens), so the text itself can't
     be the test; on 10-27 -> 10-28, 226 of 319 pages had the same figures.
  5. extract the rest (Meta daily cap in config/llm.yaml stops new calls; the run goes on)
  6. carry forward: a page whose extraction didn't run today (cap, error) reuses its latest earlier one,
     so its homes don't vanish for a day; listed in the log. Same for pages not fetched today: every page of
     a builder below its floor (10-29: Shea's start page timed out) and a page whose fetch failed (no answer
     or 5xx; a 404 or a page the builder stopped listing is gone and is not carried), for at most
     MAX_CARRY_DAYS after the page was last fetched. Carried homes are marked in price_history and don't count
     toward the guard. A rerun first drops the day's carried routes, so pages fetched since are used.
  7. load + price               8. map pins for new communities          9. snapshot into price_history
  10. export site data + price_history.csv                                 11. deploy
A builder below its floor makes the fetcher exit 2; the day goes on (its pages are carried, step 6). Any
other failure of the fetcher stops the run.
The run holds a `caffeinate` assertion so the Mac doesn't sleep mid-fetch (10-29: the 15-minute fetch took
4 hours in dark-wake bursts, and the connections dropped as it slept).
Guard: if today's load has fewer than MIN_SHARE of the last snapshot's homes from pages fetched today (a blocked fetch, a bad parse),
the run stops before the snapshot and nothing is published.
"""
import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import date as Date
from pathlib import Path

import yaml

from tools import db
from tools.fetch_builder_pages import floor_ok, page_filename
from tools.extract_listing import OUT as EXTRACT
from tools.preprocess_page import PRE, RAW
from tools.snapshot_prices import carried_pages, snapshot, write_csv

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / ".tmp" / "logs"
HISTORY_CSV = ROOT / "web" / "data" / "price_history.csv"
MIN_SHARE = 0.7
MAX_CARRY_DAYS = 3                   # a page not fetched for longer than this drops its homes (sold or gone)
FLOOR_FAILED = 2                     # fetcher exit code: some builder below its floor; anything else nonzero = crash
log = logging.getLogger("daily_refresh")


def step(*args: str, check: bool = True) -> int:
    log.info("$ %s", " ".join(args))
    return subprocess.run(args, cwd=ROOT, check=check).returncode


def py(module: str, *args: str, check: bool = True) -> int:
    return step(sys.executable, "-m", module, *args, check=check)


def expected_communities() -> dict[str, int]:
    cfg = yaml.safe_load((ROOT / "config" / "builders.yaml").read_text())
    return {b["key"]: b["expected_communities"] for b in cfg["builders"] if b.get("enabled", True)}


MONEY = re.compile(r"\$\s?\d{1,3}(?:,\d{3})+|\$\s?\d+(?:\.\d+)?\s?[kKmM]\b|\b\d+(?:\.\d+)?\s?%")
BARE = re.compile(r"(?<![\w.\-])[1-9]\d{5,7}(?![\w.\-])")      # 100,000-99,999,999 unformatted: prices in page data


def fingerprint(text: str) -> list[str]:
    """Every dollar amount, percentage and price-sized number on the page, sorted. Same list = same listing."""
    return sorted([m.replace(" ", "") for m in MONEY.findall(text)] + BARE.findall(text))


def _fetched(day: str, raw: Path) -> list[str]:
    return [f"{m.parent.name}/{r['file'].removesuffix('.html')}" for m in sorted((raw / day).glob("*/manifest.json"))
            for r in json.loads(m.read_text())
            if r.get("file") and r.get("status") == 200 and r.get("kind") in ("community", "extra", "home")]


def _latest_route(page: str, day: str, extract: Path) -> tuple[str, dict] | None:
    for d in sorted((d for d in extract.iterdir() if d.is_dir() and d.name < day), reverse=True):
        routes = d / "routes.json"
        if routes.exists():
            for r in json.loads(routes.read_text()):
                if r["page"] == page:
                    return d.name, r
    return None


def _reuse(day: str, src_day: str, r: dict, extract: Path, **extra) -> dict:
    dst = extract / day / r["model"] / f"{r['page']}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(extract / src_day / r["model"] / f"{r['page']}.json", dst)
    return {**r, "carried_from": r.get("carried_from") or src_day, **extra}


def drop_carried(day: str, extract: Path = EXTRACT) -> list[str]:
    """Remove the day's carried and reused routes before a (re)run; the run recomputes them from what it has
    now, so a page fetched since the last run is extracted instead of keeping its old extraction."""
    routes_path = extract / day / "routes.json"
    if not routes_path.exists():
        return []
    routes = json.loads(routes_path.read_text())
    keep = [r for r in routes if not r.get("carried_from")]
    routes_path.write_text(json.dumps(keep, indent=1))
    return [r["page"] for r in routes if r.get("carried_from")]


def reuse_unchanged(day: str, raw: Path = RAW, pre: Path = PRE, extract: Path = EXTRACT) -> list[str]:
    """Pages fetched on `day` whose figures match the day they were last extracted from: reuse, $0."""
    routes_path = extract / day / "routes.json"
    routes = {r["page"]: r for r in json.loads(routes_path.read_text())} if routes_path.exists() else {}
    reused = []
    for page in _fetched(day, raw):
        today_md = pre / day / f"{page}.md"
        found = _latest_route(page, day, extract)
        if page in routes or not today_md.exists() or not found:
            continue
        src_day, r = found
        then_md = pre / (r.get("carried_from") or src_day) / f"{page}.md"    # the page the extraction was made from
        if then_md.exists() and fingerprint(then_md.read_text()) == fingerprint(today_md.read_text()):
            routes[page] = _reuse(day, src_day, r, extract, unchanged=True, unfetched=False, last_fetched=day)
            reused.append(page)
    routes_path.parent.mkdir(parents=True, exist_ok=True)
    routes_path.write_text(json.dumps(sorted(routes.values(), key=lambda r: r["page"]), indent=1))
    return reused


def carry_forward(day: str, raw: Path = RAW, extract: Path = EXTRACT) -> list[str]:
    """Pages fetched on `day` with no extraction get their latest earlier one. Returns the pages carried."""
    routes_path = extract / day / "routes.json"
    routes = json.loads(routes_path.read_text()) if routes_path.exists() else []
    have = {r["page"] for r in routes}
    carried = []
    for page in _fetched(day, raw):
        if page not in have and (found := _latest_route(page, day, extract)):
            routes.append(_reuse(day, *found, extract, unfetched=False, last_fetched=day))
            carried.append(page)
    routes_path.parent.mkdir(parents=True, exist_ok=True)
    routes_path.write_text(json.dumps(sorted(routes, key=lambda r: r["page"]), indent=1))
    return carried


def _latest_builder_routes(builder: str, day: str, extract: Path) -> list[tuple[str, dict]]:
    for d in sorted((d for d in extract.iterdir() if d.is_dir() and d.name < day), reverse=True):
        routes = d / "routes.json"
        if routes.exists() and (mine := [r for r in json.loads(routes.read_text()) if r["page"].startswith(f"{builder}/")]):
            return [(d.name, r) for r in mine]
    return []


def _page(builder: str, url: str) -> str:
    return f"{builder}/{page_filename(url).removesuffix('.html')}"


def carry_unfetched(day: str, expected: dict[str, int], raw: Path = RAW, extract: Path = EXTRACT) -> list[str]:
    """Pages with no fetch today keep their latest extraction: all of a builder's pages when its listing failed
    (no manifest, a start/city page with no answer or a 5xx, or no community page fetched), and any page whose
    own fetch got no answer or a 5xx; never a page that 404'd today, never a page a working listing no longer
    shows, and never past MAX_CARRY_DAYS since the page was last fetched. Returns the pages carried."""
    routes_path = extract / day / "routes.json"
    routes = json.loads(routes_path.read_text()) if routes_path.exists() else []
    have = {r["page"] for r in routes}
    todo: list[tuple[str, dict]] = []
    gone = set()
    for builder, exp in expected.items():
        man = raw / day / builder / "manifest.json"
        recs = json.loads(man.read_text()) if man.exists() else []
        gone |= {_page(builder, r["url"]) for r in recs if r["status"] in (404, 410)}
        got = sum(r["kind"] == "community" and r["status"] == 200 for r in recs)
        listing_failed = not got or any(r["kind"] in ("start", "city") and (r["status"] == 0 or r["status"] >= 500)
                                        for r in recs)
        if listing_failed:
            log.warning("%s listing failed today (%d of %d communities): keeping its last extraction", builder, got, exp)
            todo += _latest_builder_routes(builder, day, extract)
            continue
        if not floor_ok(got, exp):
            log.warning("%s lists %d of %d expected communities; the rest are taken as gone", builder, got, exp)
        for r in recs:
            if r["status"] == 0 or r["status"] >= 500:
                todo += [found] if (found := _latest_route(_page(builder, r["url"]), day, extract)) else []
    carried = []
    for src_day, r in todo:
        # routes from before last_fetched existed: an unfetched one was last fetched no later than its extraction
        seen = r.get("last_fetched") or (r.get("carried_from") if r.get("unfetched") else None) or src_day
        if r["page"] in have or r["page"] in gone:
            continue
        if (Date.fromisoformat(day) - Date.fromisoformat(seen)).days > MAX_CARRY_DAYS:
            log.warning("%s not fetched since %s: its homes are dropped", r["page"], seen)
            continue
        routes.append(_reuse(day, src_day, r, extract, unfetched=True, last_fetched=seen))
        have.add(r["page"])
        carried.append(r["page"])
    routes_path.parent.mkdir(parents=True, exist_ok=True)
    routes_path.write_text(json.dumps(sorted(routes, key=lambda r: r["page"]), indent=1))
    return carried


def enough_homes(conn, carried: list[str] = ()) -> tuple[bool, int, int]:
    """Homes from pages fetched today (not carried) against the last snapshot's home count."""
    now = sum(r[0] not in set(carried) for r in conn.execute("SELECT source_page FROM homes"))
    last = conn.execute("SELECT COUNT(*) FROM price_history WHERE snapshot_date = "
                        "(SELECT MAX(snapshot_date) FROM price_history)").fetchone()[0]
    return (last == 0 or now >= MIN_SHARE * last), now, last


def run(day: str, deploy: bool, fetch: bool = True) -> dict:
    py("tools.fetch_market_rate")
    if fetch:
        code = py("tools.fetch_builder_pages", "--builder", "all", "--date", day, check=False)
        if code == FLOOR_FAILED:
            log.warning("a builder is below its floor; going on, its pages are carried")
        elif code:
            raise SystemExit(f"stopped: the fetcher failed (exit {code}); nothing published")
    py("tools.preprocess_page", "--date", day)
    dropped = drop_carried(day)
    log.info("dropped %d routes carried by an earlier run today", len(dropped))
    reused = reuse_unchanged(day)
    log.info("reused %d unchanged pages", len(reused))
    py("tools.extract_listing", "--date", day, "--skip-done")
    carried = carry_forward(day)
    log.info("carried forward %d pages: %s", len(carried), carried)
    unfetched = carry_unfetched(day, expected_communities())
    log.info("carried %d pages not fetched today: %s", len(unfetched), unfetched)
    py("tools.load_records", "--date", day)
    conn = db.connect()
    stale = carried_pages(day)
    ok, now, last = enough_homes(conn, stale)
    if not ok:
        raise SystemExit(f"stopped: {now} homes fetched today vs {last} in the last snapshot (< {MIN_SHARE:.0%}); "
                         "nothing published")
    py("tools.geocode_communities")
    n = snapshot(conn, day, carried=stale)
    rows = write_csv(conn, HISTORY_CSV)
    py("tools.export_site_data")
    if deploy:
        step("netlify", "deploy", "--prod", "--dir", "web")
        py("tools.build_bundle")
        step("railway", "up", ".tmp/bundle", "--path-as-root", "-s", "api", "--ci")
    py("tools.llm", "--spend")
    return {"day": day, "homes": now, "snapshot": n, "history_rows": rows, "reused": len(reused), "carried": len(carried),
            "unfetched": len(unfetched), "deployed": deploy}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-deploy", action="store_true")
    ap.add_argument("--skip-fetch", action="store_true")
    a = ap.parse_args()
    day = Date.today().isoformat()          # the fetcher names its folder by the local date
    LOGS.mkdir(parents=True, exist_ok=True)
    if shutil.which("caffeinate"):          # no idle/system sleep until this process exits
        subprocess.Popen(["caffeinate", "-is", "-w", str(os.getpid())])
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(), logging.FileHandler(LOGS / f"daily_{day}.log")])
    log.info("done: %s", run(day, deploy=not a.no_deploy, fetch=not a.skip_fetch))
