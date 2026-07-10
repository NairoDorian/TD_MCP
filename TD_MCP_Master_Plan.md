# TouchDesigner MCP & RAG "Ultimate" Master Plan

This document outlines the current state and future vision of the TouchDesigner Model Context Protocol (MCP) and Retrieval-Augmented Generation (RAG) ecosystem. It serves as the master specification for creating a seamless, production-ready AI co-pilot running inside and alongside TouchDesigner.

---

## 1. Executive Summary

The TouchDesigner MCP environment bridges the gap between large language models and TouchDesigner's visual programming environment. By combining a **local-first documentation RAG server**, a **live bidirectional control bridge**, and a **native TouchDesigner-resident agent**, it enables developers and artists to query reference materials and generate complex, working interactive networks autonomously using natural language.

---

## 2. Current State of the Project

We have successfully implemented and verified the primary components of this ecosystem:

### 2.1 Split MCP Architecture
- **`td-mcp-offline` (Docs/RAG Server)**:
  - Operates as a zero-cloud-dependency stdio MCP server.
  - Implements a parallel multi-RAG retriever: queries global BM25, per-source BM25 (operators, Python API, GLSL, tutorials), and optional dense vector embeddings (`all-MiniLM-L6-v2`) in parallel, fusing results via Reciprocal Rank Fusion (RRF).
  - Includes a pre-built database of **1091 curated chunks** (covering operator definitions, compatibility tables, and tutorial recipes), achieving a benchmark **recall@5 of 0.966**.
  - Provides mathematical templates for LED pixel layouts, universe configurations, DMX patch lists, and show-control protocols.
- **`td-mcp-live` (Bridge Client Server)**:
  - Connects to a running TouchDesigner instance over a local HTTP connection (port `9980`).
  - Exposes **39 live control tools** covering creation, deletion, wiring, inspection, reading, verification, recipes, snapshots, validation, performance, and timeline control (e.g. `create_node`, `set_parameters`, `connect_nodes`, `scan_network`, `build_and_verify`, `map_network`, `snapshot_network`, `export_recipe`/`import_recipe`, `validate_network`, `get_performance`, `timeline`).
  - Implements the self-correcting `build_and_verify` loop: creates nodes, checks parameter correctness, catches cook errors, and renders viewport thumbnails to compute an `is_flat`/`is_black`/`fully-transparent` quality verdict.
  - Features **Spatial Context Pointers**: Resolves `*here` (current pane network path) and `*this` (currently selected operator) on the fly via TouchDesigner's Python `ui` API.
  - Also runs in **Streamable-HTTP mode** (`--http`): POST `/` JSON-RPC + GET `/` SSE, multi-session via `Mcp-Session-Id`, DNS-rebind guard, so HTTP-capable MCP clients can connect on `127.0.0.1:8765`.

### 2.2 Live TouchDesigner Bridge (`bridge/td_mcp_bridge.py`)
- Pasted inside TouchDesigner as a zero-dependency script (using only built-in libraries like `http.server`, `json`, `base64`, `hmac`).
- Wraps every network change or tool batch in TouchDesigner's `ui.undo` system, making any agent-driven action revertible with a single `Ctrl+Z`.
- Runs securely on localhost with auto-generated auth tokens (constant-time `hmac.compare_digest` compare), loopback-only CORS, an `execute_python` exec gate (`TD_MCP_ALLOW_EXEC`), and `TD_MCP_PROTECTED_PATHS` shielding.
- Pure-Python RFC 6455 **WebSocket** streaming (real-time tool-activity push to the chat UI) plus SSE, JSON-RPC, and `td://` resource reads.
- Serves a **natively embedded glassmorphic Chat UI panel** (`bridge/chat_ui.html`) at `GET /` — provider selector (Ollama / Gemini / OpenAI), live network sidebar, health indicator, and a multi-step autonomous agent loop.
- Every error carries structured `recovery_hints` (`{cause, action, next_tools}`) so an agent can self-correct instead of retrying blindly.

### 2.3 Autonomous in-App Agent (`bridge/td_mcp_agent.py`)
- A zero-dependency OpenAI-compatible agent script (v2.0) running inside TouchDesigner Text DATs.
- Supports **~48 function-calling tool schemas** to query the bridge directly (all 39 live bridge tools plus macro record/replay, `plan_task`, `ask_clarification`, `save_session`/`load_session`, `execute_parallel`, and `caption_viewport` vision debugging).
- New agent capabilities: task **planning & decomposition** (plan → execute → verify → replan), interactive **clarification** when a request is ambiguous, **session memory & persistence**, **parallel tool execution**, **macro record/replay**, and smarter **error recovery** with fallback strategies.
- Fuses with **Ollama** (100% offline, local, key-free models like `qwen2.5:3b`), **Gemini API** (using the OpenAI compatibility endpoint), or **OpenAI API** to allow in-app chat commands to build networks autonomously.
- `caption_viewport` captures a node viewer and asks a vision-capable model to describe the render and detect black/flat/broken/shader errors, feeding the verdict back into the self-correction loop.

---

## 3. Current Status vs. Roadmap

The original roadmap has largely shipped. ✅ = done, 🔶 = partial, ⬜ = pending.

| # | Feature | Status |
|---|---------|--------|
| 3.1 | Natively Embedded Chat UI Panel | ✅ `bridge/chat_ui.html` served at `GET /` — glassmorphic chat, provider selector, live network sidebar, health indicator, multi-step agent loop. |
| 3.2 | Live Project RAG (workspace context) | ✅ `scan_network` tool + agent auto-injects the live topology into the system prompt at session start. |
| 3.3 | Multimodal Vision Debugging | 🔶 `capture_viewport` returns `is_black`/`is_flat`/`fully-transparent` verdicts; `caption_viewport` (agent) sends a screenshot to a vision model for critique. |
| 3.4 | WebSocket / real-time streaming | ✅ Pure-Python RFC 6455 WebSocket in the bridge (push tool activity to the chat UI) + SSE in the Streamable-HTTP server. |
| 3.5 | Single-Click Offline Installer | ⬜ `setup_env.ps1` configures the Python env; a full one-click Ollama + editor configurator is still planned. |

### Remaining / next-up
- **Single-click installer** (`3.5`): auto-install Ollama, pull `qwen2.5:3b`, and write the Claude Desktop / Cursor MCP config in one step.
- **Richer vision loop** (`3.3`): push the `caption_viewport` verdict automatically into the build-and-verify self-correction loop for fully autonomous visual healing.
- **WebRTC** option for lowest-latency streaming (currently WebSocket + SSE).

---

## 4. Current File Layout

With the documentation files moved to the root directory for maximum accessibility, the repository structure is:
```
td-mcp/
├── pyproject.toml              # Build and package config
├── README.md                   # Quick start, tool listings, and RAG configuration
├── HOW_TO_USE.md               # User guide for running the bridge & configuration
├── CHANGELOG.md                # Project version history
├── TD_MCP_Master_Plan.md       # Consolidated design specification & future roadmap
├── TouchDesigner_Links.md      # TouchDesigner official docs and Python references
├── TouchDesigner_MCP_Servers.md# Catalog of existing TouchDesigner MCP repos
├── setup_env.ps1               # Automated local virtualenv configuration
├── td_mcp/                     # Core python source package
│   ├── server_offline.py       # Offline docs RAG server (40 tools)
│   ├── server_live.py          # Live bridge client (39 tools) + Streamable HTTP/SSE
│   ├── streamable_http.py      # Streamable-HTTP transport mixin
│   ├── generators.py           # Artist network generators
│   ├── recipe_vault.py         # Recipe blueprint storage
│   ├── eval.py                 # Offline build eval gate
│   ├── tdn/                    # Diffable YAML (TDN) importer/exporter
│   ├── showcontrol/            # Show-control network builders
│   ├── led_mapping/            # LED pixel layout matrices and DMX mapping
│   ├── tools/risk.py           # Risk-tier classification
│   ├── rag/                    # BM25/dense/HyDE retrieval, RRF fusion, rerank, graph
│   └── kb/                     # Curated Knowledge Base and indexers
└── bridge/                     # Zero-dependency TouchDesigner-side files
    ├── td_mcp_bridge.py        # Pasted bridge server Text DAT (HTTP/WS/SSE/chat UI)
    ├── td_mcp_agent.py         # Pasted autonomous chat agent Text DAT (v2.0)
    ├── chat_ui.html            # Glassmorphic chat UI panel (served at GET /)
    └── bootstrap.py            # One-click bootstrap helper
```
