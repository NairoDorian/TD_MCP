---
name: td-workflow
description: Load FIRST for any TouchDesigner task done through TD_MCP. The build loop (scout, plan, build, verify, tidy), which TD_MCP tool answers which question (offline docs/catalog vs live bridge), the evidence bar for calling work done, and the map of the other td-* skills.
---

# TouchDesigner work through TD_MCP

Two servers, one loop. The **offline** server (`td_*` tools) answers from docs, a
live-probed parameter catalog and a pure-Python validator: no TouchDesigner needed.
The **live** bridge acts inside a running TouchDesigner, one undo step per call.

## The loop

1. **Scout** -- `project_info` (build, fps, bridge version), then `list_nodes` /
   `scan_network` / `get_connections` on the target COMP. Never assume `/project1`
   holds what you think; never build inside `/project1/td_mcp` (the bridge refuses).
2. **Plan** -- name every operator before creating one. For each unfamiliar type call
   `describe_op_type` (live) or `td_describe_op_type` (offline): it returns the exact
   parameter names, menu tokens and creation defaults. Draft bigger builds as a spec
   and run `td_validate_build` -> `td_self_heal` before touching TouchDesigner.
3. **Build** -- for a network, write the spec once, check it with
   `td_validate_build`, then create it with `build_network`: every operator,
   parameter, wire and flag in one call and one Ctrl+Z, with a per-operator
   report. For single edits: `create_node` (type = class name: `noiseTOP`,
   `lfoCHOP`, `gridPOP`), `set_parameters`, `connect_nodes`, or a `batch`.
4. **Verify** -- `get_errors` with `recurse=true` after every step that matters (it
   reads cook errors, warnings, script tracebacks AND GLSL compile logs). Then look:
   `capture_viewport` (any family) and `observe` (is it actually moving?). Read
   `read_chop` / `read_dat` / `read_pop` stats for data chains. Judge cost with
   `get_performance samples=10` (median per frame): one reading right after a
   build includes one-off costs. A COMP nobody is viewing does not cook at all
   (TD is pull-based), so stale readings there are the truth, not a fault.
5. **Tidy** -- see `td-network-layout`.

## Done means evidence

- Visual work is done when a captured frame shows the intended result -- not when the
  error list is empty. A black or flat verdict is a failure to investigate.
- A data chain is done when `read_chop` stats show the expected range and motion.
- Report what you verified ("errors: 0 recursive; capture not black, std 23"), not
  what you expect.

## Reading tool results

- `set_parameters` reads every write back. `ok:false` with "invalid menu value" means
  TD silently kept the old value -- use one of the tokens it lists. `notes.clamped_to`
  means TD clamped your number; `notes.mode_changed` means you overwrote an expression.
- `get_parameters` reports `eval()` values; `modes` shows expressions / binds / exports.
- Every failure carries `recovery_hints` (`cause`, `action`, `next_tools`): follow them.

## Safety rails

- Each tool call is one Ctrl+Z step in TouchDesigner; say so when a user is nervous.
- `timeline pause` is refused: the bridge's main-thread pump stops with the timeline.
- `execute_python` is for computed or looped work the typed tools cannot express. It
  blocks `open/eval/exec/compile/globals/locals/__import__`; use imports and pathlib.

## Skill map

- `td-building` -- field-verified API traps, Python and threading rules (always useful)
- `td-top-family`, `td-chop-family`, `td-pop-family`, `td-sop-render` -- per family
- `td-dat-python` -- DATs, callbacks, extensions, custom parameters
- `td-glsl` -- GLSL TOP / MAT / POP
- `td-performance` -- cook model, budgets, profiling with `get_performance`
- `td-network-layout` -- placement, colour, comments, final tidy
