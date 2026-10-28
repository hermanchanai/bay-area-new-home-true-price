"""daily_refresh: a page with no extraction today reuses its latest one; a thin load never gets published."""
import json

from tests.test_agent_tools import conn  # noqa: F401  (fixture)
from tools.daily_refresh import carry_forward, enough_homes
from tools.snapshot_prices import snapshot


def manifest(raw, day, builder, files):
    d = raw / day / builder
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(
        [{"file": f"{f}.html", "status": 200, "kind": "community", "url": f"https://x/{f}"} for f in files]
        + [{"file": None, "status": 200, "kind": "out_of_scope", "url": "https://x/skip"}]))


def extraction(ex, day, routes):
    for r in routes:
        f = ex / day / r["model"] / f"{r['page']}.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"homes": [], "day": day}))
    (ex / day / "routes.json").write_text(json.dumps(routes))


def test_pages_without_todays_extraction_reuse_their_latest_one(tmp_path):
    raw, ex = tmp_path / "raw", tmp_path / "extract"
    route = lambda page, model="m": {"page": page, "model": model, "escalated": False, "needs_review": False}
    extraction(ex, "2026-10-27", [route("b/a"), route("b/c", "big")])
    extraction(ex, "2026-10-28", [route("b/a")])
    manifest(raw, "2026-10-29", "b", ["a", "c", "new"])
    extraction(ex, "2026-10-29", [route("b/a")])          # only a was extracted today (cap hit)
    assert carry_forward("2026-10-29", raw, ex) == ["b/c"]
    routes = {r["page"]: r for r in json.loads((ex / "2026-10-29" / "routes.json").read_text())}
    assert routes["b/c"]["carried_from"] == "2026-10-27" and "carried_from" not in routes["b/a"]
    assert json.loads((ex / "2026-10-29" / "big" / "b/c.json").read_text())["day"] == "2026-10-27"
    assert "b/new" not in routes                           # never extracted anywhere: not invented
    assert carry_forward("2026-10-29", raw, ex) == []      # idempotent


def test_a_load_much_smaller_than_the_last_snapshot_is_not_published(conn):  # noqa: F811
    assert enough_homes(conn) == (True, 5, 0)              # first day: nothing to compare with
    snapshot(conn, "2026-10-27")
    with conn:
        conn.execute("DELETE FROM incentive_values")
        conn.execute("DELETE FROM true_prices")
        conn.execute("DELETE FROM homes WHERE id IN (1, 2)")
    assert enough_homes(conn) == (False, 3, 5)             # 3 of 5 < 70%
