"""Snapshot provenance + bridge/server version handshake.

- snapshot provenance on doc lookups  (Pantani/tdmcp #150, 2026-08-11)
- bridge/server version mismatch      (quentinR2/TouchDesignerMcp #8, 2026-08-18)

Both exist for the same reason: an agent that cannot tell "my snapshot is
stale" from "this does not exist" will state the wrong one as fact.
"""

from __future__ import annotations

import pytest

from td_mcp import server_live
from td_mcp.kb import corpus


# --- snapshot provenance --------------------------------------------------

def test_snapshot_version_reports_real_corpus_contents():
    version = corpus.snapshot_version()
    assert "operators @" in version
    assert str(len(corpus.load_operators())) in version


def test_known_operator_is_marked_found_in_snapshot():
    lookup = corpus.operator_lookup("Noise TOP")
    assert lookup["found"] is True
    assert lookup["lookup_status"] == "found_in_snapshot"
    assert lookup["record"]["name"] == "Noise TOP"
    assert lookup["data_version"]


def test_unknown_operator_miss_is_not_an_absence_claim():
    lookup = corpus.operator_lookup("Flurbulator TOP")
    assert lookup["found"] is False
    assert lookup["lookup_status"] == "not_in_snapshot"
    # The wording is the whole point: a miss must not read as non-existence.
    assert "does not prove" in lookup["snapshot_notice"]
    assert lookup["data_version"]


def test_miss_suggests_the_nearest_real_names():
    assert "Blur TOP" in corpus.operator_lookup("Blurr TOP")["suggestions"]


def test_wrong_family_suffix_still_gets_suggestions():
    # "Blur COP" is a plausible slip (TD has no COP family); difflib alone
    # scores it below cutoff, so the leading-word fallback has to carry it.
    assert "Blur TOP" in corpus.nearest_operator_names("Blur COP")


def test_nearest_names_are_bounded_and_real():
    names = corpus.nearest_operator_names("Noise", k=3)
    assert len(names) <= 3
    operators = corpus.load_operators()
    labels = {r.get("displayName") or r.get("name") for r in operators.values()}
    assert set(names) <= labels


def test_empty_query_suggests_nothing():
    assert corpus.nearest_operator_names("") == []
    assert corpus.nearest_operator_names("   ") == []


def test_docs_operator_prints_the_snapshot_warning_on_a_miss():
    from td_mcp.server_offline import td_docs_operator

    out = td_docs_operator("Flurbulator TOP")
    assert "Not in the documentation snapshot" in out
    assert "does not prove" in out


def test_docs_operator_cites_the_snapshot_on_a_hit():
    from td_mcp.server_offline import td_docs_operator

    out = td_docs_operator("Noise TOP")
    assert "Not in the documentation snapshot" not in out
    assert "Source: documentation snapshot" in out


# --- bridge/server version handshake -------------------------------------

def test_matching_versions_produce_no_warning():
    assert server_live.version_mismatch_warning("1.10.0", "1.10.0") is None


def test_mismatch_names_both_versions_and_the_fix():
    warning = server_live.version_mismatch_warning("1.2.0", "1.10.0")
    assert "v1.2.0" in warning and "v1.10.0" in warning
    assert "Re-paste" in warning


def test_unstamped_bridge_warns_that_it_predates_stamping():
    warning = server_live.version_mismatch_warning(None, "1.10.0")
    assert "unknown" in warning
    assert "v1.10.0" in warning


@pytest.mark.parametrize("server_version", ["", "0.0.0"])
def test_unknown_server_version_never_warns(server_version):
    # Running from a checkout without an installed dist: __version__ falls
    # back to "0.0.0", so we do not know our own version and are in no
    # position to call the bridge stale.
    assert server_live.version_mismatch_warning("1.2.0", server_version) is None
    assert server_live.version_mismatch_warning(None, server_version) is None


def test_server_version_is_resolved_at_call_time(monkeypatch):
    # Binding __version__ as a default argument would freeze it at import and
    # silently ignore the installed package's real version.
    monkeypatch.setattr(server_live, "__version__", "9.9.9")
    assert "v9.9.9" in server_live.version_mismatch_warning("1.2.0")
    assert server_live.version_mismatch_warning("9.9.9") is None


class _FakeClient(server_live.TDClient):
    """TDClient with the network replaced by a scripted bridge."""

    def __init__(self, responses, bridge_version="1.10.0"):
        super().__init__()
        self._responses = responses
        self._bridge_version = bridge_version
        self.calls = []

    def _call(self, tool, args=None):
        self.calls.append(tool)
        if tool == "describe_td_tools":
            result = {"ok": True, "tools": []}
            if self._bridge_version is not _MISSING:
                result["bridge_version"] = self._bridge_version
        else:
            result = dict(self._responses.get(tool, {"ok": True}))
        self._note_bridge_version(tool, result)
        return result


_MISSING = object()


def test_stale_bridge_warning_rides_on_an_ordinary_tool_result(monkeypatch):
    monkeypatch.setattr(server_live, "__version__", "1.10.0")
    client = _FakeClient({"list_nodes": {"ok": True, "nodes": []}},
                         bridge_version="1.2.0")
    result = client._call("list_nodes")
    assert "v1.2.0" in result["warning"]
    assert result["nodes"] == []


def test_matching_bridge_adds_no_warning(monkeypatch):
    monkeypatch.setattr(server_live, "__version__", "1.10.0")
    client = _FakeClient({"list_nodes": {"ok": True}}, bridge_version="1.10.0")
    assert "warning" not in client._call("list_nodes")


def test_handshake_probe_runs_once_per_client(monkeypatch):
    monkeypatch.setattr(server_live, "__version__", "1.10.0")
    client = _FakeClient({"list_nodes": {"ok": True}}, bridge_version="1.2.0")
    for _ in range(4):
        client._call("list_nodes")
    # One probe total, not one per call.
    assert client.calls.count("describe_td_tools") == 1


def test_unstamped_bridge_does_not_probe_forever(monkeypatch):
    # A pre-stamp bridge answers describe_td_tools without bridge_version.
    # Recording that as "unknown" is what stops the probe re-firing every call.
    monkeypatch.setattr(server_live, "__version__", "1.10.0")
    client = _FakeClient({"list_nodes": {"ok": True}}, bridge_version=_MISSING)
    first = client._call("list_nodes")
    for _ in range(3):
        client._call("list_nodes")
    assert "unknown" in first["warning"]
    assert client.calls.count("describe_td_tools") == 1


def test_describe_itself_carries_the_warning(monkeypatch):
    monkeypatch.setattr(server_live, "__version__", "1.10.0")
    client = _FakeClient({}, bridge_version="1.2.0")
    result = client._call("describe_td_tools")
    assert "v1.2.0" in result["warning"]


def test_existing_warning_is_not_overwritten(monkeypatch):
    monkeypatch.setattr(server_live, "__version__", "1.10.0")
    client = _FakeClient({"list_nodes": {"ok": True, "warning": "THREADING WARNING: ..."}},
                         bridge_version="1.2.0")
    # A tool's own warning outranks the version notice; clobbering it would
    # trade a real hazard report for a housekeeping note.
    assert client._call("list_nodes")["warning"].startswith("THREADING WARNING")
