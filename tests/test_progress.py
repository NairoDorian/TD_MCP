"""Tests for td_mcp.progress (progress reporting)."""

from td_mcp.progress import Progress


def test_steps_and_percent():
    p = Progress(total=4, label="build")
    p.step("create nodes")
    p.step("wire")
    p.step("verify")
    p.step("done")
    rep = p.report()
    assert rep["current"] == 4
    assert rep["percent"] == 100.0
    assert rep["last"]["label"] == "done"


def test_indeterminate_percent_none():
    p = Progress()
    p.step("start")
    assert p.report()["percent"] is None
