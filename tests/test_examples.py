"""The built-in examples and the one-call builder they are built with.

`scripts/demo_build.py` builds `examples/*.json` in a live TouchDesigner (7
stages, all verified there). These are the parts CI can check without one:
every shipped example is a valid, warning-free spec, and `build_network`
creates, wires, places and flags a network the way the demo relies on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from td_mcp import validation
from td_mcp.kb import param_catalog as pc
from tests.td_double import build_bridge

EXAMPLES = sorted((Path(__file__).resolve().parent.parent / "examples").glob("*.json"))


def test_there_is_a_built_in_example():
    assert any(p.name == "feedback_trails.json" for p in EXAMPLES)


@pytest.mark.skipif(not pc.available(), reason="param catalog not present")
@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_example_validates_clean_in_strict_mode(path):
    spec = json.loads(path.read_text(encoding="utf-8"))
    report = validation.validate_build(spec, strict=True)
    assert report["ok"] and not report["findings"], report["findings"]
    assert spec.get("name") and spec.get("description"), "examples are documented"


@pytest.fixture()
def bridge():
    return build_bridge()


CHAIN = {"operators": [
    {"name": "src", "type": "noiseTOP"},
    {"name": "mid", "type": "levelTOP", "inputs": ["src"]},
    {"name": "out", "type": "nullTOP", "inputs": ["mid"], "flags": {"display": True}},
]}


class TestBuildNetwork:
    def test_creates_wires_places_and_flags_in_one_call(self, bridge):
        br, td = bridge
        out = br["_do_build_network"](CHAIN, "/project1")
        assert out["ok"], out
        assert out["created"] == 3 and out["wired"] == 2
        kids = {c.name: c for c in td.project_comp.children}
        assert kids["out"].inputs == [kids["mid"]] and kids["mid"].inputs == [kids["src"]]
        assert kids["src"].nodeX < kids["mid"].nodeX < kids["out"].nodeX  # left to right
        assert getattr(kids["out"], "display", None) is True

    def test_existing_names_are_refused_unless_replace(self, bridge):
        br, td = bridge
        br["_do_build_network"](CHAIN, "/project1")
        again = br["_do_build_network"](CHAIN, "/project1")
        assert again["ok"] is False and any("already exists" in i for i in again["issues"])
        replaced = br["_do_build_network"](CHAIN, "/project1", replace=True)
        assert replaced["ok"] and len([c for c in td.project_comp.children
                                       if c.name in ("src", "mid", "out")]) == 3

    def test_unknown_parameter_is_reported_not_dropped(self, bridge):
        br, _ = bridge
        spec = {"operators": [{"name": "n", "type": "noiseTOP", "parameters": {"zzz": 1}}]}
        out = br["_do_build_network"](spec, "/project1")
        assert out["ok"] is False and "zzz" in json.dumps(out["operators"]["n"])

    def test_unresolvable_wire_is_reported(self, bridge):
        br, _ = bridge
        spec = {"operators": [{"name": "a", "type": "nullTOP", "inputs": ["ghost"]}]}
        out = br["_do_build_network"](spec, "/project1")
        assert out["ok"] is False and any("ghost" in i for i in out["issues"])

    def test_target_must_be_a_comp_and_not_the_bridge(self, bridge):
        br, td = bridge
        td.make("lonely", "noiseTOP")
        assert br["_do_build_network"](CHAIN, "/project1/lonely")["ok"] is False

    def test_layout_puts_a_source_left_of_everything_it_feeds(self, bridge):
        br, _ = bridge
        pos = br["_build_layout"](json.loads(EXAMPLES[0].read_text(encoding="utf-8"))["operators"])
        assert pos["noise_field"][0] < pos["level_shape"][0] < pos["composite_trails"][0] < pos["null_out"][0]
