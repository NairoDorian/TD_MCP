# TouchDesigner MCP (TD_MCP)

Local-first **TouchDesigner MCP (Model Context Protocol)** that fuses the best ideas from the TD-MCP ecosystem into one tool:

- **Offline doc/RAG server** (`td_mcp.server_offline`) — **parallel multi-RAG**: several retrieval backends (global BM25, per-source BM25 for operators/python/glsl/tutorials, optional MiniLM dense + HyDE, title boost) run concurrently and fuse via **Reciprocal Rank Fusion**, with an optional CrossEncoder reranker. Version-aware, no cloud/keys.
- **Eval harness** (`td_mcp.rag.eval`) — recall@k / MRR / nDCG over labelled queries, so quality is provable and regressions are caught. Includes 1091 chunks (TOP 49 / CHOP 35 / SOP 32 / DAT 24 / Python 22 / COMP 21 / tutorial 19 / POP 8 / GLSL 6 / etc. seed + tdmcp/bottobot corpora): **k=5 recall 0.966**, zero version-gating violations.
- **Live Bridge MCP Server & CLI Client** (`td_mcp.server_live`) + **TD-side Bridge** (`bridge/td_mcp_bridge.py`) — control a running TD session over HTTP using stdio MCP, **Streamable HTTP + SSE** (multi-session, DNS-rebind guard), or the legacy CLI. Exposes **39 live tools** (create/delete/wire/inspect/read/verify/snapshot/recipe/timeline…). Every mutation is wrapped in `ui.undo` so one Ctrl+Z reverts a whole agent batch. Features spatial context markers (`*here` and `*this`) to resolve the currently active network or selected node.
- **Natively Embedded Chat UI Panel** (`bridge/chat_ui.html`) — a zero-dependency glassmorphic chat panel served by the bridge at `GET /`, with provider selector (Ollama / Gemini / OpenAI), live network sidebar, and a multi-step autonomous agent loop.
- **Autonomous in-TouchDesigner Agent** (`bridge/td_mcp_agent.py`) — a zero-dependency OpenAI-compatible agent script that runs natively inside TD Text DATs. Allows you to chat offline (using local Ollama) or online (Gemini/OpenAI) to build networks autonomously.

Design notes and the full server catalog are located in the repository root:
- [TouchDesigner_MCP_Servers.md](file:///c:/Users/Z/Downloads/PROJECTS/TOUCHDESIGNER/td-mcp/TouchDesigner_MCP_Servers.md) — catalog of every TD MCP server + brainstorm
- [TD_MCP_Master_Plan.md](file:///c:/Users/Z/Downloads/PROJECTS/TOUCHDESIGNER/td-mcp/TD_MCP_Master_Plan.md) — the master plan and future roadmap this scaffold implements
- [TouchDesigner_Links.md](file:///c:/Users/Z/Downloads/PROJECTS/TOUCHDESIGNER/td-mcp/TouchDesigner_Links.md) — official docs / Python API / curriculum

## Layout

```
td-mcp/
├── pyproject.toml
├── HOW_TO_USE.md
├── README.md
├── setup_env.ps1
├── td_mcp/
│   ├── server_offline.py     # doc/RAG MCP server + CLI (35 tools, ParallelRetriever)
│   ├── server_live.py        # Streamable HTTP + SSE + stdio client for the TD bridge (39 tools)
│   ├── streamable_http.py    # Streamable-HTTP transport mixin (SSE, sessions, DNS-rebind guard)
│   ├── generators.py         # artist network generators (feedback / audio-reactive / particle / 3D / GLSL / LED / DMX)
│   ├── recipe_vault.py       # recipe blueprint storage
│   ├── eval.py               # offline build eval gate
│   ├── rag/
│   │   ├── retriever.py      # Index (BM25 + dense + version resolver)
│   │   ├── strategies.py     # per-source + dense + HyDE strategies, RRF fusion
│   │   ├── rerank.py         # optional CrossEncoder reranker
│   │   ├── knowledge_graph.py # Graph-RAG related-operator walk
│   │   └── eval.py           # recall@k / MRR / nDCG harness
│   ├── tdn/                  # Diffable YAML (TDN) importer/exporter
│   ├── showcontrol/          # Show-control network builders (Art-Net / sACN / OSC / MIDI / timecode)
│   ├── led_mapping/          # LED pixel layout matrices and DMX mapping
│   ├── tools/risk.py         # risk-tier classification (READ_ONLY / WRITE_ADDITIVE / DESTRUCTIVE)
│   └── kb/
│       ├── chunks.jsonl      # 1091 built chunks (real TD facts, source+min_version+aliases)
│       ├── corpus.py         # operator / Python / GLSL / version corpus records
│       ├── build_kb.py       # regenerates chunks.jsonl from authored records
│       ├── import_corpus.py  # import authored JSON corpus records
│       ├── embeddings.jsonl  # MiniLM vectors (after TD_MCP_DENSE=1)
│       ├── scrape.py         # crawl docs.derivative.ca
│       └── build_index.py    # validate / dense-embed
├── bridge/
│   ├── td_mcp_bridge.py      # paste into a Text DAT in TD (bridge server: JSON-RPC, WS, SSE, chat UI)
│   ├── td_mcp_agent.py       # paste into a Text DAT in TD (autonomous builder agent, ~48 schemas)
│   ├── chat_ui.html          # glassmorphic chat UI panel served at GET /
│   └── bootstrap.py          # one-click bootstrap helper
└── skills/td-building/       # Claude Code skill
```

## Quick start (no deps)

`uv` is enough — the retriever is pure standard library.

```bash
uv run python td_mcp/rag/retriever.py "blur top parameters"
uv run python -m td_mcp.server_offline "movie file in top param file"
uv run python -m td_mcp.server_offline --family TOP        # list all TOPs
uv run python -m td_mcp.server_offline --parameter "movie"  # param spec by nickname
uv run python -m td_mcp.server_offline --glossary          # full KB index
```

## Run as MCP Servers

Register in your AI client (Claude Desktop: `%APPDATA%\Claude\claude_desktop_config.json`; Cursor: `%USERPROFILE%\.cursor\mcp.json`):

```json
{
  "mcpServers": {
    "td-mcp-offline": {
      "command": "uv",
      "args": ["run", "--project", "C:/Users/Z/Downloads/PROJECTS/TOUCHDESIGNER/td-mcp", "td-mcp-offline", "--mcp"]
    },
    "td-mcp-live": {
      "command": "uv",
      "args": ["run", "--project", "C:/Users/Z/Downloads/PROJECTS/TOUCHDESIGNER/td-mcp", "td-mcp-live", "--mcp"],
      "env": {
        "TD_MCP_AUTH_TOKEN": "YOUR_AUTO_GENERATED_TOKEN"
      }
    }
  }
}
```

Exposed Offline Tools (35): `td_docs_search`, `td_docs_operator`, `td_docs_python`, `td_docs_glsl`, `td_docs_template`, `td_docs_version`, `td_docs_family`, `td_docs_parameter`, `td_docs_compare`, `td_docs_connections`, `td_docs_workflow`, `td_docs_version_info`, `td_docs_related`, `td_docs_glossary`, `td_build_network`, `td_showcontrol_plan`, `td_led_map`, `td_build_feedback`, `td_build_audio_reactive`, `td_build_particle`, `td_build_3d_scene`, `td_build_glsl_shader`, `td_build_led_wall`, `td_build_dmx_fixture`, `td_glsl_pattern`, `td_network_template`, `td_expert_prompt`, `td_compat_check`, `td_score_build`, `td_mediaserver`, `td_analyze_performance`, `td_discover`, `td_memory_save`, `td_memory_recall`, `td_scaffold_recipe`.

Exposed Live Tools (39): `create_node`, `delete_node`, `set_parameters`, `get_parameters`, `get_errors`, `execute_python`, `list_nodes`, `project_info`, `capture_viewport`, `get_resource`, `describe_td_tools`, `batch`, `read_chop`, `read_top`, `read_dat`, `scan_network`, `build_and_verify`, `connect_nodes`, `rename_node`, `copy_node`, `auto_layout`, `get_node`, `set_node_color`, `set_node_comment`, `map_network`, `disconnect_nodes`, `get_connections`, `exec_node_method`, `snapshot_network`, `restore_network`, `get_performance`, `validate_network`, `set_flags`, `find_nodes`, `set_node_position`, `timeline`, `export_recipe`, `import_recipe`, `save_tox`.

## Control a live TouchDesigner

1. In TD, create a Text DAT, paste `bridge/td_mcp_bridge.py`, and run:
   `op('text1').module.start()` — it listens on `127.0.0.1:9980` and prints the authorization token.
2. From the shell (CLI mode):

```bash
$env:TD_MCP_AUTH_TOKEN="YOUR_AUTO_GENERATED_TOKEN"
uv run td-mcp-live status
uv run td-mcp-live create /project1 CircleTOP --name my_circle
uv run td-mcp-live set /project1/my_circle '{"radius": 0.5}'
uv run td-mcp-live exec "print([c.name for c in op('/project1').children])"
```

### Streamable HTTP mode

`td-mcp-live` can also run as a Streamable-HTTP MCP server (POST `/` JSON-RPC +
GET `/` SSE, multi-session via `Mcp-Session-Id`, DNS-rebind guard). Point an
HTTP-capable MCP client at `http://127.0.0.1:8765`:

```bash
uv run td-mcp-live --http
```

## Grow the knowledge base

`chunks.jsonl` is generated by `td_mcp/kb/build_kb.py` from a curated set of
authored records (operators across every family, Python classes, GLSL, and
recipes). Edit the records there and regenerate — never hand-edit `chunks.jsonl`.

```bash
uv run python -m td_mcp.kb.build_kb          # author -> chunks.jsonl (216 chunks)
uv run python -m td_mcp.rag.eval             # re-check recall/MRR/nDCG
uv add --optional scrape
uv run python -m td_mcp.kb.scrape
uv run python -m td_mcp.kb.build_index
TD_MCP_DENSE=1 uv run python -m td_mcp.kb.build_index   # all-MiniLM upgrade
```

## Upgrade retrieval quality (opt-in, no forced download)

```bash
uv pip install -e ".[rag]"                       # sentence_transformers
TD_MCP_DENSE=1 uv run python -m td_mcp.kb.build_index   # encode + write embeddings.jsonl
TD_MCP_DENSE=1 uv run td-mcp-offline "blur top parameters"   # dense + HyDE strategies now active
TD_MCP_RERANK=1 uv run td-mcp-offline "..."   # late-stage CrossEncoder rerank
```

The dense/HyDE/rerank paths are lazy: without `.[rag]` and `TD_MCP_DENSE=1`
the server still runs (TF-IDF + BM25 + title + per-source RRF fusion).

## Evaluate (catch regressions)

```bash
uv run python -m td_mcp.rag.eval                 # zero-dep: k=5 recall 1.0
TD_MCP_DENSE=1 uv run python -m td_mcp.rag.eval --k 5
```

## Fuse an EXTERNAL RAG server (multi-process, in parallel)

Beyond per-source local backends, the ParallelRetriever can fold in a
**separate RAG process** — e.g. `cacheflowe/td-docs-mcp` or
`bottobot` running as its own MCP server. Both run concurrently
and are merged by RRF into one answer. This is the "combine
multiple RAGs in parallel" design: two independent systems, one fused
result, the slow stdio round-trip hidden behind the lexical threads.

```bash
TD_MCP_REMOTE_MCP="uv run td-docs-mcp" uv run td-mcp-offline "blur top"
# optional: TD_MCP_REMOTE_TOOL / TD_MCP_REMOTE_ARG to match its tool name
```

The external hits appear in results with `source: "remote:..."`.

## Tests

```bash
uv run python -m tests.test_rag          # recall/version/per-source + local+remote fusion
uv run python -m tests.test_mcp_server   # server wiring: 6 tools, search returns KB
```

`tests/fake_remote_mcp.py` is a tiny stdio MCP server the
fusion test uses to exercise the multi-process path without a real
external install. (Note: the mcp 1.28 stdio transport has an
internal pydantic quirk on some builds, so the server test calls
handlers in-process rather than over the wire.)

## Anchor live mutations to docs ("shots")

```bash
uv run td-mcp-live --anchor create /project1 "Blur TOP" TOP
```

Each OK response gets a `shots` field: the most relevant KB chunk(s),
so the LLM sees doc-truth next to what it just did in TD.

## Port map (avoid clashes)

`td-mcp` bridge = **9980** (same as Pantani tdmcp & benoitliard
touch-mcp — run only one, or change `TD_MCP_PORT`).  Others in the
ecosystem: Embody 9870, johnsabath 9988, twozero 40404,
superdwayne 8053.

## License

MIT. Note: `TrueFiasco/TD_Builder_alpha` (a source of the hybrid-RAG
idea) is **AGPL-3.0** — the *techniques* here are reimplemented, not
copied, so MIT stays clean.
