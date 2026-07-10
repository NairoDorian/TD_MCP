"""Recovery hints — Embody-style self-healing for every error.

Every mutating tool in the live bridge can fail: a node may not exist, a
parameter nickname may be wrong, a connection may be rejected, or TouchDesigner
may be unreachable. Instead of returning a bare ``{"ok": False, "error": ...}``
that forces the agent to guess, we attach a structured ``recovery`` block:

    {"cause": "...", "action": "...", "next_tools": ["get_errors", ...]}

so the agent can self-correct instead of retrying blindly. This is a pure,
dependency-free catalog: match an error string against known signatures and
return the most specific hint.

Run:  uv run python -m tests.test_recovery
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

# Each rule: (regex, cause, action, next_tools)
_RULES: List[tuple] = [
    (
        r"(connection refused|econnrefused|failed to connect|no connection|timed out|etimedout)",
        "The TouchDesigner bridge is not reachable on the configured host/port.",
        "Confirm the bridge Text DAT is running (`op('text1').module.start()`) and that TD_MCP_PORT matches. Verify the auth token is set.",
        ["project_info", "describe_td_tools"],
    ),
    (
        r"(enotfound|name or service not known|getaddrinfo)",
        "The bridge host could not be resolved.",
        "Use the loopback address 127.0.0.1 rather than a hostname, or fix TD_MCP_HOST.",
        ["project_info"],
    ),
    (
        r"(node|operator|op).{0,24}(not found|cannot find|does not exist|no such)",
        "The target path does not resolve to a node in the current network.",
        "List children with list_nodes/scan_network and check the exact path; resolve *here/*this if you used a spatial pointer.",
        ["list_nodes", "scan_network", "find_nodes"],
    ),
    (
        r"(parameter|par|prop).{0,24}(not found|unknown|invalid|no (such|attribute)|not a known)",
        "A parameter nickname/name was not recognized by the operator.",
        "Look up the exact parameter name with td_docs_parameter (offline) or get_parameters on the node, then re-issue set_parameters.",
        ["get_parameters", "td_docs_parameter"],
    ),
    (
        r"(cook error|cook failed|exception|traceback|error in)",
        "The node raised a cook/Python error after the change.",
        "Read get_errors to see the message, fix the offending parameter or script, then re-verify with build_and_verify.",
        ["get_errors", "build_and_verify", "validate_network"],
    ),
    (
        r"(cannot connect|connection rejected|incompatible|type mismatch|wrong (family|type))",
        "The attempted wire was rejected — the source/target families or ports are incompatible.",
        "Check valid inputs/outputs with td_docs_connections, and verify the output/input indices exist before reconnecting.",
        ["td_docs_connections", "get_connections", "connect_nodes"],
    ),
    (
        r"(read.?only|permission|unauthorized|forbidden|401|403)",
        "The request was blocked by the bridge's policy or auth.",
        "Check the Bearer token (TD_MCP_AUTH_TOKEN) and any TD_MCP_MAX_RISK / read-only policy in force.",
        ["describe_td_tools"],
    ),
    (
        r"(already exists|duplicate|name clash|name in use)",
        "A node with that name already exists in the parent network.",
        "Pick a unique name or use rename_node / copy_node to a distinct target.",
        ["rename_node", "find_nodes", "list_nodes"],
    ),
    (
        r"(exec|execute).{0,12}(disabled|blocked|not allowed|TD_MCP_ALLOW_EXEC)",
        "Arbitrary Python execution is disabled on the bridge.",
        "Enable it via the TD_MCP_ALLOW_EXEC bridge flag, or achieve the same result with dedicated tools (set_parameters, exec_node_method).",
        ["set_parameters", "exec_node_method"],
    ),
    (
        r"(parse error|json|yaml|syntax|invalid literal)",
        "The supplied spec/recipe could not be parsed.",
        "Validate the JSON/YAML (e.g. via td_build_network which checks every operator type) before importing.",
        ["td_build_network", "import_recipe"],
    ),
    (
        r"(black|flat|fully.?transparent|blank|empty render)",
        "The rendered output looks empty/dead (is_black/is_flat verdict).",
        "Check upstream connections, the node's input, and known-good defaults; iterate with build_and_verify and caption_viewport.",
        ["build_and_verify", "capture_viewport", "get_connections", "validate_network"],
    ),
    (
        r"(timeout|too (long|slow)|deadlock)",
        "The operation exceeded the bridge timeout (heavy cook or large network).",
        "Reduce scope (lower depth in scan_network/export_recipe), or raise the client timeout.",
        ["get_performance", "scan_network"],
    ),
]

_COMPILED = [(re.compile(p, re.IGNORECASE), cause, action, tools)
             for (p, cause, action, tools) in _RULES]


def recovery_hint(error: str, context: Optional[Dict] = None) -> Dict[str, object]:
    """Return the best-matching recovery hint for an error string.

    ``context`` may carry ``{"tool": <name>}`` so tool-specific next_tools can
    be preferred. Always returns a well-formed dict (never raises).
    """
    text = error or ""
    best: Optional[tuple] = None
    for rule in _COMPILED:
        pat, cause, action, tools = rule
        if pat.search(text):
            # Prefer the first (most specific) match, but if a later rule shares
            # the same leading keyword family, keep the earliest for stability.
            best = (cause, action, tools)
            break
    if best is None:
        return {
            "cause": "Unrecognized error from the bridge or TouchDesigner.",
            "action": "Inspect the raw error, then use describe_td_tools / get_errors to gather context before retrying.",
            "next_tools": ["describe_td_tools", "get_errors"],
        }
    cause, action, tools = best
    out = {"cause": cause, "action": action, "next_tools": list(tools)}
    if context and context.get("tool"):
        out["tool"] = context["tool"]
    return out


def attach_recovery(result: Dict, *, tool: Optional[str] = None,
                    error_key: str = "error") -> Dict:
    """Enrich a result dict in place with a recovery hint when it failed.

    Returns the same dict (mutated) for convenient chaining. If the result is
    already OK or has no error string, it is left untouched.
    """
    if not isinstance(result, dict):
        return result
    if result.get("ok") is False or result.get("ok") is None and result.get(error_key):
        err = result.get(error_key)
        if isinstance(err, str) and err.strip():
            result["recovery"] = recovery_hint(err, {"tool": tool})
    return result


def attach_to_error(err: str, *, tool: Optional[str] = None) -> Dict:
    """Build a standard failed result dict with an attached recovery hint."""
    return attach_recovery({"ok": False, "error": err}, tool=tool)
