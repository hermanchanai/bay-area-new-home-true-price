"""geocode_communities: a map pin for communities whose parcels aren't on the assessor roll yet."""
from tools.geocode_communities import cluster_centre, pick, street_of


def test_street_of_drops_the_house_number_and_unit():
    assert street_of("571 Ames Alley") == "Ames Alley"
    assert street_of("400 Mexico Terrace, Unit 9") == "Mexico Terrace"
    assert street_of("3664 Mt. Diablo Blvd. #E111") == "Mt. Diablo Blvd."
    assert street_of("Lot 12") is None
    assert street_of(None) is None


def test_cluster_centre_is_the_median_of_points_that_agree():
    pts = [(37.448, -121.920), (37.447, -121.919), (37.449, -121.921)]
    assert cluster_centre(pts) == (37.448, -121.92)
    assert cluster_centre([(37.38, -122.08), (37.40, -122.11)]) is None      # 3.5 km apart: which one?
    assert cluster_centre([]) is None
    assert cluster_centre([(50.0, -1.0)]) is None                               # outside the Bay Area


def test_pick_prefers_the_builders_own_map_pin_then_osm_streets():
    pin = {(37.7087, -121.8637)}
    assert pick(pin, lambda: [(37.0, -121.0)]) == ((37.7087, -121.8637), "page_pin")
    assert pick(set(), lambda: [(37.713, -121.870)]) == ((37.713, -121.87), "osm_street")
    spread = {(37.38, -122.08), (37.40, -122.11)}
    assert pick(spread, lambda: [(37.39, -122.09)]) == ((37.39, -122.09), "osm_street")
    assert pick(set(), list) == (None, None)


def test_every_new_county_is_inside_the_map_bounds():
    from tools.geocode_communities import cluster_centre
    for name, point in {"Santa Rosa": (38.44, -122.71), "Cloverdale": (38.81, -123.02), "Calistoga": (38.58, -122.58),
                        "San Bruno": (37.63, -122.41), "Half Moon Bay": (37.46, -122.43), "Dixon": (38.45, -121.82)}.items():
        assert cluster_centre({point}) == point, name
