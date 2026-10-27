"""Fetch builders' community pages (raw, for extraction later). Deterministic, no model.

    python -m tools.fetch_builder_pages --builder trumark     # or pulte, taylor_morrison, all

Writes .tmp/raw/<date>/<builder>/*.html plus manifest.json, and upserts `builders` / `communities`
(url, city, county; names and details come from extraction). Honors robots.txt read with our own
User-Agent, waits `delay_s` between requests to a host, never retries past a block.
"""
import argparse
import hashlib
import json
import logging
import re
import time
import urllib.robotparser
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import yaml

from tools import db

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / ".tmp" / "raw"
MIN_FLOOR = 0.6

log = logging.getLogger(__name__)


def load_config() -> tuple[dict, dict[str, str]]:
    cfg = yaml.safe_load((ROOT / "config" / "builders.yaml").read_text())
    cities = yaml.safe_load((ROOT / "config" / "cities.yaml").read_text())
    city_county = {slug: county for county, slugs in cities.items() for slug in slugs}
    return cfg, city_county


# ---------- pure helpers (unit-tested) ----------

def robots_from_response(status: int, text: str, robots_url: str) -> urllib.robotparser.RobotFileParser:
    """Parse robots.txt we fetched ourselves. 4xx = no robots file = allow all (the usual crawler
    convention); 5xx / network trouble = disallow all. Python's own reader treats 401/403 as
    disallow-all, which wrongly blocked a site that only 403s the default Python User-Agent."""
    rp = urllib.robotparser.RobotFileParser(robots_url)
    if status == 200:
        rp.parse(text.splitlines())
    elif 400 <= status < 500:
        rp.allow_all = True
    else:
        rp.disallow_all = True
    return rp


PAGE_CITY = r'"addressLocality"\s*:\s*"(?P<city>[^"]+)"'


def city_slug(name: str | None) -> str | None:
    """'Mountain House' / 'Mountain-House' -> 'mountain-house' (the form config/cities.yaml uses)."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") if name else None


def find_paths(text: str, pattern: str) -> list[dict]:
    """Unique regex matches (named groups) in page text, first-seen order. The URL path is the
    `path` group when the pattern has one (e.g. a JSON-LD record), else the whole match."""
    seen, out = set(), []
    for m in re.finditer(pattern, text):
        g = m.groupdict()
        path = g.get("path") or m.group(0)
        if path not in seen:
            seen.add(path)
            out.append({**g, "path": path, "city": city_slug(g.get("city"))})
    return out


def page_city(text: str, pattern: str = PAGE_CITY) -> str | None:
    """City of a community page, from its own address (for sites whose URLs don't name the city)."""
    m = re.search(pattern, text)
    return city_slug(m.group("city")) if m else None


def in_scope(matches: list[dict], city_county: dict[str, str]) -> list[dict]:
    return [{**m, "county": city_county[m["city"]]} for m in matches if m.get("city") in city_county]


def page_filename(url: str) -> str:
    path = urlparse(url).path.strip("/").replace("/", "__") or "index"
    return re.sub(r"[^a-z0-9_\-]", "-", path.lower())[:180] + ".html"


def placeholder_name(slug: str) -> str:
    """'lilac-at-oakwood-trails-211184' -> 'Lilac At Oakwood Trails' (extraction sets the real name)."""
    return re.sub(r"-\d+$", "", slug).replace("-", " ").title()


def floor_ok(found: int, expected: int) -> bool:
    return found >= MIN_FLOOR * expected


# ---------- I/O ----------

class Fetcher:
    def __init__(self, user_agent: str, delay_s: float):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = user_agent
        self.ua, self.delay = user_agent, delay_s
        self.robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.last: dict[str, float] = {}

    def _wait(self, host: str) -> None:
        gap = time.monotonic() - self.last.get(host, 0)
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self.last[host] = time.monotonic()

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        host = f"{p.scheme}://{p.netloc}"
        if host not in self.robots:
            self._wait(host)
            try:
                r = self.s.get(host + "/robots.txt", timeout=30)
                self.robots[host] = robots_from_response(r.status_code, r.text, host + "/robots.txt")
            except requests.RequestException:
                self.robots[host] = robots_from_response(599, "", host + "/robots.txt")
        return self.robots[host].can_fetch(self.ua, url)

    def get(self, url: str) -> tuple[int, str]:
        if not self.allowed(url):
            return -1, ""  # robots.txt says no
        p = urlparse(url)
        self._wait(p.netloc)
        try:
            r = self.s.get(url, timeout=45)
            return r.status_code, r.text
        except requests.RequestException as e:
            log.warning("fetch failed %s: %s", url, e)
            return 0, ""


def fetch_builder(b: dict, cfg: dict, city_county: dict, conn=None, out_root: Path = RAW) -> dict:
    f = Fetcher(cfg["user_agent"], cfg["delay_s"])
    out_dir = out_root / date.today().isoformat() / b["key"]
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []

    def save(url: str, kind: str, meta: dict | None = None) -> str:
        status, text = f.get(url)
        rec = {"url": url, "kind": kind, "status": status, "bytes": len(text),
               "sha256": hashlib.sha256(text.encode()).hexdigest() if text else None,
               "file": None, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               **(meta or {})}
        if status == 200 and text:
            rec["file"] = page_filename(url)
            (out_dir / rec["file"]).write_text(text)
        manifest.append(rec)
        log.info("%s %s %s (%s bytes)", status, kind, url, len(text))
        return text if status == 200 else ""

    pages = [save(u, "start") for u in b["start"]]
    if b.get("city_pattern"):
        # middle level: city pages (Pulte) or master-plan pages (Lennar), keyed by path since a city can hold several
        mids = {m["path"]: m for t in pages for m in in_scope(find_paths(t, b["city_pattern"]), city_county)}
        pages = [save(urljoin(b["site"], path), "city", {"city": m["city"]}) for path, m in sorted(mids.items())]
    communities, unplaced = {}, {}
    for t in pages:
        found = find_paths(t, b["community_pattern"])
        for m in found:
            url = urljoin(b["site"], m["path"]).rstrip("/") + ("/" if m["path"].endswith("/") else "")
            if m.get("city") in city_county:
                communities.setdefault(url, {**m, "county": city_county[m["city"]]})
            elif b.get("city_from_page") and not m.get("city"):
                unplaced.setdefault(url, m)
    for url, m in communities.items():
        save(url, "community", {"city": m["city"], "county": m["county"], "slug": m["slug"]})
    for url, m in unplaced.items():  # the URL doesn't name the city: read it from the page, keep only in-scope
        if url in communities:
            continue
        text = save(url, "community", {"slug": m["slug"]})
        rec, city = manifest[-1], page_city(text, b.get("page_city_pattern", PAGE_CITY))
        rec["city"] = city
        if city in city_county:
            rec["county"] = city_county[city]
            communities[url] = rec
        elif rec["file"]:
            (out_dir / rec["file"]).unlink()
            rec.update(kind="out_of_scope", file=None)
    for u in b.get("extra_pages", []):
        save(u, "extra")

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    got = [r for r in manifest if r["kind"] == "community" and r["status"] == 200]
    result = {"builder": b["key"], "communities_found": len(communities), "communities_fetched": len(got),
              "expected": b["expected_communities"], "blocked_by_robots": sum(r["status"] == -1 for r in manifest),
              "errors": sum(r["status"] not in (200, -1) for r in manifest), "dir": str(out_dir)}
    result["floor_ok"] = floor_ok(len(got), b["expected_communities"])

    conn = conn or db.connect()
    counties = {c["key"]: c["fips"] for c in yaml.safe_load((ROOT / "config" / "counties.yaml").read_text())["counties"]}
    with conn:
        conn.execute("INSERT INTO builders (name, site, discovery_method) VALUES (?, ?, ?)"
                     " ON CONFLICT(name) DO UPDATE SET site=excluded.site, discovery_method=excluded.discovery_method",
                     (b["name"], b["site"], "city_pages" if b.get("city_pattern") else "region_page"))
        bid = conn.execute("SELECT id FROM builders WHERE name=?", (b["name"],)).fetchone()[0]
        for r in got:
            conn.execute("INSERT INTO communities (builder_id, name, city, county_fips, url, fetched_at, page_hash)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(url) DO UPDATE SET"
                         " name=excluded.name, fetched_at=excluded.fetched_at, page_hash=excluded.page_hash",
                         (bid, placeholder_name(r["slug"]), r["city"].replace("-", " ").title(),
                          counties[r["county"]], r["url"], r["fetched_at"], r["sha256"]))
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--builder", required=True)
    args = ap.parse_args()
    cfg, city_county = load_config()
    todo = ([b for b in cfg["builders"] if b.get("enabled", True)] if args.builder == "all"
            else [b for b in cfg["builders"] if b["key"] == args.builder])
    if not todo:
        raise SystemExit(f"unknown builder {args.builder}")
    failed = False
    for b in todo:
        res = fetch_builder(b, cfg, city_county)
        print(json.dumps(res))
        failed |= not res["floor_ok"]
    raise SystemExit(1 if failed else 0)
