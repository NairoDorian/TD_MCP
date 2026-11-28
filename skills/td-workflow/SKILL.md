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
   Network boxes and comments are invisible to those tools: `annotate(action='list')`
   reads them (`list_nodes` says when there are some). A saved `.toe`/`.tox` can be
   read with TouchDesigner closed: `td_project_read` (what is in it, then one
   network), `td_project_grep` (code and expressions inside it), `td_project_check`
   (names this build no longer has) -- and changed with it closed:
   `td_project_edit` writes a NEW file (all edits or none, names and menu tokens
   checked; review the `changed` it returns), `td_project_extract` saves a COMP
   as a `.tox`. Edit offline only when TouchDesigner does not have the file open:
   a running TouchDesigner saving it later would write over the edit.
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
   reads cook errors, warnings, script tracebacks AND GLSL compile logs), and
   `health` before saying it works: it finds what TouchDesigner never calls an
   error -- an expression that raises (errors stay empty until the next cook), a
   dead bind, a failed extension, cooking switched off, an operator nothing pulls.
   Then look:
   `capture_viewport` (any family) and `observe` (is it actually moving?). Read
   `read_chop` / `read_dat` / `read_pop` stats for data chains. Judge cost with
   `get_performance samples=10` (median per frame): one reading right after a
   build includes one-off costs. A COMP nobody is viewing does not cook at all
   (TD is pull-based), so stale readings there are the truth, not a fault.
5. **Tidy** -- see `td-network-layout`; label sections with `annotate(action='create',
   around=[...], title=...)`.

## See and show

- **Judge content from pixels**: `capture_viewport` returns an operator's own
  image (any family). A viewer window can apply a display mode, so colours in a
  screenshot are not the data.
- **See what the user sees**: `ui_state` (their network, selection, the node
  under the mouse) first; `screenshot(path=...)` for an operator's viewer window.
- **Show, don't describe**: `show_in_editor` moves their editor to an operator,
  selects and frames it, and writes a status-bar line; `open_viewer` opens the
  result in its own window. Guiding a user: load `td-guide-user`.

## Done means evidence

- Visual work is done when a captured frame shows the intended result -- not when the
  error list is empty. A black or flat verdict is a failure to investigate.
- A data chain is done when `read_chop` stats show the expected range and motion.
- Report what you verified ("errors: 0 recursive; capture not black, std 23"), not
  what you expect.

## Reading tool results

- `set_parameters` reads every write back. `ok:false` with "invalid menu value" means
  the token was not written (TD would have reset the menu to its first item) -- use one
  of the tokens it lists. A StrMenu (Select CHOP `channames`) takes any text. `notes.clamped_to`
  means TD clamped your number; `notes.mode_changed` means you overwrote an expression
  (its text is kept, inactive); `notes.written_through_bind` means the parameter is
  bound and the value landed on ANOTHER operator, its bind master. Bind with
  `{"bind": "op('ctrl').par.Speed"}`; unbind while setting with
  `{"mode": "constant", "val": 1}`.
- Sequence parameters are `<sequence><block><field>` (`const0name`, `pt1posx`).
  Naming a higher block adds blocks (`notes.sequences_grown`), so count before you
  write; `describe_op_type` reports each type's `sequences`.
- Paths are exact: a wildcard is refused (TouchDesigner would pick one arbitrary
  match). Use `find_nodes(query='noise*')` to expand a pattern first.
- Wires join siblings only; to use an operator from another COMP, reference it
  with a Select operator (`connect_nodes` says which parameter).
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
- `td-guide-user` -- helping a person through TouchDesigner: see, point, show
