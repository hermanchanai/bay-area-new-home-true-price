"""Builder page HTML -> compact text for extraction. No model, $0.

    python -m tools.preprocess_page                     # latest fetch date, all builders
    python -m tools.preprocess_page --builder pulte

Keeps embedded data verbatim in meaning (JSON-LD, __NEXT_DATA__, application/json, Taylor
Morrison's data-fed-ref templates), compacted: image URLs, icons, CSS and empty values dropped,
HTML inside strings flattened to text. Visible HTML becomes markdown-ish text with nav, header,
forms, buttons and scripts dropped; footers stay, because incentive fine print lives there.
Writes .tmp/pre/<date>/<builder>/<page>.md.
"""
import argparse
import html as htmllib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / ".tmp" / "raw"
PRE = ROOT / ".tmp" / "pre"
CHARS_PER_TOKEN = yaml.safe_load((ROOT / "config" / "llm.yaml").read_text())["chars_per_token"]
MAX_CHUNK_CHARS = int(24_000 * CHARS_PER_TOKEN)

DROP_TAGS = ["script", "style", "noscript", "svg", "template", "iframe", "nav", "header", "form",
             "select", "button", "input", "textarea", "canvas", "img", "picture", "video"]
BLOCK_TAGS = ["p", "div", "section", "article", "aside", "main", "tr", "br", "ul", "ol", "table",
              "dl", "dt", "dd", "figcaption", "footer", "address", "blockquote"]
NOISE_KEY = re.compile(r"(?i)^(icon|icons|logo|class|classname|css|style|alt|analytics|tracking|gaevent)$"
                       r"|.*color$")
# Image hosts often serve extensionless URLs; under these keys a bare URL is an image.
IMAGE_KEY = re.compile(r"(?i)(src|image|img|photo|thumbnail)$")
IMAGE_URL = re.compile(r"(?i)\.(jpe?g|png|webp|gif|svg|avif|ashx)(\?|#|$)")
HTML_IN_STRING = re.compile(r"<[a-zA-Z/!]")

log = logging.getLogger(__name__)


@dataclass
class Doc:
    text: str
    chunks: list[str] = field(default_factory=list)

    @property
    def est_tokens(self) -> int:
        return int(len(self.text) / CHARS_PER_TOKEN)


def _clean(value, key: str = ""):
    """Drop noise from parsed JSON; returns None for anything not worth keeping."""
    if isinstance(value, dict):
        out = {k: v for k, v in ((k, _clean(v, k)) for k, v in value.items() if not NOISE_KEY.match(k))
               if v is not None}
        return out or None
    if isinstance(value, list):
        out = [v for v in (_clean(v, key) for v in value) if v is not None]
        return out or None
    if isinstance(value, str):
        s = htmllib.unescape(value).strip()
        if not s or s.lower().startswith(("<style", "<svg", "<script")):
            return None
        is_image = IMAGE_URL.search(s) or (IMAGE_KEY.search(key) and re.match(r"https?://", s))
        if is_image and " " not in s and not re.search(r"(?i)floor|plan", key):
            return None
        if HTML_IN_STRING.search(s):
            s = re.sub(r"\s+", " ", BeautifulSoup(s, "lxml").get_text(" ")).strip()
            s = re.sub(r" ([.,;:%)])", r"\1", s)
        return s or None
    return value


def compact_json(obj) -> str:
    cleaned = _clean(obj)
    return json.dumps(cleaned, separators=(",", ":"), ensure_ascii=False) if cleaned is not None else ""


JS_ASSIGN = re.compile(r"(?:\bvar|\blet|\bconst|\bwindow\.)\s*([A-Za-z_$][\w$]*)\s*=\s*(?=[\[{])")
JS_NOISE = re.compile(r"(?i)(datalayer|utag|analytics|gtm|tracking|settings|legal|config|defaults|validators|keys)")
JS_MIN_CHARS = 200


def _js_blobs(code: str) -> list[tuple[str, object]]:
    """JSON literals assigned to variables in plain scripts (KB: `var LocalQMIs = [...]`)."""
    out, dec = [], json.JSONDecoder()
    for m in JS_ASSIGN.finditer(code):
        if JS_NOISE.search(m.group(1)):
            continue
        try:
            obj, end = dec.raw_decode(code, m.end())
        except json.JSONDecodeError:
            continue
        if end - m.end() >= JS_MIN_CHARS:
            out.append((f"js:{m.group(1)}", obj))
    return out


def _blobs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    seen, out = set(), []
    for s in soup.find_all("script"):
        typ = (s.get("type") or "").lower()
        if typ in ("", "text/javascript", "application/javascript") and not s.get("src"):
            for label, obj in _js_blobs(s.string or s.get_text() or ""):
                text = compact_json(obj)
                if text and text not in seen:
                    seen.add(text)
                    out.append((label, text))
            continue
        if s.get("id") == "__NEXT_DATA__":
            label = "__NEXT_DATA__"
        elif s.has_attr("data-fed-ref"):
            label = "data-fed-ref"
        elif typ in ("application/ld+json", "application/json"):
            label = typ.split("/")[1]
        else:
            continue
        raw = s.string or s.get_text() or ""
        try:
            text = compact_json(json.loads(raw))
        except json.JSONDecodeError:
            log.debug("unparseable %s blob (%d chars) skipped", label, len(raw))
            continue
        if text and text not in seen:
            seen.add(text)
            out.append((label, text))
    return out


def _visible_text(soup: BeautifulSoup) -> str:
    for t in soup(DROP_TAGS):
        t.decompose()
    for h in soup.find_all(re.compile(r"^h[1-6]$")):
        h.insert_before("\n" + "#" * int(h.name[1]) + " ")
        h.insert_after("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
        li.insert_after("\n")
    for t in soup.find_all(["td", "th"]):
        t.insert_after(" | ")
    for t in soup.find_all(BLOCK_TAGS):
        t.insert_before("\n")
        t.insert_after("\n")
    lines, prev = [], None
    for line in soup.get_text().splitlines():
        line = re.sub(r"[ \t\xa0​]+", " ", line).strip()
        if line in ("", "-", "|") or line == prev or re.fullmatch(r"#+", line):
            continue
        lines.append(line)
        prev = line
    return "\n".join(lines)


def chunk(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split at lines starting with '#', packing whole sections up to max_chars each."""
    pieces = []
    for sec in re.split(r"\n(?=#)", text):
        if len(sec) <= max_chars:
            pieces.append(sec)
            continue
        buf = ""
        for line in sec.split("\n"):
            while len(line) > max_chars:  # one enormous line (a JSON blob): cut it
                if buf:
                    pieces.append(buf)
                    buf = ""
                pieces.append(line[:max_chars])
                line = line[max_chars:]
            if buf and len(buf) + 1 + len(line) > max_chars:
                pieces.append(buf)
                buf = line
            else:
                buf = f"{buf}\n{line}" if buf else line
        if buf:
            pieces.append(buf)
    chunks, cur = [], ""
    for p in pieces:
        if cur and len(cur) + 1 + len(p) > max_chars:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def preprocess(html: str, url: str, max_chars: int = MAX_CHUNK_CHARS) -> Doc:
    soup = BeautifulSoup(html, "lxml")
    parts = [f"# SOURCE {url}"]
    parts += [f"## EMBEDDED DATA: {label}\n{text}" for label, text in _blobs(soup)]
    parts.append("## PAGE TEXT\n" + _visible_text(soup))
    text = "\n\n".join(parts)
    return Doc(text, chunk(text, max_chars))


def latest_date() -> str:
    return sorted(p.name for p in RAW.iterdir() if p.is_dir())[-1]


def run(date: str, builders: list[str] | None = None) -> list[dict]:
    stats = []
    for bdir in sorted((RAW / date).iterdir()):
        if not bdir.is_dir() or (builders and bdir.name not in builders):
            continue
        manifest = json.loads((bdir / "manifest.json").read_text())
        out_dir = PRE / date / bdir.name
        out_dir.mkdir(parents=True, exist_ok=True)
        for rec in manifest:
            if rec.get("status") != 200 or not rec.get("file"):  # out_of_scope pages are deleted
                continue
            html = (bdir / rec["file"]).read_text(errors="replace")
            doc = preprocess(html, rec["url"])
            (out_dir / rec["file"].replace(".html", ".md")).write_text(doc.text)
            stats.append({"builder": bdir.name, "file": rec["file"], "kind": rec["kind"],
                          "html_chars": len(html), "chars": len(doc.text), "est_tokens": doc.est_tokens,
                          "chunks": len(doc.chunks)})
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--builder", action="append")
    a = ap.parse_args()
    date = a.date or latest_date()
    for s in run(date, a.builder):
        print(f"{s['builder']:16} {s['kind']:9} {s['html_chars']:>9,} -> {s['chars']:>7,} chars "
              f"~{s['est_tokens']:>6,} tok {s['chunks']} chunk(s)  {s['file']}")
