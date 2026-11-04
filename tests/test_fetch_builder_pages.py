"""Hand-made snippets only; no network."""
import types

import requests

import tools.fetch_builder_pages as fbp
from tools.fetch_builder_pages import find_paths, floor_ok, in_scope, load_config, page_filename, robots_from_response

UA = "Mozilla/5.0 (compatible; bay-area-new-home-true-price/0.1)"
ROBOTS = "User-agent: *\nAllow: /llms.txt\nDisallow: /assets\nDisallow: /sitecore\n"
CITY = {"dublin": "alameda", "lathrop": "san_joaquin"}


def test_robots_200_is_parsed():
    rp = robots_from_response(200, ROBOTS, "https://x.com/robots.txt")
    assert rp.can_fetch(UA, "https://x.com/ca/bay-area/dublin/larkspur")
    assert not rp.can_fetch(UA, "https://x.com/assets/plan.png")


def test_robots_403_means_no_file_not_blocked():
    assert robots_from_response(403, "", "https://x.com/robots.txt").can_fetch(UA, "https://x.com/anything")


def test_robots_5xx_blocks_everything():
    assert not robots_from_response(503, "", "https://x.com/robots.txt").can_fetch(UA, "https://x.com/anything")


def test_find_paths_reads_embedded_json_and_dedupes():
    text = ('<a href="/ca/bay-area/dublin/larkspur-at-francis-ranch">x</a>'
            '{"url":"/ca/bay-area/dublin/larkspur-at-francis-ranch","u2":"/ca/bay-area/vacaville/farmstead"}')
    got = find_paths(text, r"/ca/bay-area/(?P<city>[a-z0-9-]+)/(?P<slug>[a-z0-9-]+)")
    assert [g["slug"] for g in got] == ["larkspur-at-francis-ranch", "farmstead"]


def test_in_scope_keeps_only_our_counties():
    got = in_scope([{"city": "dublin", "slug": "a"}, {"city": "vacaville", "slug": "b"}], CITY)
    assert got == [{"city": "dublin", "slug": "a", "county": "alameda"}]


def test_pulte_city_pattern_does_not_match_community_paths():
    pat = r'/homes/california/bay-area/(?P<city>[a-z0-9-]+)(?=["/?#\s]|$)'
    text = '"/homes/california/bay-area/lathrop" "/homes/california/bay-area/lathrop/shores-211184"'
    # the community path also starts with the city path; both yield the same city, which dedupes by city later
    assert {m["city"] for m in find_paths(text, pat)} == {"lathrop"}


def test_page_filename():
    assert page_filename("https://trumarkhomes.com/new-homes/ca/dublin/parkton/") == "new-homes__ca__dublin__parkton.html"


def test_placeholder_name_drops_pulte_id():
    from tools.fetch_builder_pages import placeholder_name
    assert placeholder_name("lilac-at-oakwood-trails-211184") == "Lilac At Oakwood Trails"
    assert placeholder_name("the-collective-55") == "The Collective"  # trailing number dropped too; extraction renames


def test_floor():
    assert floor_ok(4, 6) and not floor_ok(3, 6)


def test_find_paths_path_group_and_city_names_become_slugs():
    # KB lists communities in JSON-LD: the URL and the city name sit apart, so the regex names `path`
    text = ('{"@type":"Place","url":"https://www.kbhome.com/new-homes-central-valley/sendero-at-griffin-park",'
            '"address":{"@type":"PostalAddress","streetAddress":"2826 Midas Ave.","addressLocality":"Mountain House"}}')
    pat = (r'"url":"https://www\.kbhome\.com(?P<path>/new-homes-[a-z-]+/(?P<slug>[a-z0-9-]+))",'
           r'"address":\{[^}]*"addressLocality":"(?P<city>[^"]+)"')
    got = find_paths(text, pat)
    assert [(g["path"], g["slug"], g["city"]) for g in got] == [
        ("/new-homes-central-valley/sendero-at-griffin-park", "sendero-at-griffin-park", "mountain-house")]


def test_city_slug():
    from tools.fetch_builder_pages import city_slug
    assert city_slug("Mountain House") == "mountain-house" == city_slug("Mountain-House")
    assert city_slug("San Jose ") == "san-jose" and city_slug(None) is None


def test_page_city_reads_the_community_pages_own_address():
    from tools.fetch_builder_pages import page_city
    html = '<script type="application/ld+json">{"address":{"addressLocality": "Lathrop","postalCode":"95330"}}</script>'
    assert page_city(html) == "lathrop"
    assert page_city("<html>no address</html>") is None
    assert page_city('{"city":"Tracy"}', r'"city":"(?P<city>[^"]+)"') == "tracy"


class FakeSession:
    """Answers in order: an exception instance is raised, an int is a status with that body."""
    def __init__(self, answers):
        self.answers, self.calls, self.headers = list(answers), [], {}

    def get(self, url, timeout, stream=False):
        self.calls.append(url)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        if isinstance(a, tuple):                           # (status, exception raised while reading the body)
            return Resp(a[0], a[1])
        return Resp(a, None)


class Resp:
    def __init__(self, status, body_error):
        self.status_code, self._err = status, body_error

    @property
    def text(self):
        if self._err:
            raise self._err
        return f"body {self.status_code}"


def fetcher(answers, monkeypatch):
    waits = []
    monkeypatch.setattr(fbp.time, "sleep", waits.append)
    f = fbp.Fetcher("ua", 0)
    f.s = FakeSession(answers)
    f.allowed = lambda url: True
    return f, waits


def test_a_dropped_connection_or_5xx_is_retried_after_a_wait(monkeypatch):
    f, waits = fetcher([requests.ConnectionError("Remote end closed connection"), 503, 200], monkeypatch)
    assert f.get("https://x/a") == (200, "body 200")
    assert len(f.s.calls) == 3 and sum(waits) >= sum(fbp.RETRY_WAITS_S[:2])


def test_retries_give_up_and_a_404_or_403_is_never_retried(monkeypatch):
    f, _ = fetcher([requests.ReadTimeout("read timed out")] * (len(fbp.RETRY_WAITS_S) + 1), monkeypatch)
    assert f.get("https://x/a") == (0, "")
    for status in (404, 403, 429):                         # gone, or a block: politeness says stop
        f, _ = fetcher([status], monkeypatch)
        assert f.get("https://x/a")[0] == status and len(f.s.calls) == 1


def test_robots_txt_survives_one_dropped_connection(monkeypatch):
    f, _ = fetcher([requests.ConnectionError("x"), 404], monkeypatch)
    del f.allowed
    assert f.allowed("https://x/a")                        # not disallow-all for the whole builder


def test_a_block_whose_body_breaks_off_is_still_a_block_and_not_retried(monkeypatch):
    """Codex review 1 #2: requests reads the body inside get(); a 403 with a truncated chunked body raised
    ChunkedEncodingError, became status 0 and was retried 3 times past the block."""
    for status in (403, 429, 404):
        f, waits = fetcher([(status, requests.exceptions.ChunkedEncodingError("truncated"))], monkeypatch)
        assert f.get("https://x/a") == (status, "") and len(f.s.calls) == 1 and not waits


def test_a_host_that_keeps_failing_stops_getting_retries(monkeypatch):
    """Codex review 1 #1: 3 tries x 45 s + 150 s of waits per page, with no limit, can run the fetch past
    midnight. After HOST_GIVE_UP pages on one host exhaust their retries, that host gets one try per page."""
    dead = [requests.ReadTimeout("x")] * ((len(fbp.RETRY_WAITS_S) + 1) * fbp.HOST_GIVE_UP)
    f, waits = fetcher(dead + [requests.ReadTimeout("x")], monkeypatch)
    for i in range(fbp.HOST_GIVE_UP):
        assert f.get(f"https://x/{i}") == (0, "")
    n, w = len(f.s.calls), sum(waits)
    assert f.get("https://x/last") == (0, "") and len(f.s.calls) == n + 1 and sum(waits) == w


def test_every_builder_lands_in_the_run_date_folder_even_past_midnight(tmp_path, monkeypatch):
    """Codex review 1 #1: fetch_builder took date.today() per builder, so a run crossing midnight split across
    two folders and the daily run (which reads its start date) missed the later builders."""
    from tools import db

    class Stub:
        def __init__(self, *a):
            pass

        def get(self, url):
            return 0, ""

    monkeypatch.setattr(fbp, "Fetcher", Stub)
    b = {"key": "kb", "name": "KB", "site": "https://kb", "start": ["https://kb/start"],
         "community_pattern": "x", "expected_communities": 1}
    res = fbp.fetch_builder(b, {"user_agent": "ua", "delay_s": 0}, {}, db.connect(":memory:"), tmp_path,
                            day="2026-11-02")
    assert res["dir"] == str(tmp_path / "2026-11-02" / "kb")


def _builder(key: str) -> dict:
    return next(b for b in load_config()[0]["builders"] if b["key"] == key)


def test_seeno_pattern_keeps_real_communities_only():
    text = ('"/communities/pittsburg/tivoli-at-tuscany-meadows" "/communities/oakley/the-preserve-at-stonewood" '
            '"/communities/oakley/the-preserve-at-stone-wood" "/communities/undefined/cadence-at-gold-hill" '
            '"/communities/west-sacramento/the-estates-at-newport" "/homes/pittsburg/tivoli-at-tuscany-meadows/1106-oak"')
    found = in_scope(find_paths(text, _builder("seeno")["community_pattern"]), load_config()[1])
    assert [m["path"] for m in found] == ["/communities/pittsburg/tivoli-at-tuscany-meadows",
                                          "/communities/oakley/the-preserve-at-stonewood"]


def test_khov_starts_from_the_state_page():
    assert _builder("khov")["start"] == ["https://www.khov.com/new-construction-homes/california/"]
