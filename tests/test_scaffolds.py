"""Tests for td_mcp.scaffolds (ai-brain show scaffolds)."""

from td_mcp.scaffolds import list_scaffolds, scaffold_show
from td_mcp.server_offline import td_scaffold_show


def test_scaffolds_available():
    assert "vj" in list_scaffolds()
    assert "audio_reactive" in list_scaffolds()
    assert "installation" in list_scaffolds()


def test_scaffold_show_vj_blueprint():
    sc = scaffold_show("vj")
    assert "error" not in sc
    assert sc["name"] == "vj"
    assert len(sc["operators"]) >= 10
    # sections include the ai-brain canonical nine
    for section in ("audio_in", "audio_analysis", "control_bus", "scene_bank",
                    "fx_rack", "mixer", "mapper", "monitor", "safety"):
        assert section in sc["sections"]


def test_scaffold_show_unknown():
    sc = scaffold_show("ghost")
    assert "error" in sc
    assert "available" in sc


def test_td_scaffold_show_tool():
    out = td_scaffold_show("vj")
    assert "Scaffold: vj" in out
    assert "Audio Device In CHOP" in out
    out2 = td_scaffold_show("ghost")
    assert "error" in out2


def test_scaffold_operators_are_tdn_ready():
    sc = scaffold_show("vj")
    for op in sc["operators"]:
        assert op.get("type")
        assert op.get("name")
        assert isinstance(op.get("inputs"), list)
