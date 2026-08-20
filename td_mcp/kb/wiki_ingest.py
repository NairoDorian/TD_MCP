"""Structured ingestion of a local TouchDesigner wiki mirror into the KB.

TouchDesigner ships a complete offline MediaWiki-rendered mirror of
``docs.derivative.ca`` (the *OfflineHelp* tree), and users often keep their own
saved dump. Each page is rich, regular HTML: an ``RLCONF`` script block with
machine-readable page metadata (``wgPageName``, ``wgTitle``, ``wgCategories``),
a meta description, a canonical ``og:url`` for citations, and ``mw-headline``
anchors on every section.

This module parses that structure properly (unlike a strip-all-tags pass):

* one chunk **per section** with a ``Page § Section`` citation and a
  ``url#anchor`` deep link (wiki-rag pattern, reimplemented from scratch);
* title-prefixed chunk text so a parameter description never loses its
  operator ("Blur TOP — Parameters - Blur Page\\n\\n...");
* family + page-type derived from ``wgCategories`` (``TOPs`` -> TOP operator,
  ``TOP Classes`` -> Python class), falling back to the title suffix;
* internal parameter names harvested from ``<code>`` markup into chunk tags so
  lexical retrieval matches ``"blur top size parameter"``;
* inherited-member sections of ``*_Class`` pages are skipped (every class page
  embeds the full OP/TOP class docs — the canonical pages already cover them);
* ~1800-char chunks split on paragraph boundaries, stubs dropped.

Pure standard library. Deterministic output order (sorted file walk) so the
KB build + integrity receipt stay reproducible.

Used by ``build_kb.py`` when a wiki root is configured (``TD_MCP_WIKI_ROOT``
env var, ``--wiki-root``, or an auto-detected OfflineHelp install path).
"""

from __future__ import annotations

import html
import json
import os
import re
from html.parser import HTMLParser
from pathlib import Path

MAX_CHUNK_CHARS = 1800
MIN_CHUNK_CHARS = 40
MIN_PAGE_CHARS = 80
MAX_PARAM_TAGS = 16

# Wiki category -> (family, category) mapping (wiki-rag idea, own table).
_CATEGORY_FAMILY = {}
for _fam in ("TOP", "CHOP", "SOP", "DAT", "MAT", "COMP", "POP"):
    _CATEGORY_FAMILY[_fam + "s"] = (_fam, "operator")
    _CATEGORY_FAMILY[_fam + " Classes"] = (_fam, "python")

_TITLE_SUFFIX_RE = re.compile(r"\b(TOP|CHOP|SOP|DAT|MAT|COMP|POP)$")

_RLCONF_PAGENAME_RE = re.compile(r'"wgPageName"\s*:\s*"([^"]*)"')
_RLCONF_TITLE_RE = re.compile(r'"wgTitle"\s*:\s*"([^"]*)"')
_RLCONF_CATEGORIES_RE = re.compile(r'"wgCategories"\s*:\s*\[([^\]]*)\]')
_META_DESC_RE = re.compile(
    r'<meta\s+name="description"\s+content="([^"]*)"', re.I)
_OG_URL_RE = re.compile(
    r'<meta\s+property="og:url"\s+content="([^"]*)"', re.I)
_MODIFIED_RE = re.compile(
    r'<meta\s+property="article:modified_time"\s+content="([^"]*)"', re.I)

# Default roots auto-probed when no explicit root is configured. Ordered:
# env override, TD install OfflineHelp mirrors, then a sibling "TD HELP" dump
# next to the repo checkout (common local layout).
def default_roots():
    roots = [os.environ.get("TD_MCP_WIKI_ROOT", "")]
    roots += [
        r"C:\Program Files\Derivative\TouchDesigner\Samples\Learn\OfflineHelp\https.docs.derivative.ca",
        r"C:\Program Files\Derivative\TouchDesigner.099\Samples\Learn\OfflineHelp\https.docs.derivative.ca",
        "/Applications/TouchDesigner.app/Contents/Resources/Samples/Learn/OfflineHelp/https.docs.derivative.ca",
    ]
    here = Path(__file__).resolve()
    for up in (3, 4, 5):
        if len(here.parents) > up:
            roots.append(str(here.parents[up] / "TD HELP"))
    return [r for r in roots if r]


def find_root(explicit=None):
    """First existing wiki root among explicit -> env -> known locations."""
    for root in ([explicit] if explicit else []) + default_roots():
        if root and os.path.isdir(root):
            return root
    return None


class _SectionParser(HTMLParser):
    """Walk the MediaWiki body, splitting text into (heading, anchor) sections.

    Skips subtree content of navigation / chrome elements (TOC, edit links,
    scripts, category footer) and records ``<code>`` runs so parameter names
    can be harvested from operator pages.
    """

    _SKIP_TAGS = {"script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections = []          # [{heading, anchor, level, parts, codes}]
        self._current = self._new_section("(intro)", "", 2)
        self._skip_depth = 0        # >0 while inside a skipped subtree
        self._stack = []            # (tag, skipped) pairs for depth tracking
        self._headline_depth = 0    # span nesting inside a mw-headline
        self._headline_buf = []
        self._headline_anchor = ""
        self._headline_level = 2
        self._in_code = False
        self._code_buf = []
        self._done = False          # set at printfooter/catlinks

    @staticmethod
    def _new_section(heading, anchor, level):
        return {"heading": heading, "anchor": anchor, "level": level,
                "parts": [], "codes": []}

    # -- subtree skipping ---------------------------------------------------
    def _should_skip(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "")
        el_id = (a.get("id") or "")
        if tag in self._SKIP_TAGS:
            return True
        if el_id == "toc" or "toc" == cls.strip():
            return True
        if "mw-editsection" in cls:
            return True
        if "mw-lingo-tooltip" in cls:
            return True
        if el_id in ("catlinks", "footer", "mw-navigation", "siteSub",
                     "jump-to-nav", "mw-head", "mw-panel"):
            return True
        if "printfooter" in cls:
            return True
        if "noprint" in cls:
            return True
        return False

    def handle_starttag(self, tag, attrs):
        if self._done:
            return
        skipped = self._skip_depth > 0 or self._should_skip(tag, attrs)
        if tag not in ("br", "img", "hr", "input", "meta", "link"):
            self._stack.append((tag, skipped))
            if skipped:
                self._skip_depth += 1
        if self._skip_depth > 0:
            return
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag in ("h2", "h3"):
            self._flush_section(level=2 if tag == "h2" else 3)
        elif tag == "span" and "mw-headline" in cls:
            self._headline_depth = 1
            self._headline_anchor = a.get("id") or ""
            self._headline_buf = []
        elif tag == "span" and self._headline_depth:
            self._headline_depth += 1   # nested lingo/format spans
        elif tag == "code":
            self._in_code = True
            self._code_buf = []
        elif tag in ("p", "div", "ul", "ol", "table", "tr", "pre"):
            self._current["parts"].append("\n")
        elif tag == "li":
            self._current["parts"].append("\n- ")
        elif tag in ("td", "th"):
            self._current["parts"].append(" | ")
        elif tag == "br":
            self._current["parts"].append("\n")

    def handle_endtag(self, tag):
        if self._done:
            return
        # unwind our stack to the matching open tag (tolerates bad nesting)
        for i in range(len(self._stack) - 1, -1, -1):
            t, skipped = self._stack[i]
            if t == tag:
                for _, s in self._stack[i:]:
                    if s:
                        self._skip_depth -= 1
                del self._stack[i:]
                break
        if self._skip_depth > 0:
            return
        if tag == "span" and self._headline_depth:
            self._headline_depth -= 1
            if self._headline_depth == 0:
                heading = " ".join("".join(self._headline_buf).split())
                if heading:
                    self._current["heading"] = heading
                    if self._headline_anchor:
                        self._current["anchor"] = self._headline_anchor
        elif tag == "code" and self._in_code:
            self._in_code = False
            code = "".join(self._code_buf).strip()
            if code:
                self._current["codes"].append(code)
                self._current["parts"].append("`" + code + "`")
        elif tag in ("p", "div", "ul", "ol", "table", "pre", "h2", "h3"):
            self._current["parts"].append("\n")

    def handle_data(self, data):
        if self._done or self._skip_depth > 0:
            return
        if self._headline_depth:
            self._headline_buf.append(data)
            return
        if self._in_code:
            self._code_buf.append(data)
            return
        self._current["parts"].append(data)

    def _flush_section(self, level=2):
        self.sections.append(self._current)
        self._current = self._new_section("", "", level)

    def close(self):
        super().close()
        self._flush_section()


def _clean_text(raw):
    """Collapse parser output into readable plain text with line structure."""
    text = html.unescape(raw)
    text = text.replace("\u2295", "").replace("\u229e", "")  # collapse toggles
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t\xa0]+", " ", line).strip()
        lines.append(line)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"(\n- )+\n?", "\n- ", out)  # collapse empty list bullets
    out = re.sub(r" - +- ", " - ", out)       # collapsed-toggle artifact
    return out.strip()


def parse_page(html_text, filename=""):
    """Parse one MediaWiki-rendered page into structured metadata + sections.

    Returns ``None`` for pages without usable content (redirect stubs, chrome
    pages). Sections after the first inherited ``*_Class`` heading on Python
    class pages are dropped.
    """
    m = _RLCONF_PAGENAME_RE.search(html_text)
    page_name = m.group(1) if m else Path(filename).stem
    m = _RLCONF_TITLE_RE.search(html_text)
    title = html.unescape(m.group(1)) if m else page_name.replace("_", " ")
    m = _RLCONF_CATEGORIES_RE.search(html_text)
    categories = []
    if m:
        categories = [c.strip().strip('"') for c in m.group(1).split(",") if c.strip().strip('"')]
    m = _META_DESC_RE.search(html_text)
    description = html.unescape(m.group(1)).strip() if m else ""
    m = _OG_URL_RE.search(html_text)
    url = m.group(1) if m else "https://docs.derivative.ca/" + page_name
    m = _MODIFIED_RE.search(html_text)
    modified = m.group(1) if m else None

    family, category = _classify(title, page_name, categories)

    # Body region: MediaWiki always wraps content in mw-parser-output inside
    # #mw-content-text; feeding from there skips head/nav chrome entirely.
    # Back up to the tag's own '<' so the div's attributes are parsed as
    # markup, not leaked into the first section's text.
    start = html_text.find('id="mw-content-text"')
    if start >= 0:
        start = html_text.rfind("<", 0, start)
    body = html_text[start:] if start >= 0 else html_text
    parser = _SectionParser()
    try:
        parser.feed(body)
        parser.close()
    except Exception:  # noqa: BLE001 - tolerate any malformed page
        return None

    sections = []
    for sec in parser.sections:
        heading = sec["heading"] or "(intro)"
        # Class pages embed every inherited parent class (OP Class, TOP Class
        # ...) after their own Members/Methods — skip those duplicated blocks;
        # the canonical OP_Class / TOP_Class pages already cover them.
        if category == "python" and re.match(r"^\w+ ?Class$", heading) \
                and _slug(heading) != _slug(title):
            break
        text = _clean_text("".join(sec["parts"]))
        if len(text) < 16:
            continue
        sections.append({
            "heading": heading,
            "anchor": sec["anchor"],
            "text": text,
            "codes": sec["codes"],
        })

    if not sections:
        return None
    total = sum(len(s["text"]) for s in sections) + len(description)
    if total < MIN_PAGE_CHARS:
        return None

    return {
        "page_name": page_name,
        "title": title,
        "family": family,
        "category": category,
        "categories": categories,
        "description": description,
        "url": url,
        "modified": modified,
        "experimental": page_name.startswith("Experimental") or title.startswith("Experimental:"),
        "sections": sections,
    }


def _classify(title, page_name, categories):
    """(family, chunk-category) from wiki categories, else title suffix."""
    for cat in categories:
        hit = _CATEGORY_FAMILY.get(cat)
        if hit:
            return hit
    if "Python Reference" in categories or page_name.endswith("_Class"):
        return None, "python"
    if "Glossary" in categories:
        return None, "concept"
    m = _TITLE_SUFFIX_RE.search(title.replace("Experimental:", "").strip())
    if m:
        return m.group(1), "operator"
    return None, "concept"


def _split_long(text, cap=MAX_CHUNK_CHARS):
    """Split overflow text on paragraph, then sentence boundaries."""
    if len(text) <= cap:
        return [text]
    parts = []
    remaining = text
    while len(remaining) > cap:
        cut = remaining.rfind("\n\n", 0, cap)
        if cut < cap // 3:
            cut = remaining.rfind(". ", 0, cap)
            cut = cut + 1 if cut > cap // 3 else cap
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return [p for p in parts if len(p) >= MIN_CHUNK_CHARS]


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")


def page_chunks(page, seen_sections=None):
    """KB chunk records for one parsed page (build_kb schema).

    ``seen_sections`` is a cross-page set of section-body hashes: boilerplate
    sections repeated verbatim on many pages (every TOP embeds the identical
    "Parameters - Common Page" / "Common Operator Info Channels" text) are
    emitted once — first page in sorted order wins — so ~150 copies of the
    same 9 KB section don't drown lexical ranking.
    """
    import hashlib

    chunks = []
    base_id = "wiki_" + _slug(page["page_name"])
    title = page["title"]
    fam_tag = page["family"].lower() if page["family"] else "wiki"
    # Version gate what we can infer: POPs shipped with the 2023.10000
    # experimental line, and Experimental: pages track the current
    # experimental branch. Everything else stays unfiltered ("all").
    min_version = "all"
    if page["family"] == "POP" or page["experimental"]:
        min_version = "2023.10000"
    used_ids = set()
    for sec in page["sections"]:
        heading = sec["heading"]
        is_intro = heading == "(intro)"
        anchor = sec["anchor"]
        url = page["url"] + ("#" + anchor if anchor and not is_intro else "")
        # Harvest internal parameter names from a Parameters section — they
        # are exactly what lexical retrieval needs for "op x param y" queries.
        codes = []
        if "parameter" in heading.lower() or heading.lower() in ("members", "methods"):
            seen = set()
            for c in sec["codes"]:
                c = c.strip("` ")
                if c and len(c) <= 40 and c not in seen and re.match(r"^[A-Za-z_][\w .()\[\]']*$", c):
                    seen.add(c)
                    codes.append(c)
                if len(codes) >= MAX_PARAM_TAGS:
                    break
        prefix = title if is_intro else f"{title} — {heading}"
        body = sec["text"]
        if is_intro and page["description"] and page["description"] not in body:
            body = page["description"] + "\n\n" + body
        # Cross-page section dedupe happens on the whole section body (before
        # length splitting) — split boundaries shift with preceding content,
        # so hashing parts would let near-identical boilerplate slip through.
        if seen_sections is not None and len(body) >= 200:
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if digest in seen_sections:
                continue
            seen_sections.add(digest)
        for i, part in enumerate(_split_long(f"{prefix}\n\n{body}")):
            cid = base_id if is_intro else f"{base_id}__{_slug(heading)}"
            if i:
                cid += f"_{i + 1}"
            n = 2
            while cid in used_ids:   # repeated headings within one page
                cid = f"{base_id}__{_slug(heading)}_{i + 1}_{n}" if i else \
                      f"{base_id}__{_slug(heading)}__{n}"
                n += 1
            used_ids.add(cid)
            tags = [fam_tag, page["category"]]
            if page["experimental"]:
                tags.append("experimental")
            chunks.append({
                "id": cid,
                "title": title,
                "family": page["family"],
                "category": page["category"],
                "source": "wiki",
                "url": url,
                "section": None if is_intro else heading,
                "min_version": min_version,
                "tags": tags + codes[:MAX_PARAM_TAGS],
                "aliases": [],
                "text": part,
                "modified": page["modified"],
            })
    if not chunks:
        # Stub pages (e.g. a class whose members are all inherited) still
        # deserve one resolvable chunk so lookups don't dead-end.
        text = "\n\n".join(
            [title] + ([page["description"]] if page["description"] else [])
            + [f"{s['heading']}: {s['text']}" for s in page["sections"]])
        chunks.append({
            "id": base_id, "title": title, "family": page["family"],
            "category": page["category"], "source": "wiki",
            "url": page["url"], "section": None, "min_version": min_version,
            "tags": [fam_tag, page["category"]], "aliases": [],
            "text": text, "modified": page["modified"],
        })
    return chunks


def iter_pages(root, limit=None):
    """Yield parsed pages from every ``*.htm`` / ``*.html`` under ``root``.

    Deterministic (sorted walk) so KB builds are reproducible. An
    ``Experimental_X`` page is skipped when its release twin ``X`` exists in
    the same mirror — the near-duplicate would otherwise crowd retrieval for
    every query naming that operator.
    """
    count = 0
    root_path = Path(root)
    files = sorted(
        p for p in root_path.rglob("*")
        if p.suffix.lower() in (".htm", ".html") and p.is_file()
    )
    stems = {p.stem for p in files}
    for path in files:
        stem = path.stem
        # Mirrors name these "Experimental-X", "Experimental_X" or
        # "Experimental%3AX" depending on how the wiki namespace was saved.
        m = re.match(r"^Experimental[-_:](.+)$|^Experimental%3A(.+)$", stem)
        if m and (m.group(1) or m.group(2)) in stems:
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        page = parse_page(raw, filename=path.name)
        if page is None:
            continue
        yield page
        count += 1
        if limit and count >= limit:
            return


def ingest(root=None, limit=None, verbose=False):
    """Parse a wiki mirror into KB chunk records. Returns ``[]`` if no root."""
    root = find_root(root)
    if not root:
        return []
    chunks = []
    pages = 0
    seen_sections = set()
    for page in iter_pages(root, limit=limit):
        chunks.extend(page_chunks(page, seen_sections=seen_sections))
        pages += 1
        if verbose and pages % 200 == 0:
            print(f"  wiki ingest: {pages} pages, {len(chunks)} chunks...", flush=True)
    if verbose:
        print(f"  wiki ingest: {pages} pages -> {len(chunks)} chunks from {root}")
    return chunks


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Parse a local TouchDesigner wiki mirror into KB chunks (stdout stats).")
    ap.add_argument("--root", help="wiki mirror root (folder of .htm pages)")
    ap.add_argument("--limit", type=int, help="cap the number of pages")
    ap.add_argument("--out", help="write chunks JSONL here instead of stats-only")
    args = ap.parse_args()
    root = find_root(args.root)
    if not root:
        print("No wiki root found. Set TD_MCP_WIKI_ROOT or pass --root.")
        return 1
    chunks = ingest(root, limit=args.limit, verbose=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            for c in chunks:
                fh.write(json.dumps(c, ensure_ascii=False) + "\n")
        print(f"Wrote {len(chunks)} chunks -> {args.out}")
    from collections import Counter
    fam = Counter((c["family"] or c["category"]) for c in chunks)
    for k, v in sorted(fam.items(), key=lambda x: -x[1]):
        print(f"  {k:10s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
