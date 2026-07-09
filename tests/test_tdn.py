"""Tests for TDN serialization/diff and the knowledge graph."""

import os

from td_mcp import tdn
from td_mcp.rag import knowledge_graph


def test_tdn_roundtrip():
    net = tdn.new_network("/project1", operators=[
        tdn.operator("noise1", "Noise TOP", position=[0, 0],
                     parameters={"type": "Random"}),
        tdn.operator("level1", "Level TOP", position=[200, 0],
                     inputs=["noise1"], parameters={"gamma": 1.2}),
    ])
    text = tdn.export_network(net)
    assert "format: tdn" in text
    back = tdn.import_network(text)
    assert back["network_path"] == "/project1"
    assert len(back["operators"]) == 2
    assert back["operators"][1]["inputs"] == ["noise1"]


def test_tdn_diff_equal_ignores_volatile():
    net = tdn.new_network(operators=[tdn.operator("a", "Null TOP")])
    text = tdn.export_network(net)
    text2 = tdn.export_network(net)
    d = tdn.diff_tdn(text, text2)
    assert d["is_equal"], d


def test_tdn_diff_changes():
    a = tdn.export_network(tdn.new_network(operators=[tdn.operator("a", "Null TOP", parameters={"x": 1})]))
    b = tdn.export_network(tdn.new_network(operators=[tdn.operator("a", "Null TOP", parameters={"x": 2})]))
    d = tdn.diff_tdn(a, b)
    assert not d["is_equal"]
    assert d["changed"][0]["param_changes"]["x"] == {"from": 1, "to": 2}


def test_tdn_build_network_tool():
    from td_mcp import server_offline
    spec = [
        {"type": "Noise TOP", "name": "noise1", "params": {"type": "Random"}},
        {"type": "NotARealOP", "name": "bad1"},
    ]
    out = server_offline.td_build_network(spec)
    assert "WARNINGS" in out  # unknown op flagged
    assert "Noise TOP" in out


def test_knowledge_graph():
    if not os.path.exists(os.path.join(os.path.dirname(__file__), "..",
                                       "td_mcp", "kb", "corpus", "operators.json")):
        return
    related = knowledge_graph.related_operators("Blur TOP", depth=1)
    assert isinstance(related, list)
    chain = knowledge_graph.suggest_chain("audio reactive", k=4)
    assert isinstance(chain, list)
