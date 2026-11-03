"""Check the figures quoted in the README and the demo script against the database.

    python -m tools.check_claims [--script path/to/demo_script.md]

Prints claim, value and match for each; exits 1 if any figure is no longer in its document.
"""
import argparse
import sys
from pathlib import Path
from statistics import median

from tools import db

ROOT = Path(__file__).resolve().parent.parent


def facts(conn) -> dict:
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    low, high = conn.execute("SELECT AVG(1.0 * value_low / asking), AVG(1.0 * value_high / asking) "
                             "FROM true_prices WHERE asking > 0").fetchone()
    return {
        "homes": one("SELECT COUNT(*) FROM homes"),
        "communities": one("SELECT COUNT(DISTINCT community_id) FROM homes"),
        "builders": one("SELECT COUNT(DISTINCT c.builder_id) FROM homes h JOIN communities c ON c.id = h.community_id"),
        "priced": one("SELECT COUNT(*) FROM true_prices"),
        "low_pct": round(100 * (low or 0), 1), "high_pct": round(100 * (high or 0), 1),
        "rate": one("SELECT rate FROM market_rates ORDER BY as_of DESC LIMIT 1"),
        "with_records": one("SELECT COUNT(*) FROM past_sales"),
        "with_median": one("SELECT COUNT(*) FROM past_sales WHERE median_value IS NOT NULL"),
        "records": one("SELECT COUNT(*) FROM past_records"),
        "withheld": one("SELECT COALESCE(SUM(withheld), 0) FROM past_sales"),
        "spend": one("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls"),
    }


def readme_claims(f: dict) -> list[tuple[str, str]]:
    return [("homes, communities, builders", f"{f['homes']} move-in-ready homes in {f['communities']} communities from {f['builders']} builders"),
            ("homes priced", f"{f['priced']} homes priced"),
            ("discount range", f"{f['low_pct']}% guaranteed, up to {f['high_pct']}% at most"),
            ("market rate", f"{f['rate']}%"),
            ("county records", f"{f['with_records']} communities show {f['records']} earlier homes"),
            ("communities with a median", f"{f['with_median']} of them"),
            ("values withheld", f"({f['withheld']} today)"),
            ("total spend", f"about ${round(f['spend'])}")]


def home_claims(conn, address: str) -> list[tuple[str, str]]:
    r = conn.execute("SELECT t.asking, t.true_high, t.value_low, t.value_high FROM true_prices t JOIN homes h "
                     "ON h.id = t.home_id WHERE h.address LIKE ?", (f"{address}%",)).fetchone()
    if r is None:
        return [(f"{address} is still listed", f"NOT LISTED: {address}")]
    return [(f"{address} asking", f"${r['asking']:,}"), (f"{address} true price", f"${r['true_high']:,}"),
            (f"{address} offer value", f"${r['value_low']:,}" + ("" if r["value_low"] == r["value_high"] else f" to ${r['value_high']:,}"))]


def script_claims(conn, f: dict) -> list[tuple[str, str]]:
    claims = [("homes, communities, builders", f"{f['homes']} move-in-ready homes in {f['communities']} communities from {f['builders']} builders")]
    for address in ("1461 N Padano Lane", "660 Ohlone St"):
        claims += home_claims(conn, address)
    cid = conn.execute("SELECT id FROM communities WHERE name = 'Cairnway'").fetchone()[0]
    ps = conn.execute("SELECT n, median_value FROM past_sales WHERE community_id = ?", (cid,)).fetchone()
    asks = [r[0] for r in conn.execute("SELECT price FROM homes WHERE community_id = ? AND price IS NOT NULL", (cid,))]
    claims += [("Cairnway records", f"{ps['n']} earlier homes"), ("Cairnway median assessed", f"${ps['median_value']:,}"),
               ("Cairnway median asking", f"${round(median(asks)):,}"), ("total spend", f"about ${round(f['spend'])}")]
    return claims


def check(name: str, text: str, claims: list[tuple[str, str]]) -> int:
    missing = 0
    print(f"\n{name}")
    for label, want in claims:
        ok = want in text
        missing += not ok
        print(f"  {'ok     ' if ok else 'MISSING'} {label}: {want}")
    return missing


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", type=Path)
    a = ap.parse_args()
    conn = db.connect()
    f = facts(conn)
    missing = check("README.md", (ROOT / "README.md").read_text(), readme_claims(f))
    if a.script:
        missing += check(a.script.name, a.script.read_text(), script_claims(conn, f))
    print(f"\n{missing} figure(s) out of date" if missing else "\nall figures current")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
