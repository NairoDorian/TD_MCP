"""Tests for the wiki-rag SQLite FTS5 fusion strategy (§2.2)."""

import sqlite3

from td_mcp.rag.retriever import Retriever
from td_mcp.rag.strategies import ParallelRetriever, SqliteWikiStrategy, WIKI_PREFIX


def _make_wiki_db(path):
    """Build a mini wiki-rag FTS5 index matching ingest.py's schema."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE VIRTUAL TABLE chunks USING fts5(
            chunk_id, text, heading, title, anchor, family, page_type, revid,
            tokenize='porter unicode61'
        );
    """)
    rows = [
        ("Noise TOP:param", "Noise TOP generates procedural noise textures.", "Parameters", "Noise TOP", "Parameters", "TOP", "operator", 1),
        ("Noise SOP:intro", "Noise SOP displaces geometry with 3D noise.", "(intro)", "Noise SOP", "", "SOP", "operator", 1),
        ("Audio Device:intro", "Audio Device In reads from a sound card.", "(intro)", "Audio Device In CHOP", "", "CHOP", "operator", 1),
    ]
    conn.executemany(
        "INSERT INTO chunks(chunk_id, text, heading, title, anchor, family, page_type, revid) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


def test_strategy_returns_wiki_uris(tmp_path):
    db = tmp_path / "manual.db"
    _make_wiki_db(db)
    strat = SqliteWikiStrategy(str(db))
    keys = strat.candidates(["noise"], "noise", None, 5)
    assert set(keys) == set(strat.wiki_docs.keys())
    assert len(keys) == 2  # Noise TOP, Noise SOP
    doc = strat.wiki_docs[keys[0]]
    assert doc["source"] == "wiki-rag"
    assert "https://docs.derivative.ca/Noise" in doc["url"]


def test_strategy_family_filter(tmp_path):
    db = tmp_path / "manual.db"
    _make_wiki_db(db)
    strat = SqliteWikiStrategy(str(db))
    # FTS matches both Noise TOP and Noise SOP; family filtering happens in
    # ParallelRetriever.search, not at the strategy level.
    keys = strat.candidates(["audio", "device"], "audio device in", None, 5)
    assert len(keys) == 1
    doc = strat.wiki_docs[keys[0]]
    assert doc["family"] == "CHOP"


def test_strategy_no_match(tmp_path):
    db = tmp_path / "manual.db"
    _make_wiki_db(db)
    strat = SqliteWikiStrategy(str(db))
    assert strat.candidates(["zzzznonexistent"], "zzzznonexistent", None, 5) == []


def test_strategy_bad_db(tmp_path):
    strat = SqliteWikiStrategy(str(tmp_path / "nonexistent.db"))
    assert strat.candidates(["noise"], "noise", None, 5) == []


def test_parallel_retriever_fuses_wiki(tmp_path):
    """Wiki chunks fuse into RRF results via the wiki: prefix."""
    db = tmp_path / "manual.db"
    _make_wiki_db(db)
    pr = ParallelRetriever(Retriever([]), wiki_db=str(db))
    # Wiki strategy is now one of the parallel strategies.
    names = [s.name() for s in pr.strategies]
    assert "Wiki[sqlite]" in names
    res = pr.search("noise", family="SOP", k=5)
    # The wiki SOP hit should be in the fused results.
    assert any(d.get("family") == "SOP" and d.get("source") == "wiki-rag" for d, _ in res)


def test_wiki_docs_persist_on_strategy(tmp_path):
    """After candidates() runs, docs stay resolvable by URI."""
    db = tmp_path / "manual.db"
    _make_wiki_db(db)
    strat = SqliteWikiStrategy(str(db))
    keys = strat.candidates(["noise"], "noise", None, 5)
    # Re-querying the same docs works (they're cached).
    for uri in keys:
        assert uri in strat.wiki_docs


def test_wiki_opt_in_not_added_by_default(tmp_path):
    """Without TD_MCP_WIKI_DB or wiki_db arg, no wiki strategy is added."""
    pr = ParallelRetriever(Retriever([]))
    names = [s.name() for s in pr.strategies]
    assert "Wiki[sqlite]" not in names
    assert pr.wiki_docs == {}
