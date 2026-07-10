# TouchDesigner MCP (`td-mcp`)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)](#tests)

**Local-first TouchDesigner [MCP](https://modelcontextprotocol.io) toolkit** that unifies the
best ideas of the public TD‑MCP ecosystem into one cohesive package:

- **Offline doc / RAG server** — version‑aware retrieval over a merged MIT corpus (no TouchDesigner required). Several lexical + semantic backends run **in parallel** and fuse via Reciprocal Rank Fusion, with an optional CrossEncoder reranker and optional external RAG fusion.
- **Eval harness** — recall@k / MRR / nDCG over a labelled query set, so retrieval quality is provable and regressions are caught. **k=5 recall ≈ 0.966**, zero version‑gating violations.
- **Live bridge + MCP server** — control a running TouchDesigner session (create / wire / inspect / verify) over **Streamable HTTP + SSE**, WebSocket, or stdio. Exposes **39 live tools**; every mutation is wrapped in `ui.undo` so one Ctrl+Z reverts a whole agent batch.
- **Natively embedded chat UI** and a **zero‑dependency autonomous agent** that run *inside* TouchDesigner Text DATs.

> Everything in `td_mcp/` is pure Python (standard library + optional `mcp` / `networkx` / `sentence-transformers`) and is fully unit‑testable without a running TouchDesigner.

---

## Table of contents

- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Installation](#installation)
- [Run as MCP servers](#run-as-mcp-servers)
- [Control a live TouchDesigner](#control-a-live-touchdesigner)
- [Grow the knowledge base](#grow-the-knowledge-base)
- [Upgrade retrieval quality (opt‑in)](#upgrade-retrieval-quality-opt-in)
- [Evaluate](#evaluate)
- [Fuse an external RAG server](#fuse-an-external-rag-server)
- [Project layout](#project-layout)
- [Tool catalog](#tool-catalog)
- [Documentation](#documentation)
- [Tests](#tests)
- [License](#license)

---

## Architecture

`td-mcp` is built around **two cooperating servers that share one authoring brain**:

| | Offline server | Live server / bridge |
|---|---|---|
| Module | `td_mcp/server_offline.py` | `td_mcp/server_live.py` + `bridge/td_mcp_bridge.py` |
| Needs TouchDesigner? | **No** | **Yes** (running instance) |
| Role | Doc/RAG answers, network *generation* (YAML, not live nodes), validation, scoring, self‑heal | Create / delete / wire / inspect a live TD document over HTTP / stdio |
| Tools | 40 (`td_*`) | 39 (`create_node`, `set_parameters`, …) |

The offline side owns the *intelligence*: `generators` → `validation` → `scoring` → `heal`
produce a diffable network description (**TDN** YAML) that the live bridge materialises inside
TD. `tdn` (diffable YAML), `showcontrol` and `led_mapping` are deterministic pure‑Python
planning layers; `rag` + `kb` provide the version‑aware retrieval backbone; `tools/` holds
cross‑cutting risk / recovery / log / layout helpers.

**Three request lifecycles**

1. **Offline doc query** — `server_offline` → `ParallelRetriever` (global + per‑source BM25,
   optional MiniLM dense + HyDE, optional CrossEncoder rerank, optional external RAG fused via
   RRF) → ranked chunks.
2. **Offline build + verify** — `_parse_build_spec` → `td_build_network` (generators) →
   `td_score_build` → `td_validate_build` → `td_self_heal` (no TD needed).
3. **Live mutation** — MCP client → `server_live` (Streamable HTTP / SSE / stdio) → bridge
   dispatch table inside TD, every mutation wrapped in `ui.undo`.

Retrieval is *local‑first and version‑aware*: the merged MIT corpus (`td_mcp/kb/corpus/`) plus
the hand‑authored `chunks.jsonl` back every answer, and the dense / HyDE / rerank paths are
lazy (enabled only with the `[rag]` extra + env flags).

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full module map and request lifecycles, and
[`SUMMARY.md`](SUMMARY.md) for a code‑free, file‑by‑file overview of the whole repository.

---

## Quick start (no deps)

`uv` is enough — the retriever is pure standard library:

```bash
uv run python td_mcp/rag/retriever.py "blur top parameters"
uv run python -m td_mcp.server_offline "movie file in top param file"
uv run python -m td_mcp.server_offline --family TOP        # list all TOPs
uv run python -m td_mcp.server_offline --parameter "movie"  # param spec by nickname
uv run python -m td_mcp.server_offline --glossary          # full KB index
```

---

## Installation

```bash
git clone <your-fork-url> td-mcp
cd td-mcp

# One-shot environment matching TouchDesigner's Python (3.11.10) via uv:
powershell -ExecutionPolicy Bypass -File setup_env.ps1

# Or manually:
uv venv --python 3.11.10
uv pip install -e ".[mcp]"          # base + MCP server
```

Optional extras:

```bash
uv pip install -e ".[rag]"           # sentence-transformers + networkx (dense / rerank / graph-RAG)
uv pip install -e ".[scrape]"        # requests + beautifulsoup4 (doc crawler)
```

---

## Run as MCP servers

Register in your AI client (Claude Desktop: `%APPDATA%\Claude\claude_desktop_config.json`;
Cursor: `%USERPROFILE%\.cursor\mcp.json`). Replace `<REPO_DIR>` with the absolute path to this
repo:

```json
{
  "mcpServers": {
    "td-mcp-offline": {
      "command": "uv",
      "args": ["run", "--project", "<REPO_DIR>", "td-mcp-offline", "--mcp"]
    },
    "td-mcp-live": {
      "command": "uv",
      "args": ["run", "--project", "<REPO_DIR>", "td-mcp-live", "--mcp"],
      "env": {
        "TD_MCP_AUTH_TOKEN": "YOUR_AUTO_GENERATED_TOKEN"
      }
    }
  }
}
```

A ready‑made config can be generated for you (no hand‑editing) with
`uv run python -m td_mcp.config_gen`.

---

## Control a live TouchDesigner

1. In TD, create a **Text DAT**, paste [`bridge/td_mcp_bridge.py`](bridge/td_mcp_bridge.py), and run:
   `op('text1').module.start()` — it listens on `127.0.0.1:9980` and prints the auth token.
2. From the shell (CLI mode):

```bash
$env:TD_MCP_AUTH_TOKEN="YOUR_AUTO_GENERATED_TOKEN"
uv run td-mcp-live status
uv run td-mcp-live create /project1 CircleTOP --name my_circle
uv run td-mcp-live set /project1/my_circle '{"radius": 0.5}'
uv run td-mcp-live exec "print([c.name for c in op('/project1').children])"
```

### Streamable HTTP mode

`td-mcp-live` can also run as a Streamable‑HTTP MCP server (`POST /` JSON‑RPC + `GET /` SSE,
multi‑session via `Mcp-Session-Id`, DNS‑rebind guard) on `127.0.0.1:8765`:

```bash
uv run td-mcp-live --http
```

### Spatial context markers

Reference your live workspace without hard‑coding paths:

- **`*here`** — the network pane you currently have open (e.g. `/project1`).
- **`*this`** — the currently selected operator in your active network pane.

> *"Add a Blur TOP under `*here` and connect it to `*this`."*

### Verification loop

`build_and_verify` creates a node, sets parameters, checks cook errors, and renders a viewport
thumbnail with an `is_black` / `is_flat` verdict — so the agent can self‑heal a broken render.

---

## Grow the knowledge base

`chunks.jsonl` (1,091 chunks) is **generated** from curated records — never hand‑edited.

```bash
uv run python -m td_mcp.kb.build_kb          # curated records -> chunks.jsonl
uv run python -m td_mcp.rag.eval             # re-check recall / MRR / nDCG
uv add --optional scrape
uv run python -m td_mcp.kb.scrape            # crawl docs.derivative.ca (optional)
uv run python -m td_mcp.kb.build_index       # validate / dense-embed
```

---

## Upgrade retrieval quality (opt‑in, no forced download)

```bash
uv pip install -e ".[rag]"                       # sentence_transformers + networkx
TD_MCP_DENSE=1 uv run python -m td_mcp.kb.build_index   # encode + write embeddings.jsonl
TD_MCP_DENSE=1 uv run td-mcp-offline "blur top parameters"   # dense + HyDE now active
TD_MCP_RERANK=1 uv run td-mcp-offline "..."       # late-stage CrossEncoder rerank
```

Without `[rag]` and `TD_MCP_DENSE=1` the server still runs (BM25 + TF‑IDF cosine + title boost +
per‑source RRF fusion).

---

## Evaluate

```bash
uv run python -m td_mcp.rag.eval                 # zero-dep: k=5 recall ≈ 0.966
TD_MCP_DENSE=1 uv run python -m td_mcp.rag.eval --k 5
```

---

## Fuse an external RAG server

The `ParallelRetriever` can fold in a separate RAG process (e.g. `cacheflowe/td-docs-mcp` or
`bottobot`) launched over stdio. Both run concurrently and are merged by RRF into one answer.

```bash
TD_MCP_REMOTE_MCP="uv run td-docs-mcp" uv run td-mcp-offline "blur top"
# optional: TD_MCP_REMOTE_TOOL / TD_MCP_REMOTE_ARG to match its tool name
```

---

## Project layout

```
td-mcp/
├── pyproject.toml            # deps: pyyaml/mcp/anyio (base) + networkx/sentence-transformers (rag extra)
├── setup_env.ps1             # one-shot env bootstrap (pins Python 3.11.10)
├── repomix.config.json       # config for `repomix` full source pack
├── scripts/
│   └── generate_summary.py   # generates SUMMARY.md (code-free file/architecture overview)
├── td_mcp/
│   ├── server_offline.py     # offline doc/RAG + build/verify MCP server (40 tools)
│   ├── server_live.py        # Streamable-HTTP/SSE/stdio MCP server for the bridge (39 tools)
│   ├── streamable_http.py    # Streamable-HTTP transport mixin (SSE, sessions, DNS-rebind guard)
│   ├── heal.py                # self-healing orchestrator: validate → score → auto-repair → hints
│   ├── validation.py          # 5-stage build validation + auto-repair (pure, TD-free)
│   ├── scoring.py             # score_build (0..100 A–F) + repair_network
│   ├── generators.py          # artist network generators (feedback/audio/particle/3D/GLSL/LED/DMX/video/midi/kinect)
│   ├── eval.py                # offline build eval gate (TrendGate, metrics)
│   ├── compat.py              # version-compat checks + connection-error cache
│   ├── perf.py                # performance-snapshot analyzer
│   ├── progress.py            # token-efficient progress reporting
│   ├── bundle.py              # .mcpb project bundling (zip-slip guarded)
│   ├── macro.py               # macro record/replay
│   ├── memory.py              # session memory (cross-session continuity)
│   ├── config_gen.py          # per-client .mcp.json / skill generation
│   ├── recipe_vault.py        # recipe blueprint storage
│   ├── discover.py            # multi-instance TD discovery (injectable probe)
│   ├── prompts.py             # expert prompts per build phase
│   ├── vision.py              # viewport caption / histogram analysis
│   ├── glsl_patterns.py       # GLSL pattern + template helpers
│   ├── spatial.py             # *here / *this / *this op resolution helpers
│   ├── tdn/                   # Diffable YAML (TDN) serialization (new_network/operator/export/import/diff/checkpoint)
│   ├── showcontrol/           # show-control network planners (Art-Net/sACN/OSC/MIDI/timecode/media-server)
│   ├── led_mapping/           # LED pixel layout matrices + DMX channel export
│   ├── tools/
│   │   ├── risk.py            # risk-tier classification (READ_ONLY / WRITE_ADDITIVE / WRITE_CHECKPOINT / DESTRUCTIVE)
│   │   ├── recovery.py        # recovery hints (Embody-style)
│   │   ├── logs.py            # token-efficient ring-buffer logs
│   │   └── layout.py          # network layout lint (overlap / origin / dock)
│   ├── rag/                   # retrieval: retriever (BM25+dense), strategies (RRF fusion), rerank, knowledge_graph, eval
│   └── kb/                    # corpus records, build_kb, import_corpus, scrape, build_index, chunks.jsonl
├── bridge/
│   ├── td_mcp_bridge.py       # paste into a Text DAT in TD (JSON-RPC/WS/SSE/chat UI server)
│   ├── td_mcp_agent.py        # paste into a Text DAT (autonomous builder agent)
│   ├── chat_ui.html           # glassmorphic chat panel served at GET /
│   └── bootstrap.py           # one-click bootstrap helper
├── skills/
│   └── td-building/           # Claude Code / agent skill (SKILL.md)
└── tests/                     # pytest suite (RAG fusion, validation, scoring, heal, bridge, etc.)
```

---

## Tool catalog

**Offline server (40 tools)** — `td_docs_search`, `td_docs_operator`, `td_docs_python`,
`td_docs_glsl`, `td_docs_template`, `td_docs_version`, `td_docs_family`, `td_docs_parameter`,
`td_docs_compare`, `td_docs_connections`, `td_docs_workflow`, `td_docs_version_info`,
`td_docs_related`, `td_docs_glossary`, `td_build_network`, `td_showcontrol_plan`, `td_led_map`,
`td_build_feedback`, `td_build_audio_reactive`, `td_build_particle`, `td_build_3d_scene`,
`td_build_glsl_shader`, `td_build_led_wall`, `td_build_dmx_fixture`, `td_build_video_pipeline`,
`td_build_midi_rig`, `td_build_kinect_skeleton`, `td_glsl_pattern`, `td_network_template`,
`td_expert_prompt`, `td_compat_check`, `td_score_build`, `td_validate_build`, `td_self_heal`,
`td_mediaserver`, `td_analyze_performance`, `td_discover`, `td_memory_save`, `td_memory_recall`,
`td_scaffold_recipe`.

**Live server (39 tools)** — `create_node`, `delete_node`, `set_parameters`, `get_parameters`,
`get_errors`, `execute_python`, `list_nodes`, `project_info`, `capture_viewport`, `get_resource`,
`describe_td_tools`, `batch`, `read_chop`, `read_top`, `read_dat`, `scan_network`,
`build_and_verify`, `connect_nodes`, `rename_node`, `copy_node`, `auto_layout`, `get_node`,
`set_node_color`, `set_node_comment`, `map_network`, `disconnect_nodes`, `get_connections`,
`exec_node_method`, `snapshot_network`, `restore_network`, `get_performance`, `validate_network`,
`set_flags`, `find_nodes`, `set_node_position`, `timeline`, `export_recipe`, `import_recipe`,
`save_tox`.

---

## Documentation

| File | Purpose |
|------|---------|
| [`README.md`](README.md) | This file — quick start, install, usage, tool catalog. |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Module map, two‑server model, request lifecycles, review status. |
| [`SUMMARY.md`](SUMMARY.md) | Code‑free, file‑by‑file overview of the whole repo (generated). |
| [`HOW_TO_USE.md`](HOW_TO_USE.md) | Step‑by‑step bridge setup, AI‑client config, autonomous agent. |
| [`CHANGELOG.md`](CHANGELOG.md) | Versioned change log. |
| [`TD_MCP_Master_Plan.md`](TD_MCP_Master_Plan.md) | Master plan / roadmap this scaffold implements. |
| [`TouchDesigner_MCP_Servers.md`](TouchDesigner_MCP_Servers.md) | Catalog + brainstorm of the TD‑MCP ecosystem. |
| [`TouchDesigner_Links.md`](TouchDesigner_Links.md) | Curated official docs / Python API / curriculum links. |

---

## Tests

```bash
uv run pytest                       # full suite
uv run python -m tests.test_rag     # retrieval fusion + version/per-source
uv run python -m tests.test_mcp_server
```

`tests/fake_remote_mcp.py` is a tiny stdio MCP server the fusion test uses to exercise the
multi‑process path without a real external install.

---

## License

MIT. See [`LICENSE`](LICENSE). Note: `TrueFiasco/TD_Builder_alpha` (a source of the hybrid‑RAG
idea) is **AGPL‑3.0** — the *techniques* here are reimplemented, not copied, so MIT stays clean.
