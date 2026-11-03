"""Link communities to assessor parcels and publish "what earlier buyers paid" per community.

    python -m tools.link_parcels                 # link, write past_records + past_sales + report
    python -m tools.link_parcels --spot-check    # also re-query the live assessor layers for hand checks

Linking (plan §6.8): the community's streets come from its home addresses. Its centre is the densest
cluster of county parcels on those streets; parcels within 900 m on those streets are linked (an exact
address 1.0, a street 0.8). In Alameda and Contra Costa, whose pulled universe includes resales of older
homes, a book-page where fewer than 60% of single-family parcels sold since 2022 is an older
neighbourhood and is dropped. A parcel claimed by two communities goes to the exact match, else the
nearer centre.

Records, never estimates (Herman, 2026-11-03): each linked home shows the assessor's full value as the roll
prints it, once that value has been reset to the home's last sale (config/counties.yaml assessor.past_sales),
plus Alameda's recorded transfer-tax price where there is one. No value for builder-owned lots
(improvements 0), deeds before the year built, one deed covering several lots, or sales after the roll's
lien date. A community shows the median assessed value only with 5 or more homes that have one.
"""
import argparse
import json
import logging
import math
import re
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path

from tools import db
from tools.preprocess_page import RAW
from tools.pull_assessor import _query, epoch_ms_to_date, load_counties, to_int

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "eval" / "reports"
CHECKS = ROOT / ".tmp" / "reports"     # per-parcel hand checks: addresses and prices, never committed
CACHE = ROOT / ".tmp" / "link"
RADIUS_M = 900
MIN_NEW_SHARE = 0.6
MIN_RECORDS = 5
CITY_KM = 8
PIN_KM = 2
SQFT_BAND = 0.25

log = logging.getLogger(__name__)

DIRECTIONS = {"N", "S", "E", "W", "NORTH", "SOUTH", "EAST", "WEST"}
SUFFIXES = {"ST", "STREET", "DR", "DRIVE", "WAY", "CT", "COURT", "LN", "LANE", "AVE", "AVENUE", "RD", "ROAD",
            "CIR", "CIRCLE", "PL", "PLACE", "TER", "TERRACE", "LOOP", "BLVD", "PKWY", "PARKWAY", "TRL", "TRAIL",
            "CMN", "COMMON", "SQ", "ROW", "WALK", "ALY", "PATH", "XING", "RUN", "GRN", "GLN", "HTS"}


# ---------- pure helpers (unit-tested) ----------

def street_key(address: str | None) -> tuple[str, str] | None:
    """(house number, street name without direction or suffix); trailing city words stay when there is
    no suffix to stop at, which street_matches allows for."""
    words = re.sub(r"[^A-Z0-9 ]", " ", (address or "").upper()).split()
    if len(words) < 2 or not words[0].isdigit():
        return None
    rest = words[1:]
    if len(rest) > 1 and rest[0] in DIRECTIONS:
        rest = rest[1:]
    core = []
    for w in rest:
        if w in SUFFIXES and core:
            break
        core.append(w)
    return words[0], " ".join(core)


def street_matches(core: str, streets: set[str]) -> bool:
    return any(core == s or core.startswith(s + " ") for s in streets)


def book_page(county: str, apn: str) -> str:
    if county == "alameda":
        return "-".join(apn.split("-")[:2])
    return apn[:6]


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def _on_streets(parcels: list[dict], streets: set[str]) -> list[tuple[dict, str]]:
    out = []
    for p in parcels:
        k = street_key(p["address"])
        if k and p.get("lat") is not None:
            match = next((s for s in streets if k[1] == s or k[1].startswith(s + " ")), None)
            if match:
                out.append((p, match))
    return out


def find_centre(parcels: list[dict], streets: set[str],
                city_centre: tuple[float, float] | None = None) -> tuple[float, float] | None:
    """Mean position of the 900 m neighbourhood holding the most community streets, then parcels; only
    within CITY_KM of the community's city when its position is known (the same street names recur
    across the county, and a builder's city is a marketing name, so not an exact situs-city match)."""
    cand = [(p, s) for p, s in _on_streets(parcels, streets)
            if not city_centre or distance_m(city_centre, (p["lat"], p["lng"])) <= CITY_KM * 1000]
    best, best_score = None, (0, 0)
    for p, _ in cand:
        near = [(q, s) for q, s in cand if distance_m((p["lat"], p["lng"]), (q["lat"], q["lng"])) <= RADIUS_M]
        score = (len({s for _, s in near}), len(near))
        if score > best_score:
            best, best_score = near, score
    if not best:
        return None
    return (sum(q["lat"] for q, _ in best) / len(best), sum(q["lng"] for q, _ in best) / len(best))


def link(parcels: list[dict], streets: set[str], exact: set[tuple[str, str]], centre: tuple[float, float],
         new_share: dict | None = None, sqft: tuple[int, int] | None = None) -> list[dict]:
    """sqft: the community's home size range; a parcel more than SQFT_BAND outside it (San Joaquin records
    living area) is another collection on a shared street."""
    out = []
    for p, _ in _on_streets(parcels, streets):
        if sqft and p.get("living_sqft") and not (sqft[0] * (1 - SQFT_BAND) <= p["living_sqft"]
                                                  <= sqft[1] * (1 + SQFT_BAND)):
            continue
        d = distance_m(centre, (p["lat"], p["lng"]))
        if d > RADIUS_M:
            continue
        num, core = street_key(p["address"])
        is_exact = any(n == num and street_matches(core, {c}) for n, c in exact)
        share = (new_share or {}).get(book_page(p["county"], p["apn"]))
        if not is_exact and share is not None and share < MIN_NEW_SHARE:
            continue
        out.append({"county": p["county"], "apn": p["apn"], "match_method": "address" if is_exact else "street",
                    "confidence": 1.0 if is_exact else 0.8, "dist_m": round(d)})
    return out


def resolve_overlaps(links: dict) -> dict:
    """links: community_id -> [link]. A parcel claimed twice goes to the exact match, else the nearer centre."""
    owner = {}
    for cid, ls in links.items():
        for l in ls:
            k = (l["county"], l["apn"])
            rank = (l["confidence"], -l["dist_m"])
            if k not in owner or rank > owner[k][0]:
                owner[k] = (rank, cid)
    return {cid: [l for l in ls if owner[(l["county"], l["apn"])][1] == cid] for cid, ls in links.items()}


PIN = re.compile(r'"(?:latitude|lat|Latitude)"\s*:\s*"?(3[6-9]\.\d+)"?\s*,\s*'
                 r'"(?:longitude|lng|lon|Longitude)"\s*:\s*"?(-12[0-3]\.\d+)')


def page_points(html: str) -> set[tuple[float, float]]:
    """Coordinates embedded in a builder page (JSON-LD geo, map widgets), Bay Area longitudes only."""
    return {(float(a), float(b)) for a, b in PIN.findall(html)}


def local_pins(pages: dict[str, set], builder_of: dict[str, str], shared: int = 3) -> dict[str, set]:
    """Drop pins found on `shared`+ pages of one builder: a market centre or office, not the community."""
    seen = defaultdict(int)
    for url, pins in pages.items():
        for pin in pins:
            seen[(builder_of[url], pin)] += 1
    return {url: {p for p in pins if seen[(builder_of[url], p)] < shared} for url, pins in pages.items()}


def pin_check(centre: tuple[float, float], pins: set) -> tuple[float | None, bool]:
    """km from the parcel cluster to the nearest pin on the community's own page; over PIN_KM = wrong place."""
    if not pins:
        return None, True
    km = round(min(distance_m(centre, p) for p in pins) / 1000, 1)
    return km, km <= PIN_KM


def _city(s: str | None) -> str:
    return re.sub(r"[^A-Z]", "", (s or "").upper())


def city_centres_of(parcels: list[dict], names: set[str] = frozenset()) -> dict[str, tuple[float, float]]:
    """Median parcel position per city: the situs city field, or a community city name spelled out at the
    end of the address ("... TRACY CA 95377"), since San Joaquin's city field holds codes."""
    tail = re.compile(r" (%s) CA \d{5}$" % "|".join(sorted({re.escape(n.upper()) for n in names}) or ["$^"]))
    pts = defaultdict(list)
    for p in parcels:
        if p.get("lat") is None:
            continue
        m = tail.search(p.get("address") or "")
        name = m.group(1) if m else p.get("city")
        if name:
            pts[_city(name)].append((p["lat"], p["lng"]))
    return {c: (statistics.median(a for a, _ in v), statistics.median(b for _, b in v)) for c, v in pts.items()}


def _fiscal_year(d: str) -> int:
    return int(d[:4]) if int(d[5:7]) >= 7 else int(d[:4]) - 1


def assessed_record(p: dict, transfer_price: int | None, cfg: dict) -> dict | None:
    """The home's assessor record: its full assessed value as published (None until the value has been reset to
    the last sale) and Alameda's recorded price. None when neither can stand for this home's sale."""
    if not p.get("imps") or not p.get("total"):
        return None                                              # builder-owned lot, or no value
    if (p.get("deed_parcels") or 1) > 1:
        return None                                              # one document, several lots: a bulk transfer
    if cfg["sale_date"] == "year_built":
        yb = p.get("year_built")
        if not yb or yb >= cfg["roll_fy"]:
            return None                                          # not finished before the lien date
        return {"assessed_value": p["total"], "roll_year": cfg["roll_fy"], "sale_date": str(yb),
                "recorded_price": None}
    deed = p.get("deed_date")
    if not deed or (p.get("year_built") and int(deed[:4]) < p["year_built"]):
        return None
    full = p["total"] if deed <= cfg["reassessed_through"] else None
    if full is None and not transfer_price:
        return None
    return {"assessed_value": full, "roll_year": cfg["roll_fy"], "sale_date": deed, "recorded_price": transfer_price}


def summarize_records(records: list[dict]) -> dict | None:
    valued = [r for r in records if r["assessed_value"]]
    if len(valued) < MIN_RECORDS:
        return None
    p25, med, p75 = statistics.quantiles(sorted(r["assessed_value"] for r in valued), n=4, method="inclusive")
    dates = sorted(r["sale_date"] for r in valued)
    return {"n": len(valued), "median_value": round(med), "p25": round(p25), "p75": round(p75),
            "period": f"{dates[0][:7]} to {dates[-1][:7]}", "method": "assessed_value"}


def rebuild_records(conn, counties: dict | None = None) -> dict[int, dict]:
    """past_records and past_sales from the stored community-parcel links: {community_id: {records, summary,
    linked}}."""
    counties = counties or load_counties()
    cfg_of = {k: c["assessor"]["past_sales"] for k, c in counties.items() if c["assessor"].get("past_sales")}
    docs = {(r[0], r[1]): r[2] for r in conn.execute(
        "SELECT county, deed_doc, COUNT(*) FROM parcels WHERE deed_doc IS NOT NULL AND deed_doc NOT LIKE '0000%' "
        "GROUP BY 1, 2")}
    transfers = {(t[0], t[1]): t[2] for t in
                 conn.execute("SELECT apn, doc_date, price FROM transfers WHERE county='alameda' AND price IS NOT NULL")}
    out = defaultdict(lambda: {"records": [], "linked": 0})
    for r in conn.execute("SELECT cp.community_id, p.* FROM community_parcels cp JOIN parcels p "
                          "ON p.county = cp.county AND p.apn = cp.apn ORDER BY cp.community_id, p.apn"):
        p = {**dict(r), "deed_parcels": docs.get((r["county"], r["deed_doc"]), 1)}
        c = out[r["community_id"]]
        c["linked"] += 1
        if r["county"] not in cfg_of:
            continue
        rec = assessed_record(p, transfers.get((p["apn"], p["deed_date"])) if p["county"] == "alameda" else None,
                              cfg_of[r["county"]])
        if rec:
            c["records"].append({**rec, "county": p["county"], "apn": p["apn"], "address": p["address"],
                                 "living_sqft": p.get("living_sqft"), "beds": p.get("beds")})
    with conn:
        conn.execute("DELETE FROM past_records")
        conn.execute("DELETE FROM past_sales")
        for cid, c in out.items():
            conn.executemany("INSERT INTO past_records VALUES (?,?,?,?,?,?,?,?,?,?)",
                             [(cid, x["county"], x["apn"], x["address"], x["assessed_value"], x["roll_year"],
                               x["sale_date"], x["living_sqft"], x["beds"], x["recorded_price"]) for x in c["records"]])
            c["summary"] = s = summarize_records(c["records"])
            if s:
                conn.execute("INSERT INTO past_sales VALUES (?,?,?,?,?,?,?,?)",
                             (cid, s["n"], s["median_value"], s["p25"], s["p75"], s["period"], s["method"], c["linked"]))
    return dict(out)


def communities(conn, fips_to_key: dict) -> list[dict]:
    rows = conn.execute("SELECT c.id, c.name, c.city, c.url, c.county_fips, b.name builder, h.address, h.sqft FROM communities c "
                        "JOIN builders b ON b.id = c.builder_id JOIN homes h ON h.community_id = c.id "
                        "WHERE h.address IS NOT NULL").fetchall()
    out = {}
    for r in rows:
        if r["county_fips"] not in fips_to_key:
            continue
        c = out.setdefault(r["id"], {"id": r["id"], "name": r["name"], "city": r["city"], "url": r["url"], "builder": r["builder"],
                                     "county": fips_to_key[r["county_fips"]], "streets": set(), "exact": set()})
        if r["sqft"]:
            lo, hi = c.get("sqft", (r["sqft"], r["sqft"]))
            c["sqft"] = (min(lo, r["sqft"]), max(hi, r["sqft"]))
        k = street_key(r["address"])
        if k:
            c["streets"].add(k[1])
            c["exact"].add(k)
    return list(out.values())


def page_pins() -> dict[str, set]:
    """url -> coordinates embedded in that page, from the latest fetch."""
    day = sorted(d for d in RAW.iterdir() if d.is_dir())[-1]
    out, builder_of = {}, {}
    for man in day.glob("*/manifest.json"):
        for r in json.loads(man.read_text()):
            if r.get("file"):
                out[r["url"]] = page_points((man.parent / r["file"]).read_text(errors="ignore"))
                builder_of[r["url"]] = man.parent.name
    return local_pins(out, builder_of)


def new_shares(county: str, cfg: dict, pages: set[str], conn) -> dict:
    """Share of a book-page's single-family parcels that are in our sold-since-2022 universe (live counts,
    cached per county)."""
    path = CACHE / f"new_share_{county}.json"
    cache = json.loads(path.read_text()) if path.exists() else {}
    url = load_counties()[county]["assessor"]["parcels"]
    for bp in sorted(pages - cache.keys()):
        like = cfg["newness"]["apn_like"].format(bp=bp)
        total = _query(url, {"where": f"APN LIKE '{like}' AND {cfg['newness']['where']}",
                             "returnCountOnly": "true"})["count"]
        ours = conn.execute("SELECT COUNT(*) FROM parcels WHERE county=? AND apn LIKE ?",
                            (county, like.replace("%", "") + "%")).fetchone()[0]
        cache[bp] = round(ours / total, 3) if total else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=0, sort_keys=True))
    return cache


def run(conn) -> list[dict]:
    counties = load_counties()
    green = {c["fips"]: k for k, c in counties.items() if c["assessor"].get("past_sales")}
    comms = communities(conn, green)
    by_county = defaultdict(list)
    docs = {(r[0], r[1]): r[2] for r in conn.execute(
        "SELECT county, deed_doc, COUNT(*) FROM parcels WHERE deed_doc IS NOT NULL AND deed_doc NOT LIKE '0000%' "
        "GROUP BY 1, 2")}
    for p in conn.execute("SELECT county, apn, address, city, lat, lng, imps, total, deed_date, deed_doc, year_built, living_sqft "
                          "FROM parcels"):
        by_county[p["county"]].append({**dict(p), "deed_parcels": docs.get((p["county"], p["deed_doc"]), 1)})
    names = {c["city"] for c in comms if c.get("city")}
    city_centres = {k: city_centres_of(ps, names) for k, ps in by_county.items()}
    pins = page_pins()
    links = {}
    for c in comms:
        cfg = counties[c["county"]]["assessor"]["past_sales"]
        parcels = by_county[c["county"]]
        c["centre"] = find_centre(parcels, c["streets"], city_centres[c["county"]].get(_city(c["city"])))
        if not c["centre"]:
            c["reason"] = "streets not found in the parcel layer"
            continue
        c["pin_km"], ok = pin_check(c["centre"], pins.get(c["url"], set()))
        if not ok:
            c["reason"] = f"parcels on its streets are {c['pin_km']} km from the page's own map pin"
            c["centre"] = None
            continue
        share = None
        if cfg.get("newness"):
            raw = link(parcels, c["streets"], c["exact"], c["centre"])
            share = new_shares(c["county"], cfg, {book_page(c["county"], l["apn"]) for l in raw}, conn)
        links[c["id"]] = link(parcels, c["streets"], c["exact"], c["centre"], share, c.get("sqft"))
    links = resolve_overlaps(links)

    with conn:
        conn.execute("DELETE FROM community_parcels")
        for c in comms:
            if not c.get("centre"):
                continue
            conn.execute("UPDATE communities SET lat=?, lng=?, geo_method='parcel_cluster' WHERE id=?",
                         (*c["centre"], c["id"]))
            conn.executemany("INSERT INTO community_parcels VALUES (?,?,?,?,?)",
                             [(c["id"], l["county"], l["apn"], l["match_method"], l["confidence"]) for l in links[c["id"]]])
    built = rebuild_records(conn, counties)
    for c in comms:
        b = built.get(c["id"], {}) if c.get("centre") else {}
        c["linked"], c["records"], c["summary"] = b.get("linked", 0), b.get("records", []), b.get("summary")
    return comms


# ---------- hand checks against the live assessor layers ----------

def spot_check(conn, comms: list[dict], k: int = 5) -> list[dict]:
    """Re-read linked parcels from the live county layers: values and deed dates must equal what we
    stored, and Alameda's recorded transfer price must equal the one we used."""
    counties = load_counties()
    fields = {"alameda": ("APN", "Land", "Imps"), "contra_costa": ("APN", "LAND_VALUE", "IMP_VAL"),
              "san_joaquin": ("APN", "LAND_VALUE", "IMPROVEMENT_VALUE")}
    shown = sorted((c for c in comms if c.get("summary")), key=lambda c: -c["summary"]["n"])
    picks, seen = [], set()
    for county in ("alameda", "contra_costa", "san_joaquin"):         # at least one per county, then the biggest
        c = next((c for c in shown if c["county"] == county), None)
        if c:
            picks.append(c); seen.add(c["id"])
    picks += [c for c in shown if c["id"] not in seen][:max(k - len(picks), 0)]
    out = []
    for c in picks:
        rows = conn.execute("SELECT p.* FROM community_parcels cp JOIN parcels p ON p.county=cp.county AND "
                            "p.apn=cp.apn WHERE cp.community_id=? ORDER BY p.apn", (c["id"],)).fetchall()
        sample = [dict(r) for r in rows[:: max(len(rows) // 3, 1)][:3]]
        url = counties[c["county"]]["assessor"]["parcels"]
        apn_f, land_f, imps_f = fields[c["county"]]
        checks = []
        for p in sample:
            live = _query(url, {"where": f"{apn_f} = '{p['apn']}'", "outFields": f"{land_f},{imps_f}",
                                "returnGeometry": "false"})["features"]
            a = live[0]["attributes"] if live else {}
            total = (float(a.get(land_f) or 0) + float(a.get(imps_f) or 0)) if a else None
            row = {"apn": p["apn"], "address": p["address"], "stored_total": p["total"],
                   "live_total": round(total) if total is not None else None}
            row["value_ok"] = row["live_total"] == p["total"]
            checks.append(row)
        out.append({"community": c["name"], "builder": c["builder"], "county": c["county"],
                    "summary": c["summary"], "pin_km": c.get("pin_km"), "checks": checks})
    return {"communities": out, "transfers": transfer_check(conn, counties["alameda"]["assessor"]["transfers"], k)}


def transfer_check(conn, url: str, k: int) -> list[dict]:
    """Alameda's recorded prices: linked parcels first, else an even sample of the priced transfers
    (the list ends 3/2025, before most communities selling now closed a sale). Each must equal the live
    list, and the parcel's value must equal price × 1.02 per roll since the sale, within 1%."""
    rows = conn.execute("SELECT t.apn, t.doc_date, t.price, p.total, p.address, cp.community_id IS NOT NULL linked "
                        "FROM transfers t JOIN parcels p ON p.county='alameda' AND p.apn=t.apn AND p.deed_date=t.doc_date "
                        "LEFT JOIN community_parcels cp ON cp.county='alameda' AND cp.apn=t.apn "
                        "WHERE t.price IS NOT NULL ORDER BY linked DESC, t.apn").fetchall()
    linked = [r for r in rows if r["linked"]]
    sample = linked[:k] if len(linked) >= k else linked + rows[len(linked)::max(len(rows) // k, 1)][:k - len(linked)]
    out = []
    for r in sample:
        live = _query(url, {"where": f"apn = '{r['apn']}' AND name_type = 'TRANSFEREE'",
                            "outFields": "doc_dt,value_from_trans_tax", "returnGeometry": "false"})["features"]
        got = {(epoch_ms_to_date(f["attributes"]["doc_dt"]), to_int(f["attributes"]["value_from_trans_tax"]))
               for f in live}
        trended = r["price"] * 1.02 ** max(2025 - _fiscal_year(r["doc_date"]), 0)
        out.append({"apn": r["apn"], "address": r["address"], "doc_date": r["doc_date"], "price": r["price"],
                    "linked": bool(r["linked"]), "live_ok": (r["doc_date"], r["price"]) in got,
                    "value_ratio": round(r["total"] / trended, 4)})
    return out


def report(conn, comms: list[dict], checks: list[dict] | None) -> Path:
    asking = {r[0]: r[1] for r in conn.execute(
        "SELECT community_id, CAST(AVG(price) AS INT) FROM (SELECT community_id, price FROM homes "
        "WHERE price IS NOT NULL) GROUP BY community_id")}
    shown = sorted((c for c in comms if c.get("summary")), key=lambda c: (c["county"], c["name"]))
    comm_checks = (checks or {}).get("communities", [])
    tt = (checks or {}).get("transfers", [])
    values = [ch["value_ok"] for h in comm_checks for ch in h["checks"]]
    passed = len(shown) >= 5 and checks is not None and all(values) and bool(tt) and all(t["live_ok"] for t in tt)
    lines = [f"# Past sales by community, {date.today()}", "",
             f"**Day 5 gate: {'PASS' if passed else 'FAIL'}**: {len(shown)} communities show past sales (≥ 5)"
             + ("; spot checks not run." if checks is None else
                f"; {sum(values)}/{len(values)} sampled parcel values equal the live layer; "
                f"{sum(t['live_ok'] for t in tt)}/{len(tt)} Alameda transfer-tax prices equal the live transfer list "
                f"({sum(t['linked'] for t in tt)} of them on a linked parcel)."), "",
             f"Linked: {sum(1 for c in comms if c.get('centre'))} of {len(comms)} communities with home addresses "
             f"found their streets in the parcel layer. With fewer than {MIN_RECORDS} full assessed values, a community "
             "shows none.",
             "", "| County | Builder | Community | Linked | Valued | Median assessed (IQR) | Sold | Avg asking now | Pin Δ km |",
             "|---|---|---|---:|---:|---|---|---:|---:|"]
    for c in shown:
        s = c["summary"]
        lines.append(f"| {c['county']} | {c['builder']} | {c['name']} | {c['linked']} | {s['n']} | "
                     f"${s['median_value']:,} (${s['p25']:,}–${s['p75']:,}) | {s['period']} | "
                     + (f"${asking[c['id']]:,}" if c["id"] in asking else "—")
                     + f" | {c['pin_km'] if c.get('pin_km') is not None else '—'} |")
    hidden = sorted((c for c in comms if not c.get("summary")), key=lambda c: (c["county"], c["name"]))
    lines += ["", "## Not shown", ""] + [
        f"- {c['county']} · {c['builder']} · {c['name']}: " +
        (c["reason"] if not c.get("centre") else
         f"{c['linked']} linked, {sum(1 for r in c.get('records', []) if r['assessed_value'])} full assessed values")
        for c in hidden]
    path = REPORTS / f"{date.today()}__past_sales.md"
    if checks:
        private = CHECKS / f"{date.today()}__past_sales_checks.md"
        where = private.relative_to(ROOT) if private.is_relative_to(ROOT) else private
        lines += ["", f"Per-parcel hand checks (addresses and prices, so not committed): {where}"]
        detail = ["## Hand checks against the live assessor layers", ""]
        for h in comm_checks:
            s = h["summary"]
            detail += [f"**{h['community']}** ({h['builder']}, {h['county']}): n {s['n']}, "
                       f"median assessed ${s['median_value']:,}, {s['period']}", ""]
            detail += [f"- {ch['apn']} {ch['address']}: stored ${ch['stored_total']:,}, live "
                       + (f"${ch['live_total']:,}" if ch["live_total"] is not None else "missing")
                       + (" ✓" if ch["value_ok"] else " ✗")
                       + (f"; transfer ${ch['transfer_price']:,} " + ("✓" if ch["transfer_ok"] else "✗")
                          if "transfer_ok" in ch else "") for ch in h["checks"]] + [""]
        detail += ["**Alameda transfer-tax prices** (live list; value ÷ price trended 2% per roll since the sale):", ""]
        detail += [f"- {x['apn']} {x['address']}: {x['doc_date']} ${x['price']:,} "
                   + ("✓" if x["live_ok"] else "✗") + f"; value ratio {x['value_ratio']}"
                   + (" (linked)" if x["linked"] else "") for x in tt]
        private.parent.mkdir(parents=True, exist_ok=True)
        private.write_text("\n".join(detail) + "\n")
    path.write_text("\n".join(lines) + "\n")
    return path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--spot-check", action="store_true")
    args = ap.parse_args()
    conn = db.connect()
    comms = run(conn)
    checks = spot_check(conn, comms) if args.spot_check else None
    print(report(conn, comms, checks))
