"""coverage_report aggregation on hand-made routes and records."""
from tools.coverage_report import builder_rows, gate


def test_builder_rows_count_addresses_lots_and_routing():
    routes = [{"page": "kb/a", "escalated": True, "needs_review": False},
              {"page": "kb/b", "escalated": True, "needs_review": True}]
    records = {"kb/a": {"homes": [{"address": "1 A St", "lot": None}, {"address": None, "lot": "12"}]},
               "kb/b": {"homes": [{"address": None, "lot": None}]}}
    floors = {"kb": {"fetched": 21, "expected": 16, "floor_ok": True, "skip_reason": None}}
    [row] = builder_rows(routes, records, floors)
    assert (row["pages"], row["homes"], row["with_address"], row["lot_only"], row["neither"]) == (2, 3, 1, 1, 1)
    assert (row["escalated"], row["needs_review"]) == (2, 1)


def test_gate_needs_95pct_address_or_lot_and_every_floor_or_a_logged_skip():
    ok = [{"builder": "kb", "homes": 100, "with_address": 90, "lot_only": 5, "floor_ok": True, "skip_reason": None},
          {"builder": "fcb", "homes": 0, "with_address": 0, "lot_only": 0, "floor_ok": False, "skip_reason": "JS"}]
    assert gate(ok)["passed"]
    short = [{**ok[0], "with_address": 80}]
    assert not gate(short)["passed"] and gate(short)["address_or_lot"] == 0.85
    unlogged = [{**ok[0]}, {**ok[1], "skip_reason": None}]
    assert not gate(unlogged)["passed"]
