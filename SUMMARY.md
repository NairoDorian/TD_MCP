# td-mcp — project summary

**td-mcp** is a local-first *TouchDesigner MCP* (Model Context Protocol) toolkit that
fuses the best ideas of the public TD-MCP ecosystem into one cohesive package:

* an **offline documentation / RAG server** (no TouchDesigner required) that answers
  operator / Python / GLSL questions and *generates* networks as diffable YAML;
* a **live bridge + MCP server** that controls a running TouchDesigner session
  (create / wire / inspect / verify) over Streamable-HTTP, SSE, WebSocket or stdio;
* a **naturally embedded chat UI** and a **zero-dependency autonomous agent** that run
  inside TouchDesigner itself.

Everything in ``td_mcp/`` is pure Python (standard library + optional ``mcp`` /
``networkx`` / ``sentence-transformers``) and is fully unit-testable without a
running TouchDesigner. Retrieval quality is provable: an eval harness measures
recall@k / MRR / nDCG over a labelled query set, and the whole project ships with
a regression test suite.

This document lists every tracked file with a one-line purpose — it intentionally
contains **no source code**, only file names, architecture, the tree and per-file
explanations.


## Architecture

td-mcp is built around **two cooperating servers that share one authoring brain**:

| | Offline server | Live server / bridge |
|---|---|---|
| Module | `td_mcp/server_offline.py` | `td_mcp/server_live.py` + `bridge/td_mcp_bridge.py` |
| Needs TouchDesigner? | **No** | **Yes** (running instance) |
| Role | Doc/RAG answers, network *generation* (YAML, not live nodes), validation, scoring, self-heal | Create / delete / wire / inspect a live TD document over HTTP / stdio |
| Tools | 1 (`td_*`) | 55 (`create_node`, `set_parameters`, …) |

The offline side owns the *intelligence*: `generators` -> `validation` ->
`scoring` -> `heal` produce a diffable network description (**TDN** YAML) that the
live bridge materialises inside TD. `tdn` (diffable YAML), `showcontrol` and
`led_mapping` are deterministic pure-Python planning layers; `rag` + `kb` provide
the version-aware retrieval backbone; `tools/` holds cross-cutting risk / recovery /
log / layout helpers.

Three request lifecycles:

1. **Offline doc query** — `server_offline` -> `ParallelRetriever` (global + per-source
   BM25, optional MiniLM dense + HyDE, optional CrossEncoder rerank, optional external
   RAG fused via Reciprocal Rank Fusion) -> ranked chunks.
2. **Offline build + verify** — `_parse_build_spec` -> `td_build_network` (generators)
   -> `td_score_build` -> `td_validate_build` -> `td_self_heal` (no TD needed).
3. **Live mutation** — MCP client -> `server_live` (Streamable HTTP / SSE / stdio) ->
   bridge dispatch table inside TD, every mutation wrapped in `ui.undo` so one
   Ctrl+Z reverts a whole agent batch.

Retrieval is *local-first and version-aware*: the merged MIT corpus
(`td_mcp/kb/corpus/`) plus the hand-authored `chunks.jsonl` back every answer, and
dense / HyDE / rerank paths are lazy (enabled only with `[rag]` + env flags).


## Directory tree

```
td-mcp/
├── bridge
│   ├── bootstrap.py
│   ├── chat_ui.html
│   ├── td_mcp_agent.py
│   └── td_mcp_bridge.py
├── client_configs
│   ├── .mcp.json
│   └── CLAUDE.md
├── docs
│   ├── AGENT_GUIDE.md
│   ├── CPP_CUSTOM_OPERATORS.md
│   ├── DIAGNOSTICS.md
│   ├── ECOSYSTEM.md
│   ├── INSTALL.md
│   ├── RAG.md
│   ├── README.md
│   ├── SECURITY.md
│   ├── TDN_FORMAT.md
│   ├── TESTING.md
│   ├── TOOLS.md
│   ├── TOUCHDESIGNER_COMPONENT.md
│   └── TROUBLESHOOTING.md
├── examples
│   ├── feedback_trails.json
│   └── README.md
├── scripts
│   ├── bump_version.py
│   ├── check_bridge_stale.py
│   ├── demo_build.py
│   ├── generate_summary.py
│   ├── install_all.py
│   ├── live_smoke.py
│   ├── probe_param_catalog_td.py
│   └── td_live.py
├── skills
│   ├── td-building
│   │   └── SKILL.md
│   ├── td-chop-family
│   │   └── SKILL.md
│   ├── td-dat-python
│   │   └── SKILL.md
│   ├── td-glsl
│   │   └── SKILL.md
│   ├── td-guide-user
│   │   └── SKILL.md
│   ├── td-network-layout
│   │   └── SKILL.md
│   ├── td-performance
│   │   └── SKILL.md
│   ├── td-pop-family
│   │   └── SKILL.md
│   ├── td-sop-render
│   │   └── SKILL.md
│   ├── td-top-family
│   │   └── SKILL.md
│   └── td-workflow
│       └── SKILL.md
├── td_mcp
│   ├── kb
│   │   ├── corpus
│   │   │   ├── experimental
│   │   │   │   ├── audio-visual.json
│   │   │   │   ├── generative-systems.json
│   │   │   │   ├── glsl.json
│   │   │   │   ├── gpu-compute.json
│   │   │   │   ├── machine-learning.json
│   │   │   │   ├── networking.json
│   │   │   │   └── python-advanced.json
│   │   │   ├── param_catalog
│   │   │   │   ├── CHOP.json
│   │   │   │   ├── COMP.json
│   │   │   │   ├── DAT.json
│   │   │   │   ├── MAT.json
│   │   │   │   ├── POP.json
│   │   │   │   ├── SOP.json
│   │   │   │   └── TOP.json
│   │   │   ├── versions
│   │   │   │   ├── experimental-builds.json
│   │   │   │   ├── operator-compatibility.json
│   │   │   │   ├── python-api-compatibility.json
│   │   │   │   └── version-manifest.json
│   │   │   ├── operators.json
│   │   │   └── python_api.json
│   │   ├── __init__.py
│   │   ├── build_index.py
│   │   ├── build_kb.py
│   │   ├── chunks.jsonl
│   │   ├── corpus.py
│   │   ├── import_corpus.py
│   │   ├── kb_receipt.json
│   │   ├── offline_import.py
│   │   ├── param_catalog.py
│   │   ├── scrape.py
│   │   └── wiki_ingest.py
│   ├── led_mapping
│   │   └── __init__.py
│   ├── rag
│   │   ├── __init__.py
│   │   ├── eval.py
│   │   ├── knowledge_graph.py
│   │   ├── rerank.py
│   │   ├── retriever.py
│   │   └── strategies.py
│   ├── showcontrol
│   │   └── __init__.py
│   ├── tdn
│   │   └── __init__.py
│   ├── tools
│   │   ├── __init__.py
│   │   ├── layout.py
│   │   ├── logs.py
│   │   ├── recovery.py
│   │   └── risk.py
│   ├── util
│   │   ├── __init__.py
│   │   ├── output_budget.py
│   │   └── stdio.py
│   ├── __init__.py
│   ├── activity.py
│   ├── bundle.py
│   ├── compat.py
│   ├── config_gen.py
│   ├── discover.py
│   ├── eval.py
│   ├── freshness.py
│   ├── generators.py
│   ├── glsl_patterns.py
│   ├── heal.py
│   ├── install.py
│   ├── instructions.py
│   ├── macro.py
│   ├── memory.py
│   ├── observability.py
│   ├── param_resolver.py
│   ├── perf.py
│   ├── progress.py
│   ├── prompts.py
│   ├── recipe_vault.py
│   ├── safety_registry.py
│   ├── scaffolds.py
│   ├── scoring.py
│   ├── server_live.py
│   ├── server_offline.py
│   ├── spatial.py
│   ├── validation.py
│   └── vision.py
├── td_mcp.egg-info
│   └── PKG-INFO
├── tests
│   ├── __init__.py
│   ├── bridge_pure.py
│   ├── fake_remote_mcp.py
│   ├── td_double.py
│   ├── test_agent.py
│   ├── test_agent_parity.py
│   ├── test_argument_parity.py
│   ├── test_bootstrap.py
│   ├── test_bridge_handlers.py
│   ├── test_bridge_readers.py
│   ├── test_bridge_wave13.py
│   ├── test_bundle.py
│   ├── test_codeguard.py
│   ├── test_compat.py
│   ├── test_config_gen.py
│   ├── test_corpus.py
│   ├── test_discover.py
│   ├── test_eval.py
│   ├── test_examples.py
│   ├── test_experimental.py
│   ├── test_freshness_integrity.py
│   ├── test_glsl.py
│   ├── test_heal.py
│   ├── test_install_lifecycle.py
│   ├── test_json_safe.py
│   ├── test_layout.py
│   ├── test_live_offline_loop.py
│   ├── test_live_server.py
│   ├── test_logs.py
│   ├── test_macro.py
│   ├── test_mcp_server.py
│   ├── test_memory.py
│   ├── test_merge_features.py
│   ├── test_motion.py
│   ├── test_observability.py
│   ├── test_offline_more_tools.py
│   ├── test_offline_new_tools.py
│   ├── test_param_catalog.py
│   ├── test_parity.py
│   ├── test_perf.py
│   ├── test_progress.py
│   ├── test_prompts.py
│   ├── test_protocol_negotiation.py
│   ├── test_provenance.py
│   ├── test_rag.py
│   ├── test_rag_wave9.py
│   ├── test_recipe.py
│   ├── test_recovery.py
│   ├── test_retriever.py
│   ├── test_review_regressions.py
│   ├── test_scaffolds.py
│   ├── test_scan_ws.py
│   ├── test_scoring.py
│   ├── test_showcontrol.py
│   ├── test_skills.py
│   ├── test_spatial.py
│   ├── test_stdio_utf8.py
│   ├── test_tdn.py
│   ├── test_tdn_fidelity.py
│   ├── test_tool_schema_conformance.py
│   ├── test_validation.py
│   ├── test_version_truth.py
│   ├── test_vision.py
│   ├── test_wave4.py
│   ├── test_wave5.py
│   ├── test_wave7.py
│   ├── test_wiki_ingest.py
│   └── test_wiki_strategy.py
├── AGENTS.md
├── ARCHITECTURE.md
├── CHANGELOG.md
├── CLAUDE.md
├── COMMIT.md
├── CONTRIBUTING.md
├── HOW_TO_USE.md
├── IMPROVEMENT_PLAN.md
├── MCP_2026_MIGRATION.md
├── PICKED_FROM.md
├── pyproject.toml
├── README.md
├── repomix.config.json
├── setup_env.ps1
├── TD_MCP_Master_Plan.md
├── TouchDesigner_Links.md
├── TouchDesigner_MCP_Servers.md
├── UPSTREAM_SYNC_2026-08-26.md
├── UPSTREAM_SYNC_2026-09-14.md
├── UPSTREAM_SYNC_2026-11-22.md
└── uv.lock
```

## File-by-file explanations

*(Each entry names the file and a one-line purpose extracted from the file itself.)*

### `bridge/`

- **`bridge/bootstrap.py`** — Build the TD_MCP bridge component inside TouchDesigner.
- **`bridge/chat_ui.html`** — Project file.
- **`bridge/td_mcp_agent.py`** — td_mcp_agent â€” run an autonomous building agent directly inside TouchDesigner.
- **`bridge/td_mcp_bridge.py`** — td_mcp_bridge — the MCP server that runs inside TouchDesigner.

### `client_configs/`

- **`client_configs/.mcp.json`** — Project file.
- **`client_configs/CLAUDE.md`** — td-mcp for claude — Local-first TouchDesigner MCP: an offline doc/RAG server plus a live

### `docs/`

- **`docs/AGENT_GUIDE.md`** — Using td-mcp as an agent — Written for the model driving these tools, and for whoever is writing its system
- **`docs/CPP_CUSTOM_OPERATORS.md`** — Custom C++ operators — the PluginBuilder knowledge base — TouchDesigner can load compiled `.dll` operators (CPlusPlus CHOP / TOP / DAT /
- **`docs/DIAGNOSTICS.md`** — The four error surfaces — This is the single most important thing to understand about automating
- **`docs/ECOSYSTEM.md`** — The TouchDesigner MCP ecosystem — A survey of **30 public TouchDesigner + MCP projects**, mirrored side by side in
- **`docs/INSTALL.md`** — Installing td-mcp — There are **two halves**, and they fail independently:
- **`docs/RAG.md`** — Retrieval — **1.000 recall @k=5** with the full knowledge base, **0.991** curated-only. No
- **`docs/README.md`** — td-mcp documentation — Start here.
- **`docs/SECURITY.md`** — Security model — The bridge opens a port inside your TouchDesigner session and lets a language
- **`docs/TDN_FORMAT.md`** — TDN — TouchDesigner Network format — Version **2.1**. A diffable YAML serialisation of a TouchDesigner network, so a
- **`docs/TESTING.md`** — Testing — **944+ tests.** `uv run pytest` on a fresh clone, after
- **`docs/TOOLS.md`** — Tool reference — **114 tools across two servers.** They are deliberately split, because they
- **`docs/TOUCHDESIGNER_COMPONENT.md`** — What goes *into* TouchDesigner — The MCP server runs *outside* TouchDesigner, as a normal Python process your AI
- **`docs/TROUBLESHOOTING.md`** — Troubleshooting — Ordered by how often each one bites. Every symptom here has been seen.

### `examples/`

- **`examples/README.md`** — Examples — template networks, built the way an agent should — Each `*.json` here is a network spec in the format `td_validate_build` checks
- **`examples/feedback_trails.json`** — Project file.

### `scripts/`

- **`scripts/bump_version.py`** — Bump the td-mcp version in ONE place and keep the changelog in sync.
- **`scripts/check_bridge_stale.py`** — Bridge staleness guard (quentinR2 generated-bridge + CI guard idea).
- **`scripts/demo_build.py`** — Build a template network from ``examples/`` the way an agent should.
- **`scripts/generate_summary.py`** — Generate a clean, code-free summary of the td-mcp repository.
- **`scripts/install_all.py`** — One-command install (ai-brain / johnsabath / 8beeeaaat idea).
- **`scripts/live_smoke.py`** — Use TD_MCP the way an agent does, and check that it works.
- **`scripts/probe_param_catalog_td.py`** — Probe every operator type in a running TouchDesigner into a parameter catalog.
- **`scripts/td_live.py`** — Drive the TEST-01 TouchDesigner project from the command line.

### `skills/`

- **`skills/td-building/SKILL.md`** — TouchDesigner building — AI assistant skill — You are helping someone build a TouchDesigner (TD) network. TD is node-graph
- **`skills/td-chop-family/SKILL.md`** — CHOP networks — - Source -> shaping -> `nullCHOP` named for its role (`null_audio`, `null_ctrl`).
- **`skills/td-dat-python/SKILL.md`** — DATs and Python — - A new `textDAT` holds `language` = `text` (its declared default is `input`, which
- **`skills/td-glsl/SKILL.md`** — GLSL in TouchDesigner — - `glslTOP` creates `<name>_pixel`, `<name>_compute` and `<name>_info` DATs beside it.
- **`skills/td-guide-user/SKILL.md`** — Guiding someone through TouchDesigner — The person sees TouchDesigner; you see it through tools. Close that gap before
- **`skills/td-network-layout/SKILL.md`** — Network layout — - Signal flows left to right: every wire goes forward (positive X). Parallel chains
- **`skills/td-performance/SKILL.md`** — Performance — - TouchDesigner cooks on demand: an operator cooks when something downstream (a
- **`skills/td-pop-family/SKILL.md`** — POP networks — - A POP is points (plus primitives) with attributes -- `P` position, `N`, `Color`,
- **`skills/td-sop-render/SKILL.md`** — Geometry, materials, instancing, render — - `geometryCOMP` (with a SOP or POP inside, render flag on) + `cameraCOMP` +
- **`skills/td-top-family/SKILL.md`** — TOP chains — - Generator -> processing -> `nullTOP` named for its role (`null_out`, `null_mask`).
- **`skills/td-workflow/SKILL.md`** — TouchDesigner work through TD_MCP — Two servers, one loop. The **offline** server (`td_*` tools) answers from docs, a

### `td_mcp/`

- **`td_mcp/__init__.py`** — Package root re-exporting the public retrieval API (`Retriever`, `build_retriever`, `tokenize`).
- **`td_mcp/activity.py`** — Activity log + history recall (TDPilot technique, MIT-safe reimpl).
- **`td_mcp/bundle.py`** — MCP Bundle (``.mcpb``) packaging.
- **`td_mcp/compat.py`** — Version compatibility + connection-error cache (8beeeaaat).
- **`td_mcp/config_gen.py`** — Per-client MCP config + skill generation (Embody / twozero).
- **`td_mcp/discover.py`** — Multi-instance TD discovery (twozero_td_mcp).
- **`td_mcp/eval.py`** — Evaluation harness for td-mcp (TD_Builder_alpha style).
- **`td_mcp/freshness.py`** — KB freshness banner (touchdesigner-wiki-rag freshness idea).
- **`td_mcp/generators.py`** — Artist generators (tdmcp Layer 1 style) — opinionated network builders.
- **`td_mcp/glsl_patterns.py`** — Named GLSL patterns + ready-to-paste network templates.
- **`td_mcp/heal.py`** — Self-healing build orchestrator (capstone of the self-healing theme).
- **`td_mcp/install.py`** — Install SOP v1 — an install lifecycle designed for an agent to drive.
- **`td_mcp/instructions.py`** — MCP ``instructions`` served at initialize by both TD_MCP servers.
- **`td_mcp/kb/__init__.py`** — KB subpackage marker (runtime corpus access lives in `corpus.py`).
- **`td_mcp/kb/build_index.py`** — Build a (optionally dense) index over the KB chunks.
- **`td_mcp/kb/build_kb.py`** — Build the ultimate local TouchDesigner knowledge base.
- **`td_mcp/kb/chunks.jsonl`** — Generated data artifact (10,329,180 bytes) — produced by the build/eval pipeline; not hand-edited.
- **`td_mcp/kb/corpus.py`** — Runtime access to the merged MIT corpus (operators, Python API, versions).
- **`td_mcp/kb/corpus/experimental/audio-visual.json`** — Project file.
- **`td_mcp/kb/corpus/experimental/generative-systems.json`** — Project file.
- **`td_mcp/kb/corpus/experimental/glsl.json`** — Project file.
- **`td_mcp/kb/corpus/experimental/gpu-compute.json`** — Project file.
- **`td_mcp/kb/corpus/experimental/machine-learning.json`** — Project file.
- **`td_mcp/kb/corpus/experimental/networking.json`** — Project file.
- **`td_mcp/kb/corpus/experimental/python-advanced.json`** — Project file.
- **`td_mcp/kb/corpus/operators.json`** — Project file.
- **`td_mcp/kb/corpus/param_catalog/CHOP.json`** — Project file.
- **`td_mcp/kb/corpus/param_catalog/COMP.json`** — Project file.
- **`td_mcp/kb/corpus/param_catalog/DAT.json`** — Project file.
- **`td_mcp/kb/corpus/param_catalog/MAT.json`** — Project file.
- **`td_mcp/kb/corpus/param_catalog/POP.json`** — Project file.
- **`td_mcp/kb/corpus/param_catalog/SOP.json`** — Project file.
- **`td_mcp/kb/corpus/param_catalog/TOP.json`** — Project file.
- **`td_mcp/kb/corpus/python_api.json`** — Project file.
- **`td_mcp/kb/corpus/versions/experimental-builds.json`** — Project file.
- **`td_mcp/kb/corpus/versions/operator-compatibility.json`** — Project file.
- **`td_mcp/kb/corpus/versions/python-api-compatibility.json`** — Project file.
- **`td_mcp/kb/corpus/versions/version-manifest.json`** — Project file.
- **`td_mcp/kb/import_corpus.py`** — Import the merged MIT operator / Python-API / version corpora.
- **`td_mcp/kb/kb_receipt.json`** — Project file.
- **`td_mcp/kb/offline_import.py`** — Local OfflineHelp ingestion — thin wrapper over the structured pipeline.
- **`td_mcp/kb/param_catalog.py`** — Ground-truth operator/parameter catalog, probed from a running TouchDesigner.
- **`td_mcp/kb/scrape.py`** — DEPRECATED — kept for backwards compatibility only.
- **`td_mcp/kb/wiki_ingest.py`** — Structured ingestion of a local TouchDesigner wiki mirror into the KB.
- **`td_mcp/led_mapping/__init__.py`** — LED / pixel mapping (wall / strip / voxel grid + DMX channel export).
- **`td_mcp/macro.py`** — Macro recorder / replay (tdmcp macroRecorder / runMacroScript).
- **`td_mcp/memory.py`** — Session memory (tdmcp memory vault / cross-session continuity, ai-brain/mcp_touch).
- **`td_mcp/observability.py`** — Opt-in observability sink: SQLite trace log of tool-call + LLM spans.
- **`td_mcp/param_resolver.py`** — Parameter name + menu-value resolver (TD_Builder_alpha param_name_resolver idea).
- **`td_mcp/perf.py`** — Performance analyzer (TD-Codex performance_analyzer).
- **`td_mcp/progress.py`** — Progress reporting (touchdesigner_agent_mcp report_progress).
- **`td_mcp/prompts.py`** — Expert prompts (TD_Builder get_expert_prompt).
- **`td_mcp/rag/__init__.py`** — RAG subpackage re-export of the retrieval API.
- **`td_mcp/rag/eval.py`** — Eval harness: proves retrieval quality and stops regressions.
- **`td_mcp/rag/knowledge_graph.py`** — Knowledge graph over the operator / Python corpus (Graph RAG, TrueFiasco idea).
- **`td_mcp/rag/rerank.py`** — Optional late-stage reranker (CrossEncoder) for the fused candidate list.
- **`td_mcp/rag/retriever.py`** — Hybrid retrieval over a local TouchDesigner knowledge base.
- **`td_mcp/rag/strategies.py`** — Parallel multi-RAG: several retrieval backends run concurrently and are fused with Reciprocal Rank Fusion (RRF).
- **`td_mcp/recipe_vault.py`** — Recipe vault — persistent, tagged, searchable blueprint storage.
- **`td_mcp/safety_registry.py`** — Source registry + show-readiness checklist (ai-brain idea).
- **`td_mcp/scaffolds.py`** — Project scaffolds (ai-brain `brain_vj_builder.py` idea).
- **`td_mcp/scoring.py`** — Build scoring & self-repair loop (tdmcp scoreBuild / repairNetwork).
- **`td_mcp/server_live.py`** — td-mcp live server: Streamable HTTP MCP server for TouchDesigner.
- **`td_mcp/server_offline.py`** — td-mcp offline server: documentation / RAG tools over a local KB.
- **`td_mcp/showcontrol/__init__.py`** — Show-control planning (Art-Net / sACN / OSC / MIDI / timecode).
- **`td_mcp/spatial.py`** — Spatial pointer resolver (twozero `*here` / `*this`).
- **`td_mcp/tdn/__init__.py`** — TDN — TouchDesigner Network serialization (diffable YAML).
- **`td_mcp/tools/__init__.py`** — Tools subpackage marker (risk / recovery / logs / layout helpers).
- **`td_mcp/tools/layout.py`** — Layout lint — Embody-style deterministic placement hygiene.
- **`td_mcp/tools/logs.py`** — Token-efficient logging (Embody discipline).
- **`td_mcp/tools/recovery.py`** — Recovery hints — Embody-style self-healing for every error.
- **`td_mcp/tools/risk.py`** — Tool risk classification (TrueFiasco 4-class, MIT-safe reimpl).
- **`td_mcp/util/__init__.py`** — Internal utilities for td-mcp (output budgeting, safe IO, etc.).
- **`td_mcp/util/output_budget.py`** — Output / token budgeting (TD_Builder_alpha output_budget idea).
- **`td_mcp/util/stdio.py`** — Pin the stdio streams to UTF-8 for MCP's JSON-RPC transport.
- **`td_mcp/validation.py`** — Build validation & self-repair (TD_Builder 5-stage + tdmcp auto-repair).
- **`td_mcp/vision.py`** — Viewport vision — deterministic pixel analysis + caption (tdmcp captionTop).

### `td_mcp.egg-info/`

- **`td_mcp.egg-info/PKG-INFO`** — Generated data artifact (28,309 bytes) — produced by the build/eval pipeline; not hand-edited.

### `tests/`

- **`tests/__init__.py`** — Project file.
- **`tests/bridge_pure.py`** — Load the bridge's pure helpers straight out of the shipped source.
- **`tests/fake_remote_mcp.py`** — Minimal stdio MCP doc-RAG server used by the integration tests to prove multi-process RAG fusion. Returns a synthetic remote doc so RemoteMCPStrategy can be exercised without a real external server.
- **`tests/td_double.py`** — A deterministic TouchDesigner API double, and a loader for the whole bridge.
- **`tests/test_agent.py`** — Test agent script compilation and schemas.
- **`tests/test_agent_parity.py`** — Agent↔bridge tool-surface parity.
- **`tests/test_argument_parity.py`** — Argument-name parity across the three surfaces a tool call crosses.
- **`tests/test_bootstrap.py`** — The TouchDesigner component builder (`bridge/bootstrap.py`).
- **`tests/test_bridge_handlers.py`** — Handler-level tests for the in-TD bridge, run against a TD API double.
- **`tests/test_bridge_readers.py`** — Reader reduction contract (Wave 12, Embody's "never a blind dump").
- **`tests/test_bridge_wave13.py`** — Wave 13 bridge behaviour, each case verified live on TouchDesigner 2025.33230 before it was modelled in the double (see UPSTREAM_SYNC_2026-11-22.md).
- **`tests/test_bundle.py`** — Tests for td_mcp.bundle (.mcpb packaging).
- **`tests/test_codeguard.py`** — Bridge hardening absorbed from the ecosystem's last-15-days commits.
- **`tests/test_compat.py`** — Tests for td_mcp.compat (version compat + error cache).
- **`tests/test_config_gen.py`** — Tests for td_mcp.config_gen + tdn idle checkpoint.
- **`tests/test_corpus.py`** — Tests for the merged corpus (operator + Python + version) loading.
- **`tests/test_discover.py`** — Tests for td_mcp.discover (multi-instance discovery).
- **`tests/test_eval.py`** — Test eval harness.
- **`tests/test_examples.py`** — The built-in examples and the one-call builder they are built with.
- **`tests/test_experimental.py`** — Tests for the experimental techniques loader and skill pack.
- **`tests/test_freshness_integrity.py`** — Tests for freshness banner + KB integrity receipt.
- **`tests/test_glsl.py`** — Tests for td_mcp.glsl_patterns (named shaders + network templates).
- **`tests/test_heal.py`** — Tests for td_mcp.heal (self-healing orchestrator).
- **`tests/test_install_lifecycle.py`** — Install SOP v1 (Wave 12, adapted from dcc-mcp-touchdesigner 0.1.2).
- **`tests/test_json_safe.py`** — Tests for JSON-safe serialization: non-finite floats and numpy types.
- **`tests/test_layout.py`** — Tests for td_mcp.tools.layout (Embody-style layout hygiene).
- **`tests/test_live_offline_loop.py`** — Tests for the live↔offline validation loop (§3.1).
- **`tests/test_live_server.py`** — Test live server MCP wiring and tool registration.
- **`tests/test_logs.py`** — Tests for td_mcp.tools.logs (token-efficient ring buffer).
- **`tests/test_macro.py`** — Tests for td_mcp.macro (record / replay / dedupe).
- **`tests/test_mcp_server.py`** — Server wiring test (framework-transport independent).
- **`tests/test_memory.py`** — Tests for td_mcp.memory (SQLite-backed session memory recall + trust tiers).
- **`tests/test_merge_features.py`** — Tests for the ultimate-merge feature additions (review pass 2).
- **`tests/test_motion.py`** — Motion verdicts (Wave 12).
- **`tests/test_observability.py`** — Tests for td_mcp.observability (TraceLog SQLite span recorder).
- **`tests/test_offline_more_tools.py`** — Tests for new offline tools: discover / memory / scaffold_recipe.
- **`tests/test_offline_new_tools.py`** — Tests for showcontrol media-server connectors + offline tool wiring.
- **`tests/test_param_catalog.py`** — The live-probed parameter catalog and everything that now reads it.
- **`tests/test_parity.py`** — Schema↔handler parity tests (TDPilot_deepseekv4 strongest regression guard).
- **`tests/test_perf.py`** — Tests for td_mcp.perf (performance analyzer).
- **`tests/test_progress.py`** — Tests for td_mcp.progress (progress reporting).
- **`tests/test_prompts.py`** — Tests for td_mcp.prompts (expert prompts / phases).
- **`tests/test_protocol_negotiation.py`** — Protocol version negotiation, in the bridge's own transport.
- **`tests/test_provenance.py`** — Snapshot provenance + bridge/server version handshake.
- **`tests/test_rag.py`** — RAG regression + multi-process fusion tests.
- **`tests/test_rag_wave9.py`** — RAG wave-9 tests: plural stemming, operator boost precision, family/tutorial boosts, sibling disambiguation, and KB stats.
- **`tests/test_recipe.py`** — Tests for recipe_vault upgrades (tdmcp-style metadata + draft_from_chain).
- **`tests/test_recovery.py`** — Tests for td_mcp.tools.recovery (Embody-style self-healing hints).
- **`tests/test_retriever.py`** — Unit tests for td_mcp.rag.retriever (Index + Retriever, pure stdlib).
- **`tests/test_review_regressions.py`** — Regression tests for fixes found during code review.
- **`tests/test_scaffolds.py`** — Tests for td_mcp.scaffolds (ai-brain show scaffolds).
- **`tests/test_scan_ws.py`** — Tests for scan_network tool and WebSocket frame helpers.
- **`tests/test_scoring.py`** — Tests for td_mcp.scoring (scoreBuild + repair_network loop).
- **`tests/test_showcontrol.py`** — Tests for show-control + LED mapping modules.
- **`tests/test_skills.py`** — The shipped skills stay true to the tools and to TouchDesigner.
- **`tests/test_spatial.py`** — Tests for td_mcp.spatial (*here / *this resolver).
- **`tests/test_stdio_utf8.py`** — UTF-8 stdio pinning (Wave 12, Embody v6.0.264).
- **`tests/test_tdn.py`** — Tests for TDN serialization/diff and the knowledge graph.
- **`tests/test_tdn_fidelity.py`** — TDN fidelity (Wave 12).
- **`tests/test_tool_schema_conformance.py`** — Tool-schema conformance tests (inspired by Embody v6.0.159, test_envoy_tool_schema).
- **`tests/test_validation.py`** — Tests for td_mcp.validation (TD_Builder 5-stage + tdmcp auto-repair).
- **`tests/test_version_truth.py`** — One version, three places (Wave 12).
- **`tests/test_vision.py`** — Tests for td_mcp.vision (deterministic viewport analysis + caption).
- **`tests/test_wave4.py`** — Tests for wave-4 merged features (param families, compaction, techniques, registry).
- **`tests/test_wave5.py`** — Tests for activity log, history recall, and per-operator version compat.
- **`tests/test_wave7.py`** — Tests for wave-7 features (activity log wiring, score floor, tool batch, offline import).
- **`tests/test_wiki_ingest.py`** — Tests for td_mcp.kb.wiki_ingest (structured local wiki-mirror ingestion).
- **`tests/test_wiki_strategy.py`** — Tests for the wiki-rag SQLite FTS5 fusion strategy (§2.2).

### Repository root

- **`AGENTS.md`** — Working on TD_MCP (for AI agents and humans) — TD_MCP exists to be driven by an AI agent, so verifying that it works *for an
- **`ARCHITECTURE.md`** — Architecture & Developer Notes — TD_MCP — This document explains how the `td-mcp` codebase is put together: the two
- **`CHANGELOG.md`** — Changelog — All notable changes to this project will be documented in this file.
- **`CLAUDE.md`** — Project file.
- **`COMMIT.md`** — Before You Commit — A short, repeatable checklist to run **before every `git commit`** so the
- **`CONTRIBUTING.md`** — Contributing — Thanks for wanting to improve **td-mcp**. This is a local-first TouchDesigner
- **`HOW_TO_USE.md`** — 1. Prerequisites — Make sure you have:
- **`IMPROVEMENT_PLAN.md`** — TD_MCP — Improvement Plan (Ultimate RAG/MCP Server for TouchDesigner) — Status: **Draft plan** · Author: NairoDorian/TD_MCP · Date: 2026-08-15
- **`MCP_2026_MIGRATION.md`** — MCP 2026-07-28 / SDK v2 — Migration Audit (COMPLETED) — Date: 2026-08-16 (audit) · 2026-08-17 (implementation) · Author: TD_MCP Phase 2
- **`PICKED_FROM.md`** — PICKED_FROM.md — TD_MCP provenance audit — This file tracks every technique, idea, and feature absorbed into TD_MCP from
- **`README.md`** — TouchDesigner MCP (`td-mcp`) — [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
- **`TD_MCP_Master_Plan.md`** — TouchDesigner MCP — Master Plan — This document is the master specification for **td-mcp**: a local-first
- **`TouchDesigner_Links.md`** — TouchDesigner — Documentation & Useful Links — ---
- **`TouchDesigner_MCP_Servers.md`** — TouchDesigner MCP Servers — Catalog & Brainstorm — ---
- **`UPSTREAM_SYNC_2026-08-26.md`** — Upstream sync — 2026-08-26 — Every repo under `github-mcp/` was fetched from its GitHub upstream and
- **`UPSTREAM_SYNC_2026-09-14.md`** — Upstream sync — 2026-09-14 — All 31 sibling repos fetched from origin. Three had moved since the last sync
- **`UPSTREAM_SYNC_2026-11-22.md`** — Upstream sync — 2026-11-22 — All sibling repos pulled; three new ones cloned. Everything below that is
- **`pyproject.toml`** — Python project metadata: dependencies, optional extras (mcp/rag/scrape) and console entry points (td-mcp-offline, td-mcp-live).
- **`repomix.config.json`** — Repomix configuration (include/exclude + output style) for emitting a full source pack.
- **`setup_env.ps1`** — One-shot PowerShell bootstrap that pins Python 3.11.10 (TD's interpreter) via uv and installs td-mcp.
- **`uv.lock`** — Generated data artifact (436,024 bytes) — produced by the build/eval pipeline; not hand-edited.

---

*Generated by `scripts/generate_summary.py`. This file contains no source code — only file names, architecture, the tree and per-file explanations.*
