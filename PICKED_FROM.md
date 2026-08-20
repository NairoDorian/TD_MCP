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
