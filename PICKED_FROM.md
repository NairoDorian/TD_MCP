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
