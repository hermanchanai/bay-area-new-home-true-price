"""Each floor plan's own page on the builder's site, read from the saved community page. No model, $0.

    python -m tools.plan_links                          # per-builder coverage and samples, read-only
    python -m tools.plan_links --raw <dir> --db <path>  # against another checkout's data

A wrong link is worse than none. A URL is a plan page only if it sits under the community page's own path on
the same site, with no query or fragment, is not a file, and is not a home, lot or sub-page. It belongs to a
plan on one of three pieces of evidence:
  slug   its last path segment spells the plan name (anchors and embedded JSON alike), allowing a numeric id
         on the end, a plans/ directory, a collection directory, and the community's words at either end;
  label  a link's text, first line or aria-label is the plan name, and the segment says nothing else (generic
         words, ids); a different plan number in the segment means the URL is trusted for no plan at all;
  series a link's text is part of the plan name and the rest is in that link's card ("Plan 1", "Series: Oasis").
A plan fitting two URLs gets neither and claims neither; a URL that is the only fit for two plans goes to
neither. URLs are never built from a slug field: a page that does not print a URL gets no link.
"""
import argparse
import html as htmllib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent

NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
                "eight": "8", "nine": "9", "ten": "10"}
PLAN_DIRS = {"plan", "plans", "floorplan", "floorplans", "floor-plan", "floor-plans", "models"}
NOT_PLAN_DIRS = {"assets", "available-homes", "brochure", "brochures", "data", "docs", "documents", "downloads",
                 "event", "events", "gallery", "globalassets", "home", "homes", "homesite", "homesites", "images",
                 "inventory", "lot", "lots", "media", "mir", "move-in-ready", "news", "offers", "projects",
                 "promotions", "qmi", "quick-move-in", "quick-move-ins", "tour", "tours", "video", "videos",
                 "virtual-tour", "virtual-tours"}
GENERIC = {"plan", "residence", "model", "the"}  # words a slug may use for "this plan" without naming it
NOT_IN_SLUG = {"modeled"}  # KB's "Plan 1610 Modeled" lives at /plan-1610
PLAN_ID = re.compile(r"^\d{3,}$")
SHORT_NUMBER = re.compile(r"^\d{1,2}[a-z]{0,2}$")
FILE_EXT = re.compile(r"\.[a-z0-9]{2,5}$", re.I)


def _tokens(text: str) -> list[str]:
    text = re.sub(r"['’]", "", htmllib.unescape(text).lower())
    return [NUMBER_WORDS.get(t, t) for t in re.findall(r"[a-z0-9]+", text)]


@dataclass(frozen=True)
class Page:
    host: str
    path: str  # no trailing slash, as printed
    community: tuple[str, ...]

    @classmethod
    def of(cls, url: str) -> "Page":
        parts = urlsplit(url)
        path = parts.path.rstrip("/")
        return cls(_bare(parts.netloc), path, tuple(_tokens(unquote(path.rsplit("/", 1)[-1]))))


def _bare(host: str) -> str:
    return host.lower().removeprefix("www.")


def _tail(url: str, page: Page) -> list[str] | None:
    """Path segments under the community page; None if the URL is off-site, elsewhere, or that page itself."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or _bare(parts.netloc) != page.host:
        return None
    path = parts.path
    if not path.lower().startswith(page.path.lower() + "/"):
        return None
    return [unquote(s) for s in path[len(page.path):].split("/") if s] or None


@dataclass(frozen=True)
class PlanPath:
    mids: tuple[str, ...]  # collection directory, if any
    whole: tuple[str, ...]  # last segment's words
    last: tuple[str, ...]  # the same with the community's own words trimmed from either end


def _plan_path(url: str, page: Page) -> PlanPath | None:
    """The shape of a plan page, or None for a query, fragment, file, home, lot or sub-page."""
    tail = _tail(url, page)
    if not tail or "?" in url or "#" in url or FILE_EXT.search(tail[-1]):
        return None
    last = _tokens(tail[-1])
    if not last or all(t.isdigit() for t in last):  # a bare number is a home id
        return None
    mids = [m.lower() for m in tail[:-1]]
    if any(m in NOT_PLAN_DIRS or any(t[0].isdigit() for t in _tokens(m)) for m in mids):  # plan-1/, 1436-oak-st/
        return None
    mids = [m for m in mids if m not in PLAN_DIRS]
    if len(mids) > 1:
        return None
    start, end = 0, len(last)
    while start < end and last[start] in page.community:
        start += 1
    while end > start and last[end - 1] in page.community:
        end -= 1
    return PlanPath(tuple(mids), tuple(last), tuple(last[start:end]) or tuple(last))


def _spells(name: list[str], p: PlanPath) -> bool:
    """The slug says exactly the plan name, with an optional numeric id on the end and collection above."""
    bodies = []
    for words in {p.whole, p.last}:
        bodies.append(list(words))
        if len(words) > 1 and PLAN_ID.match(words[-1]):
            bodies.append(list(words[:-1]))
    mids = [t for m in p.mids for t in _tokens(m)]
    return any(b == name or (mids and sorted(mids + b) == sorted(name)) for b in bodies)


def _agrees(name: list[str], p: PlanPath) -> bool | None:
    """Can a link labelled `name` go to this path? True yes; False no; None: its plan number contradicts the
    label, so the link is wrong or mislabelled and the URL is trusted for nothing."""
    left = [t for t in p.last if t not in name]
    if any(t not in GENERIC and not t[0].isdigit() for t in left):
        return False
    if not any(t in name for t in p.last) and not any(PLAN_ID.match(t) for t in p.last):
        return False
    theirs = {t for t in p.last if SHORT_NUMBER.match(t)}
    ours = {t for t in name if SHORT_NUMBER.match(t)}
    return None if ours and theirs and not theirs <= ours else True


def _key(url: str) -> str:
    return urlsplit(url).path.rstrip("/").lower()


def _printed_urls(html: str, page: Page) -> list[str]:
    """URLs under the community's path printed anywhere: JSON, escaped JSON, data attributes."""
    text = htmllib.unescape(html).replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/")
    pattern = (r"(?<![\w./?=&%:-])(?:(?:https?:)?//[a-z0-9.-]+)?" + re.escape(page.path)
               + r"/[^\"'<>\s\\(),;]*")
    return [m.group(0) for m in re.finditer(pattern, text, re.I)]


def _card_words(anchor, key: str, page: Page, base: str) -> set[str]:
    """Words of the largest block around the link that links to no other page under the community."""
    node, words = anchor, set(_tokens(anchor.get_text(" ")))
    while (parent := node.parent) is not None and parent.name:
        for a in parent.find_all("a", href=True):
            href = urljoin(base, a["href"])
            other = _key(href)
            if _tail(href, page) and other != key and not other.startswith(key + "/"):
                return words
        node, words = parent, set(_tokens(parent.get_text(" ")))
    return words


def _sublist_rest(part: list[str], whole: list[str]) -> list[str] | None:
    """What's left of `whole` when `part` is its proper start or end."""
    if not part or len(part) >= len(whole):
        return None
    if whole[-len(part):] == part:
        return whole[:-len(part)]
    return whole[len(part):] if whole[:len(part)] == part else None


def _forms(name: str) -> list[list[str]]:
    """The name's words, and the same without the words a builder leaves out of its slugs."""
    toks = _tokens(name)
    short = [t for t in toks if t not in NOT_IN_SLUG]
    return [toks, short] if short and short != toks else [toks]


def _claims(html: str, page_url: str, plan_names: list[str]) -> tuple[dict[str, dict[str, str]], set[str]]:
    """({name: {url key: url}} for every URL with evidence for that name, {url keys trusted for no plan})."""
    page = Page.of(page_url)
    names = {n: _forms(n) for n in plan_names if _tokens(n)}
    claims: dict[str, dict[str, str]] = defaultdict(dict)  # name -> url key -> url
    distrusted: set[str] = set()

    def claim(name: str, url: str) -> None:
        claims[name].setdefault(_key(url), url)

    anchors = []
    for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
        url = urljoin(page_url, a["href"].strip())
        p = _plan_path(url, page)
        if p:
            anchors.append((a, url, p))
    for url in [u for _, u, _ in anchors] + [urljoin(page_url, u) for u in _printed_urls(html, page)]:
        if p := _plan_path(url, page):
            for name, forms in names.items():
                if any(_spells(f, p) for f in forms):
                    claim(name, url)
    for a, url, p in anchors:
        labels = [_tokens(t) for t in (a.get_text(" "), next(a.stripped_strings, ""), a.get("aria-label") or "",
                                       a.get("title") or "")]
        for name, forms in names.items():
            for label in labels:
                if label in forms:
                    agrees = _agrees(label, p)
                    if agrees is None:
                        distrusted.add(_key(url))
                    elif agrees:
                        claim(name, url)
                elif (rest := _sublist_rest(label, forms[0])) and (_spells(label, p) or _agrees(label, p)):
                    if set(rest) <= _card_words(a, _key(url), page, page_url):
                        claim(name, url)
    return claims, distrusted


def plan_links(html: str, page_url: str, plan_names: list[str]) -> dict[str, str]:
    """{plan name: its own page} for the plans that have a certain one; everything else is left out."""
    if not plan_names or not html:
        return {}
    claims, distrusted = _claims(html, page_url, plan_names)
    sole = {name: next(iter(urls.items())) for name, urls in claims.items() if len(urls) == 1}
    owners = Counter(key for key, _ in sole.values())
    return {name: url for name, (key, url) in sole.items() if owners[key] == 1 and key not in distrusted}


# ---------- measure on saved pages ----------

def path_matches(name: str, url: str, page_url: str) -> bool:
    """Sanity check on a returned link: its path carries the plan's name, number or a plan id."""
    tail = _tail(url, Page.of(page_url)) or []
    words = {t for seg in tail for t in _tokens(seg)}
    toks = set(_tokens(name))
    specific = toks - GENERIC
    return bool(specific & words) or any(PLAN_ID.match(t) for t in words)


def saved_pages(raw: Path) -> dict[str, tuple[str, Path]]:
    """url -> (builder dir, file), the latest fetch of each page winning."""
    out = {}
    for day in sorted(d for d in raw.iterdir() if d.is_dir()):
        for man in day.glob("*/manifest.json"):
            for r in json.loads(man.read_text()):
                if r.get("file"):
                    out[r["url"]] = (man.parent.name, man.parent / r["file"])
    return out


def apply(conn, pages: dict[str, tuple[str, Path]]) -> int:
    """Save each plan's link from its community page; a link no longer printed is cleared. Returns plans linked."""
    linked = 0
    with conn:
        for c in conn.execute("SELECT id, url FROM communities").fetchall():
            if c[1] not in pages:
                continue
            plans = [r[0] for r in conn.execute("SELECT name FROM plans WHERE community_id = ?", (c[0],))]
            links = plan_links(pages[c[1]][1].read_text(errors="ignore"), c[1], plans) if plans else {}
            for name in plans:
                conn.execute("UPDATE plans SET url = ? WHERE community_id = ? AND name = ?", (links.get(name), c[0], name))
            linked += len(links)
    return linked


def measure(raw: Path, db: Path) -> dict[str, dict]:
    pages = saved_pages(raw)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    plans = defaultdict(list)
    for url, name in conn.execute("SELECT c.url, p.name FROM plans p JOIN communities c ON c.id = p.community_id "
                                  "ORDER BY c.url, p.id"):
        plans[url].append(name)
    report = defaultdict(lambda: {"plans": 0, "linked": 0, "no_page": 0, "off_pattern": [], "samples": [],
                                  "missed": []})
    for url, names in plans.items():
        if url not in pages:
            row = report[urlsplit(url).netloc]
            row["plans"] += len(names)
            row["no_page"] += len(names)
            continue
        builder, path = pages[url]
        links = plan_links(path.read_text(errors="ignore"), url, names)
        row = report[builder]
        row["plans"] += len(names)
        row["linked"] += len(links)
        row["missed"] += [(url, n) for n in names if n not in links]
        for name, link in links.items():
            row["samples"].append((name, link))
            if not path_matches(name, link, url):
                row["off_pattern"].append((name, link))
    return dict(report)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--raw", type=Path, default=ROOT / ".tmp" / "raw")
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "trueprice.db")
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--missed", action="store_true", help="also list every plan left without a link")
    args = ap.parse_args()
    report = measure(args.raw, args.db)
    print(f"{'builder':<18}{'plans':>7}{'linked':>8}{'share':>7}{'off-pattern':>13}")
    for builder, r in sorted(report.items()):
        print(f"{builder:<18}{r['plans']:>7}{r['linked']:>8}{r['linked'] / r['plans']:>7.0%}{len(r['off_pattern']):>13}")
    total, linked = sum(r["plans"] for r in report.values()), sum(r["linked"] for r in report.values())
    print(f"{'TOTAL':<18}{total:>7}{linked:>8}{linked / total:>7.0%}")
    for builder, r in sorted(report.items()):
        print(f"\n{builder}")
        step = max(1, len(r["samples"]) // args.samples) if args.samples else 1
        for name, url in r["samples"][::step][:args.samples]:
            print(f"  {name}: {url}")
        for name, url in r["off_pattern"]:
            print(f"  OFF-PATTERN {name}: {url}")
        if args.missed:
            for url, name in r["missed"]:
                print(f"  missed {name}: {url}")


if __name__ == "__main__":
    main()
