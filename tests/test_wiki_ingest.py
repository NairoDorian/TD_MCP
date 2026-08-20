"""Tests for td_mcp.kb.wiki_ingest (structured local wiki-mirror ingestion).

Every fixture below is hand-written MediaWiki-shaped HTML, so the suite runs
without a TouchDesigner install or a docs mirror on disk.
"""

import textwrap

import pytest

from td_mcp.kb import wiki_ingest


def _page(page_name, title, categories, body, description="A test page."):
    """Minimal but structurally faithful MediaWiki-rendered page."""
    cats = ",".join(f'"{c}"' for c in categories)
    return textwrap.dedent(f"""\
        <!DOCTYPE html>
        <html><head>
        <title>TouchDesigner Documentation</title>
        <script>RLCONF={{"wgPageName":"{page_name}","wgTitle":"{title}",
        "wgCategories":[{cats}]}};</script>
        <meta name="description" content="{description}">
        <meta property="og:url" content="https://docs.derivative.ca/{page_name}">
        <meta property="article:modified_time" content="2021-11-16T20:31:46Z">
        </head><body>
        <div id="mw-content-text" class="mw-body-content"><div class="mw-parser-output">
        {body}
        </div></div>
        <div id="catlinks"><a href="/Category:TOPs">TOPs</a></div>
        </body></html>
    """)


def _section(anchor, heading, inner):
    return (f'<h2><span class="mw-headline" id="{anchor}">'
            f'<div class="sectionBarTOP">{heading}</div></span>'
            f'<span class="mw-editsection">[edit]</span></h2>{inner}')


BLUR_HTML = _page(
    "Blur_TOP", "Blur TOP", ["TOPs"],
    _section("Summary", "Summary",
             "<p>The Blur TOP blurs the image with various kernel filters and "
             "radii. It can do multi-pass blurs and horizontal-only blurs.</p>")
    + _section("Parameters_-_Blur_Page", "Parameters - Blur Page",
               '<p><span class="parNameTOP">Filter Size</span> <code>size</code>'
               " - The amount of blur in pixels, resolution independent when "
               "driven by an expression on the parameter.</p>"
               '<p><span class="parNameTOP">Method</span> <code>method</code>'
               " - Determines how the blur is applied across the image.</p>"),
    description="The Blur TOP blurs the image with various kernel filters and radii.")


@pytest.fixture
def blur_page():
    return wiki_ingest.parse_page(BLUR_HTML, "Blur_TOP.htm")


def test_parse_page_metadata(blur_page):
    assert blur_page["title"] == "Blur TOP"
    assert blur_page["family"] == "TOP"
    assert blur_page["category"] == "operator"
    assert blur_page["url"] == "https://docs.derivative.ca/Blur_TOP"
    assert blur_page["modified"] == "2021-11-16T20:31:46Z"
    assert not blur_page["experimental"]


def test_sections_carry_anchors(blur_page):
    headings = [(s["heading"], s["anchor"]) for s in blur_page["sections"]]
    assert ("Summary", "Summary") in headings
    assert ("Parameters - Blur Page", "Parameters_-_Blur_Page") in headings


def test_chrome_is_stripped(blur_page):
    joined = " ".join(s["text"] for s in blur_page["sections"])
    assert "[edit]" not in joined          # mw-editsection subtree skipped
    assert "mw-body-content" not in joined  # container attrs not leaked as text
    assert "<" not in joined                # no raw markup survives


def test_chunks_are_cited_per_section(blur_page):
    chunks = wiki_ingest.page_chunks(blur_page)
    by_section = {c["section"]: c for c in chunks}
    params = by_section["Parameters - Blur Page"]
    assert params["url"] == (
        "https://docs.derivative.ca/Blur_TOP#Parameters_-_Blur_Page")
    # Title prefix travels with every chunk so a parameter description never
    # loses the operator it belongs to.
    assert params["text"].startswith("Blur TOP — Parameters - Blur Page")


def test_parameter_names_become_tags(blur_page):
    chunks = wiki_ingest.page_chunks(blur_page)
    params = next(c for c in chunks if c["section"] == "Parameters - Blur Page")
    assert "size" in params["tags"]
    assert "method" in params["tags"]


def test_intro_chunk_carries_description(blur_page):
    chunks = wiki_ingest.page_chunks(blur_page)
    assert "blurs the image" in chunks[0]["text"]


def test_class_page_drops_inherited_sections():
    html = _page(
        "BlurTOP_Class", "BlurTOP Class", ["TOP Classes", "Python Reference"],
        _section("Members", "Members",
                 "<p>The BlurTOP class describes an instance of a Blur TOP "
                 "operator and exposes its own members.</p>")
        + _section("TOP_Class", "TOP Class",
                   "<p>Inherited TOP class documentation that the canonical "
                   "TOP_Class page already covers in full detail.</p>")
        + _section("OP_Class", "OP Class",
                   "<p>Inherited OP class documentation duplicated onto every "
                   "single operator class page in the whole wiki.</p>"))
    page = wiki_ingest.parse_page(html, "BlurTOP_Class.htm")
    assert page["category"] == "python"
    headings = [s["heading"] for s in page["sections"]]
    assert "Members" in headings
    assert "TOP Class" not in headings
    assert "OP Class" not in headings


def test_canonical_class_page_keeps_its_own_sections():
    html = _page("OP_Class", "OP Class", ["Python Reference"],
                 _section("Members", "Members",
                          "<p>Members shared by every operator in a network, "
                          "including name, path, and digits.</p>"))
    page = wiki_ingest.parse_page(html, "OP_Class.htm")
    assert [s["heading"] for s in page["sections"]] == ["Members"]


def test_pop_and_experimental_pages_are_version_gated():
    pop = wiki_ingest.parse_page(
        _page("Particle_POP", "Particle POP", ["POPs"],
              _section("Summary", "Summary",
                       "<p>The Particle POP simulates particles on the GPU "
                       "using the point operator family.</p>")),
        "Particle_POP.htm")
    assert wiki_ingest.page_chunks(pop)[0]["min_version"] == "2023.10000"

    exp = wiki_ingest.parse_page(
        _page("Experimental:Bloom_TOP", "Experimental:Bloom TOP", ["TOPs"],
              _section("Summary", "Summary",
                       "<p>The Bloom TOP adds a configurable bloom glow to an "
                       "image using a multi-pass filter.</p>")),
        "Experimental-Bloom_TOP.htm")
    assert exp["experimental"]
    assert wiki_ingest.page_chunks(exp)[0]["min_version"] == "2023.10000"


def test_family_falls_back_to_title_suffix():
    page = wiki_ingest.parse_page(
        _page("Odd_CHOP", "Odd CHOP", ["Uncategorized"],
              _section("Summary", "Summary",
                       "<p>An operator whose wiki categories are missing but "
                       "whose title still names its family clearly.</p>")),
        "Odd_CHOP.htm")
    assert page["family"] == "CHOP"
    assert page["category"] == "operator"


def test_concept_page_has_no_family():
    page = wiki_ingest.parse_page(
        _page("Cook", "Cook", ["Glossary"],
              _section("Summary", "Summary",
                       "<p>Cooking is the process of an operator recomputing "
                       "its output when its inputs or parameters change.</p>")),
        "Cook.htm")
    assert page["family"] is None
    assert page["category"] == "concept"


def test_long_sections_split_on_paragraph_boundaries():
    para = "This paragraph documents one parameter of the operator in detail. "
    body = "".join(f"<p>{para * 6}</p>" for _ in range(10))
    page = wiki_ingest.parse_page(
        _page("Big_TOP", "Big TOP", ["TOPs"],
              _section("Parameters_Page", "Parameters Page", body)),
        "Big_TOP.htm")
    chunks = wiki_ingest.page_chunks(page)
    param_chunks = [c for c in chunks if c["section"] == "Parameters Page"]
    assert len(param_chunks) > 1
    assert all(len(c["text"]) <= wiki_ingest.MAX_CHUNK_CHARS + 200
               for c in param_chunks)
    assert len({c["id"] for c in chunks}) == len(chunks)


def test_repeated_headings_get_unique_ids():
    page = wiki_ingest.parse_page(
        _page("Dup_TOP", "Dup TOP", ["TOPs"],
              _section("General", "General",
                       "<p>The first general section of this operator page "
                       "describing common behavior.</p>")
              + _section("General_2", "General",
                         "<p>A second section that renders with the very same "
                         "heading text as the first one.</p>")),
        "Dup_TOP.htm")
    ids = [c["id"] for c in wiki_ingest.page_chunks(page)]
    assert len(ids) == len(set(ids))


def test_boilerplate_sections_are_deduped_across_pages():
    common = ("<p>" + ("Every operator page in the wiki repeats this exact "
                       "common-page boilerplate text verbatim. ") * 6 + "</p>")
    seen = set()
    kept = []
    for name in ("A_TOP", "B_TOP"):
        page = wiki_ingest.parse_page(
            _page(name, name.replace("_", " "), ["TOPs"],
                  _section("Summary", "Summary",
                           f"<p>The {name} is a unique operator with its own "
                           "distinct summary paragraph text.</p>")
                  + _section("Common", "Parameters - Common Page", common)),
            f"{name}.htm")
        kept.append(wiki_ingest.page_chunks(page, seen_sections=seen))
    assert any(c["section"] == "Parameters - Common Page" for c in kept[0])
    assert not any(c["section"] == "Parameters - Common Page" for c in kept[1])
    # The page's own unique content is never deduped away.
    assert any(c["section"] == "Summary" for c in kept[1])


def test_ingest_skips_experimental_twin(tmp_path):
    (tmp_path / "Bloom_TOP.htm").write_text(
        _page("Bloom_TOP", "Bloom TOP", ["TOPs"],
              _section("Summary", "Summary",
                       "<p>The release Bloom TOP adds a glow to bright areas "
                       "of an image with configurable falloff.</p>")),
        encoding="utf-8")
    (tmp_path / "Experimental-Bloom_TOP.htm").write_text(
        _page("Experimental:Bloom_TOP", "Experimental:Bloom TOP", ["TOPs"],
              _section("Summary", "Summary",
                       "<p>The experimental Bloom TOP adds a glow to bright "
                       "areas of an image with configurable falloff.</p>")),
        encoding="utf-8")
    titles = {p["title"] for p in wiki_ingest.iter_pages(str(tmp_path))}
    assert titles == {"Bloom TOP"}


def test_ingest_keeps_experimental_without_release_twin(tmp_path):
    (tmp_path / "Experimental-Only_TOP.htm").write_text(
        _page("Experimental:Only_TOP", "Experimental:Only TOP", ["TOPs"],
              _section("Summary", "Summary",
                       "<p>An experimental operator that has no released "
                       "counterpart page anywhere in this mirror.</p>")),
        encoding="utf-8")
    titles = {p["title"] for p in wiki_ingest.iter_pages(str(tmp_path))}
    assert titles == {"Experimental:Only TOP"}


def test_ingest_is_deterministic(tmp_path):
    for name in ("Zeta_TOP", "Alpha_TOP", "Mid_TOP"):
        (tmp_path / f"{name}.htm").write_text(
            _page(name, name.replace("_", " "), ["TOPs"],
                  _section("Summary", "Summary",
                           f"<p>The {name} operator has a distinct summary "
                           "paragraph so it is never deduplicated away.</p>")),
            encoding="utf-8")
    first = [c["id"] for c in wiki_ingest.ingest(str(tmp_path))]
    second = [c["id"] for c in wiki_ingest.ingest(str(tmp_path))]
    assert first == second == sorted(first)


def test_ingest_without_root_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setenv("TD_MCP_WIKI_ROOT", str(tmp_path / "does-not-exist"))
    monkeypatch.setattr(wiki_ingest, "default_roots",
                        lambda: [str(tmp_path / "does-not-exist")])
    assert wiki_ingest.ingest() == []
    assert wiki_ingest.find_root() is None


def test_malformed_page_is_skipped_not_fatal():
    assert wiki_ingest.parse_page("<html><body>not a wiki page</body></html>",
                                  "junk.htm") is None
    assert wiki_ingest.parse_page("", "empty.htm") is None
