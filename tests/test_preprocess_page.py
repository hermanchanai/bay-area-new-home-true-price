"""preprocess_page on small hand-made HTML; never real builder pages."""
import json

from tools.preprocess_page import chunk, compact_json, preprocess

PAGE = """<html><head>
<script>(function(w,d){w.dataLayer=[];})(window,document); var gtm = "GTM-XXXX";</script>
<script type="application/ld+json">{"@type": "Residence", "name": "Plan 2",
  "image": "https://cdn.x.com/p2.jpg", "offers": {"price": 1299000}}</script>
<script type="text/template" data-fed-ref="fedmodel">{"title": "CONV 7/6 ARM", "body": "<p>Rate <b>4.99%</b></p>",
  "promoHeader": "<style>.x{color:red}</style>", "icon": "fa-house", "empty": "", "nothing": null}</script>
<script type="text/template" data-fed-ref="fedmodel">{"title": "CONV 7/6 ARM", "body": "<p>Rate <b>4.99%</b></p>",
  "promoHeader": "<style>.x{color:red}</style>", "icon": "fa-house", "empty": "", "nothing": null}</script>
<script id="__NEXT_DATA__" type="application/json">{"props": {"homes": [{"address": "12 Elm St", "price": 999000}]}}</script>
<style>.a{b:c}</style></head><body>
<header><nav><a href="/">Home</a><a href="/buy">Buy</a></nav></header>
<h2>Available Homes</h2>
<ul><li>3960 Viggo Way</li><li>4 Beds</li></ul>
<div>4 Beds</div><div>4 Beds</div>
<form><label>Country</label><select><option>Albania</option><option>Algeria</option></select></form>
<button>Save</button>
<svg><text>pin</text></svg>
<footer><p>*Rate 4.99% (5.932% APR) on a 7/6 ARM. Buyer must finance with Builder Mortgage.</p></footer>
</body></html>"""


def test_keeps_data_blobs_drops_tracking_js():
    d = preprocess(PAGE, url="https://b.com/c")
    assert '"price":1299000' in d.text            # JSON-LD, compacted
    assert "12 Elm St" in d.text                   # __NEXT_DATA__
    assert "CONV 7/6 ARM" in d.text                # data-fed-ref template
    assert "dataLayer" not in d.text and "GTM-" not in d.text


def test_identical_blobs_kept_once():
    assert preprocess(PAGE, url="u").text.count("CONV 7/6 ARM") == 1


def test_compact_json_drops_noise_and_flattens_html():
    got = compact_json({"title": "A", "image": "https://x/y.jpg", "icon": "fa-x",
                        "promoHeader": "<style>.a{}</style>", "body": "<p>Rate <b>4.99%</b></p>",
                        "e": "", "n": None, "l": [], "pin": "<svg><g/></svg>",
                        "floorPlanImage": "https://x/plan2.png", "hero": {"src": "https://x/h.webp"}})
    obj = json.loads(got)
    assert obj == {"title": "A", "body": "Rate 4.99%", "floorPlanImage": "https://x/plan2.png"}


def test_compact_json_drops_extensionless_image_urls_and_colors():
    got = json.loads(compact_json({
        "photo": {"Src": "https://cdn.example/api/public/content/abc?v=1"},
        "backgroundColor": "#FDEEEB", "gaEvent": "hero_click", "price": 2200000,
        "floorPlanSrc": "https://cdn.example/api/public/content/plan2?v=1"}))
    assert got == {"price": 2200000, "floorPlanSrc": "https://cdn.example/api/public/content/plan2?v=1"}


def test_compact_json_decodes_html_entities():
    assert json.loads(compact_json({"d": "valid 10/19/26 (&ldquo;Promotion&rdquo;) &amp; more"})) == \
        {"d": "valid 10/19/26 (“Promotion”) & more"}


def test_visible_text_drops_chrome_keeps_fine_print():
    t = preprocess(PAGE, url="u").text
    assert "## Available Homes" in t
    assert "- 3960 Viggo Way" in t
    lines = t.splitlines()
    assert "Albania" not in t and "Buy" not in lines and "Save" not in lines and "pin" not in lines
    assert "5.932% APR" in t                       # footer disclaimers are where incentives live
    assert ".a{b:c}" not in t


def test_consecutive_duplicate_lines_collapse_but_repeats_elsewhere_survive():
    t = preprocess(PAGE, url="u").text
    body = t.split("PAGE TEXT", 1)[1]
    assert body.count("4 Beds") == 2               # list item + one of the two adjacent divs


def test_chunk_splits_on_section_boundaries_under_limit():
    text = "\n".join(f"## S{i}\n" + "x" * 90 for i in range(10))
    parts = chunk(text, max_chars=250)
    assert all(len(p) <= 250 for p in parts)
    assert all(p.startswith("## S") for p in parts)
    assert "".join(p.replace("\n", "") for p in parts) == text.replace("\n", "")


def test_chunk_hard_splits_a_single_oversized_section():
    parts = chunk("## Big\n" + "y" * 1000, max_chars=300)
    assert len(parts) >= 4 and all(len(p) <= 300 for p in parts)
