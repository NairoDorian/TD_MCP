"""Wave 15: menu parameters, re-measured on TouchDesigner 2025.33230
(TEST-01, 2026-11-28; UPSTREAM_SYNC_2026-11-28.md).

* A Menu given a token it does not have raises nothing and jumps to its FIRST
  item (random -> 'nosuch' -> perlin2d on a Noise TOP; the same on five other
  operator types). The earlier note "the old value is kept" was measured on a
  menu that was already at its first item.
* A StrMenu (Select CHOP ``channames``, Rename CHOP ``renamefrom``) reports
  ``isMenu`` True and lists suggestions (``*``), but keeps any text.
"""

from __future__ import annotations

import pytest

from td_mcp import param_resolver, validation
from td_mcp.kb import param_catalog
from tests.td_double import FakePar, build_bridge

needs_catalog = pytest.mark.skipif(not param_catalog.available(), reason="parameter catalog not built")


@pytest.fixture()
def bridge():
    return build_bridge()


def _menu(node, name, names, value, style="Menu"):
    return node.par._add(FakePar(name, value, value, style=style, menuNames=list(names)))


def test_the_double_resets_a_menu_to_its_first_item(bridge):
    _br, td = bridge
    n = td.make("n1", "noiseTOP")
    p = _menu(n, "type", ["perlin2d", "simplex3d", "random"], "random")
    p.val = "nosuch"
    assert p.eval() == "perlin2d"
    s = td.make("sel", "selectCHOP")
    q = _menu(s, "channames", ["*"], "*", style="StrMenu")
    q.val = "tx ty"
    assert q.eval() == "tx ty"
    q.val = 0
    assert q.eval() == "0"


def test_an_unknown_token_is_never_written(bridge):
    br, td = bridge
    n = td.make("n1", "noiseTOP")
    _menu(n, "type", ["perlin2d", "simplex3d", "random"], "random")
    out = br["_do_set_parameters"]("/project1/n1", {"type": "nosuch"})
    assert out["ok"] is False and "perlin2d" in out["invalid"]["type"]["not_written"]
    assert out["invalid"]["type"]["kept"] == "random"
    assert n.par.type.eval() == "random"           # TD would have reset it to perlin2d
    out = br["_do_set_parameters"]("/project1/n1", {"type": "Simplex3D"})  # case variant -> token
    assert out["ok"] is True and n.par.type.eval() == "simplex3d"


def test_free_text_menus_take_free_text(bridge):
    br, td = bridge
    s = td.make("sel", "selectCHOP")
    _menu(s, "channames", ["*"], "*", style="StrMenu")
    out = br["_do_set_parameters"]("/project1/sel", {"channames": "tx ty"})
    assert out["ok"] is True and s.par.channames.eval() == "tx ty"
    assert "notes" not in out or "channames" not in out.get("notes", {})


@needs_catalog
def test_validator_and_repair_accept_free_text_menus():
    spec = {"operators": [{"name": "sel", "type": "selectCHOP", "parameters": {"channames": "tx ty"}},
                          {"name": "ren", "type": "renameCHOP",
                           "parameters": {"renamefrom": "chan*", "renameto": "v*"}}]}
    report = validation.validate_build(spec)
    assert not [f for f in report.get("findings", []) if f["code"] == "MENU_INVALID"], report
    repaired = validation.auto_repair(spec, report)
    ops = {o["name"]: o for o in (repaired.get("desc") or repaired.get("spec") or repaired)["operators"]}
    assert ops["sel"]["parameters"]["channames"] == "tx ty"
    resolved, warnings, _ = param_resolver.resolve_parameters("selectCHOP", {"channames": "tx ty"})
    assert resolved == {"channames": "tx ty"}
    # A real Menu is still checked.
    bad = validation.validate_build({"operators": [{"name": "n", "type": "noiseTOP",
                                                    "parameters": {"type": "nosuch"}}]})
    assert [f for f in bad["findings"] if f["code"] == "MENU_INVALID"]
