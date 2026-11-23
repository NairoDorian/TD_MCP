"""The shipped skills stay true to the tools and to TouchDesigner.

Skills are prose an agent trusts, so they drift silently: a renamed tool or a
mistyped operator reads as fact. While writing the 1.13 skills, checking them
against the probed catalog caught five wrong names (`selectCHOP chop`,
`phongMAT diffcolorr`, `baseCOMP extension1`, `glslMAT pixeldat`,
`geometryCOMP instancecolorr`). These tests keep that check running.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from td_mcp.kb import param_catalog as pc

SKILLS = Path(__file__).resolve().parent.parent / "skills"
SKILL_FILES = sorted(SKILLS.glob("*/SKILL.md"))
# A name written right after "not" is a deliberate counterexample
# ("`geometryCOMP`, not `geoCOMP`") and is exempt.
_OP_TYPE = re.compile(r"(?<!not )`([a-z][a-z0-9]*(?:TOP|CHOP|SOP|POP|DAT|MAT|COMP))`")
_TOOLISH = re.compile(r"`((?:get|set|read|create|delete|capture|describe|pulse|connect|"
                      r"disconnect|list|scan|find|map|observe|validate|batch|execute|"
                      r"rename|copy|auto|export|import|save|snapshot|restore|clear|bind|"
                      r"diff|td)_[a-z_]+)`")


def _known_tools():
    from td_mcp.server_live import TOOL_REGISTRY
    from td_mcp import server_offline
    server_offline._register_offline()
    return set(TOOL_REGISTRY) | set(server_offline._OFFLINE_DISPATCH)


def test_skill_set_is_present():
    names = {p.parent.name for p in SKILL_FILES}
    assert {"td-workflow", "td-building", "td-top-family", "td-chop-family",
            "td-pop-family", "td-sop-render", "td-dat-python", "td-glsl",
            "td-performance", "td-network-layout", "td-guide-user"} <= names


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.parent.name)
def test_frontmatter_names_the_folder(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\nname: (.+)\ndescription: (.+)\n---\n", text)
    assert m, f"{path} needs name/description frontmatter"
    assert m.group(1).strip() == path.parent.name
    assert len(m.group(2)) > 40


@pytest.mark.skipif(not pc.available(), reason="param catalog not present")
@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.parent.name)
def test_every_operator_type_exists(path):
    text = path.read_text(encoding="utf-8")
    missing = sorted({t for t in _OP_TYPE.findall(text) if pc.resolve_op_type(t) != t})
    assert not missing, f"{path.parent.name}: not TouchDesigner {pc.build()} types: {missing}"


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.parent.name)
def test_every_tool_named_exists(path):
    text = path.read_text(encoding="utf-8")
    tools = _known_tools()
    named = {t for t in _TOOLISH.findall(text) if t != "td_mcp"}
    unknown = sorted(named - tools)
    assert not unknown, f"{path.parent.name}: tools that do not exist: {unknown}"


def test_both_servers_send_instructions():
    """Even a client with no skills installed gets the loop and the
    look-before-you-guess rule at initialize."""
    from td_mcp import instructions, server_offline
    app = server_offline.create_server()
    assert app.instructions == instructions.OFFLINE
    for text in (instructions.LIVE, instructions.OFFLINE):
        assert "describe_op_type" in text and "noiseTOP" in text
