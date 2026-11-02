"""Answer key for the agent eval, derived from a frozen copy of the DB with SQL written apart from
tools/agent_tools.py (the agent's own code is never called here, so a bug there can't hide in the key).

    python -m tools.derive_agent_truth --freeze      # copy data/trueprice.db to data/eval_<today>.db, derive from it
    python -m tools.derive_agent_truth               # re-derive from the copy the key already names

Writes eval/agent_questions.yaml (questions kept, `expect` blocks and `db` rewritten). Definitions:
- true price = the guaranteed end (true_prices.true_high: asking minus the offers every buyer gets);
  best case = true_low. A home with no true price is priced at its asking price.
- ready month: 'now' = the freeze month; else the builder's date ('2026-11-30', '11/30/2026', 'Nov 2026';
  a month with no year is its next occurrence, 'Dec/Jan' the later one). No date = unknown, left out.
- a home is named by its address, else its lot.
- q04 "biggest discount": top 3 by the guaranteed discount and top 3 by the best case both count; eligible =
  every San Joaquin home with any discount. q07 "under $800,000": either reading counts (cheapest by true
  price or by asking); eligible = true price under $800,000, which includes every asking price under it.
- The agent dates "ready now" by the day the eval runs, the key by the freeze day: re-freeze if the eval
  moves to a later month and q01/q08 drift.
"""
import argparse
import json
import re
import sqlite3
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
KEY = ROOT / "eval" / "agent_questions.yaml"
LIVE = ROOT / "data" / "trueprice.db"
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1)}
FIPS = {"alameda": "06001", "contra_costa": "06013", "santa_clara": "06085", "san_joaquin": "06077"}

HEADER = """\
# Agent eval: 20 buyer questions with hand-checked answers (tools/eval_agent.py scores them).
# Derived by tools/derive_agent_truth.py from the frozen DB named in `db` (data as published on {day}),
# with SQL written apart from tools/agent_tools.py. The eval's tools query that same copy, so the daily refresh
# can't make the key stale; re-freeze and re-derive to move the eval to newer data.
# - top3_any_of / top3_all_of: homes the answer must rank in its first 3 [H<id>] citations
# - eligible: every home meeting all the question's conditions; nothing else may be in the top 3
#   (true price = the guaranteed end, asking minus offers every buyer gets; "ready by" = the builder's date)
# - must_mention: text the answer must contain (a list = any one of them), case-insensitive
# - must_mention_amount: per group, the answer states one of these dollar amounts at its written precision
#   ($54.8k counts as $54,806); must_name: a name that must appear whole ('Residence 1' is not in 'Residence 1X')
# - no_homes: the right answer is "none"; citing any home fails
# - q04: top 3 by the guaranteed or by the best-case discount; q07: cheapest by true price or by asking
# Hand-computed values (ARM worth = PV over the fixed years of the payment saved vs the market rate, at the
# market rate; loan = price less the down payment; market {rate}% FRED {rate_date}):
{notes}
"""


def ready_month(ready: str | None, status: str | None, today: str) -> str | None:
    s, ym = (ready or "").strip(), None
    if m := re.match(r"(\d{4})-(\d{2})", s):
        ym = f"{m[1]}-{m[2]}"
    elif m := re.match(r"(\d{1,2})/\d{1,2}/(\d{4})", s):
        ym = f"{m[2]}-{int(m[1]):02d}"
    else:
        pairs = []                                       # [month, the year written after it or None], in order
        for t in re.findall(r"[A-Za-z]+|20\d\d", s):
            if t.isdigit():
                if pairs and pairs[-1][1] is None:
                    pairs[-1][1] = int(t)
            elif t[:3].lower() in MONTHS:
                pairs.append([MONTHS[t[:3].lower()], None])
        if pairs:
            dated = [i for i, (_, y) in enumerate(pairs) if y]
            # count forward from the last written year, or from today; a month earlier than the one before it
            # is in the next year
            i0 = dated[-1] if dated else -1
            year, prev = (pairs[i0][1], pairs[i0][0]) if dated else (int(today[:4]), int(today[5:7]))
            for m, _ in pairs[i0 + 1:]:
                if m < prev:
                    year += 1
                prev = m
            ym = f"{year}-{pairs[-1][0]:02d}"
    if status == "now":
        return max(ym or today[:7], today[:7])
    return ym


def homes(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT h.id, COALESCE(h.address, h.lot) name, h.price, h.beds, h.move_in_status status, h.ready_date, "
        "c.name community, c.city, c.county_fips fips, c.product, c.age_restricted, p.layout_json, "
        "COALESCE(t.true_high, h.price) true_price, COALESCE(t.true_low, h.price) best "
        "FROM homes h JOIN communities c ON c.id = h.community_id LEFT JOIN plans p ON p.id = h.plan_id "
        "LEFT JOIN true_prices t ON t.home_id = h.id WHERE h.price IS NOT NULL").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["downstairs"] = (json.loads(r["layout_json"]) if r["layout_json"] else {}).get("downstairs_bedroom")
        out.append(d)
    return out


def cheapest(hs: list[dict], key: str = "true_price") -> list[dict]:
    return sorted(hs, key=lambda h: (h[key], h["id"]))


def ranked(hs: list[dict], n: int = 3, key: str = "true_price") -> list[str]:
    """The first n names, plus anything tied with the n-th (a tie is not the agent's fault)."""
    s = cheapest(hs, key)
    if len(s) <= n:
        return [h["name"] for h in s]
    cut = s[n - 1][key]
    return [h["name"] for h in s if h[key] <= cut]


def names(hs: list[dict]) -> list[str]:
    return list(dict.fromkeys(h["name"] for h in cheapest(hs)))


def pmt(rate_pct: float, loan: float) -> float:
    i = rate_pct / 1200
    return loan / 360 if i == 0 else loan * i / (1 - (1 + i) ** -360)


def arm_worth(price: int, down: float, market: float, promo: float, years: int) -> int:
    """A promo at or above the market rate saves nothing: worth $0, never negative."""
    if promo >= market:
        return 0
    loan, i = price * (1 - down), market / 1200
    saving = pmt(market, loan) - pmt(promo, loan)
    return round(sum(saving / (1 + i) ** k for k in range(1, years * 12 + 1)))


def cheapest_expect(hs: list[dict]) -> dict:
    """The cheapest home must be in the top 3; with a tie, any of the tied homes will do."""
    r = ranked(hs, 1)
    return {"top3_all_of": r} if len(r) == 1 else {"top3_any_of": r}


def comment(note: str) -> str:
    return "#   " + " ".join(note.split()).removeprefix("#").strip()


def one(conn, sql: str, *args):
    rows = conn.execute(sql, args).fetchall()
    if len(rows) != 1:
        raise SystemExit(f"expected one row, got {len(rows)}: {sql} {args}")
    return rows[0]


def derive(conn, today: str) -> tuple[dict, list[str]]:
    hs = homes(conn)
    rate, rate_date = one(conn, "SELECT rate, as_of FROM market_rates WHERE series='MORTGAGE30US' "
                                "ORDER BY as_of DESC LIMIT 1")
    ready = lambda h, by: (m := ready_month(h["ready_date"], h["status"], today)) is not None and m <= by  # noqa: E731
    sj, cc = FIPS["san_joaquin"], FIPS["contra_costa"]
    e, notes = {}, []

    q = [h for h in hs if h["beds"] and h["beds"] >= 4 and h["true_price"] < 1_100_000 and h["fips"] in (cc, sj)
         and ready(h, "2027-01")]
    e["q01"] = {"top3_any_of": ranked(q), "eligible": names(q)}

    q = [h for h in hs if h["fips"] == FIPS["alameda"] and h["status"] == "now"]
    e["q02"] = {**cheapest_expect(q), "eligible": names(q)}

    q = [h for h in hs if h["fips"] == FIPS["santa_clara"] and h["product"] == "TH" and h["beds"] == 3
         and h["true_price"] < 1_300_000]
    e["q03"] = {"no_homes": True} if not q else {"top3_any_of": ranked(q), "eligible": names(q)}

    q = [dict(h, sure=h["price"] - h["true_price"], most=h["price"] - h["best"]) for h in hs if h["fips"] == sj]
    q = [h for h in q if h["most"] > 0]
    top = list(dict.fromkeys(ranked([dict(h, k=-h["sure"]) for h in q], key="k")
                             + ranked([dict(h, k=-h["most"]) for h in q], key="k")))
    e["q04"] = {"top3_any_of": top, "eligible": names(q)}

    q = [h for h in hs if h["city"] == "Mountain House" and h["beds"] and h["beds"] >= 5 and h["status"] == "now"]
    e["q05"] = {"top3_any_of": ranked(q, 1), "eligible": names(q)}

    q = [h for h in hs if h["downstairs"] is True and h["true_price"] < 650_000]
    e["q06"] = {"top3_any_of": ranked(q), "eligible": names(q)}

    q = [h for h in hs if h["city"] == "Tracy" and h["true_price"] < 800_000]
    e["q07"] = {"top3_any_of": list(dict.fromkeys(ranked(q) + ranked([h for h in q if h["price"] < 800_000],
                                                                      key="price"))), "eligible": names(q)}

    q = [h for h in hs if h["fips"] == FIPS["santa_clara"] and h["product"] == "condo" and h["true_price"] < 1_000_000
         and ready(h, "2026-12")]
    e["q08"] = {"top3_any_of": ranked(q), "eligible": names(q)}

    lilac = one(conn, "SELECT id FROM communities WHERE name LIKE 'Lilac at Oakwood Trails'")[0]
    labels = [r[0] for r in conn.execute(
        "SELECT DISTINCT label FROM incentives WHERE (scope='community' AND scope_id=?) OR (scope='home' AND "
        "scope_id IN (SELECT id FROM homes WHERE community_id=?))", (lilac, lilac))]
    if not any("4.99" in (x or "") for x in labels) or any("FLEX" in (x or "") for x in labels):
        raise SystemExit(f"q09: Lilac offers changed, re-check the question: {labels}")
    worth = [r[0] for r in conn.execute(
        "SELECT DISTINCT v.value_high FROM incentive_values v JOIN incentives i ON i.id = v.incentive_id "
        "JOIN homes h ON h.id = v.home_id WHERE h.community_id = ? AND i.kind = 'arm_promo' ORDER BY 1", (lilac,))]
    e["q09"] = {"must_mention": ["4.99"], "must_mention_amount": [worth]}
    notes.append(f"q09 Lilac at Oakwood Trails offers: {'; '.join(sorted(x for x in labels if x))}. "
                 f"The ARM is worth ${', $'.join(f'{w:,}' for w in worth)} on its homes at 20% down (an answer "
                 "must name what it is worth, not only the rate; the up-to $250K FLEX Cash offer ended 10-30).")

    for qid, name, city in (("q10", "Cairnway", "Tracy"), ("q11", "Orchard Grove", "Brentwood")):
        n, med = one(conn, "SELECT ps.n, ps.median_est_price FROM past_sales ps JOIN communities c "
                           "ON c.id = ps.community_id WHERE c.name = ? AND c.city = ?", name, city)
        notes.append(f"{qid} {name} median ${med:,} (n {n}).")
        e[qid] = ({"must_mention": [str(med)[:3], "asking"]} if qid == "q10"
                  else {"must_mention": [[f"{med / 1e6:.2f}", f"{med // 1000:,}"]]})
    e["q12"] = {"must_mention": ["Santa Clara"]}

    for qid, addr, down in (("q13", "1576 TORREY DR", 0.10), ("q14", "361 E Beatrice Avenue", 0.05)):
        hid, price, fips = one(conn, "SELECT h.id, h.price, c.county_fips FROM homes h JOIN communities c "
                                     "ON c.id = h.community_id WHERE h.address = ?", addr)
        arm = [r for r in conn.execute(
            "SELECT i.rate, i.structure FROM incentives i JOIN incentive_values v ON v.incentive_id = i.id "
            "WHERE v.home_id = ? AND i.kind = 'arm_promo'", (hid,))]
        if len(arm) != 1 or fips != sj:        # San Joaquin has no conforming limit in config: no jumbo spread
            raise SystemExit(f"{qid}: expected one ARM offer on a San Joaquin home, got {arm} {fips}")
        promo, years = arm[0][0], int(re.search(r"\d+", arm[0][1])[0])
        worth = arm_worth(price, down, rate, promo, years)
        notes.append(f"{qid} {addr} at {down:.0%} down: {promo}% {years}-yr ARM worth ${worth:,} "
                     f"-> true price ${price - worth:,} (asking ${price:,}).")
        e[qid] = {"must_mention_amount": [[price - worth] if qid == "q13" else [worth]]}

    pair = [one(conn, "SELECT price FROM homes WHERE address = ?", a)[0]
            for a in ("361 E Beatrice Avenue", "1162 Bradbury Drive")]
    e["q15"] = {"top3_all_of": ["361 E Beatrice Avenue", "1162 Bradbury Drive"],
                "must_mention_amount": [[p] for p in pair]}

    drift = conn.execute("SELECT p.name, p.layout_json FROM plans p JOIN communities c ON c.id = p.community_id "
                         "WHERE c.name = 'Driftway' AND c.city = 'Lathrop' ORDER BY p.name").fetchall()
    down = [n for n, lay in drift if lay and json.loads(lay).get("downstairs_bedroom") is True]
    notes.append(f"q16 Driftway plans with a downstairs bedroom: {', '.join(down)}.")
    e["q16"] = {"must_name": down}     # text scoring can't read a negation ('1X does not'); accepted limit

    lay = json.loads(one(conn, "SELECT p.layout_json FROM homes h JOIN plans p ON p.id = h.plan_id "
                               "WHERE h.address = '3139 Workman Avenue'")[0])
    suite = next(r for f in lay["floors"] for r in f["rooms"] if re.search(r"(?i)owner|primary", r["name"]))
    if suite["dims"] != "14' x 14' 5\"":
        raise SystemExit(f"q17: owner's suite now reads {suite['dims']}, update the must_mention forms")
    notes.append(f"q17 3139 Workman Avenue owner's suite {suite['dims']}.")
    e["q17"] = {"must_mention": [["14' x 14' 5", "14'x14'5", "14' x 14'5", "14 ft x 14 ft 5", "14′ x 14′ 5"]]}

    e["q18"] = {"no_homes": True, "must_mention": ["Napa"]}

    q = [h for h in hs if h["age_restricted"]]
    e["q19"] = {**cheapest_expect(q), "eligible": names(q)}

    q = [h for h in hs if h["city"] == "Dublin" and h["beds"] and h["beds"] >= 4 and h["true_price"] < 1_600_000]
    e["q20"] = {"top3_any_of": ranked(q), "eligible": names(q)}
    return e, [f"rate {rate}% as of {rate_date}"] + notes


def main(freeze: bool) -> Path:
    """--freeze never overwrites a snapshot (the key may point at it), and a new snapshot the key can't be
    derived from is deleted again; the key is replaced only after the new one parses."""
    spec = yaml.safe_load(KEY.read_text())
    new = None
    if freeze:
        path = Path("data") / f"eval_{date.today().isoformat()}.db"
        if (ROOT / path).exists():
            raise SystemExit(f"{ROOT / path} exists: the key may point at it; move it away to re-freeze today")
        src, dst = sqlite3.connect(LIVE), sqlite3.connect(ROOT / path)
        src.backup(dst)
        dst.close()
        src.close()
        new, spec["db"] = ROOT / path, str(path)
    try:
        day = re.search(r"\d{4}-\d{2}-\d{2}", spec["db"])[0]
        conn = sqlite3.connect(f"file:{ROOT / spec['db']}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        expect, notes = derive(conn, day)
        conn.close()
        m = re.search(r"rate ([\d.]+)% as of (\S+)", notes.pop(0))
        for q in spec["questions"]:
            q["expect"] = expect[q["id"]]
        body = yaml.safe_dump({"db": spec["db"], "questions": spec["questions"]}, sort_keys=False,
                              allow_unicode=True, width=120)
        text = HEADER.format(day=day, rate=m[1], rate_date=m[2], notes="\n".join(map(comment, notes))) + body
        if yaml.safe_load(text)["questions"] != spec["questions"]:
            raise SystemExit("the generated key doesn't read back the same; not written")
    except BaseException:
        if new:
            new.unlink()
        raise
    KEY.write_text(text)
    return KEY

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze", action="store_true")
    print(main(ap.parse_args().freeze))
