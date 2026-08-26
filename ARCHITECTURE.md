# Architecture & Developer Notes — TD_MCP

This document explains how the `td-mcp` codebase is put together: the two
server models, the module map, the request lifecycles, and how to run/test
it. It complements the user-facing [`README.md`](./README.md),
[`HOW_TO_USE.md`](./HOW_TO_USE.md), and the catalog/brainstorm docs
([`TouchDesigner_MCP_Servers.md`](./TouchDesigner_MCP_Servers.md),
[`TD_MCP_Master_Plan.md`](./TD_MCP_Master_Plan.md)).

> Scope note: this is **local-first** software. The *offline* server needs no
> TouchDesigner at all; the *live* server needs a running TD with the bridge
> Text DAT pasted in. Everything in `td_mcp/` is pure Python (stdlib + optional
> `mcp`/`networkx`/`sentence-transformers`) and is fully unit-testable.

---

## 1. Two-server model

| | Offline server | Live server / bridge |
|---|---|---|
| Module | `td_mcp/server_offline.py` | `td_mcp/server_live.py` + `bridge/td_mcp_bridge.py` |
| Needs TD? | **No** | **Yes** (running instance) |
| Role | Doc/RAG answers, network *generation* (YAML, not live nodes), validation, scoring, self-heal | Create/delete/wire/inspect a live TD document over HTTP/stdio |
| Tools | 45 (`td_*`)| | 39 (`create_node`, `set_parameters`, …) |
| Transport | stdio MCP (`td-mcp-offline --mcp`) | Streamable HTTP + SSE (`td-mcp-live --http`) or legacy stdio CLI |

The two servers share the **same authoring brain**: `generators` → `validation`
→ `scoring` → `heal` produce a diffable network description (TDN YAML) that the
live bridge would then materialise inside TD.

---

## 2. Repository layout

```
td-mcp/
├── pyproject.toml            # deps: pyyaml/mcp/anyio (base) + networkx/sentence-transformers (rag extra)
├── setup_env.ps1             # one-shot env bootstrap (pins Python 3.11.10)
├── LICENSE                   # MIT
├── README.md / ARCHITECTURE.md / HOW_TO_USE.md / SUMMARY.md / COMMIT.md
├── CHANGELOG.md              # versioned change log
├── TD_MCP_Master_Plan.md / TouchDesigner_MCP_Servers.md / TouchDesigner_Links.md  # brainstorm/docs
├── repomix.config.json       # config for `repomix` full source pack (optional)
├── scripts/
│   └── generate_summary.py   # generates SUMMARY.md (code-free file/architecture overview)
├── td_mcp/
│   ├── server_offline.py     # offline doc/RAG + build/verify MCP server (45 tools)
│   ├── server_live.py        # Streamable-HTTP/SSE/stdio MCP server for the bridge (39 tools)
│   ├── heal.py               # self-healing orchestrator: validate → score → auto-repair → hints
│   ├── validation.py          # 5-stage build validation + auto-repair (pure, TD-free)
│   ├── scoring.py             # score_build (0..100 A–F) + repair_network
│   ├── generators.py         # artist network generators (feedback/audio/particle/3D/GLSL/LED/DMX/video/midi/kinect)
│   ├── eval.py               # offline build eval gate (TrendGate, metrics)
│   ├── compat.py             # version-compat checks + error cache
│   ├── perf.py               # performance-snapshot analyzer (accepts bridge `cooks` shape)
│   ├── progress.py           # token-efficient progress reporting
│   ├── bundle.py             # .mcpb project bundling (zip-slip guarded)
│   ├── macro.py              # macro record/replay
│   ├── memory.py             # session memory (cross-session continuity)
│   ├── config_gen.py         # per-client .mcp.json / skill generation (+ CLI `main`)
│   ├── recipe_vault.py       # recipe blueprint storage
│   ├── discover.py           # multi-instance TD discovery (injectable probe)
│   ├── prompts.py            # expert prompts per build phase
│   ├── vision.py             # viewport caption / histogram analysis
│   ├── glsl_patterns.py      # GLSL pattern + template helpers
│   ├── spatial.py            # *here / *this / *parent resolution helpers
│   ├── tdn/                  # Diffable YAML (TDN) serialization
│   ├── showcontrol/          # show-control network builders (Art-Net/sACN/OSC/MIDI/timecode/media-server)
│   ├── led_mapping/          # LED pixel layout matrices + DMX mapping
│   ├── tools/                # risk / recovery / logs / layout helpers
│   ├── rag/                  # retrieval: retriever (BM25+dense), strategies (RRF fusion), rerank, knowledge_graph, eval
│   └── kb/                   # corpus records, build_kb, import_corpus, scrape, build_index (chunks.jsonl is generated)
├── bridge/                   # paste into TouchDesigner Text DATs (not a pip package)
│   ├── td_mcp_bridge.py      # TD-side bridge server (JSON-RPC/WS/SSE/chat UI)
│   ├── td_mcp_agent.py       # TD-side autonomous builder agent
│   ├── chat_ui.html          # glassmorphic chat panel served at GET /
│   └── bootstrap.py          # builds the /td_mcp COMP inside TD; can emit td_mcp.tox
├── skills/
│   └── td-building/          # agent skill (SKILL.md)
└── tests/                    # pytest suite (RAG fusion, validation, scoring, heal, bridge, etc.)
```

---

## 3. Request lifecycles

### 3a. Offline doc query

```
user query
  → server_offline.td_docs_search
    → ParallelRetriever.search
      ├─ BM25 over global + per-source indices (operators / python / glsl / tutorials)
      ├─ optional MiniLM dense + HyDE (TD_MCP_DENSE=1)
      ├─ optional CrossEncoder rerank (TD_MCP_RERANK=1)
      └─ optional EXTERNAL RAG server fused in via RRF (TD_MCP_REMOTE_MCP=...)
    → Reciprocal Rank Fusion → ranked chunks → formatted text
```
If `kb/chunks.jsonl` is missing, `build_retriever` degrades to an **empty
index** (server still boots) rather than crashing.

### 3b. Offline build + verify (no TD required)

```
spec (TDN/YAML/JSON)
  → _parse_build_spec
  → td_build_network        # generators.* → TDN YAML (validated against corpus)
  → td_score_build          # 0..100 + grade
  → td_validate_build       # 5-stage validation + recovery hints   (NEW)
  → td_self_heal            # validate → auto_repair → re-assess     (NEW)
```

`heal.py` ties together `validation` + `scoring` + `recovery` and is exposed
purely on the offline server, so an agent can self-correct a description
without ever touching a live TD.

### 3c. Live mutation (needs TD)

```
agent/LLM
  → MCP client
    → server_live (stateless Streamable HTTP / SSE / stdio, MCP SDK v2)
      → Bearer auth (hmac.compare_digest) + Host/Origin pinning
      → TOOL_REGISTRY → TDClient._call → HTTP POST /mcp
        → bridge HTTP thread: _invoke(tool) → job queue → wait
          → Execute DAT onFrameStart → pump() on TD's MAIN thread
            → _run_tool: undo block + _DISPATCH handler (create_node, …)
              → result back to the HTTP thread → response
```

`execute_python` is gated by `TD_MCP_ALLOW_EXEC` (default on) **and** Bearer
auth. The gate covers every *other* code-bearing route too — parameter
expressions, DAT text, a DAT's `file` parameter, Script/Execute/GLSL operator
creation, `exec_node_method`, `import_recipe` blueprints — because disabling
`execute_python` alone was never a claim the bridge could honour. See
[docs/SECURITY.md](docs/SECURITY.md).

### 3d. The component inside TouchDesigner

```
/project1/td_mcp                Base COMP, custom page "TD MCP"
├── bridge      Text DAT        td_mcp_bridge.py — linked (Sync to File) or embedded
├── startup     Execute DAT     Start + Create + Exit + Frame Start → starts/stops, pumps the queue
└── control     Parameter Exec  Active / Port / Token / Allowexec → restart
```

Built by `bridge/bootstrap.py`, run once inside TouchDesigner; it can also save
the COMP as `td_mcp.tox` for drag-and-drop. The `.tox` is a **build artifact** —
only TouchDesigner can author one, so the script is the source of truth. See
[docs/TOUCHDESIGNER_COMPONENT.md](docs/TOUCHDESIGNER_COMPONENT.md).

`Create` matters as much as `Start`: a dragged-in `.tox` never fires `Start`, so
without it a drop-in install looks inert until the next restart.

---

## 4. Self-healing & review status

The self-healing theme was the project's capstone. The orchestrator
(`td_mcp/heal.py`) runs the loop: **validate → score → auto-repair → attach
recovery hints**. `validation.auto_repair` drops typeless/unnamed nodes,
auto-names survivors, and removes dangling inline-input references to dropped
nodes (verified by a regression test).

A full code review (see commit history: `fix: resolve review findings…` and
`fix: second review pass…`) resolved critical/high/medium/low issues including:

- live HTTP server crashed on every request (`hmac` import missing) — fixed
- single JSON-RPC responses were double-wrapped — fixed
- `td_build_audio_reactive` always errored (dict vs list) — fixed
- `caption_viewport` never received an image file (`detail="brief"`) — fixed
- RAG offline-robustness (missing corpus, undeclared `networkx`) — fixed
- `perf` now accepts the bridge `cooks` shape — fixed
- declared runtime dependencies in `pyproject.toml` — fixed

**Test suite:** `pytest` — 150+ tests covering RAG fusion, validation, scoring,
heal, generators, bridge mocking, config/risk/logs/layout/macro/perf, etc.
Run with `uv run pytest` (or the repo's `python -m tests.test_*` entrypoints).

---

## 5. Running & testing

```bash
# Offline server (no TD)
uv run td-mcp-offline --mcp
uv run td-mcp-offline "blur top parameters"

# Live server (needs TD + bridge Text DAT)
uv run td-mcp-live --http

# Build the corpus / evaluate retrieval
uv run python -m td_mcp.kb.build_kb
uv run python -m td_mcp.rag.eval

# Tests
uv run pytest

# Code-free repository summary (file/architecture overview, no source inlined)
uv run python scripts/generate_summary.py   # writes SUMMARY.md
```

Optional quality upgrades (lazy, never forced):
```bash
uv add --optional rag                       # networkx + sentence-transformers
TD_MCP_DENSE=1   uv run python -m td_mcp.kb.build_index
TD_MCP_RERANK=1  uv run td-mcp-offline "blur top"
```

---

## 6. Known limitations / next frontiers

*(Closed in v1.12.0: the live `build_and_verify` now feeds both live signals —
all four error surfaces and the viewport verdict — into the offline
`validate → score → heal` pipeline. Both edges of that loop were previously
cut; see [`UPSTREAM_SYNC_2026-08-26.md`](UPSTREAM_SYNC_2026-08-26.md).)*

- **Smoke-tested against a live TouchDesigner 2025.33070 on 2026-08-26**:
  37 of 37 bridge calls, and a real MCP client through `td-mcp-live` end to
  end (see `CHANGELOG.md`). It took four passes. Each pass found bugs of the
  same shape — API details the test double had modelled *from the bridge*
  rather than from Derivative's docs (`errors()` is a method, `root` is an OP,
  `ui.undo` is not a context manager, `numpyArray()` is 0..1, TD objects are
  main-thread only). The double now models each of those as documented, and
  `docs/TESTING.md` records the rule: model the awkward parts, never loosen the
  test. What a smoke pass still cannot see: a *different* TouchDesigner build,
  operators the smoke did not touch, and long-session behaviour.
- The bridge's own HTTP transport still carries the `Mcp-Session-Id` session
  layer. It negotiates protocol versions correctly now, but the stateless
  rewrite the SDK servers already took is deliberately deferred
  (`MCP_2026_MIGRATION.md` §4). `td_mcp/streamable_http.py` is gone: the
  bridge could never import it inside TouchDesigner and overrode everything
  it provided.
- `td_mcp/tdn/` does not serialise parameter **sequences**. The traps to avoid
  when it does are written up in [docs/TDN_FORMAT.md](docs/TDN_FORMAT.md).
- `td_mcp/rag/strategies.py` `RemoteMCPStrategy` launches a subprocess per
  query (cached per identical query); a persistent session would remove the
  launch cost entirely.
- WebRTC transport, live multi-instance client targeting, and a thin verb façade
  over the 47 live tools remain surveyed but unbuilt.
