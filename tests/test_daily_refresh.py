"""daily_refresh: a page with no extraction today reuses its latest one; a thin load never gets published."""
import json
import subprocess

from tests.test_agent_tools import conn  # noqa: F401  (fixture)
import tools.daily_refresh as dr
from tools.daily_refresh import (
    carry_forward,
    enough_homes,
    fingerprint,
    same_listing,
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
        code = 2 if "tools.fetch_builder_pages" in args else 0     # 2 = a builder below its floor
        if check and code:
            raise subprocess.CalledProcessError(code, args)
        return subprocess.CompletedProcess(args, code)

    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    for name, value in {"reuse_unchanged": [], "carry_forward": [], "carry_unfetched": [], "drop_carried": [],
                        "snapshot": 5, "snapshot_communities": 5, "write_csv": 5, "enough_homes": (True, 5, 5), "expected_communities": {}}.items():
        monkeypatch.setattr(dr, name, lambda *a, _v=value, **k: _v, raising=False)
    monkeypatch.setattr(dr.db, "connect", lambda: None)
    out = dr.run("2026-10-29", deploy=False)
    assert out["homes"] == 5 and any("tools.export_site_data" in a for a in ran)
    fetch = next(a for a in ran if "tools.fetch_builder_pages" in a)
    assert fetch[-2:] == ("--date", "2026-10-29")              # one folder for the whole run (Codex 1 #1)


def stub_run(monkeypatch, fetch_code):
    ran = []

    def fake_run(args, cwd, check=True):
        ran.append(args)
        code = fetch_code if "tools.fetch_builder_pages" in args else 0
        if check and code:
            raise subprocess.CalledProcessError(code, args)
        return subprocess.CompletedProcess(args, code)

    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    for name, value in {"reuse_unchanged": [], "carry_forward": [], "carry_unfetched": [], "drop_carried": [],
                        "snapshot": 5, "snapshot_communities": 5, "write_csv": 5, "enough_homes": (True, 5, 5), "expected_communities": {}}.items():
        monkeypatch.setattr(dr, name, lambda *a, _v=value, **k: _v, raising=False)
    monkeypatch.setattr(dr.db, "connect", lambda: None)
    return ran


def test_a_fetcher_crash_still_stops_the_day(monkeypatch):
    """Codex review 1 (chunk 2) #4: check=False also swallowed a traceback (exit 1) or a kill (-9), and a
    half-fetched day was published. Only exit 2 (a builder below its floor) lets the day go on."""
    import pytest
    for code in (1, -9):
        ran = stub_run(monkeypatch, code)
        with pytest.raises(SystemExit):
            dr.run("2026-10-29", deploy=True)
        assert not any("netlify" in a for a in ran)


def routes_of(ex, day):
    return {r["page"]: r for r in json.loads((ex / day / "routes.json").read_text())}


def test_a_page_that_404s_today_is_not_carried_even_when_its_builder_is_below_floor(tmp_path):
    """Codex review 1 (chunk 2) #1: the below-floor branch carried every earlier page, a 404 included."""
    raw, ex = tmp_path / "raw", tmp_path / "extract"
    route = lambda page: {"page": page, "model": "m", "escalated": False, "needs_review": False}
    extraction(ex, "2026-10-28", [route("shea/a"), route("shea/b")])
    (raw / "2026-10-29" / "shea").mkdir(parents=True)
    (raw / "2026-10-29" / "shea" / "manifest.json").write_text(json.dumps(
        [{"file": None, "status": 404, "kind": "community", "url": "https://shea/a"}]))
    assert dr.carry_unfetched("2026-10-29", {"shea": 2}, raw, ex) == ["shea/b"]


def test_a_page_is_carried_for_at_most_max_carry_days_after_its_last_real_fetch(tmp_path):
    """Codex review 1 (chunk 2) #2: repeated outages re-carried yesterday's carry with no age limit, so a sold
    home could stay listed forever at its old price."""
    raw, ex = tmp_path / "raw", tmp_path / "extract"
    route = lambda page, **k: {"page": page, "model": "m", "escalated": False, "needs_review": False, **k}
    extraction(ex, "2026-10-25", [route("shea/a")])
    for day in ("2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29"):
        (raw / day / "shea").mkdir(parents=True)
        (raw / day / "shea" / "manifest.json").write_text("[]")      # nothing fetched: below floor every day
        carried = dr.carry_unfetched(day, {"shea": 1}, raw, ex)
        assert carried == (["shea/a"] if day <= "2026-10-28" else []), day
    assert routes_of(ex, "2026-10-28")["shea/a"]["last_fetched"] == "2026-10-25"
    assert dr.MAX_CARRY_DAYS == 3


def test_a_rerun_drops_todays_carried_routes_so_fresh_pages_are_used(tmp_path):
    """Codex review 1 (chunk 2) #3: a morning carry stayed in routes.json; the afternoon rerun fetched the page
    fine but --skip-done skipped it, and yesterday's price went out stamped with today's fetch time."""
    ex = tmp_path / "extract"
    route = lambda page, **k: {"page": page, "model": "m", "escalated": False, "needs_review": False, **k}
    extraction(ex, "2026-10-29", [route("b/fresh"), route("b/carried", carried_from="2026-10-28", unfetched=True),
                                  route("b/same", carried_from="2026-10-27", unchanged=True),
                                  route("b/cap", carried_from="2026-10-28")])
    assert sorted(dr.drop_carried("2026-10-29", ex, refetched=False)) == ["b/cap", "b/carried", "b/same"]
    assert list(routes_of(ex, "2026-10-29")) == ["b/fresh"]


def test_the_guard_counts_only_homes_fetched_today(conn):  # noqa: F811
    """Codex review 1 (chunk 2) #2: carried homes counted toward the 70% guard, so a day where every fetch
    failed still published yesterday's prices as today's."""
    snapshot(conn, "2026-10-28")
    with conn:
        conn.execute("UPDATE homes SET source_page = 'b/p' || id")
    assert enough_homes(conn, ["b/p1", "b/p2"]) == (False, 3, 5)     # 3 of 5 fetched today < 70%
    assert enough_homes(conn, []) == (True, 5, 5)


def test_the_run_tests_touch_no_real_extraction_files(monkeypatch):
    """Codex review 2 #4: a run() test left drop_carried unstubbed and rewrote .tmp/extract/2026-10-29 in the repo."""
    for code in (0, 2):
        stub_run(monkeypatch, code)
        touched = []
        monkeypatch.setattr(dr.Path, "write_text", lambda self, *a, **k: touched.append(self))
        dr.run("2026-10-29", deploy=False)
        assert touched == []


def test_unchanged_is_judged_against_the_page_that_produced_the_extraction(tmp_path):
    """Codex review 2 #1: 10-28's extraction failed and 10-27's ($900k) was carried; 10-29's page matched 10-28's
    page ($800k), so the $900k extraction was reused as unchanged and published as fresh."""
    raw, pre, ex = tmp_path / "raw", tmp_path / "pre", tmp_path / "extract"
    route = {"page": "b/a", "model": "m", "escalated": False, "needs_review": False}
    extraction(ex, "2026-10-27", [route])
    extraction(ex, "2026-10-28", [{**route, "carried_from": "2026-10-27", "last_fetched": "2026-10-28"}])
    manifest(raw, "2026-10-29", "b", ["a"])
    for day, text in (("2026-10-27", "$900,000"), ("2026-10-28", "$800,000"), ("2026-10-29", "$800,000")):
        (pre / day / "b").mkdir(parents=True)
        (pre / day / "b" / "a.md").write_text(text)
    assert reuse_unchanged("2026-10-29", raw, pre, ex) == []


def test_a_page_fetched_today_is_not_marked_unfetched_by_reuse_or_carry_forward(tmp_path):
    """Codex review 2 #5: carry_forward copied an unfetched route's last_fetched, so a page fetched today aged out."""
    raw, pre, ex = tmp_path / "raw", tmp_path / "pre", tmp_path / "extract"
    route = lambda page, **k: {"page": page, "model": "m", "escalated": False, "needs_review": False, **k}
    extraction(ex, "2026-10-28", [route("b/a", carried_from="2026-10-27", unfetched=True, last_fetched="2026-10-27"),
                                  route("b/c", carried_from="2026-10-27", unfetched=True, last_fetched="2026-10-27")])
    (ex / "2026-10-27" / "m" / "b").mkdir(parents=True)
    for p in ("a", "c"):
        (ex / "2026-10-27" / "m" / "b" / f"{p}.json").write_text("{}")
    manifest(raw, "2026-10-29", "b", ["a", "c"])
    for day in ("2026-10-27", "2026-10-29"):
        (pre / day / "b").mkdir(parents=True, exist_ok=True)
        (pre / day / "b" / "a.md").write_text("$100,000")
        (pre / day / "b" / "c.md").write_text("$100,000" if day == "2026-10-27" else "$200,000")
    assert reuse_unchanged("2026-10-29", raw, pre, ex) == ["b/a"]
    assert carry_forward("2026-10-29", raw, ex) == ["b/c"]
    for r in routes_of(ex, "2026-10-29").values():
        assert not r.get("unfetched") and r["last_fetched"] == "2026-10-29", r


def test_a_route_carried_before_last_fetched_existed_does_not_crash(tmp_path):
    """Codex review 2 #2: 10-29's routes were carried by the first version, without last_fetched."""
    raw, ex = tmp_path / "raw", tmp_path / "extract"
    route = {"page": "shea/a", "model": "m", "escalated": False, "needs_review": False}
    extraction(ex, "2026-10-28", [route])
    extraction(ex, "2026-10-29", [{**route, "carried_from": "2026-10-28", "unfetched": True}])
    (raw / "2026-10-30" / "shea").mkdir(parents=True)
    (raw / "2026-10-30" / "shea" / "manifest.json").write_text("[]")
    assert dr.carry_unfetched("2026-10-30", {"shea": 1}, raw, ex) == ["shea/a"]
    assert routes_of(ex, "2026-10-30")["shea/a"]["last_fetched"] == "2026-10-28"


def test_a_builder_whose_listing_page_worked_keeps_only_pages_that_failed(tmp_path):
    """Codex review 2 #3: discovery worked and listed 1 of 2 communities (below the floor); the other was removed
    by the builder, but the below-floor branch brought it back. Only a failed listing page carries everything."""
    raw, ex = tmp_path / "raw", tmp_path / "extract"
    route = lambda page: {"page": page, "model": "m", "escalated": False, "needs_review": False}
    extraction(ex, "2026-10-28", [route("b/a"), route("b/b")])
    (raw / "2026-10-29" / "b").mkdir(parents=True)
    (raw / "2026-10-29" / "b" / "manifest.json").write_text(json.dumps(
        [{"file": "s.html", "status": 200, "kind": "start", "url": "https://b/s"},
         {"file": "b.html", "status": 200, "kind": "community", "url": "https://b/b"}]))
    extraction(ex, "2026-10-29", [route("b/b")])
    assert dr.carry_unfetched("2026-10-29", {"b": 2}, raw, ex) == []


def test_carrying_an_unchanged_route_clears_unchanged_so_it_counts_as_carried(tmp_path):
    """Codex review 3 #1: carry_forward / carry_unfetched copied unchanged=True, so carried_pages skipped the page and
    its old price was snapshotted as a fresh check."""
    from tools.snapshot_prices import carried_pages
    raw, ex = tmp_path / "raw", tmp_path / "extract"
    route = lambda page, **k: {"page": page, "model": "m", "escalated": False, "needs_review": False, **k}
    (ex / "2026-10-27" / "m" / "b").mkdir(parents=True)
    for p in ("a", "c"):
        (ex / "2026-10-27" / "m" / "b" / f"{p}.json").write_text("{}")
    extraction(ex, "2026-10-28", [route("b/a", carried_from="2026-10-27", unchanged=True, last_fetched="2026-10-28"),
                                  route("b/c", carried_from="2026-10-27", unchanged=True, last_fetched="2026-10-28")])
    d = raw / "2026-10-29" / "b"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(
        [{"file": "s.html", "status": 200, "kind": "start", "url": "https://b/s"},
         {"file": "a.html", "status": 200, "kind": "community", "url": "https://b/a"},     # fetched, not extracted
         {"file": None, "status": 0, "kind": "community", "url": "https://b/c"}]))          # fetch failed
    assert carry_forward("2026-10-29", raw, ex) == ["b/a"]
    assert dr.carry_unfetched("2026-10-29", {"b": 1}, raw, ex) == ["b/c"]
    import tools.snapshot_prices as sp
    old = sp.EXTRACT
    sp.EXTRACT = ex
    try:
        assert sorted(carried_pages("2026-10-29")) == ["b/a", "b/c"]
    finally:
        sp.EXTRACT = old


def test_a_rerun_with_a_new_fetch_starts_the_day_over_and_one_without_keeps_its_extractions(tmp_path):
    """Codex review 3 #2: a morning extraction ($900k) survived an afternoon refetch ($850k, or a 404) because
    drop_carried kept routes without carried_from and --skip-done skipped them."""
    ex = tmp_path / "extract"
    route = lambda page, **k: {"page": page, "model": "m", "escalated": False, "needs_review": False, **k}
    extraction(ex, "2026-10-29", [route("b/fresh"), route("b/carried", carried_from="2026-10-28", unfetched=True)])
    assert sorted(dr.drop_carried("2026-10-29", ex, refetched=False)) == ["b/carried"]
    assert list(routes_of(ex, "2026-10-29")) == ["b/fresh"]
    assert dr.drop_carried("2026-10-29", ex, refetched=True) == ["b/fresh"]
    assert routes_of(ex, "2026-10-29") == {}


def test_the_fingerprint_sees_a_different_home_at_the_same_price():
    """Codex review 3 #3: '1 Oak St $900,000' and '3 Elm St $900,000' fingerprinted the same, so the page was
    reused and a sold home stayed listed."""
    assert fingerprint("1 Oak St $900,000 Available") != fingerprint("3 Elm St $900,000 Available")
    assert fingerprint("Homesite 12 $900,000") != fingerprint("Homesite 14 $900,000")
    assert fingerprint("1 Oak St $900,000 open 10-6") == fingerprint("1 Oak St $900,000 Closed today")


def test_the_fingerprint_sees_two_homes_trade_prices():
    """Codex review 4 #A1: addresses and prices were sorted apart, so two homes swapping prices fingerprinted the
    same and both stale prices were republished as fresh. Homes listed in another order are still the same page."""
    page = "1 Oak St $900,000 Available. 3 Elm St $950,000 Available."
    assert not same_listing(page, "1 Oak St $950,000 Available. 3 Elm St $900,000 Available.")
    assert not same_listing("Homesite 12 $900,000 Homesite 14 $950,000", "Homesite 12 $950,000 Homesite 14 $900,000")
    assert same_listing(page, "3 Elm St $950,000 Available. 1 Oak St $900,000 Available.")
    assert same_listing(page, "1 Oak St $900,000 Open 10-6. 3 Elm St $950,000 Closed today.")
    assert not same_listing(page, page.replace("$950,000", "$940,000"))


def test_a_drive_time_is_not_an_address():
    """Codex review 4 #A3: '15 min drive' matched the street pattern, so a changed drive time re-extracted a page."""
    assert fingerprint("1 Oak St $900,000. 15 min drive to BART") == \
        fingerprint("1 Oak St $900,000. 20 min drive to BART")
    assert fingerprint("100 5th Ave $900,000") != fingerprint("102 5th Ave $900,000")    # numbered streets still count
