"""Tests for wave-4 merged features (param families, compaction, techniques, registry)."""

import json

from td_mcp import param_resolver
from td_mcp.memory import compact_messages, TechniqueStore
from td_mcp import safety_registry
from td_mcp.glsl_patterns import get_gotcha_template, list_gotcha_templates
from td_mcp.server_offline import td_gotcha_template, td_source_registry, td_show_readiness


# --------------------------------------------------------------- param families
def test_expand_xyz_family():
    assert param_resolver.expand_family_parameter("t", [0, 1, 0]) == {"tx": 0, "ty": 1, "tz": 0}


def test_expand_color_family():
    assert param_resolver.expand_family_parameter("color", [1, 0, 0]) == {"colorr": 1, "colorg": 0, "colorb": 0}


def test_expand_indexed_family():
    assert param_resolver.expand_family_parameter("fromrange", [0, 1]) == {"fromrange1": 0, "fromrange2": 1}


def test_expand_three_default_xyz():
    assert param_resolver.expand_family_parameter("custom", [1, 2, 3]) == {"customx": 1, "customy": 2, "customz": 3}


def test_expand_two_default_indexed():
    assert param_resolver.expand_family_parameter("custom", [1, 2]) == {"custom1": 1, "custom2": 2}


def test_expand_scalar_passthrough():
    assert param_resolver.expand_family_parameter("custom", 5) == {"custom": 5}


# ----------------------------------------------------------- conversation compaction
def test_compaction_below_threshold():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    out, meta = compact_messages(msgs, threshold=20, keep_recent=10)
    assert out is msgs
    assert meta is None


def test_compaction_replaces_older():
    msgs = []
    for i in range(20):
        msgs.append({"role": "user", "content": [{"type": "text", "text": f"goal {i}"}]})
        msgs.append({"role": "assistant", "content": [
            {"type": "text", "text": "ok"},
            {"type": "tool_use", "name": "td_docs_search", "input": {}},
        ]})
    out, meta = compact_messages(msgs, threshold=20, keep_recent=6)
    assert meta["message_count"] == 34  # 40 - 6
    assert meta["kept_recent"] == 6
    assert len(out) == 7  # 1 synthetic + 6 recent
    assert "[[TD_MCP_COMPACTED_HISTORY]]" in out[0]["content"][0]["text"]
    assert "td_docs_search" in out[0]["content"][0]["text"]


# ------------------------------------------------------------ technique memory
def test_technique_store_ladder(tmp_path):
    ts = TechniqueStore(str(tmp_path / "techniques.db"))
    entry = ts.add("noise feedback", {"nodes": []}, scope="project", tags=["noise", "feedback"])
    assert entry["state"] == "candidate"
    # replay success -> validated_local
    entry = ts.record_replay(entry["id"], ok=True)
    assert entry["state"] == "validated_local"
    # replay success again -> validated_portable
    entry = ts.record_replay(entry["id"], ok=True)
    assert entry["state"] == "validated_portable"
    # replay fail -> demoted one rung
    entry = ts.record_replay(entry["id"], ok=False)
    assert entry["state"] == "validated_local"


def test_technique_store_promote(tmp_path):
    ts = TechniqueStore(str(tmp_path / "t.db"))
    e = ts.add("x", {"nodes": []})
    g = ts.promote(e["id"])
    assert g["scope"] == "global"
    assert g["promoted_from"] == e["id"]
    assert g["id"] != e["id"]


# ------------------------------------------------------- source registry + checklist
def test_source_registry_has_core_sources():
    sources = safety_registry.list_sources()
    ids = {s["id"] for s in sources}
    assert "td-docs-wiki" in ids
    assert "bottobot-td-mcp" in ids
    assert "td-builder-alpha" in ids  # AGPL flagged in notes


def test_source_registry_agpl_flagged():
    src = safety_registry.source_by_id("td-builder-alpha")
    assert "AGPL" in src["notes"]


def test_show_readiness_sections():
    data = safety_registry.show_readiness("before_show")
    assert len(data["items"]) >= 7
    all_data = safety_registry.show_readiness()
    assert set(all_data["checklist"]) == {"before_automation", "before_show", "asset_intake"}


# ------------------------------------------------------------- gotcha templates
def test_gotcha_templates_available():
    names = list_gotcha_templates()
    assert "feedback_loop_top" in names
    assert "render_pipeline" in names


def test_gotcha_template_has_gotchas():
    t = get_gotcha_template("glsl_top_vec4_uniform")
    assert "gotchas" in t
    assert any("Vectors" in g for g in t["gotchas"])


def test_td_gotcha_template_tool():
    out = td_gotcha_template("feedback_loop_top")
    assert "Gotchas:" in out
    assert "Composite" in out


def test_td_source_registry_tool():
    out = td_source_registry()
    data = json.loads(out)
    assert len(data) >= 5


def test_td_show_readiness_tool():
    out = td_show_readiness("asset_intake")
    data = json.loads(out)
    assert "Decision" in " ".join(data["items"])
