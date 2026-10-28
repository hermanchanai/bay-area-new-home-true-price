"""Read floor-plan images with Muse Spark vision: rooms and dimensions per floor, plus layout facts.

    python -m tools.read_floorplan                  # Lennar plans on the latest fetch
    python -m tools.read_floorplan --limit 3

Source: Lennar's plan records (per-floor SVG floor plans in the page's Apollo cache). Each SVG is
fetched politely, rendered to PNG in .tmp/ with macOS qlmanage (never committed), and all of a plan's
floors go to the model in one call. Two checks:
- labels: the SVG carries the room names and dimensions as text, a model-free truth for what the
  model read off the image (scored per room: same printed name, dimensions within 0.05 ft);
- area: summed dimensioned living area vs the plan's stated sq ft, an outlier flag (AREA_BAND).
Writes plans.layout_json and plans.image_url, and eval/reports/<date>__floorplans.md.
"""
import argparse
import base64
import html
import json
import logging
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools import db, llm
from tools.fetch_builder_pages import Fetcher, load_config
from tools.preprocess_page import RAW, latest_date

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / ".tmp" / "floorplans"
REPORTS = ROOT / "eval" / "reports"
DIMS = re.compile(r"(\d+)\s*'\s*(?:(\d+)\s*\")?\s*[xX×]\s*(\d+)\s*'\s*(?:(\d+)\s*\")?")
NOT_LIVING = re.compile(r"(?i)garage|porch|patio|deck|balcony|courtyard|terrace")
# Plan §6.6 said 60-110% of stated sq ft, assuming every room is dimensioned. Lennar dimensions only
# bedrooms, living rooms and garages (5th-95th percentile 0.44-0.54 over 107 plans), so this is an
# outlier flag around that spread, not an accuracy gate; the label check is the gate.
AREA_BAND = (0.35, 0.75)

log = logging.getLogger(__name__)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def parse_dims(text: str | None) -> tuple[float, float] | None:
    m = DIMS.search(html.unescape(text or ""))
    if not m:
        return None
    w = int(m.group(1)) + int(m.group(2) or 0) / 12
    d = int(m.group(3)) + int(m.group(4) or 0) / 12
    return w, d


def _r(d: tuple[float, float]) -> tuple[float, float]:
    return round(d[0], 3), round(d[1], 3)


def _xy(attrs: str) -> tuple[float, float]:
    m = re.search(r'transform="(?:matrix\((?:[-\d.]+\s+){4}|translate\()([-\d.]+)[\s,]+([-\d.]+)', attrs)
    if m:
        return float(m.group(1)), float(m.group(2))
    x, y = re.search(r'\bx="([-\d.]+)"', attrs), re.search(r'\by="([-\d.]+)"', attrs)
    return float(x.group(1)) if x else 0.0, float(y.group(1)) if y else 0.0


def _svg_items(svg: str) -> list[tuple[str, float, float]]:
    """Each printed line (text or tspan) with its position; duplicates (same text, same spot) once."""
    items, seen = [], set()
    for attrs, body in re.findall(r"<text([^>]*)>(.*?)</text>", svg, re.S):
        tx, ty = _xy(attrs)
        spans = re.findall(r"<tspan([^>]*)>([^<]*)</tspan>", body) or [("", re.sub(r"<[^>]+>", "", body))]
        for sattrs, text in spans:
            sx, sy = _xy(sattrs)
            text = html.unescape(text).strip()
            key = (text, round(tx + sx), round(ty + sy))
            if text and key not in seen:
                seen.add(key)
                items.append((text, tx + sx, ty + sy))
    return items


def svg_truth(svg: str) -> set[tuple[str, tuple[float, float]]]:
    """(room name, dims) pairs printed in the SVG. A dimension line belongs to the nearest label at or above
    it (Lennar prints a suite's dimensions under it, with a closet label between them in the file); with no
    positions, the nearest label before it in the file."""
    items = _svg_items(svg)
    out = set()
    for n, (text, x, y) in enumerate(items):
        d = parse_dims(text)
        if not d:
            continue
        labels = [(abs(x - lx) + abs(y - ly), n - k, lt) for k, (lt, lx, ly) in enumerate(items)
                  if not parse_dims(lt) and ly <= y + 2 and (k < n or (lx, ly) != (0.0, 0.0))]
        labels = [c for c in labels if c[1] > 0 or c[0] > 0]
        if labels:
            out.add((_norm(min(labels)[2]), _r(d)))
    return out


def score_rooms(truth: set, got: list[dict]) -> dict:
    pairs = [(_norm(g.get("name")), parse_dims(g.get("dims"))) for g in got]
    pairs = [(n, d) for n, d in pairs if d]
    matched = sum(any(n == tn and abs(d[0] - td[0]) < 0.05 and abs(d[1] - td[1]) < 0.05 for n, d in pairs)
                  for tn, td in truth)
    return {"truth": len(truth), "got": len(pairs), "matched": matched}


def area_check(rooms: list[dict], sqft: float | None) -> dict:
    total = sum(d[0] * d[1] for r in rooms if not NOT_LIVING.search(r.get("name") or "")
                and (d := parse_dims(r.get("dims"))))
    ratio = round(total / sqft, 2) if sqft else None
    return {"sum_sqft": round(total), "ratio": ratio,
            "ok": ratio is not None and AREA_BAND[0] <= ratio <= AREA_BAND[1]}


def unique_floors(floors: list[dict]) -> list[dict]:
    """Drop a floor whose rooms repeat an earlier floor exactly (the model sometimes returns one floor twice)."""
    out, seen = [], set()
    for f in floors:
        key = json.dumps(f["rooms"], sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def summarize(floors: list[dict]) -> dict:
    def where(pattern):
        return [f["floor"] for f in floors for r in f["rooms"] if re.search(pattern, r.get("name") or "", re.I)]
    garages = [int(m.group(1)) for f in floors for r in f["rooms"]
               if (m := re.search(r"(\d)\s*-?\s*(?:bay|car)", r.get("name") or "", re.I))]
    primary = where(r"owner|primary|master")
    return {"downstairs_bedroom": bool(where(r"\bbed(room)?\b|guest suite") and 1 in where(r"\bbed(room)?\b|guest suite")),
            "primary_floor": min(primary) if primary else None, "loft": bool(where(r"\bloft\b")),
            "flex_room": bool(where(r"\bflex\b|\bden\b|\boffice\b|\bstudy\b")),
            "garage_bays": max(garages) if garages else None}


# ---------- I/O ----------

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["floors", "notes"], "properties": {
    "floors": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                          "required": ["floor", "rooms"], "properties": {
        "floor": {"type": "integer"},
        "rooms": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                             "required": ["name", "dims"], "properties": {
            "name": {"type": "string"}, "dims": {"type": ["string", "null"]}}}}}}},
    "notes": {"type": ["string", "null"]}}}

PROMPT = """These are the floor plans of one home, one image per floor, in order (floor 1 first).
For every room label printed on each floor, give the name exactly as printed and its dimensions exactly as
printed (e.g. 13' 4" x 14' 1"), or null when no dimensions are printed for it. Never estimate or invent a
dimension. Include garages, porches and patios. Return JSON matching the schema."""


def lennar_plans(date: str) -> list[dict]:
    plans = {}
    for f in sorted((RAW / date / "lennar").glob("*.html")):
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', f.read_text(), re.S)
        state = json.loads(m.group(1))["props"]["pageProps"].get("initialApolloState", {}) if m else {}
        for k, p in state.items():
            if not k.startswith("PlanType:") or not p.get("floorplans"):
                continue
            floors = [{"label": (fp.get("default") or {}).get("image", {}).get("alt"),
                       "url": (fp.get("default") or {}).get("image", {}).get("url")} for fp in p["floorplans"]]
            floors = [x for x in floors if x["url"] and ".svg" in x["url"]]
            if floors and k not in plans:
                plans[k] = {"id": k, "name": p.get("name"), "sqft": p.get("sqft"), "url": p.get("url"),
                            "floors": floors}
    return list(plans.values())


def render(svg_path: Path) -> bytes:
    subprocess.run(["qlmanage", "-t", "-s", "1600", "-o", str(svg_path.parent), str(svg_path)],
                   check=True, capture_output=True, timeout=60)
    return (svg_path.parent / f"{svg_path.name}.png").read_bytes()


def read_plan(plan: dict, fetcher: Fetcher) -> dict:
    images, truth = [], set()
    for n, fl in enumerate(plan["floors"], start=1):
        path = TMP / f"{_norm(plan['id'])}_{n}.svg"
        if path.exists():  # fetched earlier in this run's .tmp: re-scoring never re-hits the CDN
            svg = path.read_text()
        else:
            status, svg = fetcher.get(fl["url"])
            if status != 200:
                raise RuntimeError(f"floor {n}: HTTP {status}")
            path.write_text(svg)
        truth |= svg_truth(svg)
        images.append(base64.b64encode(render(path)).decode())
    content = [{"type": "text", "text": PROMPT}] + [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b}"}} for b in images]
    r = llm.chat([{"role": "user", "content": content}], purpose="vision", conn=db.connect(),
                 response_format={"type": "json_schema", "json_schema": {"name": "floorplan", "schema": SCHEMA}})
    out = llm.parse_json(r.text)
    out["floors"] = unique_floors(out["floors"])
    rooms = [x for f in out["floors"] for x in f["rooms"]]
    return {**plan, "read": out, "layout": summarize(out["floors"]), "labels": score_rooms(truth, rooms),
            "area": area_check(rooms, plan["sqft"]), "cost_usd": r.cost_usd}


def store(results: list[dict], conn) -> int:
    n = 0
    with conn:
        for r in results:
            path = (r["url"] or "").rsplit("/", 1)[0]          # plan url sits under its community page
            row = conn.execute("SELECT p.id FROM plans p JOIN communities c ON c.id = p.community_id "
                               "WHERE ? LIKE '%' || rtrim(replace(c.url, 'https://www.lennar.com', ''), '/') || '%' "
                               "AND lower(replace(p.name, ' ', '')) = lower(replace(?, ' ', '')) "
                               "ORDER BY length(c.url) DESC LIMIT 1", (path, r["name"])).fetchone()
            if row:
                conn.execute("UPDATE plans SET layout_json=?, image_url=? WHERE id=?",
                             (json.dumps({"floors": r["read"]["floors"], **r["layout"], "area_check": r["area"]}),
                              r["floors"][0]["url"], row[0]))
                n += 1
    return n


def report(date: str, results: list[dict], failed: list[str], stored: int) -> Path:
    t = sum(r["labels"]["truth"] for r in results)
    m = sum(r["labels"]["matched"] for r in results)
    g = sum(r["labels"]["got"] for r in results)
    area_ok = sum(r["area"]["ok"] for r in results)
    lines = [f"# Floor plans read by Muse Spark vision, pages fetched {date}", "",
             f"{len(results)} Lennar plans read ({len(failed)} failed), {stored} matched to plans in the DB; "
             f"vision cost ${sum(r['cost_usd'] for r in results):.4f}.", "",
             f"- Room labels with dimensions (truth = the SVG's own text): {m}/{t} read exactly "
             f"({m / t:.1%}); the model gave {g} dimensioned rooms.",
             f"- Area flag (dimensioned living rooms {AREA_BAND[0]:.0%}–{AREA_BAND[1]:.0%} of stated sq ft; only "
             f"bedrooms, living rooms and garages carry dimensions): {len(results) - area_ok} plans outside: "
             + (", ".join(f"{r['name']} ({r['url'].split('/')[-3]}, {r['area']['ratio']})" for r in results
                          if not r["area"]["ok"]) or "none") + ".",
             f"- Gate (Day 4): label accuracy ≥ 85%: {'PASS' if t and m / t >= 0.85 else 'FAIL'}.", "",
             "| Plan | Sq ft | Rooms read / labelled | Area ratio | Downstairs bed | Primary floor | Loft | Garage |",
             "|---|---:|---:|---:|---|---:|---|---:|"]
    for r in sorted(results, key=lambda r: r["id"]):
        lo = r["layout"]
        lines.append(f"| {r['name']} ({r['url'].split('/')[-3] if r['url'] else ''}) | {r['sqft']} | "
                     f"{r['labels']['matched']}/{r['labels']['truth']} | {r['area']['ratio']} | "
                     f"{'yes' if lo['downstairs_bedroom'] else 'no'} | {lo['primary_floor']} | "
                     f"{'yes' if lo['loft'] else 'no'} | {lo['garage_bays']} |")
    lines += ["", "Failed: " + (", ".join(failed) or "none")]
    path = REPORTS / f"{date}__floorplans.md"
    path.write_text("\n".join(lines) + "\n")
    return path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    date = a.date or latest_date()
    TMP.mkdir(parents=True, exist_ok=True)
    cfg, _ = load_config()
    fetcher = Fetcher(cfg["user_agent"], cfg["delay_s"])
    lock, plain_get = threading.Lock(), fetcher.get

    def polite_get(url):  # vision calls run 4 at a time; fetches to the CDN stay one at a time, 2 s apart
        with lock:
            return plain_get(url)
    fetcher.get = polite_get
    plans = lennar_plans(date)[:a.limit]
    results, failed = [], []

    def one(p):
        try:
            return read_plan(p, fetcher), None
        except llm.BudgetExceeded:
            raise
        except Exception as e:  # one bad plan never sinks the run; listed in the report
            return None, f"{p['name']} ({p['id']}): {type(e).__name__}: {str(e)[:120]}"

    with ThreadPoolExecutor(4) as pool:
        for res, err in pool.map(one, plans):
            (failed.append(err) if err else results.append(res))
    stored = store(results, db.connect())
    print(report(date, results, failed, stored))
    llm.print_spend()
