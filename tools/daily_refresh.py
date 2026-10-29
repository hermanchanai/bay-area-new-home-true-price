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
     so its homes don't vanish for a day; listed in the log
  7. load + price               8. map pins for new communities          9. snapshot into price_history
  10. export site data + price_history.csv                                 11. deploy
Guard: if today's load has fewer than MIN_SHARE of the last snapshot's homes (a blocked fetch, a bad parse),
the run stops before the snapshot and nothing is published.
"""
import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
from datetime import date as Date
from pathlib import Path

from tools import db
from tools.extract_listing import OUT as EXTRACT
from tools.preprocess_page import PRE, RAW
from tools.snapshot_prices import snapshot, write_csv

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / ".tmp" / "logs"
HISTORY_CSV = ROOT / "web" / "data" / "price_history.csv"
MIN_SHARE = 0.7
log = logging.getLogger("daily_refresh")


def step(*args: str) -> None:
    log.info("$ %s", " ".join(args))
    subprocess.run(args, cwd=ROOT, check=True)


def py(module: str, *args: str) -> None:
    step(sys.executable, "-m", module, *args)


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
        then_md = pre / src_day / f"{page}.md"
        if then_md.exists() and fingerprint(then_md.read_text()) == fingerprint(today_md.read_text()):
            routes[page] = _reuse(day, src_day, r, extract, unchanged=True)
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
            routes.append(_reuse(day, *found, extract))
            carried.append(page)
    routes_path.parent.mkdir(parents=True, exist_ok=True)
    routes_path.write_text(json.dumps(sorted(routes, key=lambda r: r["page"]), indent=1))
    return carried


def enough_homes(conn) -> tuple[bool, int, int]:
    """Today's load against the last snapshot's home count."""
    now = conn.execute("SELECT COUNT(*) FROM homes").fetchone()[0]
    last = conn.execute("SELECT COUNT(*) FROM price_history WHERE snapshot_date = "
                        "(SELECT MAX(snapshot_date) FROM price_history)").fetchone()[0]
    return (last == 0 or now >= MIN_SHARE * last), now, last


def run(day: str, deploy: bool, fetch: bool = True) -> dict:
    py("tools.fetch_market_rate")
    if fetch:
        py("tools.fetch_builder_pages", "--builder", "all")
    py("tools.preprocess_page", "--date", day)
    reused = reuse_unchanged(day)
    log.info("reused %d unchanged pages", len(reused))
    py("tools.extract_listing", "--date", day, "--skip-done")
    carried = carry_forward(day)
    log.info("carried forward %d pages: %s", len(carried), carried)
    py("tools.load_records", "--date", day)
    conn = db.connect()
    ok, now, last = enough_homes(conn)
    if not ok:
        raise SystemExit(f"stopped: {now} homes today vs {last} in the last snapshot (< {MIN_SHARE:.0%}); nothing published")
    py("tools.geocode_communities")
    n = snapshot(conn, day)
    rows = write_csv(conn, HISTORY_CSV)
    py("tools.export_site_data")
    if deploy:
        step("netlify", "deploy", "--prod", "--dir", "web")
        py("tools.build_bundle")
        step("railway", "up", ".tmp/bundle", "--path-as-root", "-s", "api", "--ci")
    py("tools.llm", "--spend")
    return {"day": day, "homes": now, "snapshot": n, "history_rows": rows, "reused": len(reused), "carried": len(carried),
            "deployed": deploy}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-deploy", action="store_true")
    ap.add_argument("--skip-fetch", action="store_true")
    a = ap.parse_args()
    day = Date.today().isoformat()          # the fetcher names its folder by the local date
    LOGS.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(), logging.FileHandler(LOGS / f"daily_{day}.log")])
    log.info("done: %s", run(day, deploy=not a.no_deploy, fetch=not a.skip_fetch))
