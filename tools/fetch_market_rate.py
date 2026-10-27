"""Latest 30-yr fixed mortgage rate (Freddie Mac PMMS via FRED) into market_rates.

    python -m tools.fetch_market_rate      # fetch, store, print "<date> <rate>%"
"""
import csv
import io
import logging
from datetime import datetime, timezone

import requests

from tools import db

SERIES = "MORTGAGE30US"
URL = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={SERIES}"

log = logging.getLogger(__name__)


def parse_latest(csv_text: str) -> tuple[str, float]:
    """(date, rate%) of the newest row with a value. FRED writes '.' for a missing week."""
    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows or len(rows[0]) != 2 or rows[0][1] != SERIES:
        raise ValueError(f"unexpected FRED header: {rows[0] if rows else 'empty'}")
    for date, value in reversed(rows[1:]):
        if value not in ("", "."):
            return date, float(value)
    raise ValueError("no rate values in FRED response")


def fetch_and_store(conn=None) -> tuple[str, float]:
    # FRED stalls on our project User-Agent (2026-10-26: 18 s then reset) but answers the
    # default python-requests one in 0.1 s, so no custom header here.
    resp = requests.get(URL, timeout=30)
    resp.raise_for_status()
    as_of, rate = parse_latest(resp.text)
    if not 1.0 <= rate <= 15.0:
        raise ValueError(f"implausible 30-yr rate {rate}% on {as_of}")
    conn = conn or db.connect()
    with conn:
        conn.execute("INSERT OR REPLACE INTO market_rates VALUES (?, ?, ?, ?)",
                     (SERIES, as_of, rate, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return as_of, rate


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    d, r = fetch_and_store()
    print(f"{SERIES} {d} {r:.2f}%")
