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
