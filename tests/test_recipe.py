"""Tests for recipe_vault upgrades (tdmcp-style metadata + draft_from_chain)."""

import os
import tempfile

from td_mcp import recipe_vault


def _temp_vault(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("TD_MCP_VAULT_DIR", d)
    # Force reload of module-level paths against the new env dir.
    import importlib
    importlib.reload(recipe_vault)
    return d


def test_save_and_list_metadata(monkeypatch):
    _temp_vault(monkeypatch)
    res = recipe_vault.save_recipe(
        [{"name": "n", "type": "Noise TOP"}], "My Recipe",
        tags=["TOP"], difficulty="advanced", td_version_min="2023.10000",
        technique="feedback")
    assert res["ok"]
    listing = recipe_vault.list_recipes(query="My Recipe")
    assert listing["total"] == 1
    rec = listing["recipes"][0]
    assert rec["difficulty"] == "advanced"
    assert rec["td_version_min"] == "2023.10000"
    assert rec["technique"] == "feedback"


def test_draft_recipe_from_chain(monkeypatch):
    _temp_vault(monkeypatch)
    chain = [
        {"type": "Noise TOP", "params": {"resx": 128}, "inputs": [None]},
        {"type": "Level TOP", "params": {}, "inputs": ["Noise TOP"]},
    ]
    draft = recipe_vault.draft_recipe_from_chain(chain)
    assert draft["technique"] == "TOP"
    assert draft["difficulty"] == "beginner"
    assert len(draft["recipe"]) == 2
    assert draft["recipe"][1]["inputs"] == ["Noise TOP"]
    # Round-trips through save + get.
    rid = recipe_vault.save_recipe(draft["recipe"], draft["name"],
                                   tags=draft["tags"], technique=draft["technique"],
                                   difficulty=draft["difficulty"])["id"]
    got = recipe_vault.get_recipe(rid)
    assert got["ok"]
