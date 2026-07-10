"""Tests for new offline tools: discover / memory / scaffold_recipe."""

from td_mcp.server_offline import (
    td_discover,
    td_memory_recall,
    td_memory_save,
    td_scaffold_recipe,
)


def test_td_discover_tool():
    out = td_discover()
    assert "[]" in out or "[" in out  # valid JSON list


def test_td_memory_save_recall_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("TD_MCP_VAULT_DIR", str(tmp_path))
    import importlib
    from td_mcp import memory as mem_mod
    importlib.reload(mem_mod)
    s = td_memory_save("user", "build a feedback TOP", "feedback")
    assert '"ok"' in s
    r = td_memory_recall("feedback")
    assert "feedback" in r


def test_td_scaffold_recipe_tool():
    spec = '[{"name":"n","type":"Noise TOP","inputs":[null]},' \
           '{"name":"l","type":"Level TOP","inputs":["n"]}]'
    out = td_scaffold_recipe(spec)
    assert '"name"' in out and "TOP" in out
