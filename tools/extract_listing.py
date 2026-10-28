"""Extract communities, plans, homes and incentives from a preprocessed builder page with Muse Spark.

    python -m tools.extract_listing --truth-pages                 # the 43 eval pages
    python -m tools.extract_listing --builder trumark --limit 2
    python -m tools.extract_listing --truth-pages --purpose escalate   # one model only, no routing
    python -m tools.extract_listing --builder all                      # routed: see route()

One request per page (pages are ≤ ~31k tokens; the model's context is 1M). Output is strict JSON
(response_format json_schema) saved to .tmp/extract/<date>/<model>/<builder>/<page>.json.
Every call goes through tools/llm.py: cache, cost ledger, budget guard.
"""
import argparse
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tools import db, llm
from tools.preprocess_page import PRE, RAW, latest_date
from tools.validate_records import Page, needs_escalation, validate

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / ".tmp" / "extract"
TRUTH = ROOT / "eval" / "truth"

KINDS = ["flat_cash", "price_cut", "closing_credit", "perm_rate", "temp_buydown", "arm_promo",
         "first_year_rate", "unquantified"]

STR = {"type": ["string", "null"]}
NUM = {"type": ["number", "null"]}
INT = {"type": ["integer", "null"]}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


SCHEMA = _obj({
    "community": _obj({
        "name": STR, "city": STR,
        "status": {"type": "string", "enum": ["selling", "final_homes", "coming_soon", "sold_out", "unknown"]},
        "product": {"type": "string", "enum": ["SFD", "TH", "condo", "duet", "mixed", "unknown"]},
        "age_restricted": {"type": ["boolean", "null"]}, "price_from": INT, "evidence": STR}),
    "plans": {"type": "array", "items": _obj({
        "name": {"type": "string"}, "beds_min": INT, "beds_max": INT, "baths_min": NUM, "baths_max": NUM,
        "sqft_min": INT, "sqft_max": INT, "stories": INT, "garage": INT, "from_price": INT, "evidence": STR})},
    "homes": {"type": "array", "items": _obj({
        "address": STR, "lot": STR, "plan_name": STR, "price": INT, "was_price": INT,
        "move_in_status": {"type": "string", "enum": ["now", "month", "under_construction", "model", "unknown"]},
        "ready_date": STR, "beds": INT, "baths": NUM, "sqft": INT, "evidence": STR})},
    "incentives": {"type": "array", "items": _obj({
        "kind": {"type": "string", "enum": KINDS},
        "scope": {"type": "string", "enum": ["builder", "community", "home"]},
        "home_address": STR, "label": {"type": "string"}, "amount_usd": NUM,
        "amount_is_up_to": {"type": "boolean"}, "included_in_price": {"type": "boolean"},
        "rate": NUM, "apr": NUM, "structure": STR, "lender_condition": STR, "eligibility": STR,
        "pool_id": STR, "expires": STR, "fine_print": STR, "evidence": {"type": "string"}})},
    "notes": STR,
    "confidence": {"type": "number"},
})

INSTRUCTIONS = """You extract facts about new homes for sale from one home builder's web page, for a public price tracker.
The page was preprocessed: "## EMBEDDED DATA" sections are the site's own JSON (often the most complete facts); "## PAGE TEXT" is the visible text. Return JSON matching the schema.

Rules
1. Only this page's own community: its homes, plans and offers. Ignore "other communities nearby", regional lists, navigation and site-wide boilerplate. Offers pages (no single community) still list their incentives; leave community fields null/unknown.
2. homes = specific homes for sale identified by a street address or lot/homesite number (quick move-in, move-in ready, model home for sale, under construction). Never turn a floor plan into a home. List each home once even if the page repeats it.
3. address = the street line only, exactly as shown, including any unit ("400 Mexico Terrace, Unit 9"); no city, state or ZIP.
4. price = the current asking price shown for that home. A higher crossed-out or "Was" price goes in was_price. Never compute, infer or round a price; null when not shown. "From"/"Starting at" prices belong to plans or the community, not homes.
5. baths = full baths + 0.5 per half bath ("3 Ba, 1 Half Ba" = 3.5).
6. plans = floor plans offered in this community, with beds/baths/sq ft ranges and from_price (the "From"/"Starting at" price as a number; null if worded, e.g. "the high 2 millions").
7. incentives = every offer that lowers the buyer's cost. kind is exactly one of:
   - price_cut: a discount off the price ("$150K savings!", Was/Now pricing). included_in_price = true when the shown price already reflects it.
   - flat_cash: dollars the buyer can apply ("$50K flex cash your way", "$2,000 incentive"), including flex cash usable for several things.
   - closing_credit: dollars that can only go toward closing costs.
   - perm_rate: a fixed rate for the whole loan term (e.g. 30-year fixed at 4.99%).
   - temp_buydown: a rate that steps up over the first years (2-1, 3-2-1, 1-0).
   - arm_promo: an adjustable-rate mortgage with a promotional initial rate (e.g. 7/6 ARM at 4.99%).
   - first_year_rate: a reduced rate for year one only.
   - unquantified: an offer with no dollar amount or rate on the page.
   When the page omits an offer's loan type (e.g. "note rate as low as 5.25%"), pick the most likely kind and say why in notes.
   One incentive per distinct offer. Per-home price cuts: one incentive per home, scope "home", home_address set.
   amount_is_up_to = true for "up to $X". Rates are percent numbers (4.99, not 0.0499). structure: "7/6 ARM", "30yr fixed", "2-1", etc.
   lender_condition: the lender the buyer must use, if any. eligibility: who qualifies (e.g. "teachers, nurses, military"), if limited.
   pool_id: give offers the buyer must choose between the same short id; otherwise null.
   fine_print: the offer's disclaimer, copied verbatim, at most 600 characters.
8. evidence: a quote of at most 20 words copied exactly from the page that supports the item.
9. community.status: selling | final_homes (the page says only a few remain) | coming_soon | sold_out | unknown.
10. confidence: your 0-1 confidence that the homes, prices and incentives are complete and correct."""


def messages_for(doc_text: str, builder: str, url: str, kind: str) -> list[dict]:
    page_type = {"community": "community page", "extra": "offers / listing page",
                 "home": "single home page (one quick move-in home)"}.get(kind, kind)
    return [{"role": "developer", "content": INSTRUCTIONS},
            {"role": "user", "content": f"Builder: {builder}\nPage type: {page_type}\nURL: {url}\n\n{doc_text}"}]


def extract_page(builder: str, stem: str, url: str, kind: str, date: str, purpose: str = "extract",
                 model: str | None = None, conn=None) -> dict:
    cfg = llm.load_config()
    model = model or cfg["purposes"][purpose]["model"]
    doc = (PRE / date / builder / f"{stem}.md").read_text()
    r = llm.chat(messages_for(doc, builder, url, kind), purpose=purpose, model=model, conn=conn, cfg=cfg,
                 response_format={"type": "json_schema", "json_schema": {"name": "listing", "schema": SCHEMA}})
    data = llm.parse_json(r.text)
    data["_meta"] = {"url": url, "model": model, "cost_usd": r.cost_usd, "from_cache": r.from_cache,
                     "finish_reason": r.finish_reason, "usage": r.usage}
    out = OUT / date / model / builder / f"{stem}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1))
    return data


def _errors(issues: list[dict]) -> int:
    return sum(i["severity"] == "error" for i in issues)


def route(extract, page_text: str) -> dict:
    """Contributor-mode Muse Spark first; if validation errors or confidence < 0.7, Muse Spark standard.
    Still failing -> needs_review (never dropped), keeping whichever record has fewer errors."""
    page = Page(page_text)
    rec = extract("extract")
    issues = validate(rec, page)
    out = {"record": rec, "issues": issues, "model": rec["_meta"]["model"], "escalated": False, "needs_review": False}
    if not needs_escalation(rec, issues):
        return out
    rec2 = extract("escalate")
    issues2 = validate(rec2, page)
    ok2 = not needs_escalation(rec2, issues2)
    if ok2 or _errors(issues2) < _errors(issues):
        out.update(record=rec2, issues=issues2, model=rec2["_meta"]["model"])
    out.update(escalated=True, needs_review=not ok2)
    return out


def targets(date: str, builders: list[str] | None, truth_pages: bool) -> list[dict]:
    wanted = {json.loads(p.read_text())["page"] for p in TRUTH.glob("*.json")} if truth_pages else None
    out = []
    for bdir in sorted((RAW / date).iterdir()):
        if not bdir.is_dir() or (builders and bdir.name not in builders):
            continue
        for rec in json.loads((bdir / "manifest.json").read_text()):
            if rec.get("status") != 200 or rec["kind"] not in ("community", "extra", "home") or not rec.get("file"):
                continue
            stem = rec["file"].removesuffix(".html")
            if wanted is not None and f"{bdir.name}/{stem}" not in wanted:
                continue
            out.append({"builder": bdir.name, "stem": stem, "url": rec["url"], "kind": rec["kind"]})
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--builder", action="append")
    ap.add_argument("--truth-pages", action="store_true")
    ap.add_argument("--purpose", default=None, help="one model only (no routing): extract | escalate")
    ap.add_argument("--model", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    date = a.date or latest_date()
    todo = targets(date, a.builder, a.truth_pages)[:a.limit]
    db.connect()  # apply migrations once before threads start
    failed = []

    routed = a.purpose is None and a.model is None
    routes = []

    def one(t):
        try:
            conn = db.connect()
            if not routed:
                return t, extract_page(t["builder"], t["stem"], t["url"], t["kind"], date, a.purpose or "extract",
                                       a.model, conn=conn), None
            r = route(lambda purpose: extract_page(t["builder"], t["stem"], t["url"], t["kind"], date, purpose,
                                                   conn=conn),
                      (PRE / date / t["builder"] / f"{t['stem']}.md").read_text())
            routes.append({"page": f"{t['builder']}/{t['stem']}", "url": t["url"], "model": r["model"],
                           "escalated": r["escalated"], "needs_review": r["needs_review"], "issues": r["issues"]})
            return t, r["record"], None
        except Exception as e:  # one bad page never sinks the run (a spent cap included); it's listed below
            return t, None, f"{type(e).__name__}: {str(e)[:300]}"

    with ThreadPoolExecutor(a.workers) as pool:
        for f in as_completed([pool.submit(one, t) for t in todo]):
            t, d, err = f.result()
            name = f"{t['builder']}/{t['stem'][:60]}"
            if err:
                failed.append(name)
                print(f"ERR {name}: {err}")
                continue
            m = d["_meta"]
            print(f"ok  {name}: {len(d['homes'])} homes, {len(d['plans'])} plans, {len(d['incentives'])} "
                  f"incentives, conf {d['confidence']}, ${m['cost_usd']:.4f}{' (cache)' if m['from_cache'] else ''}")
    print(f"{len(todo) - len(failed)}/{len(todo)} pages extracted; {len(failed)} failed")
    if routed:
        # merge with earlier runs for the same date (one builder at a time), newest wins
        path = OUT / date / "routes.json"
        old = {r["page"]: r for r in json.loads(path.read_text())} if path.exists() else {}
        old.update({r["page"]: r for r in routes})
        path.write_text(json.dumps(sorted(old.values(), key=lambda r: r["page"]), indent=1))
        review = [r["page"] for r in routes if r["needs_review"]]
        print(f"escalated {sum(r['escalated'] for r in routes)}, needs review {len(review)}: {review}")
    llm.print_spend()
