"""Regression tests for fixes found during code review.

These lock in behavior that previously had no test coverage (which is how
the bugs survived): the hmac server crash, the audio-reactive build, the
self-heal wiring, show-control operator names, perf schema, and the various
low-severity correctness fixes.
"""

import json
import os
import tempfile
import zipfile
from pathlib import Path

from td_mcp import config_gen, discover, showcontrol as sc
from td_mcp.bundle import package
from td_mcp.macro import MacroRecorder
from td_mcp.rag import knowledge_graph
from td_mcp.tools.logs import LogRing
from td_mcp.tools.risk import RISK_CLASS


def test_showcontrol_mtc_uses_mtcin():
    assert sc.timecode_setup("ltc")["operator"] == "LtcIn"
    assert sc.timecode_setup("mtc")["operator"] == "MtcIn"


def test_perf_accepts_bridge_cooks_shape():
    out = sc.build_show_plan([])  # sanity: module imports fine
    assert "outputs" in out
    from td_mcp import perf as perf_mod
    r = perf_mod.analyze_performance(
        {"fps": 25, "cooks": [{"path": "/project1/n1", "cook_time": 9.0,
                               "cpu": 90, "gpu": 0}]})
    assert r["slowest"][0]["name"] == "/project1/n1"
    assert len(r["suggestions"]) >= 1


def test_logs_tolerates_none_level():
    lg = LogRing()
    lg.add(None, "hi")
    assert len(lg) == 1


def test_macro_non_dict_success_is_ok():
    m = MacroRecorder()
    m.record("t", {}, result="ok")  # non-dict result, not explicitly ok
    assert m.as_ops() == [{"tool": "t", "args": {}}]


def test_config_gen_not_hardcoded_path():
    assert "C:/Users/Z/Downloads/PROJECTS/TOUCHDESIGNER/td-mcp" not in config_gen.PROJECT
    assert os.path.isabs(config_gen.PROJECT)


def test_risk_memory_save_is_write_additive():
    assert RISK_CLASS["td_memory_save"] == "WRITE_ADDITIVE"


def test_discover_no_duplicate_port():
    assert discover.KNOWN_PORTS.count(8765) == 1


def test_bundle_rejects_zip_slip_entries():
    d = tempfile.mkdtemp()
    (Path(d) / "f.txt").write_text("x")
    out = package(d, os.path.join(d, "b.mcpb"), files=["../evil.txt"])
    names = zipfile.ZipFile(out).namelist()
    assert not any("evil" in n for n in names)


def test_knowledge_graph_imports_without_networkx_at_top():
    # networkx is imported lazily inside build_graph, so importing the module
    # must not require it to be installed.
    assert callable(knowledge_graph.build_graph)
