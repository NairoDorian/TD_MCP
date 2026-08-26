"""Protocol version negotiation, in the bridge's own transport.

Wave 12 first put this in ``td_mcp/streamable_http.py`` -- a module the bridge
could not import inside TouchDesigner, and whose ``_handle_jsonrpc`` the
bridge's own copy shadowed anyway. The negotiation never ran; the bridge kept
answering ``2026-07-28`` to every client. It is in the bridge now, and these
tests drive the bridge.
"""

from __future__ import annotations

import pytest

from tests.bridge_pure import load
from tests.td_double import build_bridge


@pytest.fixture(scope="module")
def pure():
    return load()


class TestNegotiation:
    def test_a_supported_version_is_echoed(self, pure):
        for version in pure["SUPPORTED_PROTOCOL_VERSIONS"]:
            assert pure["negotiate_protocol_version"](version) == (version, True)

    def test_an_unknown_version_falls_back_to_the_newest(self, pure):
        """MCP: a server that does not support the requested version 'SHOULD
        respond with the latest version it supports' -- newest, not oldest."""
        version, exact = pure["negotiate_protocol_version"]("2099-01-01")
        assert version == pure["SUPPORTED_PROTOCOL_VERSIONS"][0]
        assert exact is False

    def test_a_missing_version_falls_back(self, pure):
        version, exact = pure["negotiate_protocol_version"](None)
        assert version == pure["DEFAULT_PROTOCOL_VERSION"] and exact is False

    def test_supported_versions_are_listed_newest_first(self, pure):
        versions = list(pure["SUPPORTED_PROTOCOL_VERSIONS"])
        assert versions == sorted(versions, reverse=True)


class TestCapabilities:
    def test_advertises_exactly_what_the_handler_serves(self, pure):
        assert set(pure["server_capabilities"]("2026-07-28")) == {
            "tools", "resources", "prompts"}

    def test_logging_is_never_advertised(self, pure):
        """The bridge implements no logging/setLevel. Advertising a capability
        that is not there invites a call that fails."""
        for version in pure["SUPPORTED_PROTOCOL_VERSIONS"]:
            assert "logging" not in pure["server_capabilities"](version)


class TestInitializeResponse:
    """Drives the bridge's real `_Handler._handle_jsonrpc`."""

    @pytest.fixture()
    def handler(self):
        br, _ = build_bridge()
        cls = br["_Handler"]
        return cls.__new__(cls), br

    def _initialize(self, handler, requested):
        h, _ = handler
        payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": requested}}
        return h._handle_jsonrpc(payload, "sid", {})["result"]

    def test_modern_client_gets_its_version(self, handler):
        result = self._initialize(handler, "2026-07-28")
        assert result["protocolVersion"] == "2026-07-28"
        assert "instructions" not in result

    def test_older_client_gets_its_version_too(self, handler):
        """The bug: every client was answered 2026-07-28 regardless."""
        assert self._initialize(handler, "2024-11-05")["protocolVersion"] == "2024-11-05"

    def test_unknown_version_is_told_what_is_supported(self, handler):
        result = self._initialize(handler, "2099-01-01")
        assert result["protocolVersion"] == "2026-07-28"
        assert "2024-11-05" in result["instructions"]

    def test_server_info_carries_the_bridge_version(self, handler):
        _, br = handler
        info = self._initialize(handler, "2026-07-28")["serverInfo"]
        assert info["name"] == "td-mcp-bridge"
        assert info["version"] == br["BRIDGE_VERSION"]

    def test_capabilities_match_the_methods_served(self, handler):
        caps = self._initialize(handler, "2026-07-28")["capabilities"]
        assert set(caps) == {"tools", "resources", "prompts"}
