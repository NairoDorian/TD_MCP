"""MCP ``instructions`` served at initialize by both TD_MCP servers.

Clients put this text in front of the model before any tool call, so it is the
one piece of guidance an agent gets even when no skills are installed. Kept
short: the loop, the look-before-you-guess rule, and how to read results. The
long form lives in ``skills/td-workflow/SKILL.md``.
"""

_COMMON = (
    "Look before you guess: TouchDesigner abbreviates parameter names "
    "unpredictably (Blur TOP amount is `size`, Noise TOP monochrome is `mono`, "
    "Level TOP brightness is `brightness1`). Read exact names, menu tokens and "
    "defaults with describe_op_type / td_describe_op_type before setting them. "
    "Operator types are TD class names: noiseTOP, lfoCHOP, gridPOP, textDAT, "
    "baseCOMP."
)

LIVE = (
    "TD_MCP live bridge: acts inside a running TouchDesigner; every call is one "
    "undo step. Loop: project_info -> scout (list_nodes / scan_network) -> "
    "describe_op_type for unfamiliar types -> create_node / set_parameters / "
    "connect_nodes (batch for 3+ similar calls) -> get_errors recurse=true -> "
    "capture_viewport (works for any operator family) and observe (is it "
    "moving?). " + _COMMON + " set_parameters reads every write back: ok=false "
    "on an invalid menu token (TD would silently keep the old value); notes "
    "report clamps and discarded expressions. get_parameters returns eval() "
    "values with expression/bind/export modes. Done means evidence: a captured "
    "frame that is not black/flat, read_chop stats in range. Never build inside "
    "the td_mcp component (refused); timeline pause is refused because it stops "
    "the bridge."
)

OFFLINE = (
    "TD_MCP offline server: TouchDesigner documentation, a parameter catalog "
    "probed from a running TouchDesigner, and a validator -- no TouchDesigner "
    "needed. " + _COMMON + " td_docs_search for concepts, td_docs_parameter for "
    "one operator's full parameter list, td_validate_build / td_self_heal to "
    "check a network spec (unknown parameters, invalid menu values, family "
    "mismatches) before building it live."
)
