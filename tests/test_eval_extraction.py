"""eval_extraction scoring on hand-made records."""
import pytest

from tools.eval_extraction import norm_address, norm_plan, score_homes, score_kinds, score_plans


@pytest.mark.parametrize("a, b", [
    ("329 E CLYDESDALE COURT", "329 East Clydesdale Ct."),
    ("1607 Walsh Avenue", "1607 walsh ave, Dublin, CA 94568"),
    ("400 Mexico Terrace, Unit 9", "400 Mexico Ter #9"),
    ("4810 Dolcetto Common", "4810 Dolcetto Cmn"),
])
def test_norm_address_equivalents(a, b):
    assert norm_address(a) == norm_address(b)


def test_norm_address_keeps_units_apart():
    assert norm_address("400 Mexico Terrace, Unit 9") != norm_address("400 Mexico Terrace, Unit 6")


def test_norm_plan():
    assert norm_plan("Residence 2") == norm_plan("residence-2") != norm_plan("Residence 3X")


TRUTH_HOMES = [
    {"address": "3960 Viggo Way", "price": 1599463, "was_price": 1749463, "beds": 4, "baths": 3.0, "sqft": 2411},
    {"address": "3830 Viggo Way", "price": 1648681, "was_price": 1798575, "beds": 4, "baths": 3.0, "sqft": 2411},
]


def test_score_homes_counts_matches_and_field_errors():
    got = [
        {"address": "3960 Viggo Way", "price": 1599463, "beds": 4, "baths": 3, "sqft": 2411},
        {"address": "3830 Viggo Way, Dublin, CA 94568", "price": 1798575, "beds": 4, "baths": 3, "sqft": 2411},
        {"address": "1 Made Up Street", "price": 1, "beds": 1, "baths": 1, "sqft": 1},
    ]
    s = score_homes(TRUTH_HOMES, got)
    assert (s["truth"], s["got"], s["matched"]) == (2, 3, 2)
    assert s["fields"]["price"] == [1, 2]       # second home reported the was-price
    assert s["fields"]["beds"] == [2, 2] and s["fields"]["sqft"] == [2, 2]
    assert [m["field"] for m in s["misses"]] == ["price", "extra_home"]


def test_score_homes_duplicate_extraction_counts_once():
    got = [{"address": "3960 Viggo Way", "price": 1599463, "beds": 4, "baths": 3, "sqft": 2411}] * 2
    s = score_homes(TRUTH_HOMES[:1], got)
    assert (s["got"], s["matched"]) == (1, 1)


def test_score_plans():
    truth = [{"name": "Residence 1", "from_price": 945000, "sqft_min": 1913, "beds_max": 3}]
    got = [{"name": "Residence 1", "from_price": 945000, "sqft_min": 1913, "beds_max": 4}]
    s = score_plans(truth, got)
    assert s["matched"] == 1 and s["fields"]["from_price"] == [1, 1] and s["fields"]["beds_max"] == [0, 1]


def test_score_kinds_alternatives_and_false_positives():
    truth = [["price_cut"], ["flat_cash", "closing_credit"], ["arm_promo"]]
    s = score_kinds(truth, ["price_cut", "price_cut", "closing_credit", "temp_buydown"])
    assert s["truth"] == 3 and s["found"] == 2           # arm_promo missed
    assert s["got"] == 3 and s["false_pos"] == 1         # temp_buydown not on the page; dupes collapse
