"""Daily price check: fetch every builder, re-extract what changed, record dated prices, redeploy.

    python -m tools.daily_refresh                 # today, then deploy the site (Netlify) and agent API (Railway)
    python -m tools.daily_refresh --no-deploy     # everything up to the export; nothing published

Run by launchd every morning (workflows/daily_refresh.md). Steps, in order:
  1. market rate (FRED)         2. fetch all builders        3. preprocess
  4. extract (cached pages $0; Meta daily cap in config/llm.yaml stops new calls, the run goes on)
  5. carry forward: a page fetched today whose extraction didn't run (cap, error) reuses its latest earlier
     extraction, so its homes don't vanish for a day; listed in the log
  6. load + price               7. map pins for new communities          8. snapshot into price_history
  9. export site data + price_history.csv                                  10. deploy
Guard: if today's load has fewer than MIN_SHARE of the last snapshot's homes (a blocked fetch, a bad parse),
the run stops before the snapshot and nothing is published.
"""
import argparse
import json
import logging
import shutil
import subprocess
import sys
from datetime import date as Date
from pathlib import Path

from tools import db
from tools.extract_listing import OUT as EXTRACT
from tools.preprocess_page import RAW
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


def carry_forward(day: str, raw: Path = RAW, extract: Path = EXTRACT) -> list[str]:
    """Pages fetched on `day` with no extraction get their latest earlier one. Returns the pages carried."""
    routes_path = extract / day / "routes.json"
    routes = json.loads(routes_path.read_text()) if routes_path.exists() else []
    have = {r["page"] for r in routes}
    fetched = [f"{m.parent.name}/{r['file'].removesuffix('.html')}" for m in sorted((raw / day).glob("*/manifest.json"))
               for r in json.loads(m.read_text())
               if r.get("file") and r.get("status") == 200 and r.get("kind") in ("community", "extra", "home")]
    earlier = sorted((d for d in extract.iterdir() if d.is_dir() and d.name < day), reverse=True)
    carried = []
    for page in fetched:
        if page in have:
            continue
        for d in earlier:
            old = {r["page"]: r for r in json.loads((d / "routes.json").read_text())} if (d / "routes.json").exists() else {}
            if page in old:
                r = old[page]
                dst = extract / day / r["model"] / f"{page}.json"
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(d / r["model"] / f"{page}.json", dst)
                routes.append({**r, "carried_from": r.get("carried_from") or d.name})
                carried.append(page)
                break
    routes_path.parent.mkdir(parents=True, exist_ok=True)
    routes_path.write_text(json.dumps(sorted(routes, key=lambda r: r["page"]), indent=1))
    return carried


def enough_homes(conn) -> tuple[bool, int, int]:
    """Today's load against the last snapshot's home count."""
    now = conn.execute("SELECT COUNT(*) FROM homes").fetchone()[0]
    last = conn.execute("SELECT COUNT(*) FROM price_history WHERE snapshot_date = "
                        "(SELECT MAX(snapshot_date) FROM price_history)").fetchone()[0]
    return (last == 0 or now >= MIN_SHARE * last), now, last


def run(day: str, deploy: bool) -> dict:
    py("tools.fetch_market_rate")
    py("tools.fetch_builder_pages", "--builder", "all")
    py("tools.preprocess_page", "--date", day)
    py("tools.extract_listing", "--date", day)
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
    return {"day": day, "homes": now, "snapshot": n, "history_rows": rows, "carried": len(carried), "deployed": deploy}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-deploy", action="store_true")
    a = ap.parse_args()
    day = Date.today().isoformat()          # the fetcher names its folder by the local date
    LOGS.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(), logging.FileHandler(LOGS / f"daily_{day}.log")])
    log.info("done: %s", run(day, deploy=not a.no_deploy))
