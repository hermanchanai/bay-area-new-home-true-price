"""Day 3 gates over the routed extraction: per-builder coverage, escalations, review queue.

    python -m tools.coverage_report            # latest date -> eval/reports/<date>__coverage.md

Gates (plan §9, Day 3): ≥ 95% of quick move-in homes have an address or lot number, and every
builder is above its fetch floor or skipped with a logged reason (config/builders.yaml).
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

import yaml

from tools.fetch_builder_pages import floor_ok
from tools.preprocess_page import RAW

ROOT = Path(__file__).resolve().parent.parent
EXTRACT = ROOT / ".tmp" / "extract"
REPORTS = ROOT / "eval" / "reports"
GATE = 0.95


def floors(date: str) -> dict:
    out = {}
    for b in yaml.safe_load((ROOT / "config" / "builders.yaml").read_text())["builders"]:
        man = RAW / date / b["key"] / "manifest.json"
        got = sum(r["kind"] == "community" and r["status"] == 200
                  for r in json.loads(man.read_text())) if man.exists() else 0
        out[b["key"]] = {"fetched": got, "expected": b["expected_communities"],
                         "floor_ok": floor_ok(got, b["expected_communities"]), "skip_reason": b.get("skip_reason")}
    return out


def builder_rows(routes: list[dict], records: dict, floors: dict) -> list[dict]:
    rows = {k: {"builder": k, "pages": 0, "homes": 0, "with_address": 0, "lot_only": 0, "neither": 0,
                "escalated": 0, "needs_review": 0, **f} for k, f in floors.items()}
    for r in routes:
        row = rows[r["page"].split("/")[0]]
        row["pages"] += 1
        row["escalated"] += r["escalated"]
        row["needs_review"] += r["needs_review"]
        for h in records[r["page"]].get("homes") or []:
            row["homes"] += 1
            row["with_address" if h.get("address") else "lot_only" if h.get("lot") else "neither"] += 1
    return list(rows.values())


def gate(rows: list[dict]) -> dict:
    homes = sum(r["homes"] for r in rows)
    placed = sum(r["with_address"] + r["lot_only"] for r in rows)
    share = round(placed / homes, 4) if homes else None
    unlogged = [r["builder"] for r in rows if not r["floor_ok"] and not r.get("skip_reason")]
    return {"address_or_lot": share, "below_floor_unlogged": unlogged,
            "passed": share is not None and share >= GATE and not unlogged}


def run(date: str) -> tuple[list[dict], dict, list[dict]]:
    routes = json.loads((EXTRACT / date / "routes.json").read_text())
    records = {r["page"]: json.loads((EXTRACT / date / r["model"] / f"{r['page']}.json").read_text()) for r in routes}
    rows = builder_rows(routes, records, floors(date))
    return rows, gate(rows), [r for r in routes if r["needs_review"]]


def write_report(date: str, rows: list[dict], g: dict, review: list[dict]) -> Path:
    lines = [f"# Extraction coverage, pages fetched {date}", "",
             f"**Day 3 gate: {'PASS' if g['passed'] else 'FAIL'}**: {g['address_or_lot']:.1%} of homes have an "
             f"address or lot (≥ {GATE:.0%}); builders below their fetch floor without a logged skip: "
             f"{g['below_floor_unlogged'] or 'none'}.", "",
             "| Builder | Fetched / roster | Pages | Homes | Address | Lot only | Neither | Escalated | Review |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in sorted(rows, key=lambda r: -r["homes"]):
        fetched = f"{r['fetched']} / {r['expected']}" + ("" if r["floor_ok"] else f" (skipped: {r['skip_reason']})")
        lines.append(f"| {r['builder']} | {fetched} | {r['pages']} | {r['homes']} | {r['with_address']} | "
                     f"{r['lot_only']} | {r['neither']} | {r['escalated']} | {r['needs_review']} |")
    lines += ["", "## Needs review", ""] + [
        f"- {r['page']} ({r['model']}): " + "; ".join(f"{i['where']} {i['rule']}" for i in r["issues"]
                                                      if i["severity"] == "error") for r in review] or ["- none"]
    path = REPORTS / f"{date}__coverage.md"
    path.write_text("\n".join(lines) + "\n")
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    date = ap.parse_args().date or sorted(p.name for p in EXTRACT.iterdir() if p.is_dir())[-1]
    rows, g, review = run(date)
    print(write_report(date, rows, g, review))
    print(json.dumps(g))
