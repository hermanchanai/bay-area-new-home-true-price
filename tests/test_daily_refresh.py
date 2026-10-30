"""daily_refresh: a page with no extraction today reuses its latest one; a thin load never gets published."""
import json
import subprocess

from tests.test_agent_tools import conn  # noqa: F401  (fixture)
import tools.daily_refresh as dr
from tools.daily_refresh import (
    carry_forward,
    enough_homes,
    fingerprint,
    reuse_unchanged,
)
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


def test_fingerprint_ignores_page_noise_but_not_a_price_or_rate_change():
    day1 = 'Sales office 10:00AM - 6:00PM {"buildId":"a1b2c3","ts":1761600000} 400 Mexico Ter $1,090,586 Was $1,310,586 4.99% ARM'
    noise = 'Sales office Closed 10/29 {"buildId":"zz9","ts":1761686400} 400 Mexico Ter $1,090,586 Was $1,310,586 4.99% ARM'
    assert fingerprint(day1) == fingerprint(noise)
    assert fingerprint(day1) != fingerprint(day1.replace("$1,090,586", "$1,050,586"))
    assert fingerprint(day1) != fingerprint(day1.replace("4.99%", "4.75%"))
    assert fingerprint('{"price":959990}') != fingerprint('{"price":939990}')     # unformatted prices in page data


def test_unchanged_pages_reuse_their_last_extraction_and_changed_ones_wait_for_the_model(tmp_path):
    raw, pre, ex = tmp_path / "raw", tmp_path / "pre", tmp_path / "extract"
    route = {"page": "b/a", "model": "m", "escalated": False, "needs_review": False}
    extraction(ex, "2026-10-27", [route, {**route, "page": "b/c"}])
    manifest(raw, "2026-10-28", "b", ["a", "c"])
    for day, a, c in (("2026-10-27", "$900,000 open 10-6", "$800,000"), ("2026-10-28", "$900,000 Closed", "$790,000")):
        (pre / day / "b").mkdir(parents=True)
        (pre / day / "b" / "a.md").write_text(a)
        (pre / day / "b" / "c.md").write_text(c)
    assert reuse_unchanged("2026-10-28", raw, pre, ex) == ["b/a"]
    routes = json.loads((ex / "2026-10-28" / "routes.json").read_text())
    assert [(r["page"], r["carried_from"], r["unchanged"]) for r in routes] == [("b/a", "2026-10-27", True)]


def test_a_builder_below_its_floor_or_a_page_that_failed_today_keeps_its_last_extraction(tmp_path):
    """10-29: Shea's start page timed out (0 communities, below floor) and Pulte lost 2 pages to dropped
    connections. Their homes stay on the site from the last extraction; a page that 404s or that the
    builder no longer lists is gone and is not carried."""
    raw, ex = tmp_path / "raw", tmp_path / "extract"
    route = lambda page: {"page": page, "model": "m", "escalated": False, "needs_review": False}
    extraction(ex, "2026-10-28", [route(p) for p in ("shea/a", "shea/b", "pulte/ok", "pulte/flaky", "pulte/gone",
                                                     "pulte/sold-out", "pulte/broken")])
    d = raw / "2026-10-29"
    for b, recs in {"shea": [(None, 0, "start", "https://shea/start")],
                    "pulte": [("ok.html", 200, "community", "https://pulte/ok"),
                              (None, 0, "community", "https://pulte/flaky"),
                              (None, 502, "community", "https://pulte/broken"),
                              (None, 404, "community", "https://pulte/gone")]}.items():
        (d / b).mkdir(parents=True)
        (d / b / "manifest.json").write_text(json.dumps(
            [{"file": f, "status": s, "kind": k, "url": u} for f, s, k, u in recs]))
    extraction(ex, "2026-10-29", [route("pulte/ok")])
    carried = dr.carry_unfetched("2026-10-29", {"shea": 2, "pulte": 1}, raw, ex)
    assert sorted(carried) == ["pulte/broken", "pulte/flaky", "shea/a", "shea/b"]
    routes = {r["page"]: r for r in json.loads((ex / "2026-10-29" / "routes.json").read_text())}
    assert routes["shea/a"]["carried_from"] == "2026-10-28" and routes["shea/a"]["unfetched"]
    assert "pulte/gone" not in routes and "pulte/sold-out" not in routes
    assert dr.carry_unfetched("2026-10-29", {"shea": 2, "pulte": 1}, raw, ex) == []      # idempotent


def test_one_builder_failing_its_floor_does_not_stop_the_day(monkeypatch):
    """The fetcher exits 1 when a builder is below its floor; the daily run carries on to publish."""
    ran = []

    def fake_run(args, cwd, check=True):
        ran.append(args)
        code = 1 if "tools.fetch_builder_pages" in args else 0
        if check and code:
            raise subprocess.CalledProcessError(code, args)
        return subprocess.CompletedProcess(args, code)

    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    for name, value in {"reuse_unchanged": [], "carry_forward": [], "carry_unfetched": [], "snapshot": 5,
                        "write_csv": 5, "enough_homes": (True, 5, 5), "expected_communities": {}}.items():
        monkeypatch.setattr(dr, name, lambda *a, _v=value, **k: _v, raising=False)
    monkeypatch.setattr(dr.db, "connect", lambda: None)
    out = dr.run("2026-10-29", deploy=False)
    assert out["homes"] == 5 and any("tools.export_site_data" in a for a in ran)
