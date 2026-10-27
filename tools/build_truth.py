"""Truth labels for the extraction eval, built WITHOUT any model.

    python -m tools.build_truth            # latest fetch date -> eval/truth/<builder>__<page>.json

Homes and plans come from each builder's own page structure, parsed by hand-written,
builder-specific rules (Taylor Morrison's embedded JSON; Trumark's and Pulte's card layouts).
That's exactly what the model-based extractor avoids having to write for 20+ builders, which
is why it makes an independent yardstick. Incentive kinds come from rules over the page text;
each page's kinds were then reviewed by hand against the page (see workflows/extract_listing.md).

Truth format, per page:
  homes:  [{address, price, was_price, beds, baths, sqft}]  or null when not labelled
  plans:  [{name, beds_max, sqft_min, from_price}]          or null when not labelled
  incentive_kinds: [[kind, alternative kind, ...], ...]     each inner list = one incentive,
                                                            any listed kind counts as correct
"""
import argparse
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup

from tools.preprocess_page import PRE, RAW, latest_date

ROOT = Path(__file__).resolve().parent.parent
TRUTH = ROOT / "eval" / "truth"
CASH = ["flat_cash", "closing_credit"]  # "flex cash your way" is either, by the builder's own wording


def money(s: str) -> int:
    return int(re.sub(r"[^\d]", "", s))


def baths(full: float, half: float = 0) -> float:
    return float(full) + 0.5 * float(half or 0)


# --- Taylor Morrison: data-fed-ref JSON ------------------------------------------------------

def _tm_blobs(html: str) -> list[dict]:
    out = []
    for s in BeautifulSoup(html, "lxml").find_all("script", attrs={"data-fed-ref": True}):
        try:
            out.append(json.loads(s.string or ""))
        except json.JSONDecodeError:
            pass
    return out


def tm_truth(html: str, text: str) -> dict:
    homes, plans = [], []
    for d in _tm_blobs(html):
        if "availableHomesTotal" in d and isinstance(d.get("homes"), list):
            for h in d["homes"]:
                homes.append({"address": h["address"], "price": h.get("price") or None,
                              "was_price": h.get("wasPrice") or None, "beds": h.get("bed"),
                              "baths": baths(h.get("fullBath") or 0, h.get("halfBath")), "sqft": h.get("sqft")})
        if isinstance(d.get("floorplans"), list):
            for p in d["floorplans"]:
                sp = p.get("specs") or {}
                plans.append({"name": p["name"].strip(), "beds_max": int(sp["maxBed"]) if sp.get("maxBed") else None,
                              "sqft_min": int(sp["minSqFt"]) if sp.get("minSqFt") else None,
                              "from_price": p.get("isPrice") or None})
    kinds = []
    if any(h["was_price"] and h["price"] and h["was_price"] > h["price"] for h in homes):
        kinds.append(["price_cut"])
    if re.search(r"7/6 ARM", text):
        kinds.append(["arm_promo"])
    if re.search(r"(?i)flex cash", text):
        kinds.append(CASH)
    return {"homes": homes, "plans": plans, "incentive_kinds": kinds}


# --- Trumark: card text in the preprocessed page ----------------------------------------------

def _section(text: str, heading: str) -> list[str]:
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).splitlines() if m else []


def trumark_truth(html: str, text: str) -> dict:
    lines, homes = _section(text, "Available Homes"), {}
    for i, line in enumerate(lines):
        if not line.startswith("Lot #") or i < 6:
            continue
        address = lines[i - 6]
        j, prices = i + 1, []
        while j < len(lines) and lines[j].startswith("$") and "savings" not in lines[j]:
            prices.append(money(lines[j]))
            j += 1
        rest = lines[j:j + 8]
        beds = next(int(x.split()[0]) for x in rest if x.endswith("Beds") or x.endswith("Bed"))
        ba = next(x for x in rest if re.match(r"^\d+ Ba", x))
        half = re.search(r"(\d+) Half Ba", ba)
        sqft = next(money(x) for x in rest if x.endswith("Sq. Ft."))
        homes.setdefault(address, {"address": address, "price": prices[-1],
                                   "was_price": prices[0] if len(prices) > 1 else None, "beds": beds,
                                   "baths": baths(int(ba.split()[0]), half.group(1) if half else 0), "sqft": sqft})
    plans, pl = [], _section(text, "Floor Plans")
    for i, line in enumerate(pl):
        if line.startswith("From $") and i > 0:
            rest = pl[i + 1:i + 6]
            sq = next((x for x in rest if x.endswith("Sq. Ft.")), None)
            bd = next((x for x in rest if re.search(r"Beds?$", x)), None)
            plans.append({"name": pl[i - 1], "from_price": money(line),
                          "sqft_min": money(sq.split("-")[0]) if sq else None,
                          "beds_max": int(re.findall(r"\d+", bd)[-1]) if bd else None})
    homes = list(homes.values())
    kinds = []
    if any(h["was_price"] for h in homes):
        kinds.append(["price_cut"])
    if re.search(r"(?i)note rate", text):
        kinds.append(["perm_rate", "unquantified"])  # its fine print (structure, term) is not on the page
    return {"homes": homes, "plans": plans, "incentive_kinds": kinds}


# --- Pulte: QMI cards (each card appears twice, gallery + grid) -------------------------------

def pulte_truth(html: str, text: str) -> dict:
    lines, homes = text.splitlines(), {}
    city_line = re.compile(r"^[A-Za-z .'-]+, CA \d{5}$")
    addr_line = re.compile(r"^\d+ [A-Za-z0-9 .,#'-]+$")  # townhome units: "400 Mexico Terrace, Unit 9"
    for i in range(len(lines) - 1):
        if not (addr_line.match(lines[i]) and city_line.match(lines[i + 1])):
            continue
        price = was = None
        got = {}
        for line_no in range(i + 2, min(i + 80, len(lines))):
            x = lines[line_no]
            if addr_line.match(x) and line_no + 1 < len(lines) and city_line.match(lines[line_no + 1]):
                break  # ran into the next card
            if price is None and re.fullmatch(r"\$[\d,]+", x):
                price = money(x)
            elif x.startswith("Was $"):
                was = money(x)
            elif x in ("Sq. Ft.", "Beds", "Baths") and x not in got:
                got[x] = lines[line_no - 1]
            if len(got) == 3:
                break
        if price is None or len(got) < 3:
            continue
        address = lines[i].title()
        homes.setdefault(address, {"address": address, "price": price, "was_price": was,
                                   "beds": int(got["Beds"]), "baths": float(got["Baths"]),
                                   "sqft": money(got["Sq. Ft."])})
    homes = list(homes.values())
    kinds = []
    if any(h["was_price"] for h in homes):
        kinds.append(["price_cut"])
    if re.search(r"\d\.\d+%[^|\n]{0,40}7/6 ARM|7/6 ARM[^|\n]{0,40}\d\.\d+%", text):
        kinds.append(["arm_promo"])
    if re.search(r"(?i)flex cash", text):
        kinds.append(CASH)
    if re.search(r"\$2,000\* incentive", text):
        kinds.append(["flat_cash"])
    return {"homes": homes, "plans": None, "incentive_kinds": kinds}


BUILDERS = {"taylor_morrison": tm_truth, "trumark": trumark_truth, "pulte": pulte_truth}


def run(date: str) -> list[dict]:
    TRUTH.mkdir(parents=True, exist_ok=True)
    out = []
    for key, fn in BUILDERS.items():
        manifest = json.loads((RAW / date / key / "manifest.json").read_text())
        for rec in manifest:
            if rec["kind"] not in ("community", "extra") or rec.get("status") != 200:
                continue
            stem = rec["file"].removesuffix(".html")
            html = (RAW / date / key / rec["file"]).read_text(errors="replace")
            text = (PRE / date / key / f"{stem}.md").read_text()
            t = {"page": f"{key}/{stem}", "url": rec["url"], "builder": key, "fetch_date": date,
                 "page_sha256": rec["sha256"], **fn(html, text)}
            if rec["kind"] == "extra":  # offers / all-homes pages: incentives only, homes not labelled
                t["homes"] = t["plans"] = None
            (TRUTH / f"{key}__{stem}.json").write_text(json.dumps(t, indent=1))
            out.append(t)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    date = ap.parse_args().date or latest_date()
    for t in run(date):
        h = t["homes"] or []
        print(f"{t['page'][:70]:70} homes={len(h):2} plans={len(t['plans']) if t['plans'] is not None else '-':>2} "
              f"kinds={['|'.join(k) for k in t['incentive_kinds']]}")
