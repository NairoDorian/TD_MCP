# Upstream sync — 2026-11-22

All sibling repos pulled; three new ones cloned. Everything below that is
stated as TouchDesigner behaviour was verified live on **TouchDesigner
2025.33230** (the `TD_MCP_TESTS/TEST-01` project, bridge reloaded in place),
not taken from another project's prose.

## What moved

| Repo | Delta | Verdict |
|---|---|---|
| **TouchDesigner/TDMCP** (new, Derivative) | cloned, 29 commits, v1.1.58 | Server is a closed `.tox`; mined its changelog + tool design (below) |
| **TouchDesigner/TDMCPSkills** (new, Derivative) | cloned, 56 commits, skills 1.4.3 | 19 skills read for coverage; ours re-written and re-verified (license below) |
| **eviscerations/touchdesigner-bridge-mcp** (new) | cloned, Rust gateway + Python executor | Studied: live-probed catalog idea, governor, validators. PolyForm-NC: no code taken |
| **twozero-team/twozero-td-mcp** | remote moved from 404dotzero; +2 | CI only |
| dylanroscover/Embody | +282, v6.2.51 -> v6.3.8 | Mined (below); most volume is Convoy fleet + site |
| dreamrec/TDPilot | +11, 2.6.0-2.6.2 | Native transactions, owned-label rollback, tool profiles: noted |
| Pantani/tdmcp | +12 | Core tool profile, exec-off core set: noted |
| 8beeeaaat/touchdesigner-mcp | +11, v2.1/2.2 | Claude/Codex plugin packaging; nothing technical to take |
| dcc-mcp-touchdesigner | +10 | Release/docs only |
| TrueFiasco/TD_Builder_alpha | +3 | Dependency pins only |
| quentinR2/TouchDesignerMcp | +3 | get_errors fix (we already read all four surfaces) |
| familienak / bottobot | +2 / +1 | Nothing to take |

## Licensing decision

TDMCP and TDMCPSkills ship under Derivative's Shared Use License + TDMCP Tool
Terms; eviscerations under PolyForm Noncommercial (with a commercial licence).
TD_MCP is MIT and keeps its rule: **study, do not copy**. Facts (operator and
parameter names, menu tokens, defaults, behaviour) are re-derived from the
running build; prose is our own. Embody imported TDMCPSkills content *with
Derivative's permission*; we have no such permission and did not.

## The finding that mattered: our parameter knowledge was fiction

Prompted by eviscerations' live-probed catalog and TDMCP's #13 (`get_help`
trusted a `TDParameterHelp.json` that mistyped 711 parameters):

- The scraped corpus keys parameters by **label** (`'Brightness 1'`,
  `'Clamp Input'`), types every one of them float and leaves defaults null.
- `param_resolver` therefore **rewrote correct names into labels**
  (`brightness1` -> `'Brightness 1'`, `resolutionw` -> `'Resolution'`), and
  returned no warnings at all for the opType spelling (`levelTOP`) because
  `operator_record('levelTOP')` was None.
- `validate_build` reported **`UNKNOWN_TYPE` on every correctly typed
  operator** and could not see an invalid menu value -- the self-heal pipeline
  penalised valid builds and passed broken ones.

Fix: `scripts/probe_param_catalog_td.py` instantiates every class in
`families` inside `/sys/quiet` (cooking off) and writes
`td_mcp/kb/corpus/param_catalog/<FAMILY>.json`: **659 operators, 21,796
parameters, 0 failures, 15,102 with help text**. The catalog now drives the
resolver, validation (`PARAM_UNKNOWN`, `PARAM_RENAMED`, `MENU_INVALID`,
`AMBIGUOUS_TYPE`, `PARAM_UNSET`), auto-repair, `td_docs_parameter` and the new
`td_describe_op_type` / `describe_op_type`.

**Privacy:** the raw probe captured this machine's state -- every
`localaddress` menu listed its IP addresses, audio/video device menus held
hardware GUIDs, Screen Grab TOP `source` listed every open window's title,
font menus listed installed fonts, Folder DAT `rootfolder` held the project
path. `param_catalog --sanitize` replaces those menus with
`machine_state: true` (read them live with `describe_op_type`) and rewrites
install paths to `<td_install>/...`; 56 parameters affected. A test keeps
the committed catalog a fixed point of the sanitizer.

## Verified live and fixed in the bridge

| Fact (2025.33230) | Was | Now |
|---|---|---|
| `par.val` is the constant slot: expr `1.5 + 1.5` reads val 1.0, eval 3.0; a bound par reads its stale constant | `get_parameters` / `get_node` returned `val` | `eval()` + `modes` (expr / bindMaster / exportSource), `<eval error>` instead of a crash; `names` and `non_default` filters |
| A bad menu token is **silently ignored** (no exception, old value kept) | `set_parameters` reported success | fails, listing valid tokens; menu labels map to tokens |
| Writes past a hard clamp are silently clamped (`resolutionw=-5` -> 1) | unreported | `notes.clamped_to` |
| Writing a value over an expression switches the par to CONSTANT | unreported | `notes.mode_changed` |
| `True` on a pulse par does nothing | treated as a value | `par.pulse()`; new `pulse_parameter` tool |
| No `OP.parTuple`; `Par.tupletName`/`.tuplet` carry tuplets | `resolution: [w, h]` failed as unknown | expanded to components |
| `me.parent()` in the bridge DAT is `/project1/td_mcp` | path-less `create_node` created INSIDE the bridge | defaults to the viewed network; mutating tools refuse the bridge, and delete/set_flags/exec refuse its ancestors (TDMCP 1.1.57, Embody 6.2.49) |
| OP Viewer TOP renders only after frames pass (needs `outputresolution=custom` + relative path) | capture was TOP-only | any family, multi-frame on the HTTP thread, viewer destroyed with undo suspended |
| `cookTime` is the last cook, however old; `cookAbsFrame` vs `absTime.frame` tells | stale ops topped the ranking; non-COMP path returned nothing | `stale` marking, CPU+GPU ms, `budget_pct`, single-op profiling (TDMCP #14) |
| `display`/`render`/`lock` are settable OP flags | not in `_NODE_FLAGS` | added |
| Custom par names: one capital then lowercase/digits only (`MySpeed` rejected) | -- | in `td-dat-python` |
| Composite TOP `over`: input 0 is the foreground; default operand is `multiply` | -- | in `td-top-family` |
| GLSL TOP creates `_pixel`, `_compute`, `_info` DATs; GLSL POP `_compute`, `_info` | -- | in `td-glsl` |

Live scenario (`26/26` checks) exercised each row through the real HTTP tool
layer, plus a captured LFO CHOP viewer image.

## Server and tooling

- **Live registry forwards arguments unchanged.** The hand-written lambdas
  dropped `nodeX/nodeY`, `detailLevel` and every new filter. Parity test
  retargeted: schema <-> bridge handler keys.
- **Rewritten tool descriptions** for all 49 live tools, with JSON-Schema
  `required`, enums for `detailLevel`, and path conventions.
- **MCP `instructions`** served by both servers at initialize (TDMCP serves a
  system-prompt DAT the same way).
- Recovery hints: first-match rules let `par\b` claim the forbidden-builtin
  error; specific rules now come first.
- `normalize_op_type('NoiseTOP')` -> `noiseTOP`.

## Skills

Nine new skills (`td-workflow`, `td-top-family`, `td-chop-family`,
`td-pop-family`, `td-sop-render`, `td-dat-python`, `td-glsl`,
`td-performance`, `td-network-layout`) plus a corrected `td-building`. Every
operator and parameter name checked against the catalog -- which caught five
wrong names in the drafts and one long-standing one; `tests/test_skills.py`
keeps checking. Coverage was informed by TDMCPSkills; no text was taken.

## RAG

- Eval: greedy expectation matching made recall depend on tie order
  (`corpus_render_top` claimed `render_top`'s slot); now a maximum bipartite
  matching. Six parameter-lookup queries added (`ORIGINAL_QUERY_COUNT` keeps
  the old baseline reproducible).
- Catalog chunks in the main index were measured and **left opt-in**
  (`TD_MCP_PARAM_CHUNKS=1`): CI-like build MRR 0.835 -> 0.857 but recall@5
  1.000 -> 0.987, because 1,617 long chunks shift BM25 IDF/avgdl for every
  document. If enabled, they get no per-category voter and an intent gate.
  Default build: recall@5 1.000, MRR 0.857 on the 45-query set.
- With a local wiki mirror, "feedback trails recipe" missed `level_top`
  (recall@5 0.994): the experimental `technique` category's per-category voter.
  Fixed in 1.13.1 by letting it rank on merit only -- recall@5 1.000 on both
  the wiki and the wiki-less build.

## Backlog (noted, not taken)

- `edit_custom_parameters` and network-box `annotation` tools (TDMCP).
- Tool profiles (`TD_MCP_TOOL_PROFILE=core|build|full`, TDPilot 2.5 / tdmcp
  #160): 108 tools is a large always-loaded surface.
- Owned-label rollback (TDPilot 2.5): never undo past history the agent did
  not create.
- `run_soak_test` (Embody 6.2.65), dialog list/dismiss, MP4 render.
- A pump that survives a paused timeline (TDMCP counts frames on the UI clock).
