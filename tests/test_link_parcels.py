"""link_parcels: street keys, community centres, parcel links, assessor records per home, community medians."""
import pytest

from tools.link_parcels import (assessed_record, book_page, find_centre, link, resolve_overlaps, street_key,
                                street_matches, summarize_records)

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


def test_the_record_is_the_assessed_value_as_published_never_backed_out():
    """Herman 11-03: no estimates. A home shows the assessor's full value as the roll prints it, once the value has
    been reset to the home's last sale (deed on or before the lien date the roll counts through)."""
    p = P("a", "1 A ST, OAKLEY", 0, 0, deed_date="2024-08-10", total=1_020_000, living_sqft=1_900, beds=None)
    assert assessed_record(p, None, CONTRA) == {"assessed_value": 1_020_000, "roll_year": 2025,
                                                "sale_date": "2024-08-10", "recorded_price": None}


def test_alameda_adds_the_recorded_sale_price():
    p = P("2-23-11", "x", 0, 0, county="alameda", deed_date="2023-08-18", total=1_174_000)
    assert assessed_record(p, 1_129_000, ALAMEDA) == {"assessed_value": 1_174_000, "roll_year": 2025,
                                                      "sale_date": "2023-08-18", "recorded_price": 1_129_000}
    late = {**p, "deed_date": "2026-02-01"}                  # sold after the roll's lien date: value not reset yet
    assert assessed_record(late, 1_129_000, ALAMEDA) == {"assessed_value": None, "roll_year": 2025,
                                                         "sale_date": "2026-02-01", "recorded_price": 1_129_000}


def test_no_record_when_the_value_is_not_a_full_value_for_a_home_sale():
    assert assessed_record(P("a", "x", 0, 0, deed_date="2026-02-01"), None, CONTRA) is None      # not reset yet
    assert assessed_record(P("a", "x", 0, 0, imps=0), None, CONTRA) is None                     # builder-owned lot
    assert assessed_record(P("a", "x", 0, 0, deed_date="2023-05-01", year_built=2025), None, CONTRA) is None
    # Abigail Place: one 2024-05-29 document moved 7 lots to the builder (improvements partly built)
    bulk = P("145151063", "x", 0, 0, deed_date="2024-05-29", deed_doc="24050324", deed_parcels=7, total=930_714)
    assert assessed_record(bulk, None, CONTRA) is None
    assert assessed_record({**bulk, "deed_parcels": 1}, None, CONTRA) is not None


def test_san_joaquin_counts_homes_built_before_its_roll():
    p = P("a", "x", 0, 0, county="san_joaquin", deed_date=None, year_built=2024, total=1_040_400)
    assert assessed_record(p, None, SJ) == {"assessed_value": 1_040_400, "roll_year": 2026, "sale_date": "2024",
                                            "recorded_price": None}
    assert assessed_record({**p, "year_built": 2026}, None, SJ) is None                         # after the lien date


def test_community_median_is_of_assessed_values_and_needs_five():
    recs = [{"assessed_value": v, "sale_date": d, "recorded_price": None}
            for v, d in [(100_000, "2023-08-18"), (200_000, "2024-01-02"), (300_000, "2025-10-01"), (400_000, "2024-05-05")]]
    only_price = {"assessed_value": None, "sale_date": "2026-02-01", "recorded_price": 9_000_000}
    assert summarize_records(recs + [only_price]) is None                 # a recorded price alone is not a value
    s = summarize_records(recs + [only_price, {"assessed_value": 500_000, "sale_date": "2024-02-02", "recorded_price": 480_000}])
    assert s == {"n": 5, "median_value": 300_000, "p25": 200_000, "p75": 400_000, "period": "2023-08 to 2025-10",
                 "method": "assessed_value"}


def test_rebuild_from_stored_links_writes_per_home_records_and_the_median():
    from tools import db
    from tools.link_parcels import rebuild_records
    conn = db.connect(":memory:")
    conn.execute("INSERT INTO builders (id, name, site) VALUES (1, 'B', 'https://b.example')")
    conn.execute("INSERT INTO communities (id, builder_id, name, county_fips, url, fetched_at) "
                 "VALUES (1, 1, 'Example Grove', '06013', 'https://b.example/g', '2026-11-03')")
    for i in range(6):
        conn.execute("INSERT INTO parcels (county, apn, address, land, imps, total, deed_date, deed_doc, living_sqft) "
                     "VALUES ('contra_costa', ?, ?, 300000, 700000, ?, '2024-08-01', ?, 1900)",
                     (f"10010000{i}", f"{i} EXAMPLE CT, OAKLEY", 1_000_000 + i * 10_000, f"doc{i}"))
        conn.execute("INSERT INTO community_parcels VALUES (1, 'contra_costa', ?, 'street', 0.8)", (f"10010000{i}",))
    rebuild_records(conn)
    assert tuple(conn.execute("SELECT n, median_value, method, linked FROM past_sales").fetchone()) == (
        6, 1_025_000, "assessed_value", 6)
    rows = conn.execute("SELECT address, assessed_value, roll_year, sale_date, living_sqft, recorded_price "
                        "FROM past_records ORDER BY address").fetchall()
    assert [tuple(r) for r in rows][0] == ("0 EXAMPLE CT, OAKLEY", 1_000_000, 2025, "2024-08-01", 1900, None)
    assert len(rows) == 6


def test_streets_count_only_near_the_community_city():
    # Dublin's "Francis Ranch" streets also exist in Oakland, 25 km away: the centre must not land there.
    # But the builder's city is a marketing name: Brooks at Creekside says Antioch, its parcels say Oakley.
    oak = [P(f"30030000{i}", f"{i} A ST, OAKLAND", 37.80 + i / 10000, -122.27, city="OAKLAND") for i in range(1, 9)]
    near_oakley = (37.98, -121.75)                                   # ~4.5 km from the tract
    assert find_centre(TRACT + oak, {"A", "B"}, city_centre=near_oakley)[0] == pytest.approx(37.9903, abs=0.0005)
    assert find_centre(oak, {"A", "B"}, city_centre=near_oakley) is None
    assert find_centre(oak, {"A", "B"})[0] == pytest.approx(37.80045, abs=0.0005)   # no city known: densest wins


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


def test_the_committed_report_carries_no_parcel_address_or_price(tmp_path, monkeypatch):
    """Data rights: past sales ship as community aggregates only. The 10-27 report listed APNs, addresses, assessed
    values and transfer prices in eval/reports (committed); they belong in the gitignored hand-check file."""
    import tools.link_parcels as lp
    from tools import db
    monkeypatch.setattr(lp, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(lp, "CHECKS", tmp_path / "checks", raising=False)
    (tmp_path / "reports").mkdir()
    summary = {"n": 6, "median_value": 900_000, "p25": 850_000, "p75": 950_000, "period": "2025", "method": "m"}
    checks = {"communities": [{"community": "Example Grove", "builder": "B", "county": "alameda", "summary": summary,
                               "checks": [{"apn": "900-00-001", "address": "100 EXAMPLE CT", "stored_total": 1_111_111,
                                           "live_total": 1_111_111, "value_ok": True}]}],
              "transfers": [{"apn": "900-00-002", "address": "200 SAMPLE DR", "doc_date": "2024-01-01",
                             "price": 1_250_000, "live_ok": True, "value_ratio": 1.0, "linked": False}]}
    path = lp.report(db.connect(":memory:"), [], checks)
    text = path.read_text()
    for private in ("900-00-001", "100 EXAMPLE CT", "1,111,111", "900-00-002", "200 SAMPLE DR", "1,250,000"):
        assert private not in text
    assert "1/1 sampled parcel values" in text
    kept = (tmp_path / "checks" / path.name.replace("__past_sales", "__past_sales_checks")).read_text()
    assert "100 EXAMPLE CT" in kept and "1,250,000" in kept
