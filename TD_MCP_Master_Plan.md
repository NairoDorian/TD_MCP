# TouchDesigner MCP — Master Plan

This document is the master specification for **td-mcp**: a local-first
TouchDesigner [MCP](https://modelcontextprotocol.io) toolkit that fuses the best
ideas of the public TD-MCP ecosystem into one cohesive package. It records the
current state, what has shipped, and the remaining roadmap. Companion docs:
[`README.md`](README.md), [`ARCHITECTURE.md`](ARCHITECTURE.md),
[`SUMMARY.md`](SUMMARY.md) (code-free file overview),
[`COMMIT.md`](COMMIT.md) / [`CONTRIBUTING.md`](CONTRIBUTING.md) (workflow),
[`CHANGELOG.md`](CHANGELOG.md), and the ecosystem catalog
[`TouchDesigner_MCP_Servers.md`](TouchDesigner_MCP_Servers.md).

---

## 1. Vision

Bridge LLMs and TouchDesigner's node graph by combining three pillars:

1. **Local-first documentation RAG server** — answer operator / Python / GLSL
   questions and *generate* networks as diffable YAML, with **no cloud or keys**.
2. **Live bidirectional control bridge** — create / wire / inspect / verify a
   running TouchDesigner session over Streamable-HTTP, SSE, WebSocket or stdio.
3. **Native in-app agent** — a zero-dependency agent that builds networks
   autonomously inside TouchDesigner Text DATs.

The result is a production-ready AI co-pilot that runs *alongside* and *inside*
TouchDesigner.

---

## 2. Current state (shipped)

### 2.1 Split MCP architecture

- **`td-mcp-offline` (Docs / RAG server)** — zero-cloud stdio MCP server.
  - Parallel multi-RAG: global BM25, per-source BM25 (operators / Python /
    GLSL / tutorials), optional MiniLM dense + HyDE, fused via **Reciprocal
    Rank Fusion**; optional CrossEncoder rerank. An external RAG MCP server can
    be folded in over stdio and merged by RRF.
  - Version-aware **1,091-chunk** corpus (operators, Python API, GLSL,
    recipes) + merged MIT corpus; benchmark **recall@5 ≈ 0.966**.
  - Exposes **45** tools (`td_docs_*`, `td_build_*`, `td_score_build`,
    `td_validate_build`, `td_self_heal`, `td_glsl_pattern`, `td_expert_prompt`,
    `td_discover`, `td_memory_*`, `td_scaffold_recipe`, …).
  - Planners for LED pixel layouts, DMX patch lists, and show-control protocols
    (`led_mapping`, `showcontrol`).
  - Reports its version via `td_mcp.__version__`.
- **`td-mcp-live` (Bridge client server)** — connects to a running TD on
  `127.0.0.1:9980`.
  - **39** live tools (`create_node`, `set_parameters`, `connect_nodes`,
    `scan_network`, `build_and_verify`, `map_network`, `snapshot_network`,
    `export_recipe` / `import_recipe`, `validate_network`, `get_performance`,
    `timeline`, …).
  - `build_and_verify` loop: create → set params → catch cook errors → render a
    viewport thumbnail with an `is_black` / `is_flat` / `fully_transparent`
    verdict.
  - **Spatial context pointers** `*here` (active pane) / `*this` (selected op).
  - **Streamable-HTTP mode** (`--http`) on `127.0.0.1:8765`: POST `/` JSON-RPC +
    GET `/` SSE, multi-session via `Mcp-Session-Id`, DNS-rebind guard, Origin
    pinning.

### 2.2 Live TouchDesigner bridge (`bridge/td_mcp_bridge.py`)

- Zero-dependency (only `http.server`, `json`, `base64`, `hmac`); paste into a
  Text DAT.
- Wraps every mutation in TD's `ui.undo` (one Ctrl+Z reverts a whole batch).
- Secured: auto-generated auth token (constant-time `hmac.compare_digest`),
  loopback-only CORS, `execute_python` gate (`TD_MCP_ALLOW_EXEC`),
  `TD_MCP_PROTECTED_PATHS`.
- Pure-Python RFC 6455 **WebSocket** streaming + SSE + JSON-RPC + `td://`
  resource reads.
- Serves a glassmorphic **Chat UI** (`bridge/chat_ui.html`) at `GET /` with
  provider selector (Ollama / Gemini / OpenAI), live network sidebar, health
  indicator, and a multi-step agent loop.
- Every error carries structured `recovery_hints` (`{cause, action,
  next_tools}`) so the agent self-corrects instead of retrying blindly.

### 2.3 Autonomous in-app agent (`bridge/td_mcp_agent.py`)

- Zero-dependency OpenAI-compatible agent (runs inside TD Text DATs).
- **≈51 function-calling tool schemas** (all 39 live bridge tools plus the
  agent-only tools: `macro_start` / `macro_stop` / `macro_save` / `macro_load`
  / `macro_replay` / `macro_status`, `plan_task`, `ask_clarification`,
  `save_session` / `load_session`, `execute_parallel`, `caption_viewport`).
- Capabilities: task **planning & decomposition** (`_plan_task`: plan → execute
  → verify → replan), **clarification** when ambiguous (`_ask_user`),
  **session memory**, **parallel tool execution**, **macro record/replay**, and
  resilient **error recovery**. (The historical `TaskPlanner` /
  `InteractiveClarifier` classes were removed — the lightweight `_plan_task` /
  `_ask_user` helpers cover the same need without dead weight.)
- Fuses with **Ollama** (100% offline, e.g. `qwen2.5:3b`), **Gemini**, or
  **OpenAI**. `caption_viewport` sends a render to a vision model for critique.

---

## 3. Status vs. roadmap

✅ shipped · 🔶 partial · ⬜ pending

| # | Feature | Status |
|---|---------|--------|
| 3.1 | Natively embedded Chat UI panel | ✅ `bridge/chat_ui.html` at `GET /` |
| 3.2 | Live project RAG (workspace context) | ✅ `scan_network` + agent auto-injects topology |
| 3.3 | Multimodal vision debugging | 🔶 `capture_viewport` verdicts + `caption_viewport` critique (not yet auto-fed into `build_and_verify`) |
| 3.4 | WebSocket / real-time streaming | ✅ RFC 6455 WebSocket + SSE |
| 3.5 | Single-click offline installer | ⬜ `setup_env.ps1` configures Python; full one-click Ollama + client-config installer still planned |
| 3.6 | Code-free repo summary + version discipline | ✅ `scripts/generate_summary.py`, `scripts/bump_version.py`, `SUMMARY.md`, CI, `COMMIT.md`/`CONTRIBUTING.md` |
| 3.7 | Self-healing build pipeline | ✅ `validation` → `scoring` → `heal` (offline side) |

### Delivered since the first plan

- **Self-healing build pipeline** (`validation` 5-stage + `scoring` 0..100 +
  `heal` orchestrator) — pure, TD-free, exposed as offline tools.
- **Knowledge graph** (`rag/knowledge_graph.py`) for related-operator walks.
- **Eval harness** (`rag/eval.py`) with recall@k / MRR / nDCG and a trend gate.
- **Tooling & hygiene**: `config_gen` (one-step client configs), `discover`
  (multi-instance), `memory`, `progress`, `bundle` (.mcpb), `macro`,
  `compat`, `perf`, `prompts`, `vision`, `glsl_patterns`, `spatial`.
- **Repo hygiene**: MIT `LICENSE`, `.gitattributes` (LF), GitHub Actions CI,
  dead-code removal, single-source version (`td_mcp.__version__` + `bump_version.py`).

### Remaining / next-up

- **Single-click installer (3.5)** — auto-install Ollama, pull `qwen2.5:3b`, write
  the Claude Desktop / Cursor MCP config in one step.
- **Richer vision loop (3.3)** — feed `caption_viewport` verdicts automatically
  into `build_and_verify` for fully autonomous visual healing.
- **Wire live `build_and_verify` → offline `validation`/`scoring`/`heal`** so the
  live loop self-corrects using the offline orchestrator.
- **Persistent external RAG session** — `RemoteMCPStrategy` currently launches a
  subprocess per query; a persistent session removes that cost.
- **WebRTC** transport for lowest-latency streaming (currently WebSocket + SSE).

---

## 4. File layout

```
td-mcp/
├── pyproject.toml            # version (single source of truth) + deps / extras
├── setup_env.ps1             # one-shot env bootstrap (pins Python 3.11.10)
├── .gitattributes            # normalize line endings to LF
├── .github/workflows/ci.yml  # GitHub Actions: runs `uv run pytest`
├── LICENSE                   # MIT
├── README.md / ARCHITECTURE.md / HOW_TO_USE.md / SUMMARY.md / COMMIT.md / CONTRIBUTING.md
├── CHANGELOG.md              # versioned change log
├── TD_MCP_Master_Plan.md / TouchDesigner_MCP_Servers.md / TouchDesigner_Links.md
├── repomix.config.json       # optional full source pack config
├── scripts/
│   ├── generate_summary.py   # code-free SUMMARY.md (list/architecture/tree/per-file)
│   └── bump_version.py       # bump pyproject + top CHANGELOG heading in one step
├── td_mcp/
│   ├── server_offline.py     # offline doc/RAG + build/verify MCP server (45 tools)
│   ├── server_live.py        # Streamable-HTTP/SSE/stdio MCP server (39 tools)
│   ├── streamable_http.py    # Streamable-HTTP transport mixin
│   ├── heal.py / validation.py / scoring.py   # self-healing build pipeline
│   ├── generators.py         # artist network generators
│   ├── eval.py / compat.py / perf.py / progress.py / bundle.py / macro.py
│   ├── memory.py / config_gen.py / recipe_vault.py / discover.py / prompts.py
│   ├── vision.py / glsl_patterns.py / spatial.py
│   ├── tdn/                  # diffable YAML (TDN) serialization
│   ├── showcontrol/          # show-control network planners
│   ├── led_mapping/          # LED pixel layout + DMX mapping
│   ├── tools/                # risk / recovery / logs / layout helpers
│   ├── rag/                  # retriever, strategies (RRF), rerank, knowledge_graph, eval
│   └── kb/                   # corpus records, build_kb, import_corpus, scrape, build_index
├── bridge/                   # paste into TouchDesigner Text DATs (not a pip package)
│   ├── td_mcp_bridge.py      # TD-side bridge server (HTTP/WS/SSE/chat UI)
│   ├── td_mcp_agent.py       # TD-side autonomous agent
│   ├── chat_ui.html          # glassmorphic chat UI (served at GET /)
│   └── bootstrap.py          # one-click bootstrap helper
├── skills/td-building/       # agent skill (SKILL.md)
└── tests/                    # pytest suite (150+ tests)
```

---

## 5. Principles (non-negotiable)

- **Local-first & offline-capable**: the offline server needs no TD and no cloud.
- **Pure Python, fully testable**: `td_mcp/` is stdlib + optional
  `mcp` / `networkx` / `sentence-transformers`; no test requires a running TD.
- **Lazy upgrades**: dense / HyDE / rerank / scrape are opt-in and never forced.
- **Zero-dependency bridge/agent**: `bridge/*.py` run inside TD with built-ins only.
- **Version-aware**: every answer respects operator / Python-API build minimums.
