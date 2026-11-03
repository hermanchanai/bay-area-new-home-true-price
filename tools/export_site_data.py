"""Export what the site shows and what the live agent reads, from the same queries the agent's tools run.

    python -m tools.export_site_data

Writes:
  web/data/site.json          meta + one row per community (map pins) + every home, cheapest guaranteed true
                              price first + a plan index (to resolve [P<id>] citations)
  web/data/c/<id>.json        one community: homes with their offers, plans (rooms when the image was read),
                              incentives with dollar values, past sales
  web/data/demo_answers.json  hand-checked agent answers (eval runs that passed), keyed by question_key, so the
                              site answers its example questions for $0 and without the API
  data/site.db                the slim DB the live agent queries: listings, incentives, prices and community
                              past-sales aggregates only. No parcels, transfers or LLM ledger: per-address
                              assessor data never leaves this machine.

Builder fine print stays out of the JSON (the source link is there instead).
"""
import json
import logging
import os
import re
import shutil
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from tools import db
from tools.agent_tools import (
    HOME_SQL,
    SORTS,
    _county_name,
    describe_steps,
    _home,
    get_community,
    get_plan,
)
from tools.eval_agent import RUNS, cited
from tools.snapshot_prices import home_key

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web" / "data"
SITE_DB = ROOT / "data" / "site.db"
# FK order. Everything else (parcels, community_parcels, transfers, llm_calls, eval_results) stays behind.
SHIP = ("builders", "communities", "plans", "homes", "incentives", "market_rates", "incentive_values",
        "true_prices", "past_sales", "past_records")
log = logging.getLogger(__name__)


def question_key(q: str) -> str:
    """Case, spacing and punctuation don't matter: '4 bed, under $1.1M!' -> '4 bed under $1.1m'."""
    s = re.sub(r"[^a-z0-9$.%\s]", " ", q.lower())
    s = re.sub(r"\.(?!\d)", " ", s)
    return " ".join(s.split())


def _pct(part, whole):
    return round(100 * part / whole, 1) if part is not None and whole else None


def summarize(conn, today: str) -> list[dict]:
    """One row per community that has homes, a map position, or floor plans (still selling, nothing ready to move into)."""
    rows = conn.execute(
        "SELECT c.*, b.name builder, COUNT(h.id) n, MIN(h.price) min_asking, MIN(t.true_high) min_true, "
        "MAX(1.0 * t.value_low / t.asking) dlo, MAX(1.0 * t.value_high / t.asking) dhi, "
        "SUM(h.move_in_status = 'now') ready_now, ps.n past_n FROM communities c "
        "JOIN builders b ON b.id = c.builder_id LEFT JOIN homes h ON h.community_id = c.id "
        "LEFT JOIN true_prices t ON t.home_id = h.id LEFT JOIN past_sales ps ON ps.community_id = c.id "
        "GROUP BY c.id HAVING COUNT(h.id) > 0 OR c.lat IS NOT NULL "
        "OR EXISTS (SELECT 1 FROM plans p WHERE p.community_id = c.id) ORDER BY c.id").fetchall()
    return [{"id": r["id"], "name": r["name"], "builder": r["builder"],
             "city": re.sub(r",\s*CA$", "", r["city"] or ""), "county": _county_name(r["county_fips"]),
             "lat": r["lat"], "lng": r["lng"], "geo_method": r["geo_method"], "product": r["product"],
             "status": r["status"], "age_restricted": bool(r["age_restricted"]), "source_url": r["url"],
             "fetched": (r["fetched_at"] or "")[:10] or None, "homes": r["n"], "ready_now": r["ready_now"] or 0,
             "min_asking": r["min_asking"], "min_true_price": r["min_true"],
             "max_discount_pct": round(100 * r["dlo"], 1) if r["dlo"] is not None else None,
             "max_discount_pct_best": round(100 * r["dhi"], 1) if r["dhi"] is not None else None,
             "has_past_sales": r["past_n"] is not None} for r in rows]


def _why(method: str, flags: str | None) -> str | None:
    """Why an offer isn't counted for this home: already in its price, limited to some buyers, or conditional."""
    if method == "in_asking_price":
        return "in_price"
    return next((f for f in json.loads(flags or "[]") if f in ("eligibility_limited", "conditional")), None)


def history_meta(conn) -> dict:
    """How many daily checks are on record (a day of only carried homes is not one), and what changed at the last."""
    days = [r[0] for r in conn.execute("SELECT DISTINCT snapshot_date FROM price_history WHERE carried = 0 ORDER BY 1")]
    out = {"first": days[0] if days else None, "last": days[-1] if days else None, "checks": len(days)}
    if len(days) > 1:
        prev, last = days[-2], days[-1]
        q = lambda sql: conn.execute(sql, (prev, last)).fetchone()[0]
        out.update(
            price_changed=q("SELECT COUNT(*) FROM price_history a JOIN price_history b ON b.home_key = a.home_key "
                            "WHERE a.snapshot_date = ? AND b.snapshot_date = ? AND a.asking != b.asking"),
            gone=q("SELECT COUNT(*) FROM price_history a WHERE a.snapshot_date = ? AND NOT EXISTS (SELECT 1 FROM "
                   "price_history b WHERE b.home_key = a.home_key AND b.snapshot_date = ?)"),
            new=conn.execute("SELECT COUNT(*) FROM price_history b WHERE b.snapshot_date = ? AND NOT EXISTS (SELECT 1 "
                             "FROM price_history a WHERE a.home_key = b.home_key AND a.snapshot_date = ?)",
                             (last, prev)).fetchone()[0])
    return out


def community_detail(conn, cid: int, today: str) -> dict:
    d = get_community(conn, cid, today)
    d["plans"] = [get_plan(conn, p["plan_id"], rooms=True) for p in d["plans"]]
    for i in d["incentives"]:
        i.pop("fine_print", None)
    offers = {}
    for r in conn.execute("SELECT v.home_id, v.incentive_id, v.value_low, v.value_high, v.method, v.flags "
                          "FROM incentive_values v "
                          "JOIN homes h ON h.id = v.home_id WHERE h.community_id = ? ORDER BY v.value_high DESC",
                          (cid,)):
        offers.setdefault(r["home_id"], []).append(
            {"incentive_id": r["incentive_id"], "value_low": r["value_low"], "value_high": r["value_high"],
             "counted": "not_counted" not in (r["flags"] or ""), "why": _why(r["method"], r["flags"])})
    builder_id, url = conn.execute("SELECT builder_id, url FROM communities WHERE id = ?", (cid,)).fetchone()
    for h in d["homes"]:
        h["offers"] = offers.get(h["home_id"], [])
        h["history"] = [list(r) for r in conn.execute(      # [date, asking, guaranteed true price], one per daily check
            "SELECT snapshot_date, asking, true_high FROM price_history WHERE home_key = ? AND carried = 0 "
            "ORDER BY snapshot_date",
            (home_key(builder_id, url, h.get("address"), h.get("lot")),))]
    return d


def demo_answers(conn, runs: Path) -> dict:
    """Passed eval answers whose every cited id still exists (a home that sold drops its answers)."""
    known = {k: {r[0] for r in conn.execute(f"SELECT id FROM {t}")}
             for k, t in (("H", "homes"), ("C", "communities"), ("P", "plans"))}
    out = {}
    for f in sorted(runs.glob("*.json")):
        r = json.loads(f.read_text())
        if not (r.get("score") or {}).get("pass"):
            continue
        ids = cited(r["answer"])
        if any(i not in known[k] for k in ids for i in ids[k]):
            log.warning("%s cites ids no longer in the DB; dropped", f.name)
            continue
        checked = date.fromtimestamp(f.stat().st_mtime).isoformat()     # the eval run that passed it
        out[question_key(r["question"])] = {"question": r["question"], "answer": r["answer"], "checked": checked,
                                             "steps": describe_steps(r.get("trace") or [])}
    return out


def slim_db(conn, path: Path) -> None:
    path.unlink(missing_ok=True)
    out = db.connect(path)
    for t in SHIP:
        cur = conn.execute(f"SELECT * FROM {t}")
        cols = [c[0] for c in cur.description]
        out.executemany(f"INSERT INTO {t} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [tuple(r) for r in cur])
    out.commit()
    out.execute("VACUUM")
    out.close()


def export(conn, out_dir: Path = WEB, db_path: Path = SITE_DB, today: str | None = None,
           runs: Path | None = None) -> dict:
    today = today or date.today().isoformat()
    comms = summarize(conn, today)
    homes = [_home(r, today) for r in conn.execute(HOME_SQL + f" ORDER BY {SORTS['true_price']}, h.id")]
    rate = conn.execute("SELECT as_of, rate FROM market_rates WHERE series = 'MORTGAGE30US' "
                        "ORDER BY as_of DESC LIMIT 1").fetchone()
    meta = {"generated": today, "market_rate": rate["rate"] if rate else None,
            "rate_date": rate["as_of"] if rate else None, "rate_source": "FRED MORTGAGE30US",
            "assumptions": {"down_payment_pct": 20, "term_years": 30},
            "communities": sum(c["homes"] > 0 for c in comms), "mapped": sum(c["lat"] is not None for c in comms),
            "homes": len(homes), "builders": len({c["builder"] for c in comms if c["homes"] > 0}),
            "with_past_sales": sum(c["has_past_sales"] for c in comms),
            "history": history_meta(conn)}
    if (out_dir / "c").exists():
        shutil.rmtree(out_dir / "c")
    (out_dir / "c").mkdir(parents=True)
    shown = {c["id"] for c in comms}
    plans = [{"plan_id": r[0], "community_id": r[1], "name": r[2]}
             for r in conn.execute("SELECT id, community_id, name FROM plans ORDER BY id") if r[1] in shown]
    # Google Maps browser key: public by design (locked to the site's address in Google Cloud), kept out of git.
    load_dotenv(ROOT / ".env", override=True)
    (out_dir.parent / "config.js").write_text(f"window.GMAPS_KEY = {json.dumps(os.environ.get('GOOGLE_MAPS_BROWSER_KEY', ''))};\n")
    (out_dir / "site.json").write_text(json.dumps({"meta": meta, "communities": comms, "homes": homes, "plans": plans},
                                                  separators=(",", ":"), default=str))
    for c in comms:
        (out_dir / "c" / f"{c['id']}.json").write_text(
            json.dumps(community_detail(conn, c["id"], today), separators=(",", ":"), default=str))
    if runs is not None:
        (out_dir / "demo_answers.json").write_text(json.dumps(demo_answers(conn, runs), indent=1))
    slim_db(conn, db_path)
    return meta


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    m = export(db.connect(), runs=RUNS)
    print(f"{m['communities']} communities ({m['mapped']} on the map), {m['homes']} homes, "
          f"{m['with_past_sales']} with past sales -> {WEB.relative_to(ROOT)}/, {SITE_DB.relative_to(ROOT)}")
