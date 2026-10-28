"""link_parcels: street keys, community centres, parcel links, sale estimates, community aggregates."""
import pytest

from tools.link_parcels import (book_page, find_centre, link, resolve_overlaps, sale_estimate, street_key,
                                street_matches, summarize_sales)

ALAMEDA = {"key": "alameda", "roll_fy": 2025, "reassessed_through": "2025-12-31", "sale_date": "deed"}
CONTRA = {"key": "contra_costa", "roll_fy": 2025, "reassessed_through": "2025-12-31", "sale_date": "deed"}
SJ = {"key": "san_joaquin", "roll_fy": 2026, "sale_date": "year_built"}


def test_street_key_keeps_number_and_street_name_only():
    assert street_key("1161 Bradbury Drive") == ("1161", "BRADBURY")
    assert street_key("770 E AUGUSTA ST WOODBRIDGE CA 95258") == ("770", "AUGUSTA")
    assert street_key("3990 KELLOGG CREEK RD, BYRON") == ("3990", "KELLOGG CREEK")
    assert street_key("521 Headwater way") == ("521", "HEADWATER")
    assert street_key("12 Court St") == ("12", "COURT")
    assert street_key("Lot 12") is None and street_key(None) is None


def test_street_matches_tolerates_a_parcel_address_without_a_suffix():
    assert street_matches("VIA ROMA DANVILLE", {"VIA ROMA"})          # no suffix: the city is still attached
    assert street_matches("BRADBURY", {"BRADBURY"})
    assert street_matches("BRADBURY RANCH", {"BRADBURY"})         # word-boundary prefix: accepted inside 900 m
    assert not street_matches("BRADBURYS", {"BRADBURY"})


def test_book_page_per_county():
    assert book_page("alameda", "1-121-16") == "1-121"
    assert book_page("contra_costa", "002010020") == "002010"
    assert book_page("san_joaquin", "01513003") == "015130"


def P(apn, address, lat, lng, **kw):
    return {"county": "contra_costa", "apn": apn, "address": address, "lat": lat, "lng": lng, "imps": 500_000,
            "total": 1_000_000, "deed_date": "2024-08-01", "year_built": None, "city": "OAKLEY", **kw}


TRACT = [P("100100001", "1 A ST, OAKLEY", 37.9900, -121.7000), P("100100002", "3 A ST, OAKLEY", 37.9902, -121.7001),
         P("100100003", "5 B CT, OAKLEY", 37.9905, -121.7004), P("100100004", "7 B CT, OAKLEY", 37.9906, -121.7006),
         P("100100005", "9 C WAY, OAKLEY", 37.9903, -121.7002)]                       # C: not a community street
FAR = P("900900001", "1 A ST, ANTIOCH", 38.0100, -121.8000)                             # same street name, ~9 km


def test_find_centre_is_the_densest_cluster_of_community_streets():
    lat, lng = find_centre(TRACT + [FAR], {"A", "B"})
    assert lat == pytest.approx(37.9903, abs=0.0005) and lng == pytest.approx(-121.7003, abs=0.0005)


def test_link_keeps_nearby_parcels_on_community_streets():
    got = link(TRACT + [FAR], streets={"A", "B"}, exact={("3", "A")}, centre=(37.9903, -121.7003))
    assert sorted((g["apn"], g["match_method"], g["confidence"]) for g in got) == [
        ("100100001", "street", 0.8), ("100100002", "address", 1.0),
        ("100100003", "street", 0.8), ("100100004", "street", 0.8)]


def test_link_drops_book_pages_that_are_mostly_older_homes():
    old = [P("200200001", "11 A ST, OAKLEY", 37.9910, -121.7010)]
    got = link(TRACT + old, streets={"A", "B"}, exact=set(), centre=(37.9903, -121.7003),
               new_share={"100100": 1.0, "200200": 0.2})
    assert "200200001" not in {g["apn"] for g in got} and len(got) == 4


def test_overlapping_links_go_to_the_exact_match_then_the_nearest_centre():
    links = {1: [{"apn": "x", "county": "c", "match_method": "street", "confidence": 0.8, "dist_m": 500},
                 {"apn": "y", "county": "c", "match_method": "street", "confidence": 0.8, "dist_m": 100}],
             2: [{"apn": "x", "county": "c", "match_method": "street", "confidence": 0.8, "dist_m": 200},
                 {"apn": "y", "county": "c", "match_method": "address", "confidence": 1.0, "dist_m": 400}]}
    got = resolve_overlaps(links)
    assert ({g["apn"] for g in got[1]}, {g["apn"] for g in got[2]}) == (set(), {"x", "y"})


def test_alameda_uses_the_recorded_price():
    p = P("2-23-11", "x", 0, 0, county="alameda", deed_date="2023-08-18", total=1_174_000)
    assert sale_estimate(p, 1_129_000, ALAMEDA) == (1_129_000, "transfer_tax", "2023-08-18")


def test_value_backed_out_by_fiscal_years_since_the_sale():
    # Prop 13: +2% each July-1 roll after the sale; values are the 2025-26 roll (checked on Alameda's prices)
    assert sale_estimate(P("a", "x", 0, 0, deed_date="2024-08-10", total=1_020_000), None, CONTRA) == (
        1_000_000, "value_backed_out", "2024-08-10")
    assert sale_estimate(P("a", "x", 0, 0, deed_date="2023-03-01", total=1_061_208), None, CONTRA)[0] == 1_000_000
    assert sale_estimate(P("a", "x", 0, 0, deed_date="2025-09-23", total=818_000), None, CONTRA)[0] == 818_000


def test_no_estimate_when_the_value_cannot_be_a_sale_price():
    assert sale_estimate(P("a", "x", 0, 0, deed_date="2026-02-01"), None, CONTRA) is None      # not reassessed yet
    assert sale_estimate(P("a", "x", 0, 0, imps=0), None, CONTRA) is None                     # builder-owned lot
    assert sale_estimate(P("a", "x", 0, 0, deed_date="2023-05-01", year_built=2025), None, CONTRA) is None


def test_san_joaquin_assumes_the_sale_in_the_year_built():
    p = P("a", "x", 0, 0, county="san_joaquin", deed_date=None, year_built=2024, total=1_040_400)
    assert sale_estimate(p, None, SJ) == (1_000_000, "value_backed_out", "2024")
    assert sale_estimate({**p, "year_built": 2026}, None, SJ) is None                           # after the lien date


def test_summarize_needs_five_sales():
    sales = [(v, "value_backed_out", d) for v, d in [(100_000, "2023-08-18"), (200_000, "2024-01-02"),
                                                       (300_000, "2025-10-01"), (400_000, "2024-05-05")]]
    assert summarize_sales(sales) is None
    s = summarize_sales(sales + [(500_000, "transfer_tax", "2024-02-02")])
    assert s == {"n": 5, "median_est_price": 300_000, "p25": 200_000, "p75": 400_000,
                 "period": "2023-08 to 2025-10", "method": "mixed"}


def test_streets_count_only_near_the_community_city():
    # Dublin's "Francis Ranch" streets also exist in Oakland, 25 km away: the centre must not land there.
    # But the builder's city is a marketing name: Brooks at Creekside says Antioch, its parcels say Oakley.
    oak = [P(f"30030000{i}", f"{i} A ST, OAKLAND", 37.80 + i / 10000, -122.27, city="OAKLAND") for i in range(1, 9)]
    near_oakley = (37.98, -121.75)                                   # ~4.5 km from the tract
    assert find_centre(TRACT + oak, {"A", "B"}, city_centre=near_oakley)[0] == pytest.approx(37.9903, abs=0.0005)
    assert find_centre(oak, {"A", "B"}, city_centre=near_oakley) is None
    assert find_centre(oak, {"A", "B"})[0] == pytest.approx(37.80045, abs=0.0005)   # no city known: densest wins


def test_a_deed_covering_several_parcels_is_not_a_home_sale():
    # Abigail Place: one 2024-05-29 document moved 7 lots to the builder (improvements partly built)
    p = P("145151063", "x", 0, 0, deed_date="2024-05-29", deed_doc="24050324", deed_parcels=7, total=930_714)
    assert sale_estimate(p, None, CONTRA) is None
    assert sale_estimate({**p, "deed_parcels": 1}, None, CONTRA) is not None


def test_page_points_reads_embedded_coordinates():
    from tools.link_parcels import page_points
    html = ('{"geo":{"latitude":"37.9434","longitude":"-121.754"}} {"lat": 37.80045, "lng": -122.27} '
            '{"latitude": 40.1, "longitude": -74.0}')
    assert page_points(html) == {(37.9434, -121.754), (37.80045, -122.27)}     # Bay Area / Central Valley only


def test_pin_check_compares_the_parcel_cluster_with_the_page_pin():
    from tools.link_parcels import pin_check
    assert pin_check((37.9952, -121.636), {(37.9434, -121.754)}) == (11.8, False)   # Brooks: Oakley parcels
    assert pin_check((37.9440, -121.755), {(37.9434, -121.754), (37.0, -121.0)}) == (0.1, True)
    assert pin_check((37.9440, -121.755), set()) == (None, True)                    # no pin: not checked


def test_a_pin_on_three_or_more_pages_is_a_market_pin_not_the_community():
    from tools.link_parcels import local_pins
    market = (37.7646, -121.9619)                         # Trumark: "Northern California" market centre
    pages = {"t/a": {market}, "t/b": {market, (37.70, -121.90)}, "t/c": {market}, "p/x": {market}}
    assert local_pins(pages, {"t/a": "t", "t/b": "t", "t/c": "t", "p/x": "p"}) == {
        "t/a": set(), "t/b": {(37.70, -121.90)}, "t/c": set(), "p/x": {market}}


def test_city_centres_read_san_joaquin_city_names_from_the_address():
    from tools.link_parcels import city_centres_of
    sj = [{"city": "TR", "address": "2722 EMERY ST TRACY CA 95377", "lat": 37.70, "lng": -121.47},
          {"city": "LA", "address": "3345 WORKMAN AV LATHROP CA 95330", "lat": 37.79, "lng": -121.31},
          {"city": "MTH", "address": "1 MAIN ST MOUNTAIN HOUSE CA 95391", "lat": 37.78, "lng": -121.54}]
    got = city_centres_of(sj, names={"Tracy", "Lathrop", "Mountain House"})
    assert (got["TRACY"], got["LATHROP"], got["MOUNTAINHOUSE"]) == ((37.70, -121.47), (37.79, -121.31), (37.78, -121.54))


def test_parcels_far_outside_the_community_home_sizes_belong_to_another_collection():
    # Regency at Tracy Lakes: Pinecrest (1,560-1,763 sq ft) shares Lakeshore Av with larger collections
    tract = [{**p, "living_sqft": s} for p, s in zip(TRACT, (1600, 1700, 2600, None, 1650))]
    got = link(tract, streets={"A", "B"}, exact=set(), centre=(37.9903, -121.7003), sqft=(1560, 1763))
    assert sorted(g["apn"] for g in got) == ["100100001", "100100002", "100100004"]   # 2,600 sq ft dropped
