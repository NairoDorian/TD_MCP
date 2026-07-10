"""Tests for td_mcp.memory (session memory recall)."""

from td_mcp.memory import SessionMemory


def test_save_and_recall(tmp_path):
    m = SessionMemory(str(tmp_path / "mem.jsonl"))
    m.save("user", "create a feedback TOP network")
    m.save("agent", "wired Noise into Feedback")
    m.save("user", "now add an audio reactive chain")
    hits = m.recall("feedback network")
    assert len(hits) >= 1
    assert any("feedback" in h["text"].lower() for h in hits)


def test_persists_across_instances(tmp_path):
    p = str(tmp_path / "mem.jsonl")
    SessionMemory(p).save("user", "remember the LED wall mapping")
    m2 = SessionMemory(p)
    assert len(m2) == 1
    assert "LED wall" in m2.recall("LED")[0]["text"]


def test_summarize(tmp_path):
    m = SessionMemory(str(tmp_path / "mem.jsonl"))
    m.save("user", "step one")
    m.save("agent", "step two")
    assert "step one" in m.summarize()
