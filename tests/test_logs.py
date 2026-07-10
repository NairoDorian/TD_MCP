"""Tests for td_mcp.tools.logs (token-efficient ring buffer)."""

from td_mcp.tools.logs import LogRing, attach_piggyback


def test_ring_filters_by_level():
    r = LogRing()
    r.add("DEBUG", "verbose")
    r.add("INFO", "started")
    r.add("WARNING", "almost")
    r.add("ERROR", "boom")
    warns = r.filter("WARNING")
    assert len(warns) == 2
    assert {w["level"] for w in warns} == {"WARNING", "ERROR"}


def test_ring_bounded():
    r = LogRing(maxlen=3)
    for i in range(10):
        r.add("INFO", f"m{i}")
    assert len(r) == 3


def test_piggyback_only_on_failure():
    r = LogRing()
    r.add("DEBUG", "ok")
    res = attach_piggyback({"ok": True}, r)
    assert "_logs" not in res

    r.add("ERROR", "failed thing")
    res = attach_piggyback({"ok": False}, r)
    assert "_logs" in res
    assert res["_logs"][0]["level"] == "ERROR"
