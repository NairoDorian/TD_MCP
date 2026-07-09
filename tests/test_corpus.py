"""Tests for the merged corpus (operator + Python + version) loading."""

import os
import json

from td_mcp.kb import corpus

HERE = os.path.dirname(os.path.dirname(__file__))
CORPUS = os.path.join(HERE, "td_mcp", "kb", "corpus")


def _have_corpus():
    return os.path.exists(os.path.join(CORPUS, "operators.json"))


def test_corpus_loaded():
    if not _have_corpus():
        return  # import_corpus not run; skip
    ops = corpus.load_operators()
    assert len(ops) > 600, len(ops)
    # known operator present in both sources
    assert "blur_top" in ops
    rec = ops["blur_top"]
    # merged fields
    assert rec.get("displayName") == "Blur TOP"
    assert rec.get("parameters"), "parameters merged"


def test_python_classes():
    if not _have_corpus():
        return
    classes = corpus.load_python_api()
    assert len(classes) > 100, len(classes)
    assert "App" in classes or "App_Class" in classes


def test_operator_record_lookup():
    if not _have_corpus():
        return
    rec = corpus.operator_record("Blur TOP")
    assert rec is not None
    rec2 = corpus.operator_record("blur")  # nickname/alias
    assert rec2 is not None


def test_param_schema():
    if not _have_corpus():
        return
    rec = corpus.operator_record("Movie File In TOP")
    schema = corpus.param_schema(rec)
    # the classic gotcha: identifier is 'file' not 'filename' (TD lowercases)
    assert any(k.lower() == "file" for k in schema)


def test_compare_operators():
    if not _have_corpus():
        return
    res = corpus.compare_operators("Blur TOP", "Level TOP")
    assert res["ok"]
    assert "Method" in res["shared"] or "Passes" in res["shared"]


def test_version_info():
    if not _have_corpus():
        return
    v = corpus.version_info("2022")
    assert v is not None
    assert v["pythonVersion"].startswith("3.9")
    # POPs require >= 2023
    v23 = corpus.version_info("2023.10000")
    assert v23 is not None


def test_suggest_workflow():
    if not _have_corpus():
        return
    sug = corpus.suggest_workflow("audio reactive visual", k=5)
    assert len(sug) > 0
    assert all("name" in s and "family" in s for s in sug)
