"""Map pins for communities link_parcels couldn't place (new streets not on the assessor roll yet).

    python -m tools.geocode_communities

Order: the builder's own map pin on the community page (pins shared by 3+ of a builder's pages are its
office, dropped by link_parcels.local_pins), when those pins agree within 1.5 km; else OpenStreetMap
Nominatim's centre of the community's streets (street name + city, no house number, which new homes
rarely have in OSM yet), when they agree. Otherwise the community stays off the map and is listed.
Census's geocoder was tried first and knew 1 of 10 new addresses. Nominatim policy: 1 request/s, a
real User-Agent, small one-off jobs only.
"""
import logging
import re
import statistics
import time

import requests

from tools import db
from tools.link_parcels import distance_m, page_pins

NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = {"User-Agent": "bay-area-new-home-true-price/0.1 (hackathon build; github.com/hermanchanai/bay-area-new-home-true-price)"}
AGREE_KM = 1.5
BAY = (36.9, 38.4, -122.7, -120.8)       # lat min/max, lng min/max: the four counties with room to spare
PAUSE_S = 1.2
log = logging.getLogger(__name__)


def street_of(address: str | None) -> str | None:
    """'400 Mexico Terrace, Unit 9' -> 'Mexico Terrace'."""
    m = re.match(r"\s*\d+[A-Z]?\s+(.+)", (address or "").split(",")[0], re.IGNORECASE)
    if not m:
        return None
    return re.sub(r"\s*(#|\bUnit\b|\bApt\b|\bSte\b).*$", "", m.group(1), flags=re.IGNORECASE).strip() or None


def cluster_centre(points) -> tuple[float, float] | None:
    """Median point, when every point is within AGREE_KM of it and inside the Bay Area."""
    pts = [p for p in points if BAY[0] <= p[0] <= BAY[1] and BAY[2] <= p[1] <= BAY[3]]
    if not pts:
        return None
    c = (round(statistics.median(p[0] for p in pts), 5), round(statistics.median(p[1] for p in pts), 5))
    return c if all(distance_m(c, p) <= AGREE_KM * 1000 for p in pts) else None


def pick(pins: set, osm_points) -> tuple[tuple[float, float] | None, str | None]:
    """osm_points: a callable, so OSM is only asked when the page has no usable pin."""
    if c := cluster_centre(pins):
        return c, "page_pin"
    if c := cluster_centre(osm_points()):
        return c, "osm_street"
    return None, None


def osm_street(street: str, city: str) -> tuple[float, float] | None:
    time.sleep(PAUSE_S)
    r = requests.get(NOMINATIM, params={"street": street, "city": city, "state": "California", "format": "json",
                                        "limit": 1, "countrycodes": "us"}, headers=UA, timeout=30)
    r.raise_for_status()
    hits = r.json()
    return (float(hits[0]["lat"]), float(hits[0]["lon"])) if hits else None


def run(conn) -> dict:
    pins = page_pins()
    todo = conn.execute("SELECT id, name, city, url FROM communities c WHERE lat IS NULL AND EXISTS "
                        "(SELECT 1 FROM homes h WHERE h.community_id = c.id) ORDER BY id").fetchall()
    placed, missed = {}, []
    for c in todo:
        city = re.sub(r",\s*CA$", "", c["city"] or "")
        streets = list(dict.fromkeys(s for (a,) in conn.execute(
            "SELECT address FROM homes WHERE community_id = ? AND address IS NOT NULL ORDER BY id", (c["id"],))
            if (s := street_of(a))))[:3]
        centre, method = pick(pins.get(c["url"], set()),
                              lambda streets=streets, city=city: [p for s in streets if (p := osm_street(s, city))])
        if centre:
            placed[c["id"]] = (centre, method)
            log.info("%-32s %-14s %s %s", c["name"], city, method, centre)
        else:
            missed.append(f"{c['name']} ({city})")
            log.info("%-32s %-14s no location", c["name"], city)
    with conn:
        for cid, ((lat, lng), method) in placed.items():
            conn.execute("UPDATE communities SET lat=?, lng=?, geo_method=? WHERE id=?", (lat, lng, method, cid))
    return {"placed": len(placed), "missed": missed}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out = run(db.connect())
    print(f"placed {out['placed']}; no location for {len(out['missed'])}: {', '.join(out['missed'])}")
