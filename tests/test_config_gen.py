"""Tests for td_mcp.config_gen + tdn idle checkpoint."""

import json
import os
import tempfile

from td_mcp import config_gen
from td_mcp.tdn import checkpoint, export_network, import_network, restore_checkpoint


def test_mcp_json_structure():
    cfg = config_gen.generate_mcp_json(include_offline=True, include_live=True,
                                       auth_token="TOK")
    assert "mcpServers" in cfg
    assert "td-mcp-offline" in cfg["mcpServers"]
    assert cfg["mcpServers"]["td-mcp-live"]["env"]["TD_MCP_AUTH_TOKEN"] == "TOK"


def test_client_docs_lists_tools():
    doc = config_gen.generate_client_docs("cursor")
    assert "td-mcp for cursor" in doc
    assert "build_and_verify" in doc


def test_write_configs(tmp_path):
    paths = config_gen.write_configs(str(tmp_path), client="claude")
    assert os.path.exists(paths["mcp"])
    assert os.path.exists(paths["docs"])
    json.loads(open(paths["mcp"]).read())


def test_tdn_checkpoint_roundtrip(tmp_path):
    ops = [{"name": "n", "type": "Noise TOP", "inputs": [None]}]
    path = checkpoint("/project1", ops, base_dir=str(tmp_path))
    assert os.path.exists(path)
    net = restore_checkpoint(path)
    assert net["network_path"] == "/project1"
    assert net["operators"][0]["name"] == "n"


def test_tdn_diff_ignores_checkpoint_header(tmp_path):
    ops = [{"name": "n", "type": "Noise TOP", "inputs": [None]}]
    a = checkpoint("/project1", ops, base_dir=str(tmp_path), tag="v1")
    b = checkpoint("/project1", ops, base_dir=str(tmp_path), tag="v2")
    na = import_network(open(a).read())
    nb = import_network(open(b).read())
    # The only difference is the volatile exported_at header -> diff is empty.
    from td_mcp.tdn import diff_tdn
    d = diff_tdn(na, nb)
    assert d["is_equal"] is True
