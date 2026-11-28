# Upstream sync — 2026-11-28

A working session rather than a pull: the references were pulled on
2026-11-27 (`UPSTREAM_SYNC_2026-11-27.md`). This one built what that sync left
as its biggest open item -- **editing saved `.toe`/`.tox` files** -- and mined
three references that were still unread. Every statement about TouchDesigner
below was measured on **2025.33230** in TEST-01 before it was coded or
written down; the references supplied questions, not facts.

## Editing saved files: what was measured

| Fact (2025.33230) | How it was measured | Used in |
|---|---|---|
| `toecollapse name.toe` packs `name.toe.dir` + `name.toe.toc`; with `name.toe` present it first renames it to `name.toe.bkp1`, `.bkp2`... | four collapses in a scratch folder | the editor collapses in a private folder and delivers with an exclusive create, so nothing is ever renamed or replaced |
| `toecollapse` is deterministic, and `toeexpand(toecollapse(tree)) == tree` byte for byte, `.build` included | collapsed twice (identical), expanded the result, `diff -r` | every produced file is re-expanded and compared before it is delivered |
| A field is bare, or double-quoted when empty or holding white space; `\` and `"` are backslash-escaped in **both** forms; a field with a non-ASCII character is preceded by a UTF-8 BOM | 26 hand-picked strings set on custom parameters, saved, expanded (quotes, backslashes, tabs, newlines, CR LF, CJK, a lone `"`) | `toe_writer.format_field`; the reader now unescapes bare fields too (1,545 shipped expressions and 559 values read with their escapes left in before) |
| The writer re-renders **806,301 of 806,301** non-legacy parameter records of the 768 shipped components byte for byte | `toe_writer.selftest` over the expansions | `python -m td_mcp.toe_writer --selftest DIR` |
| DAT text is a 1 x 1 table: `2\n*` + `1, 1, 1, 1` + cell `(2, length, bytes)`; an empty Text DAT saved by 2025 has a zero-length cell (8 shipped files have none) | probe DATs saved and read | `text_payload`; nyan's guide calls the length a uint16 (wrong past 64 KB) |
| Custom parameter type codes: low byte = style for every `Page.append*` method (25 styles), bits 8-11 = component count, bits 12-15 = naming (1 numbered, 2 rgba, 3 xyzw, 4 uvw, 5 wh), bit 31 = starts a section | one custom parameter of each kind saved | `toe_reader.parse_cparms` (`Pos` is now `XYZ` with names `Posx Posy Posz`, was "Float of 3") |
| A new parameter line set by a script: flags `0` (built-in), `0x04000040` (custom); `0x80` disabled, `0x100` read-only | probe saves | new lines get exactly those; existing lines keep every bit |
| A COMP made by `create()` is saved with `render on display on pickable on` (Actor, Bullet Solver, FBX, Geometry, Geo Text, glTF in/out, USD) or `display on pickable on` (Blend, Bone, Camera, Camera Blend, Environment Light, Handle, Light, Null); every other operator with no flag | every COMP type created in `/sys/quiet` (none cooked) | `add` writes the same flags -- without them an added Geometry COMP would neither render nor display |
| TouchDesigner builds an inner network in Animation (8), Annotate (1), Button (5), Slider (5), Field (6), Table (12), Bone (10), Handle (11), Bullet Solver (2), FBX (5), glTF in (6) and USD (6) COMPs at creation | same probe, `len(children)` | `add` refuses them (they would be empty shells); `insert` a saved `.tox` instead |
| A Geometry COMP made by `create()` holds `torus1`, a **Torus POP**, which renders | saved a fresh one | `add` says the offline one is empty; `glsl_patterns` "Geometry COMP starts empty" corrected |
| Naming a later sequence block in a file grows the sequence when it opens; the blocks skipped take block-0 defaults -- a Constant CHOP given only `const3name` opened with `const2name = chan1`, a second `chan1` | edited file loaded in TouchDesigner | `set_params` names the skipped blocks and the name they repeat |
| A value over a bind in a file: the parameter opens as a constant with the bind text kept | edited file loaded | documented |

Proved the other way round -- TouchDesigner reading what the editor wrote:
`scripts/live_toe_edit.py` **18/18** (a component built and saved by
TouchDesigner, edited offline, loaded back: constants, expressions, an
untouched bind following its edited master, escaped and non-ASCII strings, a
menu, an XYZ, a grown sequence, DAT text, a table cell, a rename, a new wire,
an added Blur TOP, an added Geometry COMP rendering a built box, no errors).
A `.toe` edited offline -- cook rate 60 -> 30, an added Noise TOP, a changed
constant -- opened as the project with the bridge running from it; it was
closed without saving and deleted.

## Menus, re-measured

| Fact (2025.33230) | Before | Now |
|---|---|---|
| An unknown token on a **Menu** raises nothing and moves it to its **first item** (Noise TOP `type` random -> 'nosuch' -> perlin2d; Transform TOP `extend`, Level TOP `clampinput`, LFO CHOP `wavetype`, Grid SOP `surftype`, Constant TOP `outputresolution`, Noise CHOP `type` alike). An out-of-range `menuIndex` is ignored | documented (2026-11-22) as "silently ignored, old value kept" -- measured on a menu already at its first item | `set_parameters` never writes an unknown token (`not_written`); the double, docs, instructions and skill say what TD does |
| A **StrMenu** (Select CHOP `channames`, Rename CHOP `renamefrom`; 1,685 catalog parameters) reports `isMenu` True and lists suggestions (`*`) but keeps any text; an int is stored as the text `'0'`, not an index | the bridge reported `channames='tx ty'` as an invalid token; `td_validate_build` flagged it; `td_self_heal` reset it to `*` | free text everywhere; real Menus still checked |
| `project.cook` does not exist; `root.time.play/rate/frame`, `project.cookRate`, `project.realTime` do; `absTime.frame` = `absTime.seconds` x cook rate and does not loop with the timeline | the knowledge base described `project.cook.play/rate/frame` | `build_kb.py` corrected |

## References mined this session

| Repo | What was read | Verdict |
|---|---|---|
| skylarbarrera/nyan (no licence) | `docs/toeexpand-editing-guide.md`, `.claude/rules/*` | the idea of editing + collapsing; three of its byte rules were wrong here (see `PICKED_FROM.md`) |
| JazibAli360/jazib-td-mcp (licence unclear) | all 3,391 lines of `LEARNINGS.md` (macOS, 2025.31550), `docs/USING_TD.md`, `AUDIT.md` | 62 claims extracted; two probed and used (StrMenu, and through it the menu reset); the rest are leads below |
| jlopesmo/TDflowMCP, GaNotchVFX/touchbridge, grigabyte/td-atlas | journal, memory, permission and save designs, compared with TD_MCP's code | design recorded below; nothing built yet |

## Backlog — leads, NOT facts (probe each before use)

**From the design comparison (TD_MCP code read, file:line checked by the reader):**

- ~~Call journal with old values~~ -- built in 1.18.0 (`td_journal`,
  `scripts/live_journal.py` 9/9, twice in one session). Not taken from the design: a conflict
  check before a revert (the plan is built from the journal alone and is
  reviewed before it is sent), and a host-side query on the live server.
- ~~Documented gates with no code behind them, the snapshot "sandbox",
  protected-path parents, unfenced tools, `exec_node_method` classed
  additive, `save_tox` writing any path, colliding snapshot names,
  `/api/resource` without a token~~ -- all confirmed and fixed in 1.17.0
  (see below).
- Memory: stemmed FTS hits dropped by the overlap filter, a tie can raise
  comparing `sqlite3.Row`, tags not searchable, no project scope, no delete,
  `TD_MCP_MEMORY_DB` overriding an explicit path; `activity.py` entries carry
  no timestamp; the in-TD agent's session save/load pass a string where a dict
  is read.

**From jazib-td-mcp's LEARNINGS.md (macOS 2025.31550 -- re-test on Windows):**

- Parameter Execute DAT callbacks run after the script that set the parameter
  returns; a test that sets and reads in one call sees nothing.
- Expressions only depend on what was evaluated: `X if op('p') is not None
  else Y` keeps Y after `p` appears.
- The network editor pane is a demand source: every operator with its viewer
  on cooks while the pane shows its COMP -- the bridge follows edits into the
  COMP, so `get_performance` right after an edit may measure viewer pulls.
- Rewriting an Execute DAT's text from Python may stop its callbacks until
  `active` is toggled (matters for `set_dat_text`).
- A text DAT inside a cooking-disabled COMP keeps its old compiled module.
- `findChildren(type=geometryCOMP)` also returns Null and Light COMPs.
- glslTOP: `sTD2DInputs` is sized to the wired inputs (indexing past it fails
  at compile time); `inputextenduv` defaults to `zero`; three input
  connectors from Python, more textures through `tops`.
- toptoCHOP `crop` defaults to one row; dattoCHOP `firstcolumn` defaults to
  `names`; a new Table DAT has one empty cell.
- Composite TOP `useinput` resolution follows its `size` (default input 2).
- ndiout/ndiin/syphonspoutout TOPs are created active (they open sockets).
- Floats under ~1e-5 evaluating to 0; `-7 // 2` giving -3 in simple
  expressions; OP-reference parameters on a COMP resolving from its parent --
  doubtful, probe first.

## 1.17.0 — safety controls, measured and enforced

| Fact (2025.33230, TEST-01) | Fix |
|---|---|
| `GET /` with `Accept: text/event-stream`, no token: 200, stream opens; an authenticated `project_info` then times out (8 s), and again 40 s after the stream closed; `/api/status` still dead a minute later -- the server is single-threaded and the stream loop swallowed its write errors | threaded server, token required, the loop ends on a failed write, `TD_MCP_MAX_STREAMS`; after: 401 without a token, `project_info` in 0.03 s with a stream open |
| `GET /api/resource?uri=td://node//project1` with no token: 200 and the operator | 401 |
| TouchDesigner launched with `TD_MCP_MAX_RISK=WRITE_ADDITIVE` (after the fix): `create_node` ok; `delete_node`, `execute_python`, a batch containing `execute_python` refused (`error_code: policy`) | policy in the live server, offline server and bridge |
| `op('/project1/td_mcp').par.Allowexec = False`: the panel restarts the bridge, `/api/status` still `allow_exec: true` | `reload_settings()` in `start()`; after: `false`, `execute_python` refused |
| With exec off, `restore_network` of a copy of a snapshot outside the snapshot folder loaded it | refused (code route); the snapshot itself still restores |
| `save_tox` to `x.toe`: written before; existing `.tox`: replaced silently | `bad_path`; `exists` unless `overwrite=true` (`replaced: true`) |
| Two `snapshot_network` calls in one second: one file name | distinct names |

The component's **Allow Python Execution** toggle overrides `TD_MCP_ALLOW_EXEC`
in the environment (like the token): `TD_MCP_ALLOW_EXEC=0` at launch did not
turn exec off while the toggle was on.

## 1.18.0 — the call journal, measured

Recorded live in TEST-01 and reverted through the bridge's own `batch`:
`set_parameters` over a constant and an expression (`seed` came back in
EXPRESSION mode with `absTime.frame`), `set_flags`, `connect_nodes`,
`set_dat_text`, `delete_node` of a COMP holding a Constant CHOP, `rename_node`.
A COMP saved with `COMP.save()` to `<sha>/inner.tox` and loaded back with
`loadTox` returns as `inner`, with its children -- the file name gives the
operator its name, so the journal keeps a deleted COMP under its own name.

