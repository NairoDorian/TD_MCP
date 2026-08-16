"""Tests for freshness banner + KB integrity receipt."""

import json
import os

from td_mcp import freshness
from td_mcp.rag import retriever


def test_freshness_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TD_MCP_FRESHNESS", raising=False)
    freshness.reset_freshness_cache()
    assert freshness.freshness_banner() == ""


def test_freshness_no_manifest(monkeypatch, tmp_path):
    monkeypatch.setenv("TD_MCP_FRESHNESS", "1")
    monkeypatch.setenv("TD_MCP_FRESHNESS_MANIFEST", str(tmp_path / "missing.json"))
    freshness.reset_freshness_cache()
    assert freshness.freshness_banner() == ""


def test_sha256_file(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello")
    assert retriever._sha256_file(str(p)) == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_verify_receipt_ok(tmp_path):
    p = tmp_path / "chunks.jsonl"
    p.write_text("{}")
    digest = retriever._sha256_file(str(p))
    ok, issues = retriever.verify_receipt({"chunks": digest}, chunks_path=str(p))
    assert ok
    assert issues == []


def test_verify_receipt_mismatch(tmp_path):
    p = tmp_path / "chunks.jsonl"
    p.write_text("{}")
    ok, issues = retriever.verify_receipt({"chunks": "deadbeef"}, chunks_path=str(p))
    assert not ok
    assert any("integrity mismatch" in i for i in issues)


def test_verify_receipt_empty(tmp_path):
    ok, issues = retriever.verify_receipt({}, chunks_path=str(tmp_path / "none"))
    assert ok
    assert issues == []


def test_receipt_roundtrip(tmp_path):
    p = tmp_path / "chunks.jsonl"
    p.write_text("hello world")
    receipt = {"chunks": retriever._sha256_file(str(p))}
    ok, _ = retriever.verify_receipt(receipt, chunks_path=str(p))
    assert ok
