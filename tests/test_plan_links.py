"""plan_links: each floor plan's own page on the builder's site, from the saved community page. A wrong link
is worse than none, so everything uncertain returns nothing. Hand-made snippets only, never real pages."""
from tools.plan_links import plan_links

PAGE = "https://www.builder.example/new-homes/ca/oakley/willow-run"
BASE = "/new-homes/ca/oakley/willow-run"
ABS = "https://www.builder.example" + BASE


def card(href: str, title: str) -> str:
    return f'<div class="card"><a href="{href}"><h3>{title}</h3></a><p>From $900,000</p></div>'


# ---------- what should link ----------

def test_plain_anchor_card_resolves_to_an_absolute_url():
    html = card(f"{BASE}/plan-1/", "Plan 1") + card(f"{BASE}/plan-2/", "Plan 2")
    assert plan_links(html, PAGE, ["Plan 1", "Plan 2"]) == {"Plan 1": f"{ABS}/plan-1/", "Plan 2": f"{ABS}/plan-2/"}


def test_relative_href_is_resolved_against_the_page():
    assert plan_links(card("willow-run/plan-1", "Plan 1"), PAGE, ["Plan 1"]) == {"Plan 1": f"{ABS}/plan-1"}


def test_html_entities_in_href_and_name_are_decoded():
    html = card(f"{BASE}/oak&#45;and&#45;ash", "Oak &amp; Ash")
    assert plan_links(html, PAGE, ["Oak & Ash"]) == {}  # "and" is not "&": unsure, nothing
    html = card(f"{BASE}/plan&#45;1", "Plan 1")
    assert plan_links(html, PAGE, ["Plan 1"]) == {"Plan 1": f"{ABS}/plan-1"}
    html = card(f"{BASE}/oak-ash", "Oak &amp; Ash")
    assert plan_links(html, PAGE, ["Oak &amp; Ash"]) == {"Oak &amp; Ash": f"{ABS}/oak-ash"}


def test_json_blob_builder_with_no_anchors():
    html = ('<script type="application/json">{"plans":[{"name":"Residence 1","url":"https:\\/\\/www.builder.example'
            + BASE.replace("/", "\\/") + '\\/residence-1"},{"name":"Residence 2","url":"' + BASE
            + '/residence-2"}]}</script>')
    assert plan_links(html, PAGE, ["Residence 1", "Residence 2"]) == {"Residence 1": f"{ABS}/residence-1",
                                                                      "Residence 2": f"{ABS}/residence-2"}


def test_json_double_encoded_inside_a_string():
    html = f'<script>self.push("{{\\"Floorplan\\":{{\\"Id\\":7,\\"Url\\":\\"{ABS}/casa/\\"}}}}")</script>'
    assert plan_links(html, PAGE, ["Casa"]) == {"Casa": f"{ABS}/casa/"}


def test_json_inside_an_entity_encoded_attribute():
    html = f'<div data-props="{{&quot;href&quot;:&quot;{BASE}/alder&quot;}}"></div>'
    assert plan_links(html, PAGE, ["Alder"]) == {"Alder": f"{ABS}/alder"}


def test_plan_directory_and_numeric_id_suffixes():
    html = (card(f"{BASE}/plans/plan-1---1708/", "Plan 1") + card(f"{BASE}/plan-2-156257", "Plan 2")
            + card(f"{BASE}/floor-plans/plan-3", "Plan 3") + card(f"{BASE}/floorplans/plan-4", "Plan 4"))
    assert plan_links(html, PAGE, ["Plan 1", "Plan 2", "Plan 3", "Plan 4"]) == {
        "Plan 1": f"{ABS}/plans/plan-1---1708/", "Plan 2": f"{ABS}/plan-2-156257",
        "Plan 3": f"{ABS}/floor-plans/plan-3", "Plan 4": f"{ABS}/floorplans/plan-4"}


def test_slug_may_repeat_the_community_name():
    assert plan_links(f'<a href="{BASE}/willow-run-alberta">View</a>', PAGE, ["Alberta"]) == {
        "Alberta": f"{ABS}/willow-run-alberta"}


def test_slug_may_carry_part_of_the_community_name():
    page = "https://www.builder.example/ca/melrose-at-the-villages"
    assert plan_links('<a href="/ca/melrose-at-the-villages/melrose-begonia">View</a>', page, ["Begonia"]) == {
        "Begonia": "https://www.builder.example/ca/melrose-at-the-villages/melrose-begonia"}
    page = "https://www.builder.example/ca/poppy-lane"
    assert plan_links('<a href="/ca/poppy-lane/plan-1-poppy-lane">View</a>', page, ["Plan 1"]) == {
        "Plan 1": "https://www.builder.example/ca/poppy-lane/plan-1-poppy-lane"}


def test_a_plan_named_after_the_community_still_matches_its_slug():
    page = "https://www.builder.example/ca/the-heights"
    assert plan_links('<a href="/ca/the-heights/heights-plan-1">View</a>', page, ["Heights Plan 1"]) == {
        "Heights Plan 1": "https://www.builder.example/ca/the-heights/heights-plan-1"}


def test_collection_directory_may_contain_a_digit():
    assert plan_links(f'<a href="{BASE}/layton2/residence-1">x</a>', PAGE, ["Residence 1"]) == {
        "Residence 1": f"{ABS}/layton2/residence-1"}


def test_modeled_in_the_name_is_not_part_of_the_plan():
    assert plan_links(f'<a href="{BASE}/plan-1610">x</a>', PAGE, ["Plan 1610 Modeled"]) == {
        "Plan 1610 Modeled": f"{ABS}/plan-1610"}
    assert plan_links(f'<a href="{BASE}/plan-1610-modeled">x</a>', PAGE, ["Plan 1610 Modeled"]) == {
        "Plan 1610 Modeled": f"{ABS}/plan-1610-modeled"}


def test_modeled_and_unmodeled_names_on_one_url_are_rejected():
    assert plan_links(f'<a href="{BASE}/plan-1610">x</a>', PAGE, ["Plan 1610 Modeled", "Plan 1610"]) == {}


def test_first_line_of_a_link_that_wraps_a_whole_card_is_its_label():
    html = (f'<a href="{BASE}/plan-1/"><div><span>Residence 1</span><br>Single Family<br>3 Beds</div></a>'
            f'<a href="{BASE}/plan-2/"><div><span>Residence 2</span><br>Single Family<br>4 Beds</div></a>')
    assert plan_links(html, PAGE, ["Residence 1", "Residence 2"]) == {"Residence 1": f"{ABS}/plan-1/",
                                                                      "Residence 2": f"{ABS}/plan-2/"}


def test_a_card_titled_as_another_plan_distrusts_the_url_even_when_the_slug_fits():
    html = f'<a href="{BASE}/residence-1/"><div><span>Residence 8</span><br>Single Family</div></a>'
    assert plan_links(html, PAGE, ["Residence 8", "Residence 1"]) == {}


def test_number_words_match_digits():
    assert plan_links(f'<a href="{BASE}/floorplans/residence-1">View</a>', PAGE, ["Residence One"]) == {
        "Residence One": f"{ABS}/floorplans/residence-1"}


def test_label_links_a_plan_whose_slug_uses_another_word():
    html = card(f"{BASE}/plan-1/", "Residence 1") + card(f"{BASE}/residence-2/", "Residence 2")
    assert plan_links(html, PAGE, ["Residence 1", "Residence 2"]) == {"Residence 1": f"{ABS}/plan-1/",
                                                                      "Residence 2": f"{ABS}/residence-2/"}


def test_aria_label_counts_as_a_label():
    html = f'<a href="{BASE}/merrick-2-1143a" aria-label="Merrick"></a>'
    assert plan_links(html, PAGE, ["Merrick"]) == {"Merrick": f"{ABS}/merrick-2-1143a"}


def test_collection_in_the_name_picks_the_right_collection():
    html = (f'<a href="{BASE}/grant/residence-1">x</a><a href="{BASE}/mint/residence-1">x</a>')
    assert plan_links(html, PAGE, ["Residence 1 - Grant", "Residence 1 - Mint"]) == {
        "Residence 1 - Grant": f"{ABS}/grant/residence-1", "Residence 1 - Mint": f"{ABS}/mint/residence-1"}


def test_series_beside_the_link_separates_plans_with_the_same_short_name():
    def series_card(href, series):
        return (f'<div class="card"><a href="{href}">Plan 1</a><div>Series: {series}</div>'
                f'<a href="{href}"><span>View Home</span></a></div>')
    html = series_card(f"{BASE}/plan-1-699723", "Haven") + series_card(f"{BASE}/plan-1-699725", "Oasis")
    assert plan_links(html, PAGE, ["Haven Plan 1", "Oasis Plan 1"]) == {"Haven Plan 1": f"{ABS}/plan-1-699723",
                                                                       "Oasis Plan 1": f"{ABS}/plan-1-699725"}


def test_upper_case_paths_keep_their_case():
    page = "https://www.builder.example/homes/California/Willow-Run"
    html = '<a href="/homes/California/Willow-Run/Echo-Collection/Alpine">Alpine</a>'
    assert plan_links(html, page, ["Alpine"]) == {
        "Alpine": "https://www.builder.example/homes/California/Willow-Run/Echo-Collection/Alpine"}


def test_www_and_bare_host_are_the_same_site():
    html = card(f"https://builder.example{BASE}/plan-1/", "Plan 1")
    assert plan_links(html, PAGE, ["Plan 1"]) == {"Plan 1": f"https://builder.example{BASE}/plan-1/"}


def test_a_home_link_beside_the_plan_link_does_not_make_it_ambiguous():
    html = (card(f"{BASE}/residence-1/", "Residence 1") + card(f"{BASE}/residence-1/2009-cascade-way/", "Residence 1")
            + card(f"{BASE}/residence-1/25214510118", "Residence 1"))
    assert plan_links(html, PAGE, ["Residence 1"]) == {"Residence 1": f"{ABS}/residence-1/"}


# ---------- what must not link ----------

def test_shared_url_is_rejected_for_every_plan_that_claims_it():
    html = card(f"{BASE}/homes-ab", "Plan 1") + card(f"{BASE}/homes-ab", "Plan 2")
    assert plan_links(html, PAGE, ["Plan 1", "Plan 2"]) == {}
    html = f'<a href="{BASE}/residence-1">Residence 2</a><a href="{BASE}/residence-1">Residence 1</a>'
    assert plan_links(html, PAGE, ["Residence 1", "Residence 2"]) == {}


def test_same_short_name_with_no_way_to_separate_them_is_rejected():
    html = f'<a href="{BASE}/plan-1-699723">Plan 1</a><a href="{BASE}/plan-1-699725">Plan 1</a>'
    assert plan_links(html, PAGE, ["Haven Plan 1", "Oasis Plan 1", "Plan 1"]) == {}
    # One link and two series named on the page: the link cannot be told apart.
    html = f'<main><p>Haven</p><p>Oasis</p><a href="{BASE}/plan-1-699723">Plan 1</a></main>'
    assert plan_links(html, PAGE, ["Haven Plan 1", "Oasis Plan 1"]) == {}


def test_off_site_link_is_rejected():
    for host in ("https://partner.example", "https://media.builder.example", "http://builder.example.evil.test"):
        assert plan_links(card(f"{host}{BASE}/plan-1", "Plan 1"), PAGE, ["Plan 1"]) == {}
    assert plan_links(card("https://partner.example/plan-1", "Plan 1"), PAGE, ["Plan 1"]) == {}


def test_a_link_wrapped_in_another_url_is_not_read_as_a_link():
    html = f'<a href="https://partner.example/go?to={BASE}/plan-1">Go</a>'
    assert plan_links(html, PAGE, ["Plan 1"]) == {}


def test_the_community_page_itself_is_rejected():
    for href in (PAGE, PAGE + "/", BASE, BASE + "/", "", "?plan=1"):
        assert plan_links(card(href, "Plan 1"), PAGE, ["Plan 1"]) == {}


def test_anchor_fragment_is_rejected():
    for href in ("#plan-1", f"{BASE}#plan-1", f"{BASE}/plan-1#floor-plan"):
        assert plan_links(card(href, "Plan 1"), PAGE, ["Plan 1"]) == {}


def test_query_routed_listing_is_rejected():
    assert plan_links(card(f"{BASE}/floor-plans?plan=plan-1", "Plan 1"), PAGE, ["Plan 1"]) == {}


def test_shared_listing_is_rejected():
    for tail in ("floor-plans", "floorplans", "plans", "available-homes", "gallery"):
        assert plan_links(card(f"{BASE}/{tail}", "Plan 1"), PAGE, ["Plan 1"]) == {}


def test_brochure_and_files_are_rejected():
    for tail in ("plan-1.pdf", "plan-1.jpg", "docs/plan-1.PDF", "plan-1.png"):
        assert plan_links(card(f"{BASE}/{tail}", "Plan 1"), PAGE, ["Plan 1"]) == {}


def test_brochure_never_beats_the_plan_page():
    html = card(f"{BASE}/plan-1.pdf", "Plan 1") + card(f"{BASE}/plan-1", "Plan 1")
    assert plan_links(html, PAGE, ["Plan 1"]) == {"Plan 1": f"{ABS}/plan-1"}


def test_home_and_lot_pages_are_not_plan_pages():
    for tail in ("residence-1/2009-cascade-way/", "residence-1-350-cottonwood-path-0293", "residence-1-or-lot-93",
                 "residence-1/25214510118", "lots/residence-1", "homes/residence-1", "quick-move-in/residence-1",
                 "lot-4-residence-1", "homesite-10"):
        assert plan_links(card(f"{BASE}/{tail}", "Residence 1"), PAGE, ["Residence 1"]) == {}, tail


def test_sub_pages_of_a_plan_are_not_the_plan_page():
    for tail in ("residence-1/floorplan", "residence-1/video", "residence-1/virtual-tour"):
        assert plan_links(f'<a href="{BASE}/{tail}">Residence 1</a>', PAGE, ["Residence 1"]) == {}


def test_a_near_name_is_not_the_plan():
    html = f'<a href="{BASE}/residence-1x">Floor plan</a>'
    assert plan_links(html, PAGE, ["Residence 1"]) == {}
    html = f'<a href="{BASE}/residence-1">Floor plan</a>'
    assert plan_links(html, PAGE, ["Residence 1x"]) == {}
    html = f'<a href="{BASE}/residence-1-2">Floor plan</a>'  # a short number is not a plan id
    assert plan_links(html, PAGE, ["Residence 1"]) == {}


def test_a_label_cannot_override_a_different_number_in_the_url():
    assert plan_links(f'<a href="{BASE}/plan-2/">Residence 1</a>', PAGE, ["Residence 1"]) == {}


def test_a_label_needs_something_plan_specific_in_the_url():
    assert plan_links(f'<a href="{BASE}/plan/">Residence 1</a>', PAGE, ["Residence 1"]) == {}


def test_another_communitys_plan_is_rejected():
    other = "/new-homes/ca/oakley/cedar-hills/plan-1/"
    assert plan_links(card(other, "Plan 1"), PAGE, ["Plan 1"]) == {}
    # A community whose path merely starts the same.
    assert plan_links(card(f"{BASE}-east/plan-1/", "Plan 1"), PAGE, ["Plan 1"]) == {}


def test_same_plan_in_two_collections_is_rejected():
    html = f'<a href="{BASE}/emerald/residence-1">x</a><a href="{BASE}/serenity/residence-1">x</a>'
    assert plan_links(html, PAGE, ["Residence 1"]) == {}


def test_an_ambiguous_short_name_does_not_block_the_specific_one():
    html = f'<a href="{BASE}/emerald/residence-1">x</a><a href="{BASE}/serenity/residence-1">x</a>'
    assert plan_links(html, PAGE, ["Residence 1", "Emerald Residence 1"]) == {
        "Emerald Residence 1": f"{ABS}/emerald/residence-1"}


def test_two_spellings_of_one_plan_still_share_a_url_so_neither_links():
    html = f'<a href="{BASE}/emerald/residence-1">x</a>'
    assert plan_links(html, PAGE, ["Emerald Residence 1", "Residence 1 (Emerald)"]) == {}


def test_case_and_slash_variants_are_one_url_not_two():
    html = f'<a href="{BASE}/residence-1">x</a><a href="{BASE}/residence-1/">x</a><a href="{BASE}/Residence-1">x</a>'
    assert list(plan_links(html, PAGE, ["Residence 1"])) == ["Residence 1"]


def test_no_links_and_no_names():
    assert plan_links("<html></html>", PAGE, ["Plan 1"]) == {}
    assert plan_links(card(f"{BASE}/plan-1", "Plan 1"), PAGE, []) == {}
    assert plan_links("", PAGE, []) == {}


def test_apply_saves_each_plans_own_link_and_clears_a_link_that_went_away(tmp_path):
    from tools import db
    from tools.agent_tools import get_plan
    from tools.plan_links import apply
    conn = db.connect(":memory:")
    conn.execute("INSERT INTO builders (id, name, site) VALUES (1, 'B', 'https://b.example')")
    conn.execute("INSERT INTO communities (id, builder_id, name, url) VALUES (1, 1, 'Oak', 'https://b.example/oak/')")
    conn.execute("INSERT INTO plans (id, community_id, name, url) VALUES (1, 1, 'Plan 1', 'https://b.example/old')")
    conn.execute("INSERT INTO plans (id, community_id, name, url) VALUES (2, 1, 'Plan 2', 'https://b.example/old2')")
    page = tmp_path / "oak.html"
    page.write_text('<a href="/oak/plan-1">Plan 1</a>')
    assert apply(conn, {"https://b.example/oak/": ("b", page)}) == 1
    assert get_plan(conn, 1, rooms=False)["url"] == "https://b.example/oak/plan-1"
    assert get_plan(conn, 2, rooms=False)["url"] is None
