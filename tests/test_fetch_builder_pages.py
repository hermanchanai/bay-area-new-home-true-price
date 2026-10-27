"""Hand-made snippets only; no network."""
from tools.fetch_builder_pages import find_paths, floor_ok, in_scope, page_filename, robots_from_response

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
