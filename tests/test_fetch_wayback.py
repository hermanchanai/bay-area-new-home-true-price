"""fetch_wayback: older copies of builder pages become community_history rows, only where the copy backs them up."""
import json

from tests.test_agent_tools import conn as _conn
from tools.fetch_wayback import capture_row, capture_url, nearest
from tools.snapshot_prices import community_changes

conn = _conn
URL = "https://b.example/oak"
CASH = {"kind": "flat_cash", "amount_usd": 250000, "rate": None, "label": "Up to $250K", "scope": "community"}


def test_nearest_capture_to_two_months_back():
    assert nearest(["20260802120000", "20260913120000", "20261019120000"], "20260903") == "20260913120000"
    assert nearest([], "20260903") is None


def test_the_capture_link_is_the_page_as_the_builder_served_it():
    assert capture_url("20260901034601", URL) == "https://web.archive.org/web/20260901034601id_/https://b.example/oak"


def test_a_capture_becomes_a_dated_row_with_its_source():
    rec = {"community": {"price_from": 900000}, "plans": [{"from_price": 899990}], "incentives": [CASH]}
    row = capture_row(rec, URL, "20260901034601", "Plan 1 from $899,990. Up to $250K")
    assert row["snapshot_date"] == "2026-09-01" and row["from_price"] == 899990
    assert row["source_url"].startswith("https://web.archive.org/web/20260901034601")
    assert json.loads(row["incentives"])[0]["label"] == "Up to $250K"


def test_a_price_the_copy_does_not_show_is_not_recorded():
    rec = {"community": {}, "plans": [{"from_price": 899990}], "incentives": []}
    assert capture_row(rec, URL, "20260901034601", "no prices here")["from_price"] is None


def test_a_copy_with_no_priced_offers_says_nothing_about_offers():
    rec = {"community": {}, "plans": [{"from_price": 899990}], "incentives": [{"kind": "unquantified", "label": "Deals"}]}
    assert capture_row(rec, URL, "20260901034601", "$899,990")["incentives"] is None


def test_an_offer_is_not_new_just_because_an_old_copy_could_not_show_offers():
    out = community_changes([("2026-09-01", 900, None), ("2026-10-27", 900, [CASH]), ("2026-11-03", 900, [CASH])])
    assert out["tracked_since"] == "2026-09-01" and out["incentives_added"] == []
    out = community_changes([("2026-09-01", 900, [{**CASH, "amount_usd": 100}]), ("2026-10-27", 900, [CASH])])
    assert [i["date"] for i in out["incentives_added"]] == ["2026-10-27"]
