"""extract_listing: schema shape and page selection; no model calls."""
from tools.extract_listing import KINDS, SCHEMA, messages_for


def _objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for v in node.values():
            yield from _objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from _objects(v)


def test_schema_meets_the_strict_subset():
    # Meta's strict mode: every object closed and every property required (nullable via type lists).
    objs = list(_objects(SCHEMA))
    assert len(objs) >= 5
    for o in objs:
        assert o["additionalProperties"] is False
        assert set(o["required"]) == set(o["properties"])


def test_incentive_kinds_match_the_db_constraint():
    from tools import db
    conn = db.connect(":memory:")
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'incentives'").fetchone()[0]
    for k in KINDS:
        assert f"'{k}'" in sql


def test_messages_put_instructions_in_developer_role():
    m = messages_for("## PAGE TEXT\nx", "trumark", "https://t/x", "community")
    assert m[0]["role"] == "developer" and "price_cut" in m[0]["content"]
    assert m[1]["content"].startswith("Builder: trumark\nPage type: community page\nURL: https://t/x")


def _fake(records):
    """extract stand-in: returns the next record for each purpose and logs the calls."""
    calls = []

    def extract(purpose):
        calls.append(purpose)
        return {**records[purpose], "_meta": {"model": f"model-{purpose}"}}
    return extract, calls


GOOD = {"community": {"price_from": None}, "homes": [], "incentives": [], "confidence": 0.9}
LOW = {**GOOD, "confidence": 0.5}
BAD_HOME = {**GOOD, "homes": [{"address": "1 Nowhere St", "price": 1_000_000, "lot": None}]}
WORSE = {**GOOD, "homes": [{"address": "1 Nowhere St", "price": 1_000_000, "lot": None},
                           {"address": "2 Nowhere St", "price": 1_000_000, "lot": None}]}


def test_route_clean_first_pass_does_not_escalate():
    from tools.extract_listing import route
    extract, calls = _fake({"extract": GOOD})
    out = route(extract, "page text")
    assert calls == ["extract"] and out["model"] == "model-extract"
    assert not out["escalated"] and not out["needs_review"]


def test_route_escalates_on_low_confidence_and_keeps_the_fixed_record():
    from tools.extract_listing import route
    extract, calls = _fake({"extract": LOW, "escalate": GOOD})
    out = route(extract, "page text")
    assert calls == ["extract", "escalate"] and out["model"] == "model-escalate"
    assert out["escalated"] and not out["needs_review"]


def test_route_still_failing_goes_to_review_with_the_fewer_errors_record():
    from tools.extract_listing import route
    extract, calls = _fake({"extract": BAD_HOME, "escalate": WORSE})
    out = route(extract, "page text")
    assert out["needs_review"] and out["model"] == "model-extract"
    assert [i["rule"] for i in out["issues"] if i["severity"] == "error"] == ["address_not_on_page", "price_not_on_page"]
