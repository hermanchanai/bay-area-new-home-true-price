"""Score model extraction against the model-free truth labels (eval/truth/).

    python -m tools.eval_extraction --model muse-spark-1.3-contributor

Homes match on a normalized street address; plans on a normalized name. Reports home recall /
precision, field accuracy on matched homes and plans, and incentive-kind recall / precision.
Writes eval_results rows and eval/reports/<fetch date>__<model>.md.
"""
import argparse
import json
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from tools import db

ROOT = Path(__file__).resolve().parent.parent
TRUTH = ROOT / "eval" / "truth"
REPORTS = ROOT / "eval" / "reports"
EXTRACT = ROOT / ".tmp" / "extract"
GATE = 0.90

SUFFIX = {"avenue": "ave", "street": "st", "court": "ct", "drive": "dr", "lane": "ln", "road": "rd",
          "place": "pl", "circle": "cir", "boulevard": "blvd", "terrace": "ter", "parkway": "pkwy",
          "common": "cmn", "commons": "cmns", "way": "way", "trail": "trl", "loop": "loop",
          "east": "e", "west": "w", "north": "n", "south": "s"}
HOME_FIELDS = ("price", "beds", "baths", "sqft")
PLAN_FIELDS = ("from_price", "sqft_min", "beds_max")


def norm_address(a: str) -> str:
    a = a.lower()
    a = re.sub(r",\s*[a-z .]+,\s*(ca|california)\b.*$", "", a)   # trailing ", City, CA 94568"
    a = re.sub(r"\b(unit|apt|suite|ste)\b\.?\s*|#\s*", " # ", a)
    tokens = re.sub(r"[^a-z0-9#]+", " ", a).split()
    return " ".join(SUFFIX.get(t, t) for t in tokens).replace("# ", "#")


def norm_plan(n: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", n.lower())


def _eq(a, b) -> bool:
    """Exact numeric match (3 == 3.0); None only matches None."""
    if a is None or b is None:
        return a is None and b is None
    try:
        return abs(float(a) - float(b)) < 0.01
    except (TypeError, ValueError):
        return False


def _score(truth: list[dict], got: list[dict], key, fields, extra_label: str) -> dict:
    got_by = {}
    for g in got:
        k = key(g)
        if k and k not in got_by:
            got_by[k] = g
    fields_score = {f: [0, 0] for f in fields}
    misses, matched = [], 0
    truth_keys = set()
    for t in truth:
        k = key(t)
        truth_keys.add(k)
        g = got_by.get(k)
        if g is None:
            misses.append({"field": "missing", "expected": k, "got": None})
            continue
        matched += 1
        for f in fields:
            if t.get(f) is None:
                continue
            ok = _eq(t[f], g.get(f))
            fields_score[f][0] += ok
            fields_score[f][1] += 1
            if not ok:
                misses.append({"field": f, "key": k, "expected": t[f], "got": g.get(f)})
    for k in got_by:
        if k not in truth_keys:
            misses.append({"field": extra_label, "expected": None, "got": k})
    return {"truth": len(truth), "got": len(got_by), "matched": matched, "fields": fields_score, "misses": misses}


def score_homes(truth: list[dict], got: list[dict]) -> dict:
    return _score(truth, got, lambda h: norm_address(h["address"]) if h.get("address") else None,
                  HOME_FIELDS, "extra_home")


def score_plans(truth: list[dict], got: list[dict]) -> dict:
    return _score(truth, got, lambda p: norm_plan(p["name"]) if p.get("name") else None, PLAN_FIELDS, "extra_plan")


def score_kinds(truth: list[list[str]], got_kinds: list[str]) -> dict:
    got = set(got_kinds)
    found = sum(any(k in got for k in alts) for alts in truth)
    allowed = {k for alts in truth for k in alts}
    false_pos = sorted(got - allowed)
    return {"truth": len(truth), "found": found, "got": len(got), "false_pos": len(false_pos),
            "false_pos_kinds": false_pos,
            "missed": [alts for alts in truth if not any(k in got for k in alts)]}


def _pct(n, d) -> str:
    return f"{n / d:.1%}" if d else "n/a"


def evaluate(model: str, date: str | None = None, conn=None, builders: list[str] | None = None) -> dict:
    truths = [json.loads(p.read_text()) for p in sorted(TRUTH.glob("*.json"))]
    truths = [t for t in truths if not builders or t["builder"] in builders]
    date = date or truths[0]["fetch_date"]
    run_id = uuid.uuid4().hex[:12]
    tot = defaultdict(lambda: [0, 0])
    pages, rows, missing_pages = [], [], []
    for t in truths:
        out = EXTRACT / date / model / f"{t['page']}.json"
        if not out.exists():
            missing_pages.append(t["page"])
            continue
        ex = json.loads(out.read_text())
        page = {"page": t["page"]}
        if t["homes"] is not None:
            h = score_homes(t["homes"], ex.get("homes") or [])
            tot["home_recall"][0] += h["matched"]; tot["home_recall"][1] += h["truth"]
            tot["home_precision"][0] += h["matched"]; tot["home_precision"][1] += h["got"]
            for f, (ok, n) in h["fields"].items():
                tot[f"home_{f}"][0] += ok; tot[f"home_{f}"][1] += n
            page["homes"] = h
        if t["plans"] is not None:
            p = score_plans(t["plans"], ex.get("plans") or [])
            tot["plan_recall"][0] += p["matched"]; tot["plan_recall"][1] += p["truth"]
            for f, (ok, n) in p["fields"].items():
                tot[f"plan_{f}"][0] += ok; tot[f"plan_{f}"][1] += n
            page["plans"] = p
        k = {"missed": [], "false_pos_kinds": []}
        if t["incentive_kinds"] is not None:  # null = offers not labelled for this page
            k = score_kinds(t["incentive_kinds"], [i.get("kind") for i in ex.get("incentives") or []])
            tot["incentive_kind_recall"][0] += k["found"]; tot["incentive_kind_recall"][1] += k["truth"]
            tot["incentive_kind_precision"][0] += k["got"] - k["false_pos"]; tot["incentive_kind_precision"][1] += k["got"]
        page["kinds"] = k
        pages.append(page)
        for part in ("homes", "plans"):
            for m in page.get(part, {}).get("misses", []):
                rows.append((t["page"], f"{part}.{m['field']}", json.dumps(m.get("expected")), json.dumps(m.get("got")), 0))
        for alts in k["missed"]:
            rows.append((t["page"], "incentive_kind.missing", json.dumps(alts), None, 0))
        for fp in k["false_pos_kinds"]:
            rows.append((t["page"], "incentive_kind.extra", None, json.dumps(fp), 0))

    conn = conn or db.connect()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with conn:
        conn.executemany("INSERT INTO eval_results (page_id, field, expected, got, match, run_at, run_id, model) "
                         "VALUES (?,?,?,?,?,?,?,?)", [(*r, now, run_id, model) for r in rows])
    summary = {k: (ok, n) for k, (ok, n) in tot.items()}
    gate_keys = ["home_recall", "home_precision", "home_price", "home_beds", "home_sqft",
                 "incentive_kind_recall", "incentive_kind_precision"]
    gate = {k: (summary[k][0] / summary[k][1] if summary.get(k, (0, 0))[1] else None) for k in gate_keys}
    passed = all(v is not None and v >= GATE for v in gate.values())
    result = {"model": model, "date": date, "run_id": run_id, "builders": builders, "pages": len(pages), "missing_pages": missing_pages,
              "summary": summary, "gate": gate, "passed": passed, "per_page": pages}
    write_report(result)
    return result


def write_report(r: dict) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    lines = [f"# Extraction eval: {r['model']} on pages fetched {r['date']}"
             + (f" ({', '.join(r['builders'])})" if r.get("builders") else ""), "",
             f"Run `{r['run_id']}`, {r['pages']} pages scored against model-free truth (`tools/build_truth.py`)."
             + (f" {len(r['missing_pages'])} truth pages had no extraction." if r["missing_pages"] else ""), "",
             f"**Gate (every metric ≥ {GATE:.0%}): {'PASS' if r['passed'] else 'FAIL'}**", "",
             "| Metric | Correct | Of | Score |", "|---|---:|---:|---:|"]
    for k, (ok, n) in sorted(r["summary"].items()):
        mark = " (gate)" if k in r["gate"] else ""
        lines.append(f"| {k}{mark} | {ok} | {n} | {_pct(ok, n)} |")
    lines += ["", "## Misses by page", ""]
    for p in r["per_page"]:
        ms = []
        for part in ("homes", "plans"):
            ms += [f"{part}.{m['field']}: expected {m.get('expected')!r}, got {m.get('got')!r}"
                   + (f" ({m['key']})" if m.get("key") else "") for m in p.get(part, {}).get("misses", [])]
        ms += [f"incentive kind missed: {'|'.join(a)}" for a in p["kinds"]["missed"]]
        ms += [f"incentive kind not on page: {k}" for k in p["kinds"]["false_pos_kinds"]]
        if ms:
            lines.append(f"- **{p['page']}**")
            lines += [f"  - {m}" for m in ms]
    scope = f"__{'-'.join(r['builders'])}" if r.get("builders") else ""
    path = REPORTS / f"{r['date']}__{r['model'].replace('/', '_')}{scope}.md"
    path.write_text("\n".join(lines) + "\n")
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--date", default=None)
    ap.add_argument("--builder", action="append")
    a = ap.parse_args()
    r = evaluate(a.model, a.date, builders=a.builder)
    for k, (ok, n) in sorted(r["summary"].items()):
        print(f"{k:28} {ok:4}/{n:<4} {_pct(ok, n):>7}{'  <- gate' if k in r['gate'] else ''}")
    print(f"pages scored {r['pages']}, missing {len(r['missing_pages'])}; gate {'PASS' if r['passed'] else 'FAIL'}")
