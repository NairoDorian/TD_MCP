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
    "health (what TD never calls an error: expressions that raise, dead binds, "
    "failed extensions, cooking off, nothing pulling an operator) -> "
    "capture_viewport (works for any operator family) and observe (is it "
    "moving?). " + _COMMON + " Paths are exact (a wildcard is refused: TD would "
    "pick one arbitrary match); wires join siblings only. set_parameters reads "
    "every write back: ok=false on an invalid menu token (never written: TD would "
    "silently reset the menu to its first item) or a write that raised; notes report clamps, an expression "
    "switched to constant, a write that went through a bind to another operator, "
    "and sequences grown by naming a later block. Annotations (network boxes, "
    "comments) are invisible to list_nodes: use annotate. get_parameters returns eval() "
    "values with expression/bind/export modes. Done means evidence: a captured "
    "frame that is not black/flat, read_chop stats in range. SEE: capture_viewport "
    "= an operator's pixels (judge colour/content here); screenshot(path=...) = its "
    "viewer window as the user sees it; ui_state = what the user is looking at. "
    "SHOW: the user's network editor FOLLOWS your edits (it moves into the "
    "network of the operator you change; results carry `view`); after "
    "execute_python use show_in_editor, which also writes a status-bar message; "
    "open_viewer opens a window -- close every window you open when done. screenshot targets 'td'/'desktop' "
    "capture other applications and need allow_other_apps=true -- only when the "
    "user asks. Never build inside "
    "the td_mcp component (refused); timeline pause is refused because it stops "
    "the bridge."
)

OFFLINE = (
    "TD_MCP offline server: TouchDesigner documentation, a parameter catalog "
    "probed from a running TouchDesigner, and a validator -- no TouchDesigner "
    "needed. " + _COMMON + " td_docs_search for concepts, td_docs_parameter for "
    "one operator's full parameter list, td_validate_build / td_self_heal to "
    "check a network spec (unknown parameters, invalid menu values, family "
    "mismatches) before building it live. Saved .toe/.tox files are read with "
    "TouchDesigner closed: td_project_read (summary, then a network; "
    "format='spec' for build_network), td_project_grep (code and expressions "
    "inside the file), td_project_diff (two saves), td_project_check (names this "
    "build does not have). td_project_edit changes one into a NEW file (never "
    "overwrites; all edits or none; parameter names and menu tokens checked; the "
    "result is re-read to prove it) and td_project_extract saves one COMP as a .tox."
)
