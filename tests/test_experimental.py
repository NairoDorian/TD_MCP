"""Tests for the experimental techniques loader and skill pack."""

import json
import os

from td_mcp.kb import corpus
from td_mcp.server_offline import td_docs_experimental


def test_experimental_categories_loaded():
    cats = corpus.list_experimental_categories()
    assert len(cats) == 7
    cat_names = {c for c, _, _ in cats}
    assert "glsl" in cat_names
    assert "gpu-compute" in cat_names
    assert "machine-learning" in cat_names


def test_load_experimental_glsl():
    data = corpus.load_experimental_techniques("glsl")
    assert data is not None
    assert data["category"] == "glsl"
    assert len(data["techniques"]) >= 3
    assert any(t["id"] == "raymarching_basic" for t in data["techniques"])


def test_load_experimental_alias():
    # "shader" is a synonym for "glsl"
    data = corpus.load_experimental_techniques("shader")
    assert data is not None
    assert data["category"] == "glsl"


def test_load_experimental_unknown():
    assert corpus.load_experimental_techniques("ghost") is None


def test_td_docs_experimental_category():
    out = td_docs_experimental("glsl")
    data = json.loads(out)
    assert data["category"] == "glsl"
    assert "techniques" in data


def test_td_docs_experimental_technique():
    out = td_docs_experimental("glsl", technique_id="raymarching_basic")
    data = json.loads(out)
    assert data["category"] == "glsl"
    assert data["technique"]["id"] == "raymarching_basic"
    assert "code" in data["technique"]


def test_td_docs_experimental_no_code():
    out = td_docs_experimental("glsl", technique_id="raymarching_basic", show_code=False)
    data = json.loads(out)
    assert "code" not in data["technique"]


def test_td_docs_experimental_unknown_technique():
    out = td_docs_experimental("glsl", technique_id="ghost")
    data = json.loads(out)
    assert "error" in data


def test_td_docs_experimental_unknown_category():
    out = td_docs_experimental("ghost")
    data = json.loads(out)
    assert "error" in data
    assert "available" in data


def test_skill_md_exists():
    skill_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "skills", "td-building", "SKILL.md")
    assert os.path.exists(skill_path)
    text = open(skill_path, encoding="utf-8").read()
    assert "pull-based" in text.lower()
    assert "ui.undo" in text
