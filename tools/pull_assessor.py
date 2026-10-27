"""Pull county assessor parcels (and Alameda's priced transfers) from public ArcGIS layers.

    python -m tools.pull_assessor --county alameda      # or contra_costa, san_joaquin, all

Pulls each county's `universe_where` set (config/counties.yaml) by object id in chunks, checks the
row count against the layer's own returnCountOnly, and upserts into `parcels` / `transfers`.
"""
import argparse
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

from tools import db

ROOT = Path(__file__).resolve().parent.parent
CHUNK = 500
PAUSE_S = 0.5  # between requests to one county server

log = logging.getLogger(__name__)
session = requests.Session()


def load_counties() -> dict[str, dict]:
    cfg = yaml.safe_load((ROOT / "config" / "counties.yaml").read_text())
    return {c["key"]: c for c in cfg["counties"]}


# ---------- pure helpers (unit-tested) ----------

def clean(s) -> str | None:
    """Collapse whitespace; ArcGIS pads fixed-width fields. Empty -> None."""
    if s is None:
        return None
    s = " ".join(str(s).split()).replace(" ,", ",")
    return s or None


def epoch_ms_to_date(ms) -> str | None:
    if ms in (None, ""):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def yyyymmdd_to_date(s) -> str | None:
    s = clean(s)
    if not s or len(s) != 8 or not s.isdigit() or s == "00000000":
        return None
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


def to_int(v) -> int | None:
    """'000001250000.' / ' 2024' / 500000 / '' -> int or None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().rstrip(".")
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def ring_centroid(geometry: dict | None) -> tuple[float, float] | None:
    """Average of the outer ring's vertices (closing point dropped). Good enough for a lot."""
    rings = (geometry or {}).get("rings") or []
    if not rings or len(rings[0]) < 3:
        return None
    pts = rings[0][:-1] if rings[0][0] == rings[0][-1] else rings[0]
    return (sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts))


def _total(land, imps):
    return None if land is None and imps is None else (land or 0) + (imps or 0)


def norm_alameda(a: dict, latlng) -> dict:
    land, imps = to_int(a.get("Land")), to_int(a.get("Imps"))
    prefix, series = clean(a.get("LatestDocument_Prefix")), clean(a.get("LatestDocumentSeries"))
    return dict(county="alameda", apn=clean(a["APN"]), address=clean(a.get("SitusAddress")),
                city=clean(a.get("SitusCity")), land=land, imps=imps, total=_total(land, imps),
                roll_year=None, deed_date=epoch_ms_to_date(a.get("LatestDocumentDate")),
                deed_doc=f"{prefix}-{series}" if prefix and series else None, year_built=None,
                use_code=clean(a.get("UseCode")), beds=None, living_sqft=None, latlng=latlng)


def norm_contra_costa(a: dict, latlng) -> dict:
    land, imps = to_int(a.get("LAND_VALUE")), to_int(a.get("IMP_VAL"))
    return dict(county="contra_costa", apn=clean(a["APN"]), address=clean(a.get("full_address_display")),
                city=clean(a.get("s_city")), land=land, imps=imps, total=_total(land, imps),
                roll_year=None, deed_date=yyyymmdd_to_date(a.get("C_DED_DT")),
                deed_doc=clean(a.get("C_DED_NBR")), year_built=to_int(a.get("YR_BUILT")),
                use_code=clean(a.get("USE_CODE")), beds=None, living_sqft=None, latlng=latlng)


def norm_san_joaquin(a: dict, latlng) -> dict:
    land, imps = to_int(a.get("LAND_VALUE")), to_int(a.get("IMPROVEMENT_VALUE"))
    return dict(county="san_joaquin", apn=clean(a["APN"]), address=clean(a.get("FULL_ADDRESS")),
                city=clean(a.get("SITUSCITY")), land=land, imps=imps, total=_total(land, imps),
                roll_year=to_int(a.get("VALUE_ROLL_YEAR")), deed_date=None, deed_doc=None,
                year_built=to_int(a.get("YEAR_BUILT")), use_code=clean(a.get("USECODE")),
                beds=to_int(a.get("BEDROOMS")), living_sqft=to_int(a.get("TOTALLIV_AREA")), latlng=latlng)


NORMALIZERS = {"alameda": norm_alameda, "contra_costa": norm_contra_costa, "san_joaquin": norm_san_joaquin}


def norm_transfers(rows: list[dict]) -> list[dict]:
    """Alameda transfer list: one row per buyer, so collapse to one per (apn, doc date).
    A document covering several parcels carries the price of all of them: keep the sale, drop the price."""
    out = {}
    for a in rows:
        apn, doc_date = clean(a.get("apn")), epoch_ms_to_date(a.get("doc_dt"))
        if not apn or not doc_date:
            continue
        count = to_int(a.get("doc_parcel_count")) or 1
        price = to_int(a.get("value_from_trans_tax"))
        prefix, series = clean(a.get("doc_prefix")), clean(a.get("doc_series"))
        out[(apn, doc_date)] = dict(county="alameda", apn=apn, doc_date=doc_date,
                                    doc_id=f"{prefix}-{series}" if prefix and series else None,
                                    price=price if (price and count == 1) else None, parcel_count=count)
    return list(out.values())


# ---------- ArcGIS I/O ----------

def _query(url: str, params: dict, post: bool = False) -> dict:
    params = {**params, "f": "json"}
    for attempt in range(3):
        try:
            r = (session.post(url + "/query", data=params, timeout=120) if post
                 else session.get(url + "/query", params=params, timeout=120))
            r.raise_for_status()
            j = r.json()
            if "error" in j:
                raise RuntimeError(j["error"])
            time.sleep(PAUSE_S)
            return j
        except (requests.RequestException, RuntimeError) as e:
            if attempt == 2:
                raise
            log.warning("retry %s after %s", attempt + 1, e)
            time.sleep(5 * (attempt + 1))


def fetch_layer(url: str, where: str, centroid: str | None) -> list[tuple[dict, tuple | None]]:
    """All features matching `where`, as (attributes, (lat, lng) | None). Raises if the row count
    differs from the server's own count."""
    expected = _query(url, {"where": where, "returnCountOnly": "true"})["count"]
    ids = sorted(_query(url, {"where": where, "returnIdsOnly": "true"})["objectIds"] or [])
    out = []
    for i in range(0, len(ids), CHUNK):
        p = {"objectIds": ",".join(map(str, ids[i:i + CHUNK])), "outFields": "*", "outSR": 4326}
        if centroid == "server":
            p.update(returnGeometry="false", returnCentroid="true")
        elif centroid == "geometry":
            p.update(returnGeometry="true", geometryPrecision=6, maxAllowableOffset=0.00005)
        else:
            p.update(returnGeometry="false")
        for f in _query(url, p, post=True).get("features", []):
            if centroid == "server" and f.get("centroid"):
                ll = (f["centroid"]["y"], f["centroid"]["x"])
            elif centroid == "geometry":
                ll = ring_centroid(f.get("geometry"))
            else:
                ll = None
            out.append((f["attributes"], ll))
        log.info("  %s/%s", min(i + CHUNK, len(ids)), len(ids))
    if len(out) != expected:
        raise RuntimeError(f"row count {len(out)} != server count {expected} for {url}")
    return out


def upsert_parcels(conn, rows: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO parcels (county, apn, address, city, land, imps, total, roll_year, deed_date,"
            " deed_doc, year_built, use_code, beds, living_sqft, lat, lng, pulled_at)"
            " VALUES (:county, :apn, :address, :city, :land, :imps, :total, :roll_year, :deed_date,"
            " :deed_doc, :year_built, :use_code, :beds, :living_sqft, :lat, :lng, :pulled_at)",
            [{**r, "lat": r["latlng"][0] if r["latlng"] else None,
              "lng": r["latlng"][1] if r["latlng"] else None, "pulled_at": now} for r in rows])


def upsert_transfers(conn, rows: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO transfers VALUES (:county, :apn, :doc_date, :doc_id, :price, :parcel_count, :pulled_at)",
            [{**r, "pulled_at": now} for r in rows])


def pull(county_key: str, conn=None) -> dict:
    c = load_counties()[county_key]
    a = c["assessor"]
    if a.get("status") != "green":
        raise SystemExit(f"{c['name']}: no free assessor source (status {a.get('status')})")
    conn = conn or db.connect()
    log.info("%s parcels", c["name"])
    feats = fetch_layer(a["parcels"], a["universe_where"], a.get("centroid"))
    rows = [NORMALIZERS[county_key](attrs, ll) for attrs, ll in feats]
    upsert_parcels(conn, rows)
    result = {"county": county_key, "parcels": len(rows),
              "with_latlng": sum(1 for r in rows if r["latlng"])}
    if a.get("transfers"):
        log.info("%s transfers", c["name"])
        t = norm_transfers([attrs for attrs, _ in fetch_layer(a["transfers"], a["transfers_where"], None)])
        upsert_transfers(conn, t)
        result.update(transfers=len(t), priced=sum(1 for r in t if r["price"]))
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--county", required=True, choices=["alameda", "contra_costa", "san_joaquin", "all"])
    args = ap.parse_args()
    keys = ["alameda", "contra_costa", "san_joaquin"] if args.county == "all" else [args.county]
    for k in keys:
        print(pull(k))
