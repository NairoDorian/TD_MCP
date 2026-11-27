# PICKED_FROM.md — TD_MCP provenance audit

This file tracks every technique, idea, and feature absorbed into TD_MCP from
the 27+ reference repos in the sibling `github-mcp/` folder. TD_MCP is the
"ultimate" merge: it keeps the good ideas, drops the useless/complicated
ones, and stays simple, fast, and stdlib-first.

Format: `[reference repo] — what was picked → where it lives in TD_MCP`.

## Repos mined

| Reference repo | License | What was picked | Where it lives |
|---|---|---|---|
| **touchdesigner-wiki-rag** | MIT | SQLite FTS5 wiki-wide coverage (manual.db), adaptive HyDE with trigger threshold, sibling-operator disambiguation ("Noise TOP" flags Noise CHOP/SOP/POP), L0 browse + L2 page read pattern | `rag/strategies.py` (SqliteWikiStrategy), `rag/knowledge_graph.py` (sibling_operators), `server_offline.py` (td_docs_browse/td_docs_read_page) |
| **TrueFiasco** | AGPL (clean-room reimpl) | KB integrity receipt (SHA-256 chunks/embeddings), operator-aware intent boost (0.08), score-floor abstention, risk-tier annotations (read_only/additive/destructive), name-integrity eval gate | `rag/retriever.py` (receipt), `rag/strategies.py` (boost + floor), `server_offline.py` (annotations), `tests/test_wave7.py` |
| **TDPilot** | MIT | SQLite session memory with trust tiers (official>bundled>personal>community>experimental), technique store, pre-turn injection, activity log, tool_batch | `memory.py`, `activity.py`, `server_offline.py` (td_tool_batch, td_activity_log, td_history_recall) |
| **bottobot** | MIT | Experimental techniques KB (7 JSON categories), per-operator version compat (addedIn/removedIn/changedIn), operator lookup by name/nickname | `kb/corpus/experimental/`, `kb/corpus.py` (operator_version_compat, load_experimental_techniques), `server_offline.py` (td_docs_experimental, td_docs_version_compat) |
| **cacheflowe/td-docs-mcp** | MIT | Skills pack structure (16-topic conventions), pull-based philosophy, common mistakes, GLSL Vectors page gotcha, geometryCOMP (not geoCOMP) | `skills/td-building/SKILL.md` |
| **touchdesigner-mcp-aliphi** | MIT | Bridge caps (batch/params/depth/samples/rows/bytes), env-tunable safety toggles | `bridge/td_mcp_bridge.py` (MAX_*, TD_MCP_* env) |
| **dcc-mcp-touchdesigner** | MIT | Bounded operations pattern, server-side caps, clear env-var naming | `bridge/td_mcp_bridge.py` |
| **touchdesigner_agent_mcp** | MIT | Scaffold show (vj/audio_reactive/installation blueprints), show readiness checklist | `server_offline.py` (td_scaffold_show, td_show_readiness) |
| **touchdesigner-mcp (other variants)** | various | Param expansion (xyz/rgba/common suffix expansion), gotcha templates, GLSL pattern library | `param_resolver.py`, `glsl_patterns.py`, `server_offline.py` (td_gotcha_template) |
| **twozero_td_mcp** | MIT | Exec-mode tiers (off/restricted/standard/full) with restricted whitelist | `bridge/td_mcp_bridge.py` |
| **touchdesigner_mcp_server** | MIT | Source registry (show-readiness: official/bundled/personal/community/experimental) | `sources.py`, `server_offline.py` (td_source_registry) |
| **touchDesigner-MCP-vedant1317** | MIT | Dataset/operator corpus from local OfflineHelp mirror (sourceFile field, full param tables) | `kb/corpus.py`, `kb/import_corpus.py` |
| **Touchdesigner___MCP_server** | MIT | DAT set with SHA-256 CAS, JSON-safe serialization of exec results | `bridge/td_mcp_bridge.py` |
| **Embody / RepoJifrex / others** | various | Viewport verdicts (black/flat/transparent) feed into build scoring; loud degradation (refuse to load on integrity mismatch) | `rag/scoring.py`, `rag/retriever.py` (verify_receipt) |
| **mcp_touch / tdmcp / touch_mcp** | various | Live bridge status reporting, parameter info/get_non_default_params, bind_parameter_expression | `bridge/td_mcp_bridge.py`, `server_live.py` |
| **github-mcp (meta)** | n/a | CI ordering: build artifacts → fast standalone guard scripts → pytest | `.github/workflows/ci.yml`, `scripts/check_bridge_stale.py` |

## Sweep of 2026-08-19 — repos that moved in the preceding 15 days

All 28 repos re-pulled to HEAD on 2026-08-19. Six had commits since 2026-08-04;
those were weighted above the rest, on the theory that what a maintainer fixed
last week is what the ecosystem is currently getting wrong.

| Reference repo | Commit (date) | What was picked | Where it lives |
|---|---|---|---|
| **Pantani/tdmcp** | `c25e5bd` *enforce raw-off across code-bearing routes* (#143, 08-11) | The insight that disabling `execute_python` disables *one* code route out of seven. Their `codeBearing.ts` classifier (executable operator types, code-bearing parameter names, `*expr` suffix, `ext<n>object`, type-specific `filter` params, DAT `file` params) reimplemented in Python | `bridge/td_mcp_bridge.py` (`code_bearing_sources`, `code_route_denied`, guards on `create_node` / `set_parameters` / `bind_parameter_expression` / `set_dat_text` / `exec_node_method` / `import_recipe`) |
| **dylanroscover/Embody** | `e04d27a` *v6.0.251 worker-side run() advisory* (08-17) | Derivative's 2026-08-17 confirmation that off-main-thread `run()` corrupts TD silently rather than raising, and the write-time static lint that answers it: thread-target resolution, one level of helper following, `Thread` subclass `run()` overrides, module-scope shadowing rule, byte cap + `run(` prefilter | `bridge/td_mcp_bridge.py` (`worker_run_findings`, `threading_warning`), attached by `execute_python` and `set_dat_text` |
| **quentinR2/TouchDesignerMcp** | `1ea1ef8` *bridge/server version mismatch* (#8, 08-18) | Stamping the pasted-in bridge with its version, learning it once per client via a cheap handshake, and riding a warning on every response until it is fixed | `bridge/td_mcp_bridge.py` (`BRIDGE_VERSION`, `project_info`, `describe`), `td_mcp/server_live.py` (`version_mismatch_warning`, `TDClient._note_bridge_version`), `scripts/check_bridge_stale.py` (parity gate) |
| **Pantani/tdmcp** | `da2a3e4` *expose operator snapshot provenance* (#150, 08-11) | Epistemic honesty on doc misses: `found` / `lookup_status` / `data_version` and an explicit "snapshot absence is not build absence" notice, so an agent stops reporting a stale corpus as ground truth | `td_mcp/kb/corpus.py` (`operator_lookup`, `snapshot_version`, `SNAPSHOT_NOTICE`), `td_mcp/server_offline.py` (`td_docs_operator`) |
| **Pantani/tdmcp** | `38c7a1c` *distinguish warnings in diagnostic summaries* (#146, 08-11) | Errors and warnings arrive on the same channel and were being counted as one; split by severity while keeping the old field for compatibility | `bridge/td_mcp_bridge.py` (`split_diagnostics`, `_do_get_errors`), `td_mcp/server_live.py` (`get_errors`) |
| **dcc-mcp/dcc-mcp-touchdesigner** | `fd75de3` *expand typed control surface* (#5, 08-13) | `_host_info.touchdesigner_release`: `app.version` still reports the legacy `099` generation, so the public `year.build` id must come from `app.build`. Fixed a latent bug — TD_MCP's `td_build` was not comparable against its own corpus builds | `bridge/td_mcp_bridge.py` (`touchdesigner_release`, `_do_project_info`) |
| **vedant1317/touchDesigner-MCP** | (no new commits; idea still unmined) | Close-match parameter/operator suggestions on a lookup miss | `td_mcp/kb/corpus.py` (`nearest_operator_names`) |

Repos re-pulled with no new commits in the window, and no newly-identified gap:
RepoJifrex, TDPilot, TDPilot_deepseekv4, TD_Builder_alpha, Touchdesigner___MCP_server,
mcp_touch, td_docs_mcp, touch_mcp, touchdesigner-ai-brain, touchdesigner-mcp-aliphi,
touchdesigner-mcp-michaelslain, touchdesigner-mcp-mrinalghosh,
touchdesigner-mcp-rickengineroom, touchdesigner-mcp-skill,
touchdesigner-mcp-wangpolanshan, touchdesigner-mcp, touchdesigner-wiki-rag,
touchdesigner_agent_mcp, touchdesigner_mcp (dependency bumps only),
touchdesigner_mcp_server, twozero_td_mcp.

## Sweep of 2026-09-14 — Embody v6.0.280 → v6.2.51

All 31 repos re-fetched. Embody had moved 162 commits; hermes-skill (docs
package) and 8beeeaaat/touchdesigner_mcp (dep bump) had nothing to take. Full
account: `UPSTREAM_SYNC_2026-09-14.md`.

| Reference repo | Commit (date) | What was picked | Where it lives |
|---|---|---|---|
| **dylanroscover/Embody** | `c5d2991` *Agent rules: seven TD gotchas verified live* (09-13) | Six live-verified (2025.33230) API traps we did not already hold: `Par.default` never sets a value; Extension Object par is constant-mode Str (never `.expr` — the only trace is a `SyntaxError ... Context:(Extension 1)` on `scriptErrors()`); Timer CHOP ships `cyclelimit` on / `maxcycles` 4; CHOP Execute DAT demands its CHOP every frame (and Execute DAT `framestart` defaults off); `allowCooking=False` is COMP-only | `skills/td-building/SKILL.md` (§ Field-verified API traps) |
| **dylanroscover/Embody** | `c5d2991` (same finding, applied to code) | `_do_set_flags` now returns actionable guidance for `allowCooking=False` on a non-COMP instead of a raw `tdError`; the API double models the raising behavior per the "model the awkward parts" rule | `bridge/td_mcp_bridge.py` (`_do_set_flags`), `tests/td_double.py`, `tests/test_bridge_handlers.py` |
| **Plugin_FFT / PluginBuilder_V2** (local `TD_PROJECTS/PluginBuilder`, not a repo) | — | Complete custom C++ operator SDK knowledge: C-ABI plugin exports, `CHOP_CPlusPlusBase` overrides, one-eval-per-cook parameter snapshots, CHOP/TOP-CPU/CUDA buffer patterns, TripleBuffer hard-real-time cook-thread architecture, FFTW planner hazards, CMake/Ninja build + DLL rename-in-place hot reload | `docs/CPP_CUSTOM_OPERATORS.md` |

## Learned / deferred — 2026-09-14 feature hunt (still to take)

A codebase-wide comparison of TD_MCP's 47 live tools + 44 offline doc tools
against Embody v6.2.19, johnsabath/touchdesigner-mcp, twozero_td_mcp and
8beeeaaat/touchdesigner_mcp. The following were confirmed as *real* gaps by
reading each repo's bridge code, but are deferred (not taken this pass) —
they require a live TouchDesigner host to verify and are logged here per the
"backlog, not relitigated" rule from `UPSTREAM_SYNC_2026-09-14.md`:

| Priority | Feature | Source | Why take | TD_MCP gap |
|---|---|---|---|---|
| High | Any-family `capture` (CHOP/SOP/COMP/MAT via a transient `opviewerTOP`) | Embody `envoy_read.py:1113-1235` | `capture_viewport` is TOP-only today; non-TOP ops can't be read as images | `capture_viewport` (`bridge/td_mcp_bridge.py:2506`) calls `node.save()`/`numpyArray()` (TOP methods only) |
| High | `list_dialogs`/`dismiss_dialog` while TD's main thread is hung | Embody `envoy_bridge.py:5007-5069` (host-side Win32 `PostMessageW` WM_CLOSE/WM_KEYDOWN) | A frozen modal (`ui.messageBox`/native) times out every MCP tool with no recovery | No dialog enumeration or dismissal anywhere |
| High | `render` MP4 + synchronized audio (MovieFileOut + AudioFileOut, PNG-seq→H.264 via ffmpeg) | johnsabath `td_mcp_server.py:662-913` | Video capture is entirely absent; only `capture_viewport` PNG stills exist | No video recording tool |
| Medium | Project-wide `get_project_performance` with hotspots | Embody `EnvoyExt.py:3505-3524` | `get_performance` walks one op's children only; no project-wide aggregation | `get_performance` (`2082`) is per-op only |
| Medium | `save_project` as an idempotent background job with log capture | Embody `EnvoyExt.py:4292-4332` | `save_tox` saves one COMP; no project-level save/job tracking | `save_tox` (`1905`) is COMP-only |
| Low | Stable machine-readable `error_code` on every error envelope | Embody `EnvoyExt.py:108-249` | TD_MCP already has recovery hints (`RECOVERY_HINT_RULES`); the delta is a stable `error_code` field | `_wrap()`/`recovery_hints()` attach text only, no `error_code` |
| Low | Animated-GIF mode on `observe` (two-pass ffmpeg palette) | johnsabath `td_mcp_server.py:462-627` | Nice-to-have; TD_MCP's motion-detect `observe` is non-redundant | `observe` (`2585`) returns metadata + still PNG, no GIF/base64 |

Skipped in this hunt (confirmed non-take): `map` (TD_MCP already has DOT graphs, `map_network`); twozero_td_mcp (server is proprietary inside twozero.tox, no repo code); touchdesigner_mcp/8beeeaaat (14 tools, strict subset of TD_MCP — only Python-class introspection missing, cosmetic).

## Sweep of 2026-11-22 — three new repos, Embody v6.3.8, TDPilot 2.6

New: TouchDesigner/TDMCP, TouchDesigner/TDMCPSkills (Derivative),
eviscerations/touchdesigner-bridge-mcp. Ideas only -- their licences
(Derivative Shared Use, PolyForm-NC) are not MIT; every fact was re-derived
from TouchDesigner 2025.33230. Full account: `UPSTREAM_SYNC_2026-11-22.md`.

| Reference repo | What was picked (idea) | Where it lives |
|---|---|---|
| **eviscerations/touchdesigner-bridge-mcp** | A parameter catalog probed from the running build (theirs not used; ours generated live) | `scripts/probe_param_catalog_td.py`, `td_mcp/kb/param_catalog.py`, `corpus/param_catalog/` |
| **TouchDesigner/TDMCP** 1.1.57 | Mutating tools refuse the server's own component (`self_path`) | `bridge/td_mcp_bridge.py` (`_self_guard`, `_SELF_GUARDED`) |
| **TouchDesigner/TDMCP** #13 | Type-level parameter help from the live build, not a lagging JSON | `describe_op_type`, `td_describe_op_type` |
| **TouchDesigner/TDMCP** #14 | Stale cook times marked and ranked last; CPU+GPU budget | `_do_get_performance` |
| **TouchDesigner/TDMCP** | `pulse_parameter`; MCP `instructions` at initialize | bridge, `td_mcp/instructions.py` |
| **TouchDesigner/TDMCPSkills** 1.4.3 | Skill coverage (family / workflow / cross-cutting split) -- no text | `skills/td-*` (own words, catalog-checked) |
| **dylanroscover/Embody** v6.2.65 / v6.3.8 | `describe_op_type` via `/sys/quiet`; get_parameter reads bind/export/broken-expression modes | bridge `_do_describe_op_type`, `_par_reading` |
| **dylanroscover/Embody** v6.2.19 | Non-TOP capture through a transient OP Viewer TOP across frames | `_capture_any_family`, `_MULTI_FRAME_TOOLS` |

Closed from the 2026-09-14 backlog: any-family `capture` (High), project-wide
performance with stale detection (Medium, partially: COMP-wide with depth).

## Sweep of 2026-11-27 — 24 new repos, the saved-file reader, silent failures

See `UPSTREAM_SYNC_2026-11-27.md`. Every TouchDesigner fact below was
re-probed on 2025.33230 before use; the references supplied the questions.

| Reference repo | What was picked | Where it lives |
|---|---|---|
| **grigabyte/td-atlas** (MIT) | Reading `.toe`/`.tox` offline through TouchDesigner's own `toeexpand`, on a cached copy; its format notes as the starting map (two rules came out differently here: the mode is `flags & 3`, tables are rows-then-columns) | `td_mcp/toe_reader.py`, `td_project_read/grep/diff/check`, `docs/PROJECT_FILES.md` |
| **grigabyte/td-atlas** (MIT) | `td_health`: a report of what TouchDesigner does not call an error, sampled across frames, with a paused/stalled clock treated as "no evidence" | bridge `_do_health`, `health_report`, `_health_across_frames` |
| **KingRaver/touchme** (no licence: ideas only) | Questions from its probe tables -- glob paths in `op()`, cross-network wiring, destroy-rewires, writes through binds, Script OP errors on no surface, sequence growth -- each answered by our own probe | bridge `path_argument_error`, `_do_connect_nodes`, `_do_delete`, `_write_readback`, `_docked_script_errors`, `_grows_sequence` |
| **cacheflowe/td-http-api** (no licence: ideas only) | The lead that utility operators (annotations) are invisible to `op()` and `ops()` | bridge `_do_annotate`, `_annotations_in` |

## Deliberately skipped (too complex / useless for TD_MCP)

- **ChromaDB + OpenAI embeddings**: too heavy for a stdlib-first offline tool. TF-IDF cosine + BM25 is enough; dense opt-in via `TD_MCP_DENSE=1`.
- **Cohere Rerank API**: needs network + key. Local CrossEncoder opt-in only.
- **Agent profiles/roles** (e.g. "architect", "reviewer" profiles in some repos): added complexity without real value for a doc/live-bridge. One behavior, no profile switching.
- **Web dashboard / browser UI**: TD_MCP is a CLI/MCP server, not a web app.
- **Multi-server orchestration**: the offline + live two-server split is the sweet spot. More servers = more config pain.
- **YAML config files per operator**: corpus JSON is simpler and works.

## License notes

All code in TD_MCP is MIT-licensed clean-room reimplementation. Reference
repos with AGPL code were *not* copied verbatim; their ideas were understood
and re-expressed from scratch in TD_MCP's own style.
