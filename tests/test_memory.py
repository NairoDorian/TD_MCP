"""Tests for td_mcp.memory (SQLite-backed session memory recall + trust tiers)."""

import sqlite3

from td_mcp.memory import SessionMemory, TIERS


def test_save_and_recall(tmp_path):
    m = SessionMemory(str(tmp_path / "session.db"))
    m.save("user", "create a feedback TOP network")
    m.save("agent", "wired Noise into Feedback")
    m.save("user", "now add an audio reactive chain")
    hits = m.recall("feedback network")
    assert len(hits) >= 1
    assert any("feedback" in h["text"].lower() for h in hits)
    assert "trust_tier" in hits[0]


def test_persists_across_instances(tmp_path):
    p = str(tmp_path / "session.db")
    SessionMemory(p).save("user", "remember the LED wall mapping")
    m2 = SessionMemory(p)
    assert len(m2) == 1
    assert "LED wall" in m2.recall("LED")[0]["text"]


def test_summarize(tmp_path):
    m = SessionMemory(str(tmp_path / "session.db"))
    m.save("user", "step one")
    m.save("agent", "step two")
    assert "step one" in m.summarize()


def test_trust_tier_ranking(tmp_path):
    """Higher-trust (bundled) hits rank before lower-trust (community) ones."""
    m = SessionMemory(str(tmp_path / "t.db"))
    m.save("learned", "noise top drives a level chop chain", trust_tier="bundled")
    m.save("learned", "noise top is great for organic motion", trust_tier="community")
    hits = m.recall("noise top chain")
    # Both match, but the bundled (trusted) entry should come first.
    assert len(hits) == 2
    assert hits[0]["trust_tier"] == "bundled"
    assert hits[1]["trust_tier"] == "community"


def test_trust_tier_invalid_defaults(tmp_path):
    m = SessionMemory(str(tmp_path / "b.db"))
    e = m.save("user", "hello", trust_tier="bogus")
    assert e["trust_tier"] == "personal"


def test_recall_empty_query(tmp_path):
    m = SessionMemory(str(tmp_path / "e.db"))
    m.save("user", "something about colors")
    assert m.recall("   ") == []


def test_clear(tmp_path):
    m = SessionMemory(str(tmp_path / "c.db"))
    m.save("user", "a")
    m.save("user", "b")
    assert len(m) == 2
    m.clear()
    assert len(m) == 0


def test_uses_sqlite(tmp_path):
    p = str(tmp_path / "s.db")
    m = SessionMemory(p)
    m.save("user", "persistence test")
    m.close()
    # The store is a real SQLite database.
    conn = sqlite3.connect(p)
    count = conn.execute("SELECT count(*) FROM memory").fetchone()[0]
    assert count == 1
    conn.close()


def test_tiers_constant():
    assert TIERS == ("official", "bundled", "personal", "community", "experimental")
