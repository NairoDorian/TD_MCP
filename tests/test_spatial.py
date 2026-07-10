"""Tests for td_mcp.spatial (*here / *this resolver)."""

from td_mcp.spatial import resolve_args, resolve_pointer

CTX = {"pane_path": "/project1/scene1", "selected": "/project1/scene1/geo1"}


def test_resolve_here():
    assert resolve_pointer("*here", CTX) == "/project1/scene1"


def test_resolve_this():
    assert resolve_pointer("*this op", CTX) == "/project1/scene1/geo1"


def test_passthrough():
    assert resolve_pointer("/project1/x", CTX) == "/project1/x"


def test_resolve_args_path_key():
    args = {"path": "*here", "op_type": "Noise TOP"}
    out = resolve_args(args, CTX)
    assert out["path"] == "/project1/scene1"


def test_resolve_args_list():
    args = {"paths": ["*here", "/project1/y"]}
    out = resolve_args(args, CTX)
    assert out["paths"] == ["/project1/scene1", "/project1/y"]
