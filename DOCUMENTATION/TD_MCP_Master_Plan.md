# TD_MCP — Master Plan & Implementation Specification

> **Repository**: `NairoDorian/TD_MCP`
> **Goal**: A local-first, production-grade Model Context Protocol (MCP) server environment that (a) controls a live TouchDesigner session safely, and (b) answers documentation, parameter schemas, and Python API questions from a version-accurate local knowledge base (RAG) so the LLM never hallucinates.

---

## 1. Core Design Principles

1. **Local-first & Key-free** — Embeddings run on a lightweight local SentenceTransformer (`all-MiniLM-L6-v2`) with a fast numpy fallback. No cloud APIs, no developer costs.
2. **Split Architecture (Two Servers, One Config)** — Exposes `td-mcp-offline` (docs/RAG/build) and `td-mcp-live` (bridge control) separately so the live control tools are only loaded when TouchDesigner is open.
3. **Safe by Default** — Every live mutation is wrapped in TouchDesigner's `ui.undo` (one tool/batch = one Ctrl+Z). Auth tokens are auto-generated and CORS loopback-only is enforced. Risk-tiering annotations classify tool safety.
4. **Honest by Default** — Every code-generation response is anchored to retrieved operator specs ("shots"). Compatibility logs prevent the model from using 2025/experimental Vulkan features in older TD builds.
5. **Diffable Networks** — Networks are serialized to text (TDN/YAML) so builds are reviewable and restorable in Git.
6. **Eval-Gated Correctness** — An automated test harness ensures retrieval and wiring outputs are correct.

---

## 2. System Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  AI Client (Claude / Codex / Cursor / Copilot / Gemini / ...)│
└───────┬───────────────────────────────┬──────────────────────┘
        │ stdio                         │ stdio (MCP mode) or HTTP/WS (CLI)
        ▼                               ▼
┌────────────────┐              ┌─────────────────────────┐
│ td-mcp-offline │              │ td-mcp-live             │
│ ( RAG / Docs ) │              │ ( Live Bridge Client )  │
└──────┬─────────┘              └───────────┬─────────────┘
       │                                    │ HTTP (localhost:9980)
       ▼ (Vector + Lexical)                 ▼
┌────────────────┐              ┌─────────────────────────┐
│ Local KB       │              │ TouchDesigner Bridge    │
│ - 1091 Chunks  │              │ ( bridge/td_mcp_bridge) │
│ - operators.json              │ - Native ui.undo        │
│ - python_api.json             │ - Spatial markers       │
└────────────────┘              └─────────────────────────┘
```

### Module Layout
```
td-mcp/
├── pyproject.toml           # Setuptools package configuration
├── HOW_TO_USE.md            # User setup and configurations guide
├── CHANGELOG.md             # Versioning history
├── td_mcp/
│   ├── server_offline.py     # Offline RAG MCP server + CLI search
│   ├── server_live.py        # Live bridge MCP server + CLI client
│   ├── generators.py         # Artist-level network templates (feedback, led_wall, etc.)
│   ├── tdn/                  # Diffable YAML (TDN) importer/exporter
│   ├── showcontrol/          # Show control network builders
│   ├── led_mapping/          # LED pixel layout matrices and DMX mapping
│   ├── tools/risk.py         # TrueFiasco risk-tier definitions
│   └── kb/
│       ├── corpus/           # Merged operator, python class, and version JSONs
│       ├── chunks.jsonl      # 1091 RAG facts database
│       └── build_kb.py       # Rebuilds the chunk database
└── bridge/
    ├── td_mcp_bridge.py      # zero-dependency HTTP server pasted into TD
    └── td_mcp_agent.py       # zero-dependency autonomous agent pasted into TD
```

---

## 3. Merged Feature Catalog

### 3.1 Local Knowledge Base & RAG
- **Corpus Breadth**: Merged tdmcp (631) and bottobot (661) operator profiles into **1091 chunks** covering CHOP, TOP, SOP, DAT, COMP, MAT, and POP families.
- **RRF Parallel Search**: Integrates global BM25, per-source BM25, and optional dense embeddings (`all-MiniLM-L6-v2`) via Reciprocal Rank Fusion.
- **Graph RAG**: Builds a `networkx` knowledge graph of related operators to support workflow suggestion.
- **Retrieval Quality**: Automated evaluations report **recall@5 = 0.966**, with zero version violations.

### 3.2 Live TouchDesigner Control Bridge
- **Zero-Dependency Server**: Pasted into a Text DAT inside TouchDesigner, starting an `http.server.HTTPServer` on port `9980` with zero external pip dependencies.
- **Undo Integration**: Every mutation is wrapped in `with ui.undo:` blocks.
- **Spatial Markers**: Resolves `*here` (current editor pane COMP) and `*this` (current selected operator) using TD's `ui` API at runtime.
- **Detail Level Token Economy**: Read tools support `detailLevel=brief|normal|full` to save model context.
- **Verification Loop**: Exposes `build_and_verify` tool which creates a node, applies parameters, checks for cook errors, renders a viewport screenshot, and returns an `is_black` / `is_flat` quality verdict.

### 3.3 Autonomous in-TouchDesigner Agent
- **DAT-based Agent**: Pasting `bridge/td_mcp_agent.py` into TouchDesigner spawns a self-contained OpenAI-compatible agent that queries local Ollama (`qwen2.5:3b`), Gemini, or OpenAI, decodes tool calls, and manipulates the graph autonomously over the localhost bridge.

### 3.4 Show-Control & LED Pixel Mapping
- **Show Control planning**: Builders for sACN, Art-Net, OSC, MIDI, and timecode (LTC/MTC).
- **LED Mapping**: Layout coordinates and DMX channel-map generator math for rectangular walls, strips, and 3D voxel grids.
- **Artist Generators**: Ready-made generators (`td_build_led_wall` and `td_build_dmx_fixture`) to compile pixel mapping and show control pipelines inside TouchDesigner.

---

## 4. Verification & Testing

The repository runs a 30-suite test harness verifying every module.
```powershell
uv run pytest
```
- `test_agent.py` — Verifies in-TD agent schemas compile and fail gracefully.
- `test_corpus.py` — Validates operator/python dataset parsing and version resolvers.
- `test_live_server.py` — Assures the live MCP server compiles and registers all tools.
- `test_mcp_server.py` — Validates the offline server wiring and parallel retriever.
- `test_rag.py` — Checks vector + lexical RAG search relevance.
- `test_showcontrol.py` — Validates Art-Net/sACN show control plan outputs.
- `test_tdn.py` — Assures lossless YAML network serialization.
