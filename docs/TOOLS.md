# Tool reference

**120 tools across two servers.** They are deliberately split, because they
have different requirements and different risks.

| Server | Tools | Needs TouchDesigner | What it is |
|---|---|---|---|
| `td-mcp-offline` | 63 | No | Documentation, RAG, network authoring, validation, scoring, planning, reading saved `.toe`/`.tox` files |
| `td-mcp-live` | 57 | Yes | Everything that touches a running session |

Register only the offline server if you want documentation and offline
authoring without exposing a live session.

---

## Risk classes

Every tool carries an explicit class, mapped to MCP annotations
(`readOnlyHint` / `destructiveHint` / `idempotentHint`). Two tests enforce that
*no tool relies on the default* — an unclassified capability reaching the model
is how an unreviewed tool ships.

| Class | Meaning | Annotations |
|---|---|---|
| `READ_ONLY` | Reads or computes; mutates nothing | readOnly, idempotent |
| `WRITE_ADDITIVE` | Creates or sets; deletes nothing | — |
| `WRITE_CHECKPOINT` | Mutates, but re-running is a no-op | idempotent |
| `DESTRUCTIVE` | Deletes, overwrites, or executes code | destructive |

Enforce a ceiling with `TD_MCP_MAX_RISK=WRITE_ADDITIVE`, or make everything
read-only with `TD_BUILDER_LIVE_READONLY=1` -- checked by the live server, the
offline server and the bridge (from TouchDesigner's environment); a batch is
judged by its steps, an unknown value fails closed. `build_network(replace=true)`,
`annotate(action="delete")` and `save_tox(overwrite=true)` count as
DESTRUCTIVE. See [SECURITY.md](SECURITY.md#risk-classes).

**The four DESTRUCTIVE tools** are `delete_node`, `execute_python`,
`set_dat_text` and `bind_parameter_expression`. The last two are there because
they hand TouchDesigner caller-supplied Python by another name: a parameter
expression is evaluated, and a DAT's text *is* the program for an
Execute/Script DAT. Classifying either below `execute_python` would let a
max-risk policy admit code it meant to block.

---

## Live server — 57 tools

Everything below requires the bridge component running inside TouchDesigner.
See [TOUCHDESIGNER_COMPONENT.md](TOUCHDESIGNER_COMPONENT.md).

**Every path argument names one operator.** `path`, `from_path`, `to_path`,
`other_path`, `new_parent` and `target_parent` are refused when they hold a
wildcard (`*`, `?`, `[`) or are not a string: TouchDesigner's `op()` resolves a
pattern to *one arbitrary match* and a number to the operator with that id, so
a tool would act on an operator nobody named (`error_code: wildcard_path` /
`bad_path`). `*here` and `*this` still work; `find_nodes` takes patterns.
A path that names nothing is `not found` -- it no longer falls back to the
project root.

### Diagnostics

The most important tools in the server, and the ones most recently repaired.
See [DIAGNOSTICS.md](DIAGNOSTICS.md) for the full story.

| Tool | Risk | Notes |
|---|---|---|
| `get_errors` | READ_ONLY | **All four** diagnostic surfaces: cook errors, warnings, script tracebacks, GLSL compile errors. Tagged by `kind`. `errorCount`/`hasErrors` mean *what the network shows red*. A Script operator's traceback is read off its docked callbacks DAT (the operator itself reports nothing); GLSL errors come from `compileResult`, recursively under a COMP. One TouchDesigner message is one diagnostic. |
| `health` | READ_ONLY | What TouchDesigner does **not** call an error, sampled across real frames: expressions that raise (`errors()` stays empty until the next cook), dead binds, failed extensions, COMPs with cooking off, bypassed, never-cooked and idle operators, operators over half a frame, a paused or stalled timeline -- plus every error and warning. `clean` only with no error and no warning. Run it before saying a network works. |
| `clear_script_errors` | WRITE_CHECKPOINT | Script errors persist until cleared — a transient failure keeps badging an operator whose code is already correct. |
| `validate_network` | READ_ONLY | Layout lint (overlaps, origin, docking) plus cook errors. |
| `get_performance` | READ_ONLY | Ranks child cook times to surface hotspots. |

```jsonc
// get_errors on a GLSL TOP that failed to compile
{
  "ok": true, "path": "/project1/glsl1",
  "errors": ["0(12) : error C1008: undefined variable \"half\""],
  "cook_errors": [], "script_errors": [],
  "shader_errors": ["0(12) : error C1008: undefined variable \"half\""],
  "warnings": [],
  "diagnostics": [{ "message": "...", "severity": "error", "kind": "shader" }],
  "errorCount": 1, "hasErrors": true
}
```

### Creating and wiring

| Tool | Risk | Notes |
|---|---|---|
| `create_node` | WRITE_ADDITIVE | Accepts a display name (`"Noise TOP"`) or an opType (`"noiseTOP"`); auto-places in family lanes when no position is given. |
| `build_and_verify` | WRITE_ADDITIVE | create → set → **all four error surfaces** → viewport verdict → offline validate/score/heal, in one call. |
| `connect_nodes` / `disconnect_nodes` | WRITE_ADDITIVE | Wire by path and index. A wire across networks is refused (TouchDesigner would drop the existing wire and report nothing) with the Select operator to use instead; a connector index that does not exist is explained. |
| `delete_node` | **DESTRUCTIVE** | Refuses paths under `TD_MCP_PROTECTED_PATHS`. Reports the rewire TouchDesigner makes (deleting the middle of a chain connects its input to what it fed) under `rewired`, and inputs left empty under `left_unwired`. |
| `annotate` | WRITE_ADDITIVE | Comments, titled boxes and network boxes: `list` / `create` (frame operators named in `around`, or a `rect`) / `update` / `delete`. Annotations are utility operators -- invisible to `list_nodes`, `find_nodes`, `get_node` and `op()` -- so this is the only tool that sees them. `list_nodes` says how many a network has. |
| `rename_node`, `copy_node` | WRITE_ADDITIVE | |
| `auto_layout`, `set_node_position` | WRITE_ADDITIVE | |
| `set_node_color`, `set_node_comment`, `set_flags` | WRITE_ADDITIVE | |
| `batch` | WRITE_ADDITIVE | N ops in one round-trip, capped by `TD_MCP_MAX_BATCH`. One `ui.undo` block. |

### Parameters

| Tool | Risk | Notes |
|---|---|---|
| `get_parameters` | READ_ONLY | |
| `screenshot` | READ_ONLY | What is ON SCREEN as an image the model sees: an operator's floating viewer window (`path`, opened, captured, closed unless `keep_open`), or with `allow_other_apps=true` TouchDesigner's window / a display. Every target is a screen region: menus and windows on top are in the picture. |
| `open_viewer` | WRITE_ADDITIVE | Open (or bring to front) an operator's floating viewer window, as right-click > View...; `close=true` closes it. UI only. |
| `show_in_editor` | WRITE_ADDITIVE | Move the user's network editor to an operator, select and frame it, and show a status-bar message. UI only. |
| `move_window` | WRITE_ADDITIVE | Move TouchDesigner's window or an operator's viewer to a display ('165hz', 'primary', 'secondary', 'DISPLAY1', 'index:N'); defaults to `TD_MCP_MONITOR`. UI only, Windows. |
| `ui_state` | READ_ONLY | What the user is looking at: network per editor pane, current/selected operators, operator under the mouse, window, monitors, perform mode, status bar, timeline. |
| `build_network` | WRITE_ADDITIVE | A whole network in ONE call (one Ctrl+Z): create, parameters (read back), wires, flags, DAT text, left-to-right placement, per-operator report and cook errors. Takes the spec `td_validate_build` checks offline; names are resolved against the catalog first. |
| `describe_op_type` | READ_ONLY | Parameter names, labels, styles, creation defaults and menu tokens for an operator **type**, probed off a throwaway instance in `/sys/quiet` -- which is never cooked, so sequence block parameters that only exist after a first cook (Primitive POP `pt0posx`) are added from the catalog, marked `from_catalog`. Reports `sequences`. Look before you guess. |
| `pulse_parameter` | WRITE_ADDITIVE | Pulse a pulse parameter (reset, reload, start). Setting `true` on one does nothing in TD. |
| `get_parameter_info` | READ_ONLY | Full schema: label, value, **expr**, mode, default, page, style, bounds, menu. The expression *is* the configuration; `value` is one reading of it. A name past the last block of a sequence is reported under `missing` and **not looked up** -- in TouchDesigner the lookup itself would add the blocks. |
| `get_non_default_params` | READ_ONLY | Only what differs from default — the token-efficient read for a large network. |
| `set_parameters` | WRITE_ADDITIVE | Close-match suggestions on a misspelled name. Values: a constant, `{"expr": ...}`, `{"bind": ...}`, `{"mode": "constant", "val": ...}`. Every write is read back: an invalid menu token, a write that raised (a dead bind) or an unknown name makes the call `ok: false`; notes report clamps, an expression switched to constant (its text is kept), a write that went **through a bind** to its master, and sequences grown by naming a later block (`const2value`). Growth of 64+ blocks in one call is refused. |
| `bind_parameter_expression` | **DESTRUCTIVE** | Sets `.expr`, snaps mode to EXPRESSION, evaluates, and returns any **new** cook errors so a bad expression cannot silently no-op. |

### Reading data — reductions, not dumps

A read tool answers *"what did this chain do to my data"*. Returning 4,096 raw
numbers answers that badly and expensively. Every reader reduces by default.

| Tool | Risk | Default | Raw form |
|---|---|---|---|
| `read_chop` | READ_ONLY | per-channel `count/min/max/mean/std` | `format="values"` |
| `diff_chop` | READ_ONLY | min/max/mean/std deltas between two CHOPs | — |
| `read_dat` | READ_ONLY | per-column stats + first/last rows | `format="rows"` |
| `read_pop` | READ_ONLY | attribute metadata; values gated on point count | `format="values"` |
| `read_top` | READ_ONLY | width/height | — |
| `set_dat_text` | **DESTRUCTIVE** | Optimistic concurrency via `expected_sha256` | — |

> **`read_pop` will refuse a large POP.** `POP.points(attr, start, count)`
> accepts both slicing arguments and **ignores them** — TouchDesigner returns
> the whole attribute either way, and a 4,096-point "slice" off a 160k POP
> measures *slower* than reading all of it. There is no cheap partial read, so
> above the ceiling the tool declines and says why rather than stalling for
> four frames. Raise `max_points` deliberately if the stall is acceptable.

### Seeing

| Tool | Risk | Notes |
|---|---|---|
| `capture_viewport` | READ_ONLY | PNG + `is_black` / `is_flat` verdict. Single frame. Returns a real MCP **image block** a client can render, not base64 in the text. |
| `observe` | READ_ONLY | Still **plus** motion: `is_static` across N frames. |

> A network that renders one correct frame and then freezes is structurally
> valid, visually plausible and completely broken. No single-frame verdict can
> see it. `observe` compares 8×8 luminance grids between frames — a scalar mean
> is useless here, because a pattern that scrolls holds its mean exactly
> constant while every pixel changes. `is_static` is **`None`** when too few
> frames were captured to judge: unknown, not false.

### Inspecting the graph

| Tool | Risk | Notes |
|---|---|---|
| `list_nodes`, `find_nodes`, `get_node` | READ_ONLY | |
| `scan_network` | READ_ONLY | Non-default params + errors per node, depth-capped. |
| `map_network` | READ_ONLY | Graphviz DOT of connections, positions, topology. |
| `get_connections` | READ_ONLY | |
| `project_info`, `describe_td_tools` | READ_ONLY | `describe_td_tools` reports `bridge_version` — check it against the package version. |
| `get_resource` | READ_ONLY | `td://` URIs: `chop/<path>`, `node/<path>`, `errors/<path>`, `project`. |

### State and code

| Tool | Risk | Notes |
|---|---|---|
| `snapshot_network` / `restore_network` | WRITE_ADDITIVE | Durable `.tox` checkpoint that survives the undo stack. **Preserves code** — unlike a TDN checkpoint. |
| `export_recipe` / `import_recipe` | WRITE_ADDITIVE | Portable JSON blueprint carrying params, **expressions**, **binds** and **authored DAT text**. |
| `save_tox` | WRITE_ADDITIVE | |
| `timeline` | WRITE_ADDITIVE | status / play / pause / frame / seek / rate. **Pausing is refused by default** — see below. |
| `exec_node_method` | **DESTRUCTIVE** | Gated by the exec flag. Calls any method, `destroy()` included (classed additive before 1.17). |
| `execute_python` | **DESTRUCTIVE** | Gated. Statically lints for TD's global `run()` on a worker thread. |

---

## Offline server — 65 tools

No TouchDesigner required. Every one is a pure function over a description or
the knowledge base.

### Documentation and retrieval

| Tool | What it answers |
|---|---|
| `td_docs_search` | Hybrid BM25 + optional dense + rerank, RRF-fused. The general entry point. |
| `td_docs_operator` | Full parameter and connector spec for one operator, plus its Python class docs. |
| `td_describe_op_type` | Offline twin of `describe_op_type`: exact names, menus and defaults from the live-probed catalog, filtered by name/label glob or page. |
| `td_docs_parameter` | The parameter schema for one operator. **Call this before guessing a name.** |
| `td_docs_family` | Every operator in a family, one line each. |
| `td_docs_compare` | Shared / only-A / only-B parameters between two operators. |
| `td_docs_connections` | Inputs, outputs and related operators. |
| `td_docs_related` | Graph-RAG neighbours (needs `[rag]`). |
| `td_docs_combos` | Operators that work *together* with this one. |
| `td_docs_workflow` | An operator chain for a stated goal. |
| `td_docs_python`, `td_docs_glsl`, `td_docs_glossary` | Python API, GLSL reference, KB index. |
| `td_docs_browse` → `td_docs_read_page` | L0 orient → L2 read a full page. |
| `td_docs_version`, `td_docs_version_info`, `td_docs_version_compat` | Build-gated answers and per-operator `addedIn`/`removedIn`. |
| `td_docs_experimental` | Techniques flagged experimental. |
| `td_docs_template`, `td_kb_stats` | |

Every hit carries a **citation** (`Page § Section` + URL) and a **family
disambiguation warning** when the name collides across families — "Noise" is
four different operators.

### Authoring

| Tool | Notes |
|---|---|
| `td_build_network` | A list of operator specs → diffable TDN YAML, with every type validated against the corpus. |
| `td_build_feedback`, `td_build_audio_reactive`, `td_build_particle`, `td_build_3d_scene`, `td_build_glsl_shader`, `td_build_video_pipeline`, `td_build_midi_rig`, `td_build_kinect_skeleton`, `td_build_led_wall`, `td_build_dmx_fixture` | Known-good starting networks. |
| `td_network_template`, `td_gotcha_template`, `td_glsl_pattern` | Templates, gotcha-annotated templates, paste-ready shaders. |
| `td_scaffold_show`, `td_scaffold_recipe` | Whole-project scaffolds; reusable recipe blueprints. |

### Validating and healing

| Tool | Notes |
|---|---|
| `td_validate_build` | 5-stage validation with recovery hints. |
| `td_validate_live` | The live↔offline bridge: validate + score **with** a `capture_viewport` verdict and a `get_errors` payload, so the score reflects what the network does, not just its shape. |
| `td_score_build` | 0–100 + grade. Weighs structure, viewport verdict **and live diagnostics**. |
| `td_self_heal` | validate → auto-repair → re-assess, iteratively. |
| `td_validate_live` | The live↔offline bridge: takes a viewport verdict *and* a `get_errors` payload. |
| `td_analyze_build` | Liveness: isolated nodes, only-fed nodes, empty COMPs, broken paths. |
| `td_diff_networks`, `td_optimize_layout`, `td_resolve_params` | |

### Planning and production

| Tool | Notes |
|---|---|
| `td_showcontrol_plan` | Art-Net / sACN / OSC / MIDI / timecode. |
| `td_led_map` | LED wall / strip / voxel mapping + DMX channel map. |
| `td_mediaserver` | Resolume / Notch / Disguise / Millumin connectors. |
| `td_show_readiness` | Pre-show checklist. |
| `td_analyze_performance` | fps + per-node cook times → hotspots. |
| `td_compat_check`, `td_discover` | Version compatibility; find running bridges. |
| `td_expert_prompt`, `td_source_registry` | |

### Saved project files

Read a `.toe` or `.tox` **with TouchDesigner closed**. The file is copied into
`~/.td_mcp/cache/toe/` (keyed by its SHA-256) and expanded there with
TouchDesigner's own `toeexpand`; the original is never touched. The reader's
grammar was measured on 2025.33230 over all 768 components that ship with the
install. See [PROJECT_FILES.md](PROJECT_FILES.md).

| Tool | Notes |
|---|---|
| `td_project_read` | No `path`: build, operator counts, top-level networks, code size, files it points at. With `path`: the operators under it with real opTypes, wires, flags and non-default parameters. `text=head` or `full` adds DAT code and tables; `format="spec"` emits a build spec for `td_validate_build` / `build_network`. |
| `td_project_grep` | Regex inside the file: DAT code with line numbers, table cells, parameter values and expressions, names and comments. |
| `td_project_diff` | Two saves compared by meaning: added / removed / retyped / rewired / re-parameterised / re-flagged operators and DAT code diffs; moves counted apart. |
| `td_project_check` | The file against this build's parameter catalog: unknown types, parameter names the build does not have (with close matches), menu values outside the menu, missing absolute file paths. |
| `td_project_edit` | **Edit** a saved file with TouchDesigner closed. The result is a NEW file (`out`, default `<name>.edited.toe`; an existing file is never replaced), packed by TD's `toecollapse` and expanded again to prove it. `edits` is a list of `{op: ...}` applied all or nothing: `set_params`, `reset_params`, `set_text`, `replace_text`, `set_table`, `set_cells`, `replace`, `relink`, `set_flags`, `set_node`, `connect`, `disconnect`, `rename`, `delete`, `add`, `build`, `insert`, `set_project`. Names and menu tokens are checked against the catalog. `new="name"` starts an empty component; `dry_run` writes nothing. WRITE_ADDITIVE. |
| `td_project_extract` | One COMP of a saved file as its own `.tox`; lists parameters that point outside it. WRITE_ADDITIVE. |

### Memory

| Tool | Notes |
|---|---|
| `td_memory_save` / `td_memory_recall` | SQLite FTS5 session memory that survives restarts. |
| `td_history_recall` | Across session memory *and* the technique store. |
| `td_activity_log` | Recent tool calls. |
| `td_tool_batch` | Multiple offline tools in one round-trip. |

---

## Two patterns worth internalising

**Never guess a parameter name.** `td_describe_op_type` / `describe_op_type` cost one call.
`set_parameters` on a wrong name costs a failed write, a confusing error, and a
retry — and if the name is close, the close-match suggestion still needs a
round-trip. This is the single highest-value habit with this server.

**Verify with `build_and_verify`, not `create_node`.** It does
create → set → read all four error surfaces → capture the viewport → run the
offline validate/score/heal pipeline, and returns the lot. `create_node`
returns a path and tells you nothing about whether the thing works.

---

## See also

- [DIAGNOSTICS.md](DIAGNOSTICS.md) — the four error surfaces, in depth
- [AGENT_GUIDE.md](AGENT_GUIDE.md) — how to sequence these tools
- [RAG.md](RAG.md) — how retrieval actually works
- [SECURITY.md](SECURITY.md) — the exec gate and what it covers
