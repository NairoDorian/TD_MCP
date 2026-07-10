"""Tests for td_mcp.tools.recovery (Embody-style self-healing hints)."""

from td_mcp.tools.recovery import (
    attach_recovery,
    attach_to_error,
    recovery_hint,
)


def test_connection_refused():
    h = recovery_hint("Connection refused: unable to reach 127.0.0.1:9980")
    assert "bridge" in h["cause"].lower()
    assert "project_info" in h["next_tools"]


def test_node_not_found():
    h = recovery_hint("Error: node '/project1/foo' does not exist")
    assert "not resolve" in h["cause"].lower()
    assert "list_nodes" in h["next_tools"]


def test_parameter_unknown():
    h = recovery_hint("Parameter 'radeus' is not a known parameter")
    assert "parameter" in h["cause"].lower()
    assert "td_docs_parameter" in h["next_tools"]


def test_family_mismatch_error():
    h = recovery_hint("connection rejected: incompatible families TOP->CHOP")
    assert "rejected" in h["cause"].lower()
    assert "td_docs_connections" in h["next_tools"]


def test_unknown_error_falls_back():
    h = recovery_hint("some totally novel cosmic error 42")
    assert "Unrecognized" in h["cause"]
    assert h["next_tools"]


def test_attach_recovery_on_failure():
    res = {"ok": False, "error": "econnrefused while contacting bridge"}
    attach_recovery(res, tool="create_node")
    assert "recovery" in res
    assert res["recovery"]["tool"] == "create_node"
    assert res["recovery"]["next_tools"]


def test_attach_recovery_skips_ok():
    res = {"ok": True, "data": 1}
    out = attach_recovery(res)
    assert "recovery" not in out


def test_attach_to_error_builds_result():
    res = attach_to_error("HTTP 401 unauthorized", tool="delete_node")
    assert res["ok"] is False
    assert res["recovery"]["cause"]
