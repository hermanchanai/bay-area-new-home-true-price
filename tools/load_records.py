"""Load the routed extraction into SQLite and price every home. Deterministic, $0.

    python -m tools.load_records            # latest date: communities, plans, homes, incentives, true prices

Rules
- A home listed on several pages (Lennar master + sub-community, Trumark's all-homes page) is kept
  once: its own single-home page if it has one, else the most specific community page. Region-wide
  listing pages ("extra") never add homes on their own.
- A home gets the offers on its page: its own home-scope offers and the page's community offers.
  Builder offers pages only fill in missing terms (a "7/6 ARM" banner with no rate); they are never
  stacked as extra offers.
- A price cut matching was-price minus price is already in the asking price: shown, valued at $0.
- A home that already shows savings (was-price above price) has had the builder's "up to $X" cash
  offer applied: Pulte's "$220,000 in Savings" on a home next to "Up to $250K in FLEX Cash" is that
  FLEX Cash. The up-to offer is shown, valued at $0; rate offers and fixed-amount offers still count.
- Offers don't stack unless the fine print says they combine (plan §6.7): by default a home's offers
  are one choice and the best counts. Offers limited to a buyer group (teachers, military ...; named in
  the eligibility or the offer's own label) are shown, not counted, and so are guarantees that pay only
  if the builder misses a date ("reimburse up to $5,000 if not ready").
"""
import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

from tools import db
from tools.eval_extraction import norm_address
from tools.preprocess_page import RAW
from tools.price_incentives import RATE_KINDS, Assumptions, Incentive, true_price, value_incentive

ROOT = Path(__file__).resolve().parent.parent
EXTRACT = ROOT / ".tmp" / "extract"
MOVE_IN = {"now": "now", "month": "month", "under_construction": "under_construction"}
PRODUCT = {"SFD": "SFD", "TH": "TH", "duet": "TH", "condo": "condo", "mixed": "mixed"}


def _name(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _depth(url: str) -> int:
    return len([p for p in urlparse(url).path.split("/") if p])


def _home_key(builder: str, h: dict, page: dict) -> tuple | None:
    if h.get("address"):
        return builder, norm_address(h["address"])
    if h.get("lot"):
        return builder, _name(page["record"]["community"].get("name")), "lot", str(h["lot"])
    return None


def _community_for(home_page: dict, communities: list[dict]) -> dict | None:
    """A single-home page's community: same builder, names contain one another ("Alder Grove [at Manteca]")."""
    want = _name(home_page["record"]["community"].get("name"))
    hits = [c for c in communities if c["builder"] == home_page["builder"]
            and (n := _name(c["record"]["community"].get("name"))) and want and (n in want or want in n)]
    return max(hits, key=lambda c: len(_name(c["record"]["community"]["name"])), default=None)


def assign_homes(pages: list[dict]) -> list[dict]:
    communities = [p for p in pages if p["kind"] == "community"]
    best: dict[tuple, dict] = {}
    for p in pages:
        if p["kind"] == "extra":
            continue
        cp = p if p["kind"] == "community" else _community_for(p, communities)
        if cp is None:
            continue
        rank = (p["kind"] == "home", _depth(p["url"]))
        for h in p["record"].get("homes") or []:
            key = _home_key(p["builder"], h, p)
            if key and (key not in best or rank > best[key]["rank"]):
                best[key] = {"home": h, "page": p, "community_page": cp, "rank": rank}
    return [{k: v for k, v in b.items() if k != "rank"} for b in best.values()]


def _already_in_price(i: dict, home: dict) -> bool:
    was, price, amt = home.get("was_price"), home.get("price"), i.get("amount_usd")
    if not (was and price and was > price):
        return False
    if i["kind"] in ("flat_cash", "price_cut") and i.get("amount_is_up_to"):
        return True                                       # the savings shown are this offer, applied
    return i["kind"] == "price_cut" and bool(amt) and abs((was - price) - amt) <= max(1000, 0.01 * amt)


def stable_home_id(conn, community_id: int, address: str | None, lot: str | None) -> int:
    """The home's permanent id (migrations/007_home_ids.sql): the same home keeps it across daily reloads."""
    from tools.snapshot_prices import home_key
    builder_id, url = conn.execute("SELECT builder_id, url FROM communities WHERE id = ?", (community_id,)).fetchone()
    key = home_key(builder_id, url, address, lot)
    row = conn.execute("SELECT id FROM home_ids WHERE home_key = ?", (key,)).fetchone()
    if row and not conn.execute("SELECT 1 FROM homes WHERE id = ?", (row[0],)).fetchone():
        return row[0]
    new = conn.execute("SELECT MAX(m) + 1 FROM (SELECT COALESCE(MAX(id), 0) m FROM home_ids "
                       "UNION ALL SELECT COALESCE(MAX(id), 0) FROM homes)").fetchone()[0]
    if not row:                     # a key already used today (no address or lot to tell homes apart): unmapped id
        conn.execute("INSERT INTO home_ids VALUES (?, ?)", (key, new))
    return new


def home_incentives(home: dict, page: dict, community_page: dict) -> list[dict]:
    me = norm_address(home["address"]) if home.get("address") else None
    out, seen = [], set()
    sources = [page] + ([community_page] if community_page is not page else [])
    for src in sources:
        for i in src["record"].get("incentives") or []:
            if i.get("scope") == "home":
                if src is not page or not me or not i.get("home_address") or norm_address(i["home_address"]) != me:
                    continue
            key = (i["kind"], i.get("amount_usd"), i.get("rate"), i.get("structure"))
            if key in seen:
                continue
            seen.add(key)
            x = dict(i)
            x["included_in_price"] = bool(i.get("included_in_price")) or _already_in_price(i, home)
            x["pool_id"] = f"{src['page']}:{i['pool_id']}" if i.get("pool_id") else None
            out.append(x)
    return out


def fill_from_offers(incs: list[dict], offers: list[dict]) -> list[dict]:
    out = []
    for i in incs:
        x = {**i, "filled_from_offers": False}
        if i["kind"] in RATE_KINDS and i.get("rate") is None:
            digits = re.findall(r"\d+", i.get("structure") or i.get("label") or "")
            for o in offers:
                if o["kind"] == i["kind"] and o.get("rate") is not None and \
                        re.findall(r"\d+", o.get("structure") or "") == digits and digits:
                    x.update(rate=o["rate"], apr=o.get("apr"), structure=o.get("structure"),
                             fine_print=i.get("fine_print") or o.get("fine_print"), filled_from_offers=True)
                    break
        out.append(x)
    return out


# offers only some buyers can get; "qualified buyers", "FHA", "select homes" stay open to everyone
BUYER_GROUP = re.compile(r"(?i)teacher|nurse|\bems\b|police|firefight|first responder|military|veteran|hero|"
                         r"educator|healthcare|employee")
GUARANTEE = re.compile(r"(?i)guarantee|reimburs")
COMBINABLE = re.compile(r"(?i)\b(?:may|can) be combined\b|\bcombinable with\b")
NOT_COMBINABLE = re.compile(r"(?i)\b(?:cannot|can ?not|may not|not) be combined\b|\bnot combinable\b")


def pricing_set(incs: list[dict], home_key: str) -> tuple[list[dict], list[tuple[dict, str]]]:
    """Which offers count toward one home's true price. Plan §6.7: offers don't stack unless the fine
    print says they combine, so by default they form one choice (the biggest counts). Offers limited to
    some buyers (teachers, military ...) and cuts already in the asking price are shown, not counted."""
    counted, excluded = [], []
    for i in incs:
        if i.get("included_in_price"):
            excluded.append((i, "in_asking_price"))
        elif BUYER_GROUP.search(" ".join(filter(None, [i.get("eligibility"), i.get("label")]))):
            excluded.append((i, "eligibility_limited"))
        elif GUARANTEE.search(i.get("label") or ""):
            excluded.append((i, "conditional"))
        else:
            text = " ".join(filter(None, [i.get("fine_print"), i.get("label")]))
            stacks = bool(COMBINABLE.search(text)) and not NOT_COMBINABLE.search(text)
            counted.append({**i, "pool_id": None if stacks else f"{home_key}:one-offer"})
    return counted, excluded


# ---------- I/O ----------

def _manifest_records(date: str) -> dict[str, dict]:
    kinds = {}
    for man in (RAW / date).glob("*/manifest.json"):
        for r in json.loads(man.read_text()):
            if r.get("file"):
                kinds[f"{man.parent.name}/{r['file'].removesuffix('.html')}"] = r
    return kinds


def read_pages(date: str) -> list[dict]:
    """A page not fetched today (builder below its floor, a failed fetch) but carried from an earlier
    extraction takes its record from the manifest of the day it was last fetched."""
    days = {date: _manifest_records(date)}
    pages = []
    for r in json.loads((EXTRACT / date / "routes.json").read_text()):
        m = days[date].get(r["page"])
        if m is None and (seen := r.get("last_fetched") or r.get("carried_from")):
            m = days.setdefault(seen, _manifest_records(seen))[r["page"]]
        rec = json.loads((EXTRACT / date / r["model"] / f"{r['page']}.json").read_text())
        pages.append({"page": r["page"], "builder": r["page"].split("/")[0], "kind": m["kind"], "url": m["url"],
                      "manifest": m, "record": rec, "needs_review": r["needs_review"]})
    return pages


def load(date: str, conn) -> dict:
    pages = read_pages(date)
    cfg = yaml.safe_load((ROOT / "config" / "builders.yaml").read_text())
    names = {b["key"]: b["name"] for b in cfg["builders"]}
    counties = {c["key"]: c for c in yaml.safe_load((ROOT / "config" / "counties.yaml").read_text())["counties"]}
    fips_limit = {c["fips"]: c["conforming_limit_1unit"] for c in counties.values()}
    rate_row = conn.execute("SELECT as_of, rate FROM market_rates WHERE series='MORTGAGE30US' "
                            "ORDER BY as_of DESC LIMIT 1").fetchone()
    rate_date, market = rate_row["as_of"], rate_row["rate"]
    offers = {}
    for p in pages:
        if p["kind"] == "extra":
            offers.setdefault(p["builder"], []).extend(p["record"].get("incentives") or [])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    stats = {"homes": 0, "priced": 0, "no_price": 0, "with_incentive": 0, "incentives": 0, "review_pages_used": 0}

    with conn:
        for t in ("true_prices", "incentive_values", "incentives", "homes"):  # plans keep their floor-plan reads
            conn.execute(f"DELETE FROM {t}")
        cid = {}
        for p in pages:
            if p["kind"] != "community":
                continue
            c, m = p["record"]["community"], p["manifest"]
            row = conn.execute("SELECT id FROM communities WHERE url=?", (p["url"],)).fetchone()
            if row is None:
                bid = conn.execute("SELECT id FROM builders WHERE name=?", (names[p["builder"]],)).fetchone()[0]
                row = conn.execute("INSERT INTO communities (builder_id, name, url) VALUES (?, ?, ?) RETURNING id",
                                   (bid, c.get("name") or p["page"], p["url"])).fetchone()
            cid[p["page"]] = row[0]
            conn.execute("UPDATE communities SET name=COALESCE(?, name), city=COALESCE(?, city), county_fips="
                         "COALESCE(?, county_fips), status=?, product=?, age_restricted=? WHERE id=?",
                         (c.get("name"), c.get("city"), counties.get(m.get("county"), {}).get("fips"),
                          c.get("status"), PRODUCT.get(c.get("product")), int(bool(c.get("age_restricted"))), row[0]))
            for pl in p["record"].get("plans") or []:
                conn.execute("INSERT INTO plans (community_id, name, beds_min, beds_max, baths, sqft_min, "
                             "sqft_max, stories, garage, from_price) VALUES (?,?,?,?,?,?,?,?,?,?) "
                             "ON CONFLICT(community_id, name) DO UPDATE SET beds_min=excluded.beds_min, "
                             "beds_max=excluded.beds_max, baths=excluded.baths, sqft_min=excluded.sqft_min, "
                             "sqft_max=excluded.sqft_max, stories=excluded.stories, garage=excluded.garage, "
                             "from_price=excluded.from_price",
                             (row[0], pl["name"], pl.get("beds_min"), pl.get("beds_max"), pl.get("baths_max"),
                              pl.get("sqft_min"), pl.get("sqft_max"), pl.get("stories"), pl.get("garage"),
                              pl.get("from_price")))

        inc_ids = {}
        for a in assign_homes(pages):
            h, p, cp = a["home"], a["page"], a["community_page"]
            community_id = cid[cp["page"]]
            fips = conn.execute("SELECT county_fips FROM communities WHERE id=?", (community_id,)).fetchone()[0]
            plan = conn.execute("SELECT id FROM plans WHERE community_id=? AND name=?",
                                (community_id, h.get("plan_name"))).fetchone()
            home_id = conn.execute(
                "INSERT INTO homes (id, community_id, plan_id, address, lot, price, was_price, price_basis, "
                "move_in_status, ready_date, beds, baths, sqft, url, fetched_at, source_page) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
                (stable_home_id(conn, community_id, h.get("address"), h.get("lot")),
                 community_id, plan[0] if plan else None, h.get("address"), h.get("lot"), h.get("price"),
                 h.get("was_price"), "quick_move_in", MOVE_IN.get(h.get("move_in_status")), h.get("ready_date"),
                 h.get("beds"), h.get("baths"), h.get("sqft"), p["url"], p["manifest"].get("fetched_at"),
                 p["page"])).fetchone()[0]
            stats["homes"] += 1
            stats["review_pages_used"] += p["needs_review"]
            incs = fill_from_offers(home_incentives(h, p, cp), offers.get(p["builder"], []))
            stats["with_incentive"] += bool(incs)
            priced = []
            for i in incs:
                scope, scope_id = ("home", home_id) if i.get("scope") == "home" or p["kind"] == "home" \
                    else ("community", community_id)
                key = (scope, scope_id, i["kind"], i.get("amount_usd"), i.get("rate"), i.get("structure"), i["label"])
                if key not in inc_ids:
                    inc_ids[key] = conn.execute(
                        "INSERT INTO incentives (scope, scope_id, kind, amount_usd, amount_is_up_to, rate, apr, "
                        "structure, lender_condition, lender, pool_id, expires, fine_print, source_url, label, "
                        "evidence, eligibility, included_in_price, filled_from_offers) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
                        (scope, scope_id, i["kind"], i.get("amount_usd"), int(bool(i.get("amount_is_up_to"))),
                         i.get("rate"), i.get("apr"), i.get("structure"), int(bool(i.get("lender_condition"))),
                         i.get("lender_condition"), i.get("pool_id"), i.get("expires"),
                         i.get("fine_print") or i.get("evidence") or i["label"], p["url"], i["label"],
                         i.get("evidence"), i.get("eligibility"), int(i["included_in_price"]),
                         int(i["filled_from_offers"]))).fetchone()[0]
                    stats["incentives"] += 1
                priced.append((inc_ids[key], i))
            if not h.get("price"):
                stats["no_price"] += 1
                continue
            a_ = Assumptions(conforming_limit=fips_limit.get(fips))
            counted, excluded = pricing_set([i for _, i in priced], f"home{home_id}")
            reasons = {id(i): r for i, r in excluded}
            for inc_id, i in priced:
                if reasons.get(id(i)) == "in_asking_price":
                    v_lo = v_hi = 0
                    method, flags = "in_asking_price", ["not_counted", "in_asking_price"]
                else:
                    v = value_incentive(_inc(i), h["price"], market, a_)
                    v_lo, v_hi, method, flags = v.low, v.high, v.method, list(v.flags)
                    if id(i) in reasons:
                        flags += ["not_counted", reasons[id(i)]]
                conn.execute("INSERT OR REPLACE INTO incentive_values VALUES (?,?,?,?,?,?,?,?,?)",
                             (inc_id, home_id, market, rate_date, v_lo, v_hi, method,
                              json.dumps(a_.__dict__), json.dumps(flags)))
            tp = true_price(h["price"], [_inc(i) for i in counted], market, a_)
            conn.execute("INSERT INTO true_prices VALUES (?,?,?,?,?,?,?)",
                         (home_id, tp.asking, tp.value_low, tp.value_high, tp.true_low, tp.true_high, now))
            stats["priced"] += 1
        # a community offer is "in the price" only if it is for every home it was attached to (savings vary by home)
        conn.execute("UPDATE incentives SET included_in_price = NOT EXISTS (SELECT 1 FROM incentive_values v "
                     "WHERE v.incentive_id = incentives.id AND v.method != 'in_asking_price') "
                     "AND EXISTS (SELECT 1 FROM incentive_values v WHERE v.incentive_id = incentives.id)")
    stats["market_rate"] = f"{market}% ({rate_date})"
    return stats


def _inc(i: dict) -> Incentive:
    return Incentive(kind=i["kind"], amount_usd=i.get("amount_usd"), amount_is_up_to=bool(i.get("amount_is_up_to")),
                     rate=i.get("rate"), structure=i.get("structure"), pool_id=i.get("pool_id"), label=i["label"])


def report(date: str, stats: dict, conn) -> Path:
    q = lambda sql: conn.execute(sql).fetchall()  # noqa: E731
    gap = q("SELECT count(*) FROM homes h WHERE h.price IS NOT NULL AND NOT EXISTS (SELECT 1 FROM true_prices t "
            "WHERE t.home_id = h.id) AND EXISTS (SELECT 1 FROM incentive_values v WHERE v.home_id = h.id)")[0][0]
    lines = [f"# True prices, pages fetched {date}", "",
             f"Market rate {stats['market_rate']} (FRED MORTGAGE30US); 20% down, 30-year term; jumbo loans "
             f"+0.25 pt. {stats['homes']} homes, {stats['priced']} with an asking price, {stats['with_incentive']} "
             f"with at least one offer.", "",
             f"**Day 4 gate: {'PASS' if gap == 0 else 'FAIL'}**: homes with a price and an offer but no "
             f"true-price range: {gap}.", "",
             "| Offer kind | Offers × homes | Counted | Not counted: in price | Not counted: buyer-limited | "
             "Filled from offers page | Avg value high |", "|---|---:|---:|---:|---:|---:|---:|"]
    for r in q("SELECT i.kind, count(*), sum(v.flags NOT LIKE '%not_counted%'), sum(v.method='in_asking_price'), "
               "sum(v.flags LIKE '%eligibility_limited%'), sum(i.filled_from_offers), round(avg(v.value_high)) "
               "FROM incentive_values v JOIN incentives i ON i.id = v.incentive_id GROUP BY 1 ORDER BY 2 DESC"):
        lines.append("| " + " | ".join(str(x) for x in r) + " |")
    lo, hi, n = q("SELECT avg(1.0*value_low/asking), avg(1.0*value_high/asking), sum(value_high > 0) "
                  "FROM true_prices")[0]
    lines += ["", f"Average discount {lo:.1%} guaranteed to {hi:.1%} at most; {n} homes have an offer worth "
              "more than $0 (offers don't stack unless the fine print says they combine).", "",
              "## Ten biggest discounts", "", "| Home | Asking | True price | Counted offers |", "|---|---:|---:|---|"]
    for hid, addr, page, ask, tl, th in q("SELECT h.id, h.address, h.source_page, t.asking, t.true_low, t.true_high "
                                           "FROM true_prices t JOIN homes h ON h.id = t.home_id "
                                           "ORDER BY 1.0*t.value_high/t.asking DESC LIMIT 10"):
        offers = q(f"SELECT i.label, v.value_low, v.value_high FROM incentive_values v JOIN incentives i ON "
                   f"i.id = v.incentive_id WHERE v.home_id = {hid} AND v.flags NOT LIKE '%not_counted%' "
                   f"AND v.value_high > 0")
        lines.append(f"| {addr} ({page.split('/')[0]}) | ${ask:,} | ${tl:,}–${th:,} | "
                     + "; ".join(f"{(l or '')[:50]} (${a:,}–${b:,})" for l, a, b in offers) + " |")
    path = ROOT / "eval" / "reports" / f"{date}__true_prices.md"
    path.write_text("\n".join(lines) + "\n")
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    date = ap.parse_args().date or sorted(p.name for p in EXTRACT.iterdir() if p.is_dir())[-1]
    conn = db.connect()
    stats = load(date, conn)
    print(json.dumps(stats, indent=1))
    print(report(date, stats, conn))
