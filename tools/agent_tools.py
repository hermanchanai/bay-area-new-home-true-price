"""The agent's tools: read-only queries over data/trueprice.db, plus the incentive math rerun with the buyer's
own down payment. Every result carries the ids the agent cites ([H12], [C3], [P7]) and the source page.

Tool specs (OpenAI function format, which the Meta Model API accepts) are in TOOLS; call() dispatches one.
"""
import calendar
import json
import re
from datetime import date
from pathlib import Path
from statistics import median

import yaml

from tools.link_parcels import street_key
from tools.load_records import pricing_set
from tools.price_incentives import Assumptions, Incentive, true_price

ROOT = Path(__file__).resolve().parent.parent
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
MAX_ROWS = 25
# True price ranks and budgets use the guaranteed end (true_high): an "up to $250K" offer may pay $0.
SORTS = {"true_price": "COALESCE(t.true_high, h.price) IS NULL, COALESCE(t.true_high, h.price)",
         "asking": "h.price IS NULL, h.price",
         "discount": "COALESCE(1.0 * t.value_high / t.asking, 0) DESC"}


def _counties() -> list[dict]:
    return yaml.safe_load((ROOT / "config" / "counties.yaml").read_text())["counties"]


def county_fips(name: str) -> str:
    """'San Joaquin', 'san_joaquin', 'San Joaquin County' or '06077' -> '06077'."""
    k = re.sub(r"[^a-z0-9]", "", name.lower()).removesuffix("county")
    for c in _counties():
        if k in (re.sub(r"[^a-z0-9]", "", c["name"].lower()), c["key"].replace("_", ""), c["fips"]):
            return c["fips"]
    raise ValueError(f"no data for county {name!r}; covered: {', '.join(c['name'] for c in _counties())}")


def _county_name(fips: str | None) -> str | None:
    return next((c["name"] for c in _counties() if c["fips"] == fips), None)


def ready_month(ready: str | None, status: str | None, today: str) -> str | None:
    """'YYYY-MM' a home is ready, from the builder's free-text ready date. A home marked ready now is ready this
    month. A month with no year is its next occurrence; 'Dec/Jan' means the later month. Unknown -> None."""
    now = today[:7]
    ym = None
    s = (ready or "").strip()
    if m := re.match(r"(\d{4})-(\d{2})-\d{2}", s):
        ym = f"{m[1]}-{m[2]}"
    elif m := re.match(r"(\d{1,2})/\d{1,2}/(\d{4})", s):
        ym = f"{m[2]}-{int(m[1]):02d}"
    elif months := [MONTHS[w[:3].lower()] for w in re.findall(r"[A-Za-z]{3,}", s) if w[:3].lower() in MONTHS]:
        # a year belongs to the month written just before it ('Nov 2026'), or else to the next one ('2027 November');
        # months after the last dated one roll forward from it ('Dec 2026 / Jan' = 2027-01); no year: from today
        dated, pending = {}, None
        k = -1
        for t in re.findall(r"[A-Za-z]{3,}|\b20\d{2}\b", s):
            if t.isdigit():
                if k >= 0 and k not in dated:
                    dated[k] = int(t)
                else:
                    pending = int(t)
            elif t[:3].lower() in MONTHS:
                k += 1
                if pending:
                    dated[k], pending = pending, None
        last = max(dated) if dated else -1
        y, prev = (dated[last], months[last]) if dated else (int(today[:4]), int(today[5:7]))
        for mo in months[last + 1:]:
            if mo < prev:
                y += 1
            prev = mo
        ym = f"{y}-{months[-1]:02d}"
    if status == "now":
        return max(ym or now, now)
    return ym


def _layout(row) -> dict:
    return json.loads(row["layout_json"]) if row["layout_json"] else {}


def _home(r, today: str) -> dict:
    lay = _layout(r)
    asking, hi = r["price"], r["value_high"]
    return {"home_id": r["id"], "community_id": r["community_id"], "community": r["community"],
            "builder": r["builder"], "city": r["city"], "county": _county_name(r["county_fips"]),
            "product": r["product"], "address": r["address"], "lot": r["lot"], "plan_id": r["plan_id"],
            "plan": r["plan"], "beds": r["beds"], "baths": r["baths"], "sqft": r["sqft"], "asking": asking,
            "true_price_low": r["true_low"], "true_price_high": r["true_high"],
            "discount_low": r["value_low"], "discount_high": hi,
            "discount_pct_high": round(100 * hi / asking, 1) if asking and hi is not None else None,
            "move_in_status": r["move_in_status"], "ready": r["ready_date"],
            "ready_month": ready_month(r["ready_date"], r["move_in_status"], today),
            "downstairs_bedroom": lay.get("downstairs_bedroom"), "source_url": r["url"],
            "fetched": (r["fetched_at"] or "")[:10] or None}


# Builders write "Dublin" or "Dublin, CA"
CITY_MATCH = "lower(trim(replace(c.city, ', CA', ''))) = lower(trim(replace(?, ', CA', '')))"
HOME_SQL = ("SELECT h.*, c.name community, c.city, c.county_fips, c.product, b.name builder, p.name plan, "
            "p.layout_json, t.true_low, t.true_high, t.value_low, t.value_high FROM homes h "
            "JOIN communities c ON c.id = h.community_id JOIN builders b ON b.id = c.builder_id "
            "LEFT JOIN plans p ON p.id = h.plan_id LEFT JOIN true_prices t ON t.home_id = h.id")


def search_homes(conn, county: str | list | None = None, city: str | None = None, address: str | None = None,
                 max_price: int | None = None,
                 max_true_price: int | None = None, min_beds: float | None = None, max_beds: float | None = None,
                 product: str | None = None, downstairs_bedroom: bool | None = None, ready_by: str | None = None,
                 move_in_now: bool | None = None, builder: str | None = None, age_restricted: bool | None = None,
                 sort: str = "true_price",
                 limit: int = 10, today: str | None = None) -> dict:
    today = today or date.today().isoformat()
    where, args = [], []
    if county:
        names = county if isinstance(county, list) else [county]
        fips = [county_fips(n) for n in names]
        where.append(f"c.county_fips IN ({','.join('?' * len(fips))})")
        args += fips
    if city:
        where.append(CITY_MATCH)
        args.append(city)
    if age_restricted is not None:
        where.append("c.age_restricted = ?")
        args.append(int(age_restricted))
    if builder:
        where.append("lower(b.name) LIKE lower(?)")
        args.append(f"%{builder.strip()}%")
    if max_price is not None:
        where.append("h.price <= ?")
        args.append(max_price)
    if max_true_price is not None:
        where.append("COALESCE(t.true_high, h.price) <= ?")
        args.append(max_true_price)
    if min_beds is not None:
        where.append("h.beds >= ?")
        args.append(min_beds)
    if max_beds is not None:
        where.append("h.beds <= ?")
        args.append(max_beds)
    if product:
        if product not in ("SFD", "TH", "condo"):
            raise ValueError("product must be SFD, TH or condo")
        where.append("c.product = ?")
        args.append(product)
    if move_in_now:
        where.append("h.move_in_status = 'now'")
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {', '.join(SORTS)}")
    sql = HOME_SQL + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY {SORTS[sort]}, h.id"
    rows = [_home(r, today) for r in conn.execute(sql, args)]
    notes = []
    if address:  # house number + street name; "Street"/"St", direction and city don't matter
        want = street_key(address)
        if want is None:
            raise ValueError("address needs a house number and street name")
        rows = [h for h in rows if h["address"] and street_key(h["address"]) == want]
    if ready_by:
        if not re.fullmatch(r"\d{4}-\d{2}", ready_by):
            raise ValueError("ready_by must be YYYY-MM")
        unknown = sum(h["ready_month"] is None for h in rows)
        rows = [h for h in rows if h["ready_month"] and h["ready_month"] <= ready_by]
        if unknown:
            notes.append(f"{unknown} otherwise-matching homes have no ready date and were left out.")
    if downstairs_bedroom is not None:
        rows = [h for h in rows if h["downstairs_bedroom"] is downstairs_bedroom]
        notes.insert(0, "Only homes whose floor plan image was read can be checked for a downstairs bedroom "
                        "(Lennar plans so far); other homes may also have one.")
    return {"total_matching": len(rows), "homes": rows[:min(int(limit), MAX_ROWS)], "note": " ".join(notes) or None,
            "true_price_basis": "asking minus incentives at 20% down, 30-year fixed, FRED market rate. "
                                "true_price_high = guaranteed (sorted and filtered on this); true_price_low = "
                                "best case, if every 'up to' offer pays in full"}


def _incentives(conn, community_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT i.*, MIN(v.value_low) vlo, MAX(v.value_high) vhi, COUNT(v.home_id) homes FROM incentives i "
        "LEFT JOIN incentive_values v ON v.incentive_id = i.id WHERE (i.scope = 'community' AND i.scope_id = ?) "
        "OR (i.scope = 'home' AND i.scope_id IN (SELECT id FROM homes WHERE community_id = ?)) "
        "GROUP BY i.id ORDER BY vhi DESC", (community_id, community_id))
    return [{"incentive_id": r["id"], "label": r["label"], "kind": r["kind"], "scope": r["scope"],
             "amount_usd": r["amount_usd"], "amount_is_up_to": bool(r["amount_is_up_to"]), "rate": r["rate"],
             "structure": r["structure"], "lender": r["lender"], "eligibility": r["eligibility"],
             "expires": r["expires"], "included_in_price": bool(r["included_in_price"]),
             "value_low": r["vlo"], "value_high": r["vhi"], "homes_priced": r["homes"],
             "fine_print": (r["fine_print"] or "")[:400], "source_url": r["source_url"]} for r in rows]


def find_communities(conn, name: str | None = None, county: str | None = None, city: str | None = None) -> dict:
    where, args = [], []
    if name:
        where.append("lower(c.name) LIKE lower(?)")
        args.append(f"%{name.strip()}%")
    if county:
        where.append("c.county_fips = ?")
        args.append(county_fips(county))
    if city:
        where.append(CITY_MATCH)
        args.append(city)
    rows = conn.execute(
        "SELECT c.id, c.name, c.city, c.county_fips, c.product, b.name builder, COUNT(h.id) homes, "
        "MIN(h.price) min_asking, ps.n past_sales_n FROM communities c JOIN builders b ON b.id = c.builder_id "
        "LEFT JOIN homes h ON h.community_id = c.id LEFT JOIN past_sales ps ON ps.community_id = c.id"
        + (" WHERE " + " AND ".join(where) if where else "") + " GROUP BY c.id ORDER BY c.name", args).fetchall()
    return {"communities": [{"community_id": r["id"], "name": r["name"], "builder": r["builder"], "city": r["city"],
                             "county": _county_name(r["county_fips"]), "product": r["product"],
                             "homes_listed": r["homes"], "min_asking": r["min_asking"],
                             "has_past_sales": r["past_sales_n"] is not None} for r in rows[:50]],
            "total_matching": len(rows)}


def past_sales(conn, community_id: int) -> dict:
    c = conn.execute("SELECT id, name, county_fips FROM communities WHERE id = ?", (community_id,)).fetchone()
    if c is None:
        raise ValueError(f"no community {community_id}")
    asks = [r[0] for r in conn.execute("SELECT price FROM homes WHERE community_id = ? AND price IS NOT NULL",
                                       (community_id,))]
    out = {"community_id": c["id"], "community": c["name"], "county": _county_name(c["county_fips"]),
           "median_asking_now": round(median(asks)) if asks else None}
    r = conn.execute("SELECT * FROM past_sales WHERE community_id = ?", (community_id,)).fetchone()
    if r is None:
        out["past_sales"] = None
        out["reason"] = ("Santa Clara County's open assessor data has no sale prices or values."
                         if c["county_fips"] == "06085" else
                         "Fewer than 5 earlier sales could be linked to this community in the assessor roll.")
        return out
    method = {"transfer_tax": "the recorded price (county transfer tax)",
              "value_backed_out": "the Prop 13 assessed value with the 2%/yr trending backed out",
              "mixed": "recorded prices and Prop 13 assessed values"}[r["method"]]
    return {**out, "past_sales": "found", "n": r["n"], "median_est_price": r["median_est_price"], "p25": r["p25"],
            "p75": r["p75"], "period": r["period"], "method": method,
            "caveat": "Estimates from the county assessor roll, community-level only; not appraisals."}


def get_community(conn, community_id: int, today: str | None = None) -> dict:
    today = today or date.today().isoformat()
    c = conn.execute("SELECT c.*, b.name builder FROM communities c JOIN builders b ON b.id = c.builder_id "
                     "WHERE c.id = ?", (community_id,)).fetchone()
    if c is None:
        raise ValueError(f"no community {community_id}; use find_communities to look one up by name")
    homes = [_home(r, today) for r in conn.execute(HOME_SQL + " WHERE h.community_id = ? ORDER BY h.price",
                                                   (community_id,))]
    plans = [get_plan(conn, r[0], rooms=False) for r in
             conn.execute("SELECT id FROM plans WHERE community_id = ? ORDER BY from_price, name", (community_id,))]
    keep = ("home_id", "address", "lot", "plan", "beds", "baths", "sqft", "asking", "true_price_low",
            "true_price_high", "ready_month", "move_in_status")
    return {"community_id": c["id"], "name": c["name"], "builder": c["builder"], "city": c["city"],
            "county": _county_name(c["county_fips"]), "product": c["product"], "status": c["status"],
            "age_restricted": bool(c["age_restricted"]), "source_url": c["url"],
            "homes": [{k: h[k] for k in keep} for h in homes], "plans": plans,
            "incentives": _incentives(conn, community_id), "past_sales": past_sales(conn, community_id)}


def get_plan(conn, plan_id: int, rooms: bool = True) -> dict:
    p = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if p is None:
        raise ValueError(f"no plan {plan_id}")
    lay = _layout(p)
    out = {"plan_id": p["id"], "community_id": p["community_id"], "name": p["name"], "beds_min": p["beds_min"],
           "beds_max": p["beds_max"], "baths": p["baths"], "sqft_min": p["sqft_min"], "sqft_max": p["sqft_max"],
           "stories": p["stories"], "garage": p["garage"], "from_price": p["from_price"],
           "layout_read_from_image": bool(lay)}
    for k in ("downstairs_bedroom", "primary_floor", "loft", "flex_room", "garage_bays"):
        if k in lay:
            out[k] = lay[k]
    if rooms and lay:
        out["rooms"] = [{"floor": f["floor"], **r} for f in lay.get("floors", []) for r in f["rooms"]]
    return out


def reprice_home(conn, home_id: int, down_payment_pct: float = 20) -> dict:
    if not 3 <= float(down_payment_pct) <= 100:
        raise ValueError("down_payment_pct is a percent between 3 and 100")
    h = conn.execute("SELECT h.*, c.county_fips FROM homes h JOIN communities c ON c.id = h.community_id "
                     "WHERE h.id = ?", (home_id,)).fetchone()
    if h is None:
        raise ValueError(f"no home {home_id}")
    if not h["price"]:
        raise ValueError(f"home {home_id} has no asking price to price incentives against")
    rows = conn.execute("SELECT i.*, v.flags, v.market_rate, v.rate_date FROM incentive_values v "
                        "JOIN incentives i ON i.id = v.incentive_id WHERE v.home_id = ?", (home_id,)).fetchall()
    rate = conn.execute("SELECT as_of, rate FROM market_rates WHERE series = 'MORTGAGE30US' "
                        "ORDER BY as_of DESC LIMIT 1").fetchone()
    limit = next((c["conforming_limit_1unit"] for c in _counties() if c["fips"] == h["county_fips"]), None)
    a = Assumptions(down_payment_pct=float(down_payment_pct) / 100, conforming_limit=limit)
    counted = [r for r in rows if "not_counted" not in (r["flags"] or "")]
    pooled, _ = pricing_set([dict(r) for r in counted], f"home{home_id}")   # same stacking rule as the loader
    incs = [Incentive(kind=r["kind"], amount_usd=r["amount_usd"], amount_is_up_to=bool(r["amount_is_up_to"]),
                      rate=r["rate"], structure=r["structure"], pool_id=r["pool_id"], label=r["label"])
            for r in pooled]
    tp = true_price(h["price"], incs, rate["rate"], a)
    offer = lambda inc, v, best: {"label": inc.label, "kind": inc.kind, "value_low": v.low,  # noqa: E731
                                  "value_high": v.high, "method": v.method, "best_case_choice": best}
    return {"home_id": home_id, "address": h["address"], "asking": tp.asking, "market_rate": rate["rate"],
            "rate_date": rate["as_of"], "comparison_rate": tp.market_rate,
            "incentive_value_low": tp.value_low, "incentive_value_high": tp.value_high,
            "true_price_low": tp.true_low, "true_price_high": tp.true_high,
            "incentives": [offer(i, v, True) for i, v in tp.counted]
                          + [offer(i, v, False) for i, v in tp.not_counted],
            "not_counted": [{"label": r["label"], "why": [f for f in json.loads(r["flags"]) if f != "not_counted"]}
                            for r in rows if r not in counted],
            "assumptions": {"down_payment_pct": float(down_payment_pct), "term_years": a.term_months // 12,
                            "closing_cost_pct": a.closing_cost_pct * 100, "jumbo_spread_pct": a.jumbo_spread_pct,
                            "conforming_limit": limit},
            "method_note": "A permanent rate cut is a range: low = the builder's cost (PV of the payment savings), "
                           "high = the price cut that gives the same payment at the market rate. Offers don't stack "
                           "unless the fine print says they combine: the buyer picks one, so the guaranteed end "
                           "uses the offer with the biggest sure value and the best case the offer with the "
                           "biggest possible value (best_case_choice)."}


def compare(conn, home_ids: list[int], today: str | None = None) -> dict:
    today = today or date.today().isoformat()
    ids = [int(i) for i in home_ids][:6]
    rows = {r["id"]: _home(r, today) for r in conn.execute(
        HOME_SQL + f" WHERE h.id IN ({','.join('?' * len(ids))})", ids)}
    missing = [i for i in ids if i not in rows]
    if missing:
        raise ValueError(f"no home(s) {missing}")
    out = []
    for i in ids:
        h = rows[i]
        h["offers"] = [{"label": r["label"], "value_low": r["value_low"], "value_high": r["value_high"],
                        "counted": "not_counted" not in (r["flags"] or "")} for r in conn.execute(
            "SELECT i.label, v.value_low, v.value_high, v.flags FROM incentive_values v JOIN incentives i ON "
            "i.id = v.incentive_id WHERE v.home_id = ? ORDER BY v.value_high DESC", (i,))]
        out.append(h)
    return {"homes": out, "offers_note": "A home's offers don't stack unless the fine print says they combine: the "
                                         "buyer picks one (write 'or', not '+'). The true price range already "
                                         "reflects that."}


def _fn(name, desc, props, required=()):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": list(required)}}}


COUNTY = {"type": "array", "items": {"type": "string"},
          "description": "County names: Alameda, Contra Costa, Santa Clara, San Joaquin"}
TOOLS = [
    _fn("search_homes", "Find move-in-ready and under-construction homes for sale. Returns homes sorted by true "
        "price (asking minus the dollar value of builder incentives; sorted on the guaranteed end) with ids to "
        "cite.", {
            "county": COUNTY, "city": {"type": "string"},
            "address": {"type": "string", "description": "a street address, to look up one home"},
            "max_price": {"type": "integer", "description": "max asking price, USD"},
            "max_true_price": {"type": "integer", "description": "max guaranteed true price, USD"},
            "min_beds": {"type": "number"}, "max_beds": {"type": "number"},
            "product": {"type": "string", "enum": ["SFD", "TH", "condo"],
                        "description": "SFD = detached house, TH = townhome"},
            "downstairs_bedroom": {"type": "boolean"},
            "ready_by": {"type": "string", "description": "YYYY-MM: ready on or before this month"},
            "move_in_now": {"type": "boolean"}, "builder": {"type": "string"},
            "age_restricted": {"type": "boolean", "description": "true = 55+ communities only, false = none"},
            "sort": {"type": "string", "enum": list(SORTS)}, "limit": {"type": "integer"}}),
    _fn("find_communities", "Look up communities by (part of) name, county or city.", {
        "name": {"type": "string"}, "county": {"type": "string"}, "city": {"type": "string"}}),
    _fn("get_community", "One community: its homes, floor plans, incentives with dollar values, and past sales.",
        {"community_id": {"type": "integer"}}, ["community_id"]),
    _fn("get_plan", "One floor plan, with rooms and dimensions when the plan image was read.",
        {"plan_id": {"type": "integer"}}, ["plan_id"]),
    _fn("price_incentives", "Re-price one home's incentives with the buyer's own down payment.",
        {"home_id": {"type": "integer"}, "down_payment_pct": {"type": "number", "description": "percent, e.g. 10"}},
        ["home_id", "down_payment_pct"]),
    _fn("past_sales", "What earlier buyers paid in a community, from county assessor records.",
        {"community_id": {"type": "integer"}}, ["community_id"]),
    _fn("compare", "Side-by-side facts and offers for 2-6 homes.",
        {"home_ids": {"type": "array", "items": {"type": "integer"}}}, ["home_ids"]),
]
HANDLERS = {"search_homes": search_homes, "find_communities": find_communities, "get_community": get_community,
            "get_plan": get_plan, "price_incentives": reprice_home, "past_sales": past_sales, "compare": compare}


def call(conn, name: str, args: dict) -> dict:
    """Run one tool. Bad input comes back as {"error": ...} for the model to read, never an exception."""
    fn = HANDLERS.get(name)
    if fn is None:
        return {"error": f"unknown tool {name!r}; tools: {', '.join(HANDLERS)}"}
    try:
        if name == "search_homes" and isinstance(args.get("county"), str):
            args = {**args, "county": [args["county"]]}
        return fn(conn, **args)
    except (TypeError, ValueError, KeyError) as e:
        return {"error": str(e)}
