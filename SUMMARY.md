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
| Tools | 45 (`td_*`) | 39 (`create_node`, `set_parameters`, …) |

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
├── scripts
│   ├── bump_version.py
│   └── generate_summary.py
├── skills
│   └── td-building
│       └── SKILL.md
├── td_mcp
│   ├── kb
│   │   ├── corpus
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
│   │   ├── embeddings.jsonl
│   │   ├── import_corpus.py
│   │   └── scrape.py
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
│   │   └── output_budget.py
│   ├── __init__.py
│   ├── bundle.py
│   ├── compat.py
│   ├── config_gen.py
│   ├── discover.py
│   ├── eval.py
│   ├── generators.py
│   ├── glsl_patterns.py
│   ├── heal.py
│   ├── macro.py
│   ├── memory.py
│   ├── param_resolver.py
│   ├── perf.py
│   ├── progress.py
│   ├── prompts.py
│   ├── recipe_vault.py
│   ├── scoring.py
│   ├── server_live.py
│   ├── server_offline.py
│   ├── spatial.py
│   ├── streamable_http.py
│   ├── validation.py
│   └── vision.py
├── td_mcp.egg-info
│   └── PKG-INFO
├── tests
│   ├── __init__.py
│   ├── fake_remote_mcp.py
│   ├── test_agent.py
│   ├── test_bundle.py
│   ├── test_compat.py
│   ├── test_config_gen.py
│   ├── test_corpus.py
│   ├── test_discover.py
│   ├── test_eval.py
│   ├── test_glsl.py
│   ├── test_heal.py
│   ├── test_layout.py
│   ├── test_live_server.py
│   ├── test_logs.py
│   ├── test_macro.py
│   ├── test_mcp_server.py
│   ├── test_memory.py
│   ├── test_merge_features.py
│   ├── test_offline_more_tools.py
│   ├── test_offline_new_tools.py
│   ├── test_perf.py
│   ├── test_progress.py
│   ├── test_prompts.py
│   ├── test_rag.py
│   ├── test_recipe.py
│   ├── test_recovery.py
│   ├── test_review_regressions.py
│   ├── test_scan_ws.py
│   ├── test_scoring.py
│   ├── test_showcontrol.py
│   ├── test_spatial.py
│   ├── test_tdn.py
│   ├── test_validation.py
│   └── test_vision.py
├── ARCHITECTURE.md
├── CHANGELOG.md
├── COMMIT.md
├── CONTRIBUTING.md
├── HOW_TO_USE.md
├── pyproject.toml
├── README.md
├── repomix.config.json
├── setup_env.ps1
├── TD_MCP_Master_Plan.md
├── TouchDesigner_Links.md
├── TouchDesigner_MCP_Servers.md
└── uv.lock
```

## File-by-file explanations

*(Each entry names the file and a one-line purpose extracted from the file itself.)*

### `bridge/`

- **`bridge/bootstrap.py`** — td_mcp_bridge — auto-generated by bootstrap.py. Do not edit by hand.
- **`bridge/chat_ui.html`** — Project file.
- **`bridge/td_mcp_agent.py`** — td_mcp_agent â€” run an autonomous building agent directly inside TouchDesigner.
- **`bridge/td_mcp_bridge.py`** — td_mcp_bridge — run this inside TouchDesigner (paste into a Text DAT).

### `scripts/`

- **`scripts/bump_version.py`** — Find files still hardcoding the old version (excluding build/changelog).
- **`scripts/generate_summary.py`** — Optionally emit a full repomix pack if the CLI is available.

### `skills/`

- **`skills/td-building/SKILL.md`** — TouchDesigner building — You are helping someone build a TouchDesigner (TD) network. TD is node-graph creative-coding software by Derivative. Operator families: TOP (images), CHOP (channels/animation), SOP (geometry), POP (particles, 2023+), DAT (data/text), COMP (containers/3D).

### `td_mcp/`

- **`td_mcp/__init__.py`** — Package root re-exporting the public retrieval API (`Retriever`, `build_retriever`, `tokenize`).
- **`td_mcp/bundle.py`** — Project bundling (.mcpb) (tdmcp multi-client packaging).
- **`td_mcp/compat.py`** — Version compatibility + connection-error cache (8beeeaaat).
- **`td_mcp/config_gen.py`** — Per-client MCP config + skill generation (Embody / twozero).
- **`td_mcp/discover.py`** — Multi-instance TD discovery (twozero_td_mcp).
- **`td_mcp/eval.py`** — Evaluation harness for td-mcp (TD_Builder_alpha style).
- **`td_mcp/generators.py`** — Artist generators (tdmcp Layer 1 style) — opinionated network builders.
- **`td_mcp/glsl_patterns.py`** — Named GLSL patterns + ready-to-paste network templates.
- **`td_mcp/heal.py`** — Self-healing build orchestrator (capstone of the self-healing theme).
- **`td_mcp/kb/__init__.py`** — KB subpackage marker (runtime corpus access lives in `corpus.py`).
- **`td_mcp/kb/build_index.py`** — Build a (optionally dense) index over the KB chunks.
- **`td_mcp/kb/build_kb.py`** — Build the ultimate local TouchDesigner knowledge base.
- **`td_mcp/kb/chunks.jsonl`** — Generated data artifact (845,140 bytes) — produced by the build/eval pipeline; not hand-edited.
- **`td_mcp/kb/corpus.py`** — Runtime access to the merged MIT corpus (operators, Python API, versions).
- **`td_mcp/kb/corpus/operators.json`** — Project file.
- **`td_mcp/kb/corpus/python_api.json`** — Project file.
- **`td_mcp/kb/corpus/versions/experimental-builds.json`** — Project file.
- **`td_mcp/kb/corpus/versions/operator-compatibility.json`** — Project file.
- **`td_mcp/kb/corpus/versions/python-api-compatibility.json`** — Project file.
- **`td_mcp/kb/corpus/versions/version-manifest.json`** — Project file.
- **`td_mcp/kb/embeddings.jsonl`** — Generated data artifact (194,587 bytes) — produced by the build/eval pipeline; not hand-edited.
- **`td_mcp/kb/import_corpus.py`** — Import the merged MIT operator / Python-API / version corpora.
- **`td_mcp/kb/scrape.py`** — Scrape docs.derivative.ca into KB chunks (optional deps: requests, beautifulsoup4).
- **`td_mcp/led_mapping/__init__.py`** — LED / pixel mapping (wall / strip / voxel grid + DMX channel export).
- **`td_mcp/macro.py`** — Macro recorder / replay (tdmcp macroRecorder / runMacroScript).
- **`td_mcp/memory.py`** — Session memory (tdmcp Obsidian vault / AI session memory, Embody).
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
- **`td_mcp/scoring.py`** — Build scoring & self-repair loop (tdmcp scoreBuild / repairNetwork).
- **`td_mcp/server_live.py`** — td-mcp live server: Streamable HTTP MCP server for TouchDesigner.
- **`td_mcp/server_offline.py`** — td-mcp offline server: documentation / RAG tools over a local KB.
- **`td_mcp/showcontrol/__init__.py`** — Show-control planning (Art-Net / sACN / OSC / MIDI / timecode).
- **`td_mcp/spatial.py`** — Spatial pointer resolver (twozero `*here` / `*this`).
- **`td_mcp/streamable_http.py`** — Streamable HTTP transport for the td-mcp bridge (8beeeaaat / TD_Builder_alpha style).
- **`td_mcp/tdn/__init__.py`** — TDN — TouchDesigner Network serialization (diffable YAML, from Embody).
- **`td_mcp/tools/__init__.py`** — Tools subpackage marker (risk / recovery / logs / layout helpers).
- **`td_mcp/tools/layout.py`** — Layout lint — Embody-style deterministic placement hygiene.
- **`td_mcp/tools/logs.py`** — Token-efficient logging (Embody discipline).
- **`td_mcp/tools/recovery.py`** — Recovery hints — Embody-style self-healing for every error.
- **`td_mcp/tools/risk.py`** — Tool risk classification (TrueFiasco 4-class, MIT-safe reimpl).
- **`td_mcp/util/__init__.py`** — Internal utilities for td-mcp (output budgeting, safe IO, etc.).
- **`td_mcp/util/output_budget.py`** — Output / token budgeting (TD_Builder_alpha output_budget idea).
- **`td_mcp/validation.py`** — Build validation & self-repair (TD_Builder 5-stage + tdmcp auto-repair).
- **`td_mcp/vision.py`** — Viewport vision — deterministic pixel analysis + caption (tdmcp captionTop).

### `td_mcp.egg-info/`

- **`td_mcp.egg-info/PKG-INFO`** — Generated data artifact (17,255 bytes) — produced by the build/eval pipeline; not hand-edited.

### `tests/`

- **`tests/__init__.py`** — Project file.
- **`tests/fake_remote_mcp.py`** — Minimal stdio MCP doc-RAG server used by the integration tests to prove multi-process RAG fusion. Returns a synthetic remote doc so RemoteMCPStrategy can be exercised without a real external server.
- **`tests/test_agent.py`** — Test agent script compilation and schemas.
- **`tests/test_bundle.py`** — Tests for td_mcp.bundle (.mcpb packaging).
- **`tests/test_compat.py`** — Tests for td_mcp.compat (version compat + error cache).
- **`tests/test_config_gen.py`** — Tests for td_mcp.config_gen + tdn idle checkpoint.
- **`tests/test_corpus.py`** — Tests for the merged corpus (operator + Python + version) loading.
- **`tests/test_discover.py`** — Tests for td_mcp.discover (multi-instance discovery).
- **`tests/test_eval.py`** — Test eval harness.
- **`tests/test_glsl.py`** — Tests for td_mcp.glsl_patterns (named shaders + network templates).
- **`tests/test_heal.py`** — Tests for td_mcp.heal (self-healing orchestrator).
- **`tests/test_layout.py`** — Tests for td_mcp.tools.layout (Embody-style layout hygiene).
- **`tests/test_live_server.py`** — Test live server MCP wiring and tool registration.
- **`tests/test_logs.py`** — Tests for td_mcp.tools.logs (token-efficient ring buffer).
- **`tests/test_macro.py`** — Tests for td_mcp.macro (record / replay / dedupe).
- **`tests/test_mcp_server.py`** — Server wiring test (framework-transport independent).
- **`tests/test_memory.py`** — Tests for td_mcp.memory (session memory recall).
- **`tests/test_merge_features.py`** — Tests for the ultimate-merge feature additions (review pass 2).
- **`tests/test_offline_more_tools.py`** — Tests for new offline tools: discover / memory / scaffold_recipe.
- **`tests/test_offline_new_tools.py`** — Tests for showcontrol media-server connectors + offline tool wiring.
- **`tests/test_perf.py`** — Tests for td_mcp.perf (performance analyzer).
- **`tests/test_progress.py`** — Tests for td_mcp.progress (progress reporting).
- **`tests/test_prompts.py`** — Tests for td_mcp.prompts (expert prompts / phases).
- **`tests/test_rag.py`** — RAG regression + multi-process fusion tests.
- **`tests/test_recipe.py`** — Tests for recipe_vault upgrades (tdmcp-style metadata + draft_from_chain).
- **`tests/test_recovery.py`** — Tests for td_mcp.tools.recovery (Embody-style self-healing hints).
- **`tests/test_review_regressions.py`** — Regression tests for fixes found during code review.
- **`tests/test_scan_ws.py`** — Tests for scan_network tool and WebSocket frame helpers.
- **`tests/test_scoring.py`** — Tests for td_mcp.scoring (scoreBuild + repair_network loop).
- **`tests/test_showcontrol.py`** — Tests for show-control + LED mapping modules.
- **`tests/test_spatial.py`** — Tests for td_mcp.spatial (*here / *this resolver).
- **`tests/test_tdn.py`** — Tests for TDN serialization/diff and the knowledge graph.
- **`tests/test_validation.py`** — Tests for td_mcp.validation (TD_Builder 5-stage + tdmcp auto-repair).
- **`tests/test_vision.py`** — Tests for td_mcp.vision (deterministic viewport analysis + caption).

### Repository root

- **`ARCHITECTURE.md`** — Architecture & Developer Notes — TD_MCP — This document explains how the `td-mcp` codebase is put together: the two
- **`CHANGELOG.md`** — Changelog — All notable changes to this project will be documented in this file.
- **`COMMIT.md`** — Before You Commit — A short, repeatable checklist to run **before every `git commit`** so the
- **`CONTRIBUTING.md`** — Contributing — Thanks for wanting to improve **td-mcp**! This is a local-first TouchDesigner MCP
- **`HOW_TO_USE.md`** — 1. Prerequisites — Make sure you have:
- **`README.md`** — TouchDesigner MCP (`td-mcp`) — [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
- **`TD_MCP_Master_Plan.md`** — TouchDesigner MCP — Master Plan — This document is the master specification for **td-mcp**: a local-first
- **`TouchDesigner_Links.md`** — TouchDesigner — Documentation & Useful Links — ---
- **`TouchDesigner_MCP_Servers.md`** — TouchDesigner MCP Servers — Catalog & Brainstorm — ---
- **`pyproject.toml`** — Python project metadata: dependencies, optional extras (mcp/rag/scrape) and console entry points (td-mcp-offline, td-mcp-live).
- **`repomix.config.json`** — Repomix configuration (include/exclude + output style) for emitting a full source pack.
- **`setup_env.ps1`** — One-shot PowerShell bootstrap that pins Python 3.11.10 (TD's interpreter) via uv and installs td-mcp.
- **`uv.lock`** — Generated data artifact (495,011 bytes) — produced by the build/eval pipeline; not hand-edited.

---

*Generated by `scripts/generate_summary.py`. This file contains no source code — only file names, architecture, the tree and per-file explanations.*
