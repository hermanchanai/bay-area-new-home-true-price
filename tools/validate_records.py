"""Rule checks on one page's extraction. Deterministic, $0. Errors trigger escalation to Muse Spark.

    python -m tools.validate_records --model muse-spark-1.3-contributor     # summary over saved extractions

Rules (plan §6.5; amount band widened after real data): value ranges; a quick move-in home needs an
address or lot (lot-only is a warning); an incentive over 25% of the price warns, over 50% fails; rates
1–10%; ARM structure "N/M". Anti-hallucination: each home's address and price, and every evidence quote,
must be found in the page text the model was given.
"""
import argparse
import json
import re
from collections import Counter
from bisect import bisect_left
from difflib import SequenceMatcher
from pathlib import Path

from tools.eval_extraction import SUFFIX

ROOT = Path(__file__).resolve().parent.parent
EXTRACT = ROOT / ".tmp" / "extract"
PRE = ROOT / ".tmp" / "pre"

RANGES = {"price": (300_000, 5_000_000), "sqft": (500, 6_000), "beds": (1, 7), "baths": (1, 6)}
EVIDENCE_MIN = 0.9
MIN_CONFIDENCE = 0.7


def _tokens(s: str) -> list[str]:
    s = re.sub(r"(?<=\d),(?=\d)", "", s.lower())                     # 1,234,500 -> 1234500
    s = re.sub(r"\b(unit|apt|suite|ste)\b\.?\s*|#\s*", " # ", s)
    return [SUFFIX.get(t, t) for t in re.sub(r"[^a-z0-9#.%]+|(?<!\d)\.|\.(?!\d)", " ", s).split()]


class Page:
    """Page text indexed once for the address / price / evidence checks."""

    def __init__(self, text: str):
        self.text = text
        self.toks = _tokens(text)
        self.joined = " " + " ".join(self.toks) + " "
        self.at: dict[str, list[int]] = {}
        for i, t in enumerate(self.toks):
            self.at.setdefault(t, []).append(i)

    def has_phrase(self, s: str) -> bool:
        q = _tokens(s)
        return bool(q) and f" {' '.join(q)} " in self.joined

    def best_ratio(self, quote: str) -> float:
        q = _tokens(quote)
        if not q:
            return 0.0
        target, best = " ".join(q), 0.0
        for k in range(min(3, len(q))):                      # anchor on any of the first three tokens
            for j in self.at.get(q[k], []):
                start = max(0, j - k)
                for n in (len(q) - 1, len(q), len(q) + 1):
                    r = SequenceMatcher(None, target, " ".join(self.toks[start:start + n])).ratio()
                    if r > best:
                        best = r
                        if best == 1.0:
                            return best
        return best


    def stitched(self, quote: str) -> bool:
        """Every quote token on the page within a short span: the model often joins a card's separate
        lines (plan, address, price) into one quote. An invented number or word still fails."""
        q = set(_tokens(quote))
        if not q or any(t not in self.at for t in q):
            return False
        span = 8 * len(q) + 40
        anchor = min(q, key=lambda t: len(self.at[t]))
        for a in self.at[anchor]:
            if all(_near(self.at[t], a, span) for t in q):
                return True
        return False


def _near(positions: list[int], a: int, span: int) -> bool:
    k = bisect_left(positions, a - span)
    return k < len(positions) and positions[k] <= a + span


def evidence_found(quote: str, page: "Page | str") -> bool:
    page = page if isinstance(page, Page) else Page(page)
    return page.best_ratio(quote) >= EVIDENCE_MIN or page.stitched(quote)


def _issue(where: str, rule: str, detail=None, severity: str = "error") -> dict:
    return {"where": where, "rule": rule, "detail": detail, "severity": severity}


def validate(rec: dict, page_text: "Page | str") -> list[dict]:
    page = page_text if isinstance(page_text, Page) else Page(page_text)
    issues = []
    homes = rec.get("homes") or []
    for n, h in enumerate(homes):
        w = f"homes[{n}]"
        for f, (lo, hi) in RANGES.items():
            if h.get(f) is not None and not lo <= h[f] <= hi:
                issues.append(_issue(w, f"{f}_range", h[f]))
        if not h.get("address") and not h.get("lot"):
            issues.append(_issue(w, "no_address_or_lot"))
        elif not h.get("address"):
            issues.append(_issue(w, "lot_only", h.get("lot"), "warn"))
        elif not page.has_phrase(h["address"]):
            issues.append(_issue(w, "address_not_on_page", h["address"]))
        if h.get("price") is not None and not page.has_phrase(str(h["price"])):
            issues.append(_issue(w, "price_not_on_page", h["price"]))
        if h.get("evidence") and not evidence_found(h["evidence"], page):
            # warn only: the address and price checks above are what catch an invented home
            issues.append(_issue(w, "evidence_not_on_page", h["evidence"], "warn"))

    by_addr = {h["address"]: h.get("price") for h in homes if h.get("address")}
    base = rec.get("community", {}).get("price_from") or min((h["price"] for h in homes if h.get("price")), default=None)
    for n, i in enumerate(rec.get("incentives") or []):
        w = f"incentives[{n}]"
        for f in ("rate", "apr"):
            if i.get(f) is not None and not 1 <= i[f] <= 10:
                issues.append(_issue(w, f"{f}_range", i[f]))
        if i.get("kind") == "arm_promo":
            if not i.get("structure"):
                issues.append(_issue(w, "arm_structure_missing", None, "warn"))
            elif not re.search(r"\b\d{1,2}/\d{1,2}\b", i["structure"]):
                issues.append(_issue(w, "arm_structure", i["structure"]))
        price = by_addr.get(i.get("home_address")) if i.get("scope") == "home" else None
        price = price or base
        if i.get("amount_usd") and price and i["amount_usd"] > 0.25 * price:
            # real Bay Area cuts reach ~40% ("$300,000 in savings" on $789,925); past half the price is an error
            over = i["amount_usd"] > 0.5 * price
            issues.append(_issue(w, "amount_over_50pct" if over else "amount_over_25pct", [i["amount_usd"], price],
                                 "error" if over else "warn"))
        if i.get("evidence") and not evidence_found(i["evidence"], page):
            issues.append(_issue(w, "evidence_not_on_page", i["evidence"]))
    return issues


def needs_escalation(rec: dict, issues: list[dict]) -> bool:
    return (rec.get("confidence") or 0) < MIN_CONFIDENCE or any(i["severity"] == "error" for i in issues)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    root = EXTRACT / (a.date or sorted(p.name for p in EXTRACT.iterdir())[-1]) / a.model
    rules, flagged, pages = Counter(), [], 0
    for f in sorted(root.glob("*/*.json")):
        rec = json.loads(f.read_text())
        doc = PRE / root.parent.name / f.parent.name / f"{f.stem}.md"
        issues = validate(rec, doc.read_text())
        pages += 1
        rules.update(f"{i['severity']}:{i['rule']}" for i in issues)
        if needs_escalation(rec, issues):
            flagged.append(f"{f.parent.name}/{f.stem}")
    print(f"{pages} pages, {len(flagged)} need escalation")
    for r, n in rules.most_common():
        print(f"  {r:40} {n}")
