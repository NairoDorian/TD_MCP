"""KB freshness banner (touchdesigner-wiki-rag freshness idea).

When a ``fetch-manifest.json`` is present next to the corpus, the offline
server can report how stale the local KB is vs the upstream wiki. The manifest
records the last successful fetch timestamp; a ``recentchanges`` check is
attempted only when enabled via ``TD_MCP_FRESHNESS=1`` (cached hourly,
offline-silent) so the zero-dependency default is unchanged.

If the check finds the wiki changed after the manifest timestamp, the server
returns a visible ``⚠ STALE INDEX`` banner the agent should see and can act on
by rebuilding the KB.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

try:
    from td_mcp.kb import corpus as _corpus
except Exception:  # noqa: BLE001
    _corpus = None

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MANIFEST = os.path.join(HERE, "kb", "corpus", "fetch-manifest.json")
CACHE_TTL = 3600  # 1 hour

_cache: dict = {"ts": 0.0, "result": None}


def _manifest_path() -> Optional[Path]:
    env = os.environ.get("TD_MCP_FRESHNESS_MANIFEST")
    p = Path(env) if env else Path(DEFAULT_MANIFEST)
    return p if p.exists() else None


def _load_manifest() -> Optional[dict]:
    p = _manifest_path()
    if p is None:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _recentchanges() -> Optional[dict]:
    """Fetch wiki recentchanges (silently skipped on network/parse failure)."""
    import urllib.request
    url = ("https://docs.derivative.ca/api.php?action=query&list=recentchanges"
           "&rcprop=title|timestamp&rclimit=5&format=json")
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data.get("query", {}).get("recentchanges", [])
    except Exception:  # noqa: BLE001
        return None


def _check_once() -> Optional[dict]:
    """Return freshness info (cached hourly), or None when disabled."""
    global _cache
    if os.environ.get("TD_MCP_FRESHNESS") != "1":
        return None
    now = time.time()
    if now - _cache["ts"] < CACHE_TTL:
        return _cache["result"]
    manifest = _load_manifest()
    if not manifest:
        _cache["ts"] = now
        _cache["result"] = None
        return None
    rc = _recentchanges()
    if rc is None:
        # network failure — never fabricate a stale banner.
        _cache["ts"] = now
        _cache["result"] = None
        return None
    last_fetch = manifest.get("last_fetch") or manifest.get("timestamp")
    changed = []
    for entry in rc:
        ts = entry.get("timestamp")
        if last_fetch and ts and ts > str(last_fetch):
            changed.append(entry.get("title", "?"))
    info = {
        "last_fetch": last_fetch,
        "recent_changes": len(rc),
        "changed_pages": changed[:10],
        "stale": bool(changed),
    }
    _cache["ts"] = now
    _cache["result"] = info
    return info


def freshness_banner() -> str:
    """Return a one-line banner for doc responses, or ``""`` when fresh/off."""
    info = _check_once()
    if not info or not info.get("stale"):
        return ""
    n = len(info.get("changed_pages", []))
    pages = ", ".join(info.get("changed_pages", [])[:5])
    if n > 5:
        pages += f", +{n - 5} more"
    return (f"⚠ STALE INDEX — {n} wiki page(s) changed since "
            f"{info.get('last_fetch')}: {pages}. Rebuild the KB for current docs.")


def reset_freshness_cache():
    """Test helper: clear the hourly cache."""
    global _cache
    _cache = {"ts": 0.0, "result": None}
