"""Agent eval: 20 buyer questions with hand-checked answers (eval/agent_questions.yaml).

    python -m tools.eval_agent                  # run all, write eval/reports/<date>__agent.md
    python -m tools.eval_agent --only q01,q07   # a subset
    python -m tools.eval_agent --purpose agent_open   # another model (config/llm.yaml purposes)

A question passes when the homes the answer ranks first (in order of their [H<id>] citations) include the
expected ones and nothing outside the eligible set, every required fact is mentioned, and every dollar figure
in the answer traces to a value some tool returned from the database (or to the question itself).

The tools query the frozen DB the answer key names (`db:` in eval/agent_questions.yaml), so the daily refresh
can't make the key stale; tools/derive_agent_truth.py rebuilds the key from that copy. Spend is still logged
to the main DB, which holds the caps.
"""
import argparse
import json
import logging
import re
import sqlite3
from datetime import date
from pathlib import Path

import yaml

from tools import db, llm
from tools.agent import run as run_agent
from tools.llm import print_spend

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "eval" / "agent_questions.yaml"
REPORTS = ROOT / "eval" / "reports"
RUNS = ROOT / ".tmp" / "agent_runs"
BARE = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{3})+|\d+)(?![\w.]|,\d)")
MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*([kKmM])?(?![a-zA-Z])")

log = logging.getLogger(__name__)


def cited(text: str) -> dict:
    """{'H': [...], 'C': [...], 'P': [...]}: ids cited as [H12] / [C3] / [P7], in order of first mention."""
    out = {"H": [], "C": [], "P": []}
    for group in re.findall(r"\[([^\]]+)\]", text):
        for kind, n in re.findall(r"\b([HCP])(\d+)\b", group):
            if int(n) not in out[kind]:
                out[kind].append(int(n))
    return out


def money_in(text: str) -> list[tuple[int, int]]:
    """Dollar figures as (value, precision): '$1.08M' -> (1_080_000, 10_000); '$804k' -> (804_000, 1_000);
    a bare 789925 or 789,925 (100,000 and up) -> (789_925, 1)."""
    out = []
    for num, suffix in MONEY.findall(text):
        whole, _, frac = num.replace(",", "").partition(".")
        unit = {"k": 1_000, "m": 1_000_000}.get(suffix.lower(), 1)
        prec = unit // 10 ** len(frac) if unit > 1 else 1
        out.append((round(float(num.replace(",", "")) * unit), max(prec, 1)))
    # a money-sized figure written without "$" (e.g. "Asking 789925") is checked too, exactly
    out += [(int(n.replace(",", "")), 1) for n in BARE.findall(MONEY.sub(" ", text))
            if int(n.replace(",", "")) >= 100_000]
    return out


def _numbers(obj) -> list[float]:
    if isinstance(obj, bool):
        return []
    if isinstance(obj, (int, float)):
        return [obj]
    if isinstance(obj, str):
        return [v for v, _ in money_in(obj)]
    if isinstance(obj, dict):
        return [n for v in obj.values() for n in _numbers(v)]
    if isinstance(obj, list):
        return [n for v in obj for n in _numbers(v)]
    return []


def untraced(answer: str, trace: list[dict], question: str) -> list[int]:
    """Dollar figures in the answer that no tool result (or the question) supports at the printed precision."""
    known = _numbers([t["result"] for t in trace]) + [v for v, _ in money_in(question)]
    return [v for v, prec in money_in(answer) if not any(abs(v - k) <= prec / 2 for k in known)]


def _norm(address: str) -> str:
    return re.sub(r"\s+", " ", address or "").strip().casefold()


def score(q: dict, answer: str, trace: list[dict], homes: dict, known: dict | None = None) -> dict:
    """homes: home_id -> address for every home in the DB. known: {'C': ids, 'P': ids} to check other citations."""
    e, reasons = q["expect"], []
    ids = cited(answer)
    bad = [f"H{i}" for i in ids["H"] if i not in homes]
    if known:
        bad += [f"{k}{i}" for k in ("C", "P") for i in ids[k] if i not in known[k]]
    if bad:
        reasons.append(f"cites ids not in the database: {bad}")
    top3 = [_norm(homes[i]) for i in ids["H"] if i in homes][:3]
    if e.get("no_homes") and ids["H"]:
        reasons.append(f"should cite no homes, cites {ids['H']}")
    if want := e.get("top3_any_of"):
        if not {_norm(a) for a in want} & set(top3):
            reasons.append(f"none of {want} in the top 3")
    for a in e.get("top3_all_of") or []:
        if _norm(a) not in top3:
            reasons.append(f"{a} not in the top 3")
    if "eligible" in e:
        ok = {_norm(a) for a in e["eligible"]}
        if wrong := [a for a in top3 if a not in ok]:
            reasons.append(f"ineligible homes in the top 3: {wrong}")
    low = answer.casefold()
    for m in e.get("must_mention") or []:
        alts = m if isinstance(m, list) else [m]
        if not any(str(a).casefold() in low for a in alts):
            reasons.append(f"doesn't mention {alts}")
    figures = money_in(answer)
    for group in e.get("must_mention_amount") or []:   # any one amount, matched at the precision it is written in
        if not any(abs(v - want) <= prec / 2 for v, prec in figures for want in group):
            reasons.append(f"doesn't state any of {group}")
    for name in e.get("must_name") or []:              # whole name: 'Residence 1' doesn't match inside 'Residence 1X'
        if not re.search(rf"(?<!\w){re.escape(name)}(?!\w)", answer, re.IGNORECASE):
            reasons.append(f"doesn't name {name!r}")
    for m in e.get("must_not_mention") or []:
        if str(m).casefold() in low:
            reasons.append(f"mentions {m!r}")
    if missing := untraced(answer, trace, q["question"]):
        reasons.append(f"dollar figures not in any tool result: {missing}")
    return {"id": q["id"], "pass": not reasons, "reasons": reasons, "top3": top3}


def frozen(path: str | Path) -> sqlite3.Connection:
    p = Path(path) if Path(path).is_absolute() else ROOT / path
    if not p.exists():
        raise SystemExit(f"frozen eval DB {p} is missing: rebuild it with tools/derive_agent_truth.py --freeze")
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def main(only: set[str] | None = None, purpose: str = "agent") -> Path:
    spec = yaml.safe_load(QUESTIONS.read_text())
    conn, ledger = frozen(spec["db"]), db.connect()
    chat = lambda messages, conn=None, **k: llm.chat(messages, conn=ledger, **k)  # noqa: E731
    qs = [q for q in spec["questions"] if not only or q["id"] in only]
    homes = {r[0]: r[1] or r[2] or "" for r in conn.execute("SELECT id, address, lot FROM homes")}
    known = {"C": {r[0] for r in conn.execute("SELECT id FROM communities")},
             "P": {r[0] for r in conn.execute("SELECT id FROM plans")}}
    runs = RUNS if purpose == "agent" else RUNS / purpose
    runs.mkdir(parents=True, exist_ok=True)
    results, cost = [], 0.0
    for q in qs:
        r = run_agent(q["question"], conn=conn, purpose=purpose, chat=chat)
        s = score(q, r["answer"], r["trace"], homes, known)
        s.update(cost=r["cost_usd"], turns=r["turns"], tools=[t["tool"] for t in r["trace"]], answer=r["answer"])
        (runs / f"{q['id']}.json").write_text(json.dumps({**r, "score": s}, indent=1, default=str))
        cost += r["cost_usd"]
        results.append(s)
        log.info("%s %s %s", q["id"], "PASS" if s["pass"] else "FAIL", "; ".join(s["reasons"]))
    passed = sum(s["pass"] for s in results)
    gate = passed >= 16 and len(results) == 20
    suffix = "" if purpose == "agent" else f"__{purpose}"
    path = REPORTS / f"{date.today().isoformat()}__agent{suffix}.md"
    lines = [f"# Agent eval, {date.today().isoformat()} (purpose {purpose}, data {spec['db']})", "",
             f"{passed}/{len(results)} pass. Spend this run ${cost:.4f} (cached calls cost $0).", "",
             f"**Day 6 gate: {'PASS' if gate else 'FAIL'}** (≥16/20; every dollar figure traces to a tool result).",
             "", "| Q | Result | Turns | Tools | Why it failed |", "|---|---|---:|---|---|"]
    for s in results:
        lines.append(f"| {s['id']} | {'pass' if s['pass'] else 'FAIL'} | {s['turns']} | {', '.join(s['tools'])} "
                     f"| {'; '.join(s['reasons']).replace('|', '/')} |")
    lines += ["", "## Answers", ""]
    for q, s in zip(qs, results):
        lines += [f"### {q['id']}: {q['question']}", "", s["answer"].strip(), ""]
    if not only:
        path.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:4 + len(results) + 4]))
    return path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--purpose", default="agent")
    a = ap.parse_args()
    main(set(a.only.split(",")) if a.only else None, a.purpose)
    print_spend()
