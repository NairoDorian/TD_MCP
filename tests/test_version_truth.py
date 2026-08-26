"""One version, three places (Wave 12).

`pyproject.toml`, `bridge/td_mcp_bridge.py`'s `BRIDGE_VERSION` and the runtime
`td_mcp.__version__` all name the same thing. When they drift, the live server
compares the bridge's stamp against `__version__` and tells the user their
bridge is stale — advice that is wrong and that costs them a re-paste. Found
in the field: an editable install's metadata sat at 1.10.0 while the source
said 1.11.0, and every session warned about a correct bridge.
"""

from __future__ import annotations

import re
from pathlib import Path

import td_mcp
from td_mcp.server_live import version_mismatch_warning

ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1)


def _bridge_version() -> str:
    text = (ROOT / "bridge" / "td_mcp_bridge.py").read_text(encoding="utf-8")
    return re.search(r'^BRIDGE_VERSION = "([^"]+)"', text, re.M).group(1)


def test_runtime_version_matches_the_source():
    """Stale editable-install metadata must not win over the checkout."""
    assert td_mcp.__version__ == _pyproject_version()


def test_bridge_stamp_matches_the_package():
    assert _bridge_version() == _pyproject_version()


def test_a_matching_bridge_produces_no_warning():
    assert version_mismatch_warning(_bridge_version()) is None


def test_a_genuinely_stale_bridge_still_warns():
    warning = version_mismatch_warning("0.0.1")
    assert warning and "0.0.1" in warning


def test_an_unstamped_bridge_warns_about_its_age():
    warning = version_mismatch_warning(None)
    assert warning and "predates version stamping" in warning


def test_source_version_is_preferred_over_metadata(monkeypatch):
    """The mechanism, not just the outcome."""
    assert td_mcp._source_version() == _pyproject_version()
    monkeypatch.setattr(td_mcp, "_source_version", lambda: None)
    # With no source tree the resolver falls back to installed metadata and
    # must still produce something rather than raising.
    assert td_mcp._resolve_version()
