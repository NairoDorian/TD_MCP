"""Output / token budgeting (TD_Builder_alpha output_budget idea).

Large tool responses (full operator specs, RAG hits, network dumps) can blow the
model context window. Instead of silently truncating mid-payload, we cap the
serialized size and return an explicit ``_truncated`` marker reporting how much
was omitted — matching td-mcp's "never silently truncate" philosophy.

Pure stdlib; environment-tunable caps.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Tuple

# Env-tunable caps. None = unlimited.
DEFAULT_MAX_CHARS = int(os.environ.get("TD_MCP_OUTPUT_MAX_CHARS", "0") or "0") or 0
DEFAULT_MAX_BYTES = int(os.environ.get("TD_MCP_OUTPUT_MAX_BYTES", "0") or "0") or 0


def _measure(obj: Any) -> int:
    try:
        return len(json.dumps(obj, default=str, ensure_ascii=False).encode("utf-8"))
    except Exception:  # pragma: no cover - defensive
        return len(str(obj).encode("utf-8"))


def truncate_text(text: str, max_chars: int = 0) -> Tuple[str, bool, Dict[str, Any]]:
    """Truncate a string to ``max_chars`` (0 = no limit).

    Returns ``(text, truncated, info)`` where ``info`` reports the original
    length and omitted character count when truncation occurred.
    """
    if not max_chars or len(text) <= max_chars:
        return text, False, {"truncated": False}
    info = {
        "truncated": True,
        "original_chars": len(text),
        "kept_chars": max_chars,
        "omitted_chars": len(text) - max_chars,
    }
    return text[:max_chars].rstrip() + "\n…[truncated]", True, info


def budget_payload(data: Any, max_bytes: int = 0) -> Tuple[Any, bool, Dict[str, Any]]:
    """Cap a structured payload to ``max_bytes`` (0 = no limit).

    When over budget, list-typed leaves are trimmed from the tail (keeping a
    count of what was omitted) rather than dropping the whole payload. Returns
    ``(payload, truncated, info)``.
    """
    if not max_bytes:
        return data, False, {"truncated": False}

    if _measure(data) <= max_bytes:
        return data, False, {"truncated": False}

    def _shrink(node: Any, budget: int) -> Any:
        if isinstance(node, dict):
            kept = {}
            used = 0
            omitted = 0
            for k, v in node.items():
                child = _shrink(v, max(0, budget - used))
                sz = _measure(child)
                if used + sz <= budget or not kept:
                    kept[k] = child
                    used += sz
                else:
                    omitted += 1
            if omitted:
                kept["_omitted_fields"] = omitted
            return kept
        if isinstance(node, list):
            kept = []
            used = 0
            for v in node:
                child = _shrink(v, max(0, budget - used))
                sz = _measure(child)
                if used + sz <= budget or not kept:
                    kept.append(child)
                    used += sz
                else:
                    break
            if len(kept) < len(node):
                kept.append({"_truncated": True, "kept": len(kept), "omitted": len(node) - len(kept)})
            return kept
        return node

    shrunk = _shrink(data, max_bytes)
    info = {
        "truncated": True,
        "full_bytes": _measure(data),
        "cap_bytes": max_bytes,
    }
    return shrunk, True, info


def budget_text(text: str, max_chars: int = 0) -> Tuple[str, bool, Dict[str, Any]]:
    """Convenience wrapper around :func:`truncate_text` honoring env caps."""
    cap = max_chars or DEFAULT_MAX_CHARS
    return truncate_text(text, cap)
