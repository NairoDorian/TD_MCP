# Upstream sync — 2026-11-27

All sibling repos pulled; 24 new ones cloned (a GitHub search plus the
[wavgen community tools scan](https://wavgen.ca/video/touchdesigner-signal-flow/community-tools-scan/),
which lists no GitHub URL for any MCP project, so search did the finding).
Every statement below about TouchDesigner was reproduced on **2025.33230** in
the TEST-01 project, through the bridge's tool layer, before it was modelled
in `tests/td_double.py` or written into a tool. Other projects' findings were
treated as leads, not facts.

## What moved

| Repo | Delta | Verdict |
|---|---|---|
| dylanroscover/Embody | +3, v6.3.8 release + docs build | Read `EnvoyExt.py` diff: parameter-tool tests only. Nothing to take |
| dcc-mcp/dcc-mcp-touchdesigner | +1 | CI toolchain only |
| bottobot/touchdesigner-mcp-server | renamed to **r-spring/touchdesigner-mcp-server** | remote updated; no new commits |
| Everything else | 0 | — |

## New references (cloned this session)

| Repo | License | What it is | Mined |
|---|---|---|---|
| **grigabyte/td-atlas** | MIT | Atom index, live bridge, **offline `.toe` reader**, `td_health`, call journal | **Yes** -- format notes were the map for `toe_reader`; `td_health` the idea for `health` |
| **KingRaver/touchme** | none (ideas only) | FastMCP + bridge with published live-probe research | **Yes** -- its probe tables were leads; each one used was re-probed here |
| **cacheflowe/td-http-api** | none (ideas only) | HTTP bridge + agent skills | `td-common-mistakes`: utility-node and cook-time leads, re-probed |
| jlopesmo/TDflowMCP | none | Permissions, audit store, project memory, 17 ADRs | Backlog |
| GaNotchVFX/touchbridge | MIT | Async file bridge, never-overwrite saves, GPU cost | Backlog |
| acronym-lol/acro-td-mcp | MIT | `.tox` + Node server, 8 reference docs | Backlog |
| JazibAli360/jazib-td-mcp | unclear | 1,354 files, a 387 KB `LEARNINGS.md` | Backlog (largest unread source) |
| skylarbarrera/nyan | none | Typed parser, `toeexpand` editing guide | Backlog: write-back of `.toe` |
| niklazhallberg/touch-designer-skill | MIT | Self-improving Claude skill (Embody/Envoy) | Backlog |
| sashabedard, XTNX, Gleinkaa, tai5863 `td-mcp`; FFZackFair92, H-lab, kalektika, konraddiesch, smartiumai, yogithasri07 (x2), MichaelKramerGuitar, AhnthinkworlD, danieldalfovo/TDTerminal, ekakit/lightfast-mcp, rslnt1270/omt07l | mixed | Bridges, wiki crawlers, class kits, experiments | Indexed in `github-mcp/README.md`; not mined |

## Verified live, and what changed because of it

| Fact (2025.33230) | Before | Now |
|---|---|---|
| `op('/p/glob_*')` returns **one arbitrary match** (`glob_beta`, though `glob_alpha` was created first); `op(5)` returns the operator with id 5 (`/sys/drag`) | a tool given a pattern or a number acted on whatever came back | every path argument is refused unless it is one exact path (`wildcard_path`, `bad_path`); `*here`/`*this` kept |
| A path naming nothing fell back to the root in seven readers (`_op(path) or root`) | a typo listed the whole project | `not found` |
| Wiring across networks raises nothing, connects nothing and **removes the wire that was on that input** | `connect_nodes` reported success and broke the network | refused (`cross_network`) with the Select operator to use; out-of-range connector index explained; the result is read back |
| Destroying an operator mid-chain wires its first input into what it fed | silent | `delete_node` reports `rewired` / `left_unwired` |
| A Script CHOP whose `onCook` raises: its own `errors()`, `warnings()`, `scriptErrors()` are all empty, recursive or not; the traceback is on the docked callbacks DAT | `get_errors` called it clean | docked DATs' script errors are read |
| GLSL: `compileResult` holds the compiler output (`ERROR: /…/gl_pixel:3: 'undefined_var' : undeclared identifier`) with no cook, and survives a deleted Info DAT; a recursive read of a COMP saw only the vague warning | Info DAT only, never recursive | `compileResult` first, also under a COMP with `recurse` |
| One message is `Error: …` plus continuation lines that are not indented (an extension NameError is three lines; a traceback ends with its path line) | split into 2-3 "errors" | grouped by the `Error:`/`Warning:` opener |
| Writing a value to a **bound** parameter stays bound and lands on the bind master (`tx`→`holder.Amount` read 0.42; `tx.val` stayed 0.0) | reported as "mode changed to CONSTANT" | `written_through_bind` note naming the master |
| Writing over an **expression** switches to CONSTANT and keeps the text | note said the expression was discarded | says it is kept and inactive |
| Bound to something that does not exist: assigning the bind does not raise; writing a value raises `'NoneType' object has no attribute 'par'` and changes nothing | `ok: true` with `"error: …"` under `applied` | `ok: false`, the dead bind named, the way out given; `{"mode": "constant", "val": …}` unbinds while setting |
| Assigning `bindExpr` switches the mode to BIND | no way to bind through `set_parameters` | `{"bind": …}`; recipes carry binds (they exported the stale constant) |
| **Sequence parameters** (`const0name`, `pt0posx`): looking up a name past the last block *creates* the blocks (`par['const4value']` left five channels; `getattr` too); a bogus suffix (`const3bogus`) is None | a read tool checking a name grew the operator | reads never look such a name up (`missing`); writes say `sequences_grown`; growth of 64+ blocks refused |
| Block parameters exist lazily: a fresh Primitive POP has no `pt*` parameter at all | catalog and every listing omitted them | catalog records them (+24 parameters on 10 types, `sequences` on 111 types); live listings show them once the operator has cooked; live `describe_op_type` adds them from the catalog (`from_catalog`) |
| **Iterating `OP.seq` cooks a never-cooked operator** -- Constant CHOP, Noise TOP, Alembic SOP alike, even in `/sys/quiet` with cooking off (the Alembic SOP read its file). Reaching a sequence through `Par.sequence`, or by an exact block-0 lookup, cooks nothing | (the first version of this wave iterated it) | the bridge never iterates `seq` on an operator that has not cooked; the catalog probe cooks only after recording, never a hardware/network type |
| `create('annotateCOMP', 'my_note')` returns `annotate1`; an annotation made in the editor is a **utility** operator: `op(path)`, `parent.op()`, `ops('*')`, `children`, `findChildren()` all skip it; only `findChildren(includeUtility=True)` returns it | no tool could see, edit or delete one; `create_node` ignored the name | `annotate` tool (list/create/update/delete, frames operators by name); `create_node` assigns the name; `list_nodes` counts hidden annotations |
| An extension written the wiki's way (`MyExt(me)`) leaves `extensions[0]` None; `errors()`/`warnings()` empty; `scriptErrors()` holds the NameError; `op('./MyExt').module.MyExt(me)` works | invisible | `health` reports `extension-failed` |
| A dead expression: `errors()` is empty until the operator cooks; `eval()` raises | `get_errors` clean | `health` evaluates every expression under a time budget (`dead-expression`) |
| `totalCooks`, `cookAbsFrame`, `cpuCookTime`, `gpuCookTime`, `absTime.stepSeconds` exist; `cookCount`, `app.licenseType` do not; TD's Python has numpy 2.1.2, cv2 4.11.0, no PIL | — | `health` samples `totalCooks` across real frames |
| Perform CHOP toggles and channels are named differently (`droppedframes`→`dropped_frames`, `gputemp`→`gpu0_chip_temp`…) | — | recorded for a future perf tool |
| `toeexpand.exe` / `toecollapse.exe` ship in `bin/`; `toeexpand` exits 1 on success; a `.toe` expands in ~0.2 s | no offline project reader | `td_mcp/toe_reader.py` + four offline tools |

## The `.toe` / `.tox` format, measured

Built a network with one of everything (constant, expression, inactive
expression, bind, export, custom pages of every style, a COMP wired in and out,
a table, a text DAT, flags, comment, tags, storage), saved it as `.tox`,
expanded it, then expanded **all 768 shipped components** (93,153 operators)
and ran the reader over them. Full write-up: `docs/PROJECT_FILES.md`.

- **The mode is `flags & 3`**, in `ParMode` order. `0x10` only means "an
  expression is stored": 22,394 shipped constants keep an inactive
  expression. td-atlas reads `0x10` as expression mode; on this build that
  reports the TEST-01 Out TOP label `trails` as the expression `me.name`.
- **Tables are `1, rows, cols, 0`** then cells, row-major (td-atlas: "unknown,
  columns, rows, padding"). All 3,981 shipped tables are consumed exactly.
- DAT text: `2\n*` + six uint32, the last the length (td-atlas: confirmed); an
  empty DAT is a 19-byte header with no length word (8 shipped files).
- Quoted constants span lines (51 lines); 6 files use the pre-099
  `{ version 0.8 }` grammar; `pageindex1`/`stdswitcher1` are UI state.
- Files store `OP.type` (`comp`, `geo`, `eval`); the catalog maps them.
- Against the catalog, 689,509 of 714,943 shipped built-in parameter records
  resolve; the rest are block parameters of higher sequence blocks (now
  matched through block 0) and names older examples still carry.

## The parameter catalog

Re-probed (`scripts/probe_param_catalog_td.py`, 25 s): **659 operators,
21,820 parameters** (was 21,796), 0 failures, sanitized. The only changes:
`sequences` on 111 types, and 24 parameters that exist only after a first cook
(`after_cook: true`). Fourteen defaults that are time-dependent expressions
(`index` = the frame, Noise SOP `tz` = `absTime`, random group ids) kept their
committed reading, so a re-probe on the same build no longer churns them.

**One mistake to record.** The first re-probe of this session iterated
`OP.seq` on every type before learning that it cooks the operator -- so every
type, device and network operators included, was cooked once in `/sys/quiet`.
Nothing visible happened, but a cook is what opens a camera, a socket or a
MIDI port. That catalog was discarded; the committed one comes from the second
probe, which records first and never cooks a hardware or network type.

## Agent path

`scripts/live_smoke.py`: **30/30** (offline: `td_project_read` and
`td_project_grep` on TEST-01's saved `.toe`; live: wildcard refusal, `health`
finding a dead expression `get_errors` cannot see yet, `annotate`,
`describe_op_type` listing Primitive POP `pt0posx` from the catalog). A
47-check script exercised every row of the table above through the HTTP tool
layer.

## Backlog (noted, not taken)

- **Call journal** (td-atlas): one JSONL line per bridge call on disk, with the
  old value of every write; answers "what did the agent change yesterday".
- **Network text on save** (td-atlas): a diffable file beside the `.toe` on
  every save. `td_project_diff` covers the comparison on demand.
- **`not-pulled` vs `static`** (td-atlas): ask an idle operator to cook twice
  and see whether it computes something new. `health` reports `idle` as a note
  instead, because that probe cooks operators.
- **Write-back** of an edited expansion with `toecollapse` (nyan's editing
  guide, td-atlas variants): needs the original flags words; not started.
- Mining TDflowMCP (permission model, audit store), touchbridge
  (never-overwrite saves, GPU cost), jazib-td-mcp's `LEARNINGS.md`.
- Still open from 2026-11-22: tool profiles, owned-label rollback,
  `edit_custom_parameters`, soak test.
