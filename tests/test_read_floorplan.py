"""read_floorplan pure helpers: dimension parsing, SVG-label truth, area check, layout summary."""
import pytest

from tools.read_floorplan import area_check, parse_dims, score_rooms, summarize, svg_truth

SVG = """<svg><text><tspan>Porch</tspan></text><text><tspan>Kitchen</tspan></text>
<text><tspan>2-Bay Garage</tspan></text><text><tspan>20&apos; 3&quot; x 20&apos; 11&quot;</tspan></text>
<text><tspan>Bedroom 4</tspan></text><text><tspan>10&apos; 5&quot; x 10&apos; 8&quot;</tspan></text>
<text><tspan>Family Room</tspan></text><text><tspan>13&apos; 4&quot; x 14&apos; 1&quot;</tspan></text></svg>"""


def test_parse_dims():
    assert parse_dims("13' 4\" x 14' 1\"") == pytest.approx((13 + 4 / 12, 14 + 1 / 12))
    assert parse_dims("12'x14'") == (12.0, 14.0)
    assert parse_dims("13'4\" X 14'") == pytest.approx((13 + 4 / 12, 14.0))
    assert parse_dims(None) is None and parse_dims("varies") is None


def test_svg_truth_pairs_each_dimension_with_the_label_before_it():
    assert svg_truth(SVG) == {("2baygarage", (20.25, 20.917)), ("bedroom4", (10.417, 10.667)),
                              ("familyroom", (13.333, 14.083))}


def test_score_rooms():
    truth = svg_truth(SVG)
    got = [{"name": "Bedroom 4", "dims": "10' 5\" x 10' 8\""}, {"name": "Family Rm.", "dims": "13'4\" x 14'1\""},
           {"name": "2-Bay Garage", "dims": "20' 3\" x 21' 11\""}]                       # misread depth
    s = score_rooms(truth, got)
    assert (s["matched"], s["truth"]) == (1, 3)   # "Family Rm." is not the printed label; garage depth misread


def test_area_check_counts_living_rooms_only():
    rooms = [{"name": "Family Room", "dims": "20' x 20'"}, {"name": "2-Bay Garage", "dims": "20' x 20'"},
             {"name": "Porch", "dims": "10' x 10'"}, {"name": "Kitchen", "dims": None}]
    # Lennar dimensions only bedrooms, living rooms and garages: dimensioned living area is ~44-54% of sq ft
    assert area_check(rooms, 800) == {"sum_sqft": 400, "ratio": 0.5, "ok": True}
    assert area_check(rooms, 500)["ok"] is False                    # 80%: more than the labels can cover
    assert area_check(rooms, 2000)["ok"] is False                   # 20%: rooms missed
    assert area_check([], 1000)["ok"] is False


def test_summarize_layout():
    floors = [{"floor": 1, "rooms": [{"name": "Bedroom 4", "dims": "10' x 10'"}, {"name": "2-Bay Garage", "dims": None},
                                     {"name": "Flex", "dims": None}]},
              {"floor": 2, "rooms": [{"name": "Owner's Suite", "dims": "14' x 16'"}, {"name": "Loft", "dims": None}]}]
    assert summarize(floors) == {"downstairs_bedroom": True, "primary_floor": 2, "loft": True, "flex_room": True,
                                 "garage_bays": 2}


def test_svg_truth_pairs_by_position_not_file_order():
    # Lennar: "Owner's Suite", then a separate "Walk-In Closet" label, then the suite's dimensions under the suite
    svg = """<svg>
    <text transform="matrix(1 0 0 1 600 340)" class="a">Owner&apos;s Suite</text>
    <text transform="matrix(1 0 0 1 150 150)" class="a">Walk-In Closet</text>
    <text transform="matrix(1 0 0 1 150 330)" class="a">Bedroom 2</text>
    <text transform="matrix(1 0 0 1 602 352)" class="a">14&apos; x 14&apos; 5&quot;</text>
    <text transform="matrix(1 0 0 1 152 342)" class="a">10&apos; 11&quot; x 12&apos; 6&quot;</text>
    <text transform="matrix(1 0 0 1 600 340)" class="a">Owner&apos;s Suite</text></svg>"""
    assert svg_truth(svg) == {("ownerssuite", (14.0, 14.417)), ("bedroom2", (10.917, 12.5))}


def test_identical_floors_count_once():
    # Mezzano Residence 1 has one floor image; the model returned that floor twice: rooms must not double
    from tools.read_floorplan import unique_floors
    f = {"floor": 1, "rooms": [{"name": "Family Room", "dims": "16' x 16'"}]}
    assert unique_floors([f, {**f}, {"floor": 2, "rooms": [{"name": "Loft", "dims": None}]}]) == [
        f, {"floor": 2, "rooms": [{"name": "Loft", "dims": None}]}]
