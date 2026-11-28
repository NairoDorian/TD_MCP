"""Suite-wide isolation.

Every test runs with ``TD_MCP_HOME`` pointing at a temporary folder, so
nothing the code under test writes there -- the bridge's call journal and its
blobs, the `.toe` expansion cache -- lands in the developer's real
``~/.td_mcp``. A test that needs its own home sets ``TD_MCP_HOME`` itself.
"""

import pytest


@pytest.fixture(scope="session")
def _suite_home(tmp_path_factory):
    return tmp_path_factory.mktemp("td_mcp_home")


@pytest.fixture(autouse=True)
def _isolated_td_mcp_home(_suite_home, monkeypatch):
    monkeypatch.setenv("TD_MCP_HOME", str(_suite_home))
