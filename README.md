# TouchDesigner MCP (`td-mcp`)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)](#tests)
[![CI](https://github.com/NairoDorian/TD_MCP/actions/workflows/ci.yml/badge.svg)](https://github.com/NairoDorian/TD_MCP/actions/workflows/ci.yml)

**Local-first TouchDesigner [MCP](https://modelcontextprotocol.io) toolkit** that unifies the
best ideas of the public TD‑MCP ecosystem into one cohesive package:

- **Offline doc / RAG server** — version‑aware retrieval over a merged MIT corpus **plus the complete official documentation** (no TouchDesigner required, no network, no API keys). Several lexical + semantic backends run **in parallel** and fuse via Reciprocal Rank Fusion, with an optional CrossEncoder reranker and optional external RAG fusion.
- **Eval harness** — recall@k / MRR / nDCG over a labelled query set, so retrieval quality is provable and regressions are caught. **k=5 recall 1.000** with the wiki KB (**0.991** curated‑only), zero version‑gating violations.
- **Live bridge + MCP server** — control a running TouchDesigner session (create / wire / inspect / verify) over **Streamable HTTP + SSE**, WebSocket, or stdio. Exposes **57 live tools**; every mutation is wrapped in `ui.undo` so one Ctrl+Z reverts a whole agent batch.
- **All four of TouchDesigner's error surfaces** — cook errors, warnings, **script tracebacks** and **GLSL compile errors** (which appear on none of the API's error accessors). Most tooling reads one of the four and calls a visibly broken network clean. See **[docs/DIAGNOSTICS.md](docs/DIAGNOSTICS.md)**.
- **Natively embedded chat UI** and a **zero‑dependency autonomous agent** that run *inside* TouchDesigner Text DATs.

> Everything in `td_mcp/` is pure Python (standard library + optional `mcp` / `networkx` / `sentence-transformers`) and is fully unit‑testable without a running TouchDesigner.

---

## 📚 Documentation

Full docs live in **[`docs/`](docs/README.md)**.

| | |
|---|---|
| **[What goes into TouchDesigner](docs/TOUCHDESIGNER_COMPONENT.md)** | The `/td_mcp` component, drag-and-drop `.tox`, and how the other 30 projects do it |
| **[Install](docs/INSTALL.md)** | Both halves, the lifecycle, environment variables |
| **[Tools](docs/TOOLS.md)** | All 120 tools with risk classes |
| **[Saved projects](docs/PROJECT_FILES.md)** | Reading `.toe`/`.tox` files with TouchDesigner closed, and the measured format |
| **[Diagnostics](docs/DIAGNOSTICS.md)** | The four error surfaces — read this one |
| **[Agent guide](docs/AGENT_GUIDE.md)** | How to sequence the tools |
| **[Troubleshooting](docs/TROUBLESHOOTING.md)** | Symptom-first |
| **[Retrieval](docs/RAG.md)** · **[TDN format](docs/TDN_FORMAT.md)** · **[Testing](docs/TESTING.md)** · **[Security](docs/SECURITY.md)** · **[Ecosystem](docs/ECOSYSTEM.md)** | |

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
- [Demonstration](#demonstration-build-a-template-network-as-an-agent)
- [Verify in TouchDesigner — the major rule](#verify-in-touchdesigner--the-major-rule)
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
| Tools | 45 (`td_*`)| | 39 (`create_node`, `set_parameters`, …) |

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
uv pip install -e ".[scrape]"        # DEPRECATED: legacy network doc crawler (wiki_ingest is now preferred)
```

### Install lifecycle (Install SOP v1)

`td-mcp-install` is the agent-facing installer. Every verb accepts `--json`,
mutating verbs require `--yes`, and `--dry-run` never writes:

```bash
td-mcp-install install --dry-run --json   # the full plan, nothing written
td-mcp-install install --yes              # stage the bridge + client configs
td-mcp-install verify --json              # is this actually usable?
td-mcp-install status --json              # installed / drifted / outdated / absent
```

A successful first install exits **50** (`requires_restart`), not 0, and returns
one machine-executable `next_steps` entry. That is deliberate: the bridge runs
inside TouchDesigner as a Text DAT, and no external process can create an
operator in a running project or restart it. `verify` reports
`directly_usable: true` only once the staged artifacts still match their
recorded SHA-256, the knowledge base is built, and a real read-only probe
answers — so "installed" never means "the files copied".

Exit codes are per stage — `10` preflight, `20` acquire, `30` install, `40`
verify, `50` requires_restart — so a caller can tell *which* thing went wrong
without parsing prose.

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

**1. Put the bridge into TouchDesigner.** Open the Textport (**Alt+T**) and run:

```python
TD_MCP_REPO = r'C:/path/to/td-mcp'
exec(open(TD_MCP_REPO + '/bridge/bootstrap.py').read())
```

That builds a `/td_mcp` Base COMP — a Text DAT carrying the bridge, an Execute
DAT that starts it on **Start** *and* **Create**, and a Parameter Execute DAT so
the component is operable from its parameter panel. It prints the port and the
auth token. Re-running is safe.

Add `TD_MCP_SAVE_TOX = 1` before the `exec(...)` and it also writes
`td_mcp.tox` — the drag-and-drop file for everyone else. A `.tox` is opaque
compressed binary that only TouchDesigner can author, so this repository treats
the **script as the source and the `.tox` as a build artifact**. Full rationale,
plus how the other 30 projects package themselves, in
**[docs/TOUCHDESIGNER_COMPONENT.md](docs/TOUCHDESIGNER_COMPONENT.md)**.

> Prefer to do it by hand? Create a Text DAT, point its **File** parameter at
> `bridge/td_mcp_bridge.py` with **Sync to File** on (or paste the contents),
> then `op('text1').module.start()`.

**2. From the shell (CLI mode):**

```bash
$env:TD_MCP_AUTH_TOKEN="YOUR_AUTO_GENERATED_TOKEN"
uv run td-mcp-live status
uv run td-mcp-live create /project1 CircleTOP --name my_circle
uv run td-mcp-live set /project1/my_circle '{"radius": 0.5}'
uv run td-mcp-live exec "print([c.name for c in op('/project1').children])"
```

### Streamable HTTP mode

`td-mcp-live` can also run as a **stateless** Streamable‑HTTP MCP server (MCP SDK v2,
no `Mcp-Session-Id`, DNS‑rebind guard) on `127.0.0.1:8765`:

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

`chunks.jsonl` is **generated** — never hand‑edited. `kb_receipt.json` is generated
alongside it and pins the SHA‑256 for integrity verification on load.

```bash
uv run python -m td_mcp.kb.build_kb          # curated + corpus (+ wiki) -> chunks.jsonl + kb_receipt.json
uv run python -m td_mcp.rag.eval             # re-check recall / MRR / nDCG
uv run python -m td_mcp.kb.build_index       # validate / dense-embed
```

### Ingest the full official documentation (recommended, offline)

TouchDesigner ships a complete local mirror of `docs.derivative.ca` under
`<TD_INSTALL>/Samples/Learn/OfflineHelp/https.docs.derivative.ca`. Point the build
at it (or at any saved dump of the wiki) and the KB grows from **1,091 curated
chunks to ~9,600**, parsed in about 17 seconds with no network access:

```bash
uv run python -m td_mcp.kb.build_kb --wiki-root "C:/Program Files/Derivative/TouchDesigner/Samples/Learn/OfflineHelp/https.docs.derivative.ca"
# or set TD_MCP_WIKI_ROOT once and just run build_kb
```

The mirror is auto‑detected from `TD_MCP_WIKI_ROOT`, the standard install paths, or a
sibling `TD HELP` folder. Pass `--no-wiki` to skip it.

Ingestion is **structured**, not a tag‑strip: one chunk per documentation section,
each carrying a `Page § Section` citation with a deep link to its anchor, the
operator title prefixed onto the text, and the *internal* parameter names
(`size`, `horzandvert`) harvested into tags so lexical retrieval matches the name
an agent must actually write. Inherited class sections, cross‑page boilerplate, and
experimental duplicates of released operators are dropped; POP and experimental
pages are version‑stamped so build‑filtered queries exclude them.

> **CI / fresh-clone note:** `chunks.jsonl` is **not** committed. A fresh clone has no
> KB until `uv run python -m td_mcp.kb.build_kb` runs — the CI workflow does this
> automatically before pytest. The retriever degrades gracefully to an empty index
> (returns "no docs", never crashes) if you skip that step.

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
uv run python -m td_mcp.rag.eval                 # zero-dep: k=5 recall 1.000 (wiki KB)
uv run python -m td_mcp.rag.eval --name-integrity  # + every labelled id must resolve
TD_MCP_DENSE=1 uv run python -m td_mcp.rag.eval --k 5
```

Each labelled expectation is credited to at most one retrieved chunk, so a page
that contributes several section chunks cannot inflate the score. Queries whose
answer only exists in the wiki are skipped automatically when the KB was built
without a mirror (curated‑only baseline: **recall@5 0.991**).

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
├── .gitattributes            # normalize line endings (LF) across OSes
├── .github/workflows/ci.yml  # GitHub Actions: runs `uv run pytest`
├── LICENSE                   # MIT
├── README.md / ARCHITECTURE.md / HOW_TO_USE.md / SUMMARY.md / COMMIT.md / CONTRIBUTING.md
├── CHANGELOG.md              # versioned change log
├── TD_MCP_Master_Plan.md / TouchDesigner_MCP_Servers.md / TouchDesigner_Links.md  # brainstorm/docs
├── repomix.config.json       # config for `repomix` full source pack (optional)
├── scripts/
│   └── generate_summary.py   # generates SUMMARY.md (code-free file/architecture overview)
├── td_mcp/
│   ├── server_offline.py     # offline doc/RAG + build/verify MCP server (66 tools)
│   ├── server_live.py        # Streamable-HTTP/SSE/stdio MCP server for the bridge (57 tools)
│   ├── toe_reader.py         # read saved .toe/.tox files offline (toeexpand on a cached copy)
│   ├── toe_writer.py         # edit them offline into a NEW file (toecollapse, re-expanded to prove it)
│   ├── journal.py            # read the bridge's call journal; build the calls that undo changes
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
│   └── kb/                    # corpus records, build_kb, import_corpus, wiki_ingest, build_index (scrape.py deprecated)
├── bridge/
│   ├── td_mcp_bridge.py       # paste into a Text DAT in TD (JSON-RPC/WS/SSE/chat UI server)
│   ├── td_mcp_agent.py        # paste into a Text DAT (autonomous builder agent)
│   ├── chat_ui.html           # glassmorphic chat panel served at GET /
│   └── bootstrap.py           # builds the /td_mcp COMP inside TD; can emit td_mcp.tox
├── skills/                   # agent skills (SKILL.md each); start with td-workflow
│   ├── td-workflow/  td-building/  td-top-family/  td-chop-family/
│   ├── td-pop-family/  td-sop-render/  td-dat-python/  td-glsl/
│   └── td-performance/  td-network-layout/
└── tests/                     # pytest suite (RAG fusion, validation, scoring, heal, bridge, etc.)
```

---

## Tool catalog

**Offline server (66 tools)** — `td_docs_search`, `td_docs_operator`, `td_docs_python`,
`td_docs_glsl`, `td_docs_template`, `td_docs_version`, `td_docs_family`, `td_docs_parameter`,
`td_docs_compare`, `td_docs_connections`, `td_docs_workflow`, `td_docs_version_info`,
`td_docs_related`, `td_docs_glossary`, `td_build_network`, `td_showcontrol_plan`, `td_led_map`,
`td_build_feedback`, `td_build_audio_reactive`, `td_build_particle`, `td_build_3d_scene`,
`td_build_glsl_shader`, `td_build_led_wall`, `td_build_dmx_fixture`, `td_build_video_pipeline`,
`td_build_midi_rig`, `td_build_kinect_skeleton`, `td_glsl_pattern`, `td_network_template`,
`td_expert_prompt`, `td_compat_check`, `td_score_build`, `td_validate_build`, `td_self_heal`,
`td_mediaserver`, `td_analyze_performance`, `td_discover`, `td_memory_save`, `td_memory_recall`,
`td_scaffold_recipe`, `td_analyze_build`, `td_diff_networks`, `td_optimize_layout`,
`td_resolve_params`, `td_docs_combos`, and for saved project files `td_project_read`,
`td_project_grep`, `td_project_diff`, `td_project_check`, `td_project_edit`, `td_project_extract`,
and `td_journal` for what the agent changed live.

**Live server (57 tools)** — `build_network`, `health`, `annotate`, `screenshot`, `open_viewer`, `show_in_editor`, `ui_state`, `move_window`, `create_node`, `delete_node`, `set_parameters`, `get_parameters`,
`describe_op_type`, `pulse_parameter`,
`get_parameter_info`, `get_non_default_params`, `bind_parameter_expression`, `get_errors`,
`clear_script_errors`, `execute_python`, `list_nodes`, `project_info`, `capture_viewport`,
`observe`, `get_resource`, `describe_td_tools`, `batch`, `read_chop`, `diff_chop`, `read_pop`,
`read_top`, `read_dat`, `set_dat_text`, `scan_network`, `build_and_verify`, `connect_nodes`,
`rename_node`, `copy_node`, `auto_layout`, `get_node`, `set_node_color`, `set_node_comment`,
`map_network`, `disconnect_nodes`, `get_connections`, `exec_node_method`, `snapshot_network`,
`restore_network`, `get_performance`, `validate_network`, `set_flags`, `find_nodes`,
`set_node_position`, `timeline`, `export_recipe`, `import_recipe`, `save_tox`.

**A journal of every change, and its undo (v1.18.0).** The bridge writes one
line per mutating call to `~/.td_mcp/journal/` with the state the call was
about to change -- parameter mode, value, expression and bind; flags; DAT
text; wiring; names; a deleted COMP as a `.tox`. TouchDesigner's undo stack
forgets everything when it closes; the journal does not. `td_journal` lists
what changed (by operator, tool, time) and builds the calls that put it back,
for review and one `batch`. Verified by changing a network, sending the
journal's revert plan and reading every value back (`scripts/live_journal.py`).

**Safety controls enforced (v1.17.0).** The risk ceiling
(`TD_MCP_MAX_RISK`, `TD_BUILDER_LIVE_READONLY`) is now applied by both servers
and by the bridge itself; one unauthenticated event-stream request can no
longer freeze the bridge; switching **Allow Python Execution** off takes
effect on the restart it triggers; `save_tox` never writes over a file by
accident. See `docs/SECURITY.md`.

**Edit saved projects with TouchDesigner closed (v1.16.0).** `td_project_edit`
changes a `.toe`/`.tox` -- parameters, expressions, binds, DAT code and
tables, flags, wires, renames, deletes, new operators or whole networks from a
build spec, a `.tox` dropped in, media paths relinked, project cook rate --
and writes the result to a **new** file with TouchDesigner's own
`toecollapse`, expanding it again to prove it holds what was written. The
source is never touched and no file is ever replaced; one bad edit and nothing
is written. Names and menu tokens are checked against the catalog the way the
live `set_parameters` checks them. `td_project_extract` lifts one COMP out as
a `.tox`. Every rule was measured in TouchDesigner and the writer re-renders
all 806,301 parameter records of the shipped examples byte for byte; edited
files were loaded back into TouchDesigner and read
(`scripts/live_toe_edit.py`, `docs/PROJECT_FILES.md`). Re-probing also
corrected the live bridge: an unknown menu token **resets a menu to its first
item** (it was documented as ignored) and is now never written, and StrMenu
parameters such as Select CHOP `channames` accept free text.

**Saved projects and silent failures (v1.15.0).** `td_project_read` /
`td_project_grep` / `td_project_diff` / `td_project_check` read a `.toe` or
`.tox` with TouchDesigner closed: a cached copy is expanded with TD's own
`toeexpand` and parsed by a reader whose grammar was measured over all 768
components that ship with the install (`docs/PROJECT_FILES.md`). Live, the
new `health` tool reports what TouchDesigner does not call an error --
expressions that raise, dead binds, failed extensions, cooking switched off,
bypassed / never-cooked / idle operators, a paused or stalled timeline -- and
`annotate` reads and writes network boxes and comments, which every other
tool (and `op()`) cannot see. Paths must name one operator: TD resolves a
pattern to one arbitrary match. Wires across networks are refused (TD drops
the old wire and reports nothing), deletes report TD's automatic rewire, a
Script operator's traceback is read off its callbacks DAT, writes report when
they went through a bind, and sequence parameters (`const2value`, `pt0posx`)
are handled without growing or cooking the operator. See
`UPSTREAM_SYNC_2026-11-27.md`.

**Ground truth (v1.13.0).** Parameter names now come from TouchDesigner
itself. `scripts/probe_param_catalog_td.py` instantiates every operator class
(659 on 2025.33230) in `/sys/quiet` and records each parameter's exact name,
label, style, page, tuplet, menu tokens, clamps, creation default and help text
(21,820 parameters since 1.15, sequence blocks included) into `td_mcp/kb/corpus/param_catalog/`. The resolver,
`td_validate_build`, `td_self_heal`, `td_docs_parameter` and the new
`td_describe_op_type` / live `describe_op_type` all read it. Before this, they
read a scrape keyed by *label* (`'Brightness 1'`), so the resolver rewrote
correct names into labels, every correctly typed spec failed `UNKNOWN_TYPE`,
and invalid menu values passed. On the live side, `get_parameters` returns
`eval()` values with expression/bind/export modes (it returned the stale
constant slot); `set_parameters` reads every write back (TD silently ignores a
bad menu token and silently clamps); `capture_viewport` captures any operator
family through a transient OP Viewer; `get_performance` marks stale cook times;
and mutating tools refuse to touch the bridge's own component. Every one of
these was verified live; see `UPSTREAM_SYNC_2026-11-22.md`.

**Diagnostics (v1.12.0).** `get_errors` reads all four of TouchDesigner's
diagnostic surfaces — cook errors, warnings, **script tracebacks**
(`scriptErrors`) and **GLSL compile errors** from the auto-docked Info DAT —
tagged by `kind`, with `errorCount`/`hasErrors` meaning *what the network shows
red*. Before v1.12.0 the bridge read `node.errors` as an attribute rather than
calling the method TouchDesigner documents, so `get_errors` always failed and
every scanner reported a clean network; see
[`UPSTREAM_SYNC_2026-08-26.md`](UPSTREAM_SYNC_2026-08-26.md) §3.1.

**Motion.** `observe` captures a still *and* judges whether the render is
moving. A feedback or audio-reactive network that renders one correct frame and
then freezes is structurally valid, visually plausible and completely broken,
and no single-frame verdict can see it. `is_static` is `None` when there were
too few frames to judge — unknown, not false.

**Reads reduce by default.** `read_chop` returns per-channel
count/min/max/mean/std, `read_dat` per-column stats plus edge rows, `read_pop`
attribute metadata free with value readback gated on point count. Pass
`format="values"` / `format="rows"` for the raw forms.

---

## Documentation

| File | Purpose |
|------|---------|
| [`README.md`](README.md) | This file — quick start, install, usage, tool catalog. |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Module map, two‑server model, request lifecycles, review status. |
| [`SUMMARY.md`](SUMMARY.md) | Code‑free, file‑by‑file overview of the whole repo (generated). |
| [`COMMIT.md`](COMMIT.md) | Pre‑commit checklist to follow before every commit. |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | How to contribute. |
| [`HOW_TO_USE.md`](HOW_TO_USE.md) | Step‑by‑step bridge setup, AI‑client config, autonomous agent. |
| [`CHANGELOG.md`](CHANGELOG.md) | Versioned change log. |
| [`TD_MCP_Master_Plan.md`](TD_MCP_Master_Plan.md) | Master plan / roadmap this scaffold implements. |
| [`TouchDesigner_MCP_Servers.md`](TouchDesigner_MCP_Servers.md) | Catalog + brainstorm of the TD‑MCP ecosystem. |
| [`TouchDesigner_Links.md`](TouchDesigner_Links.md) | Curated official docs / Python API / curriculum links. |

---

## Demonstration: build a template network as an agent

```bash
python scripts/td_live.py launch     # TouchDesigner on TD_MCP_TESTS/TEST-01
python scripts/demo_build.py         # examples/feedback_trails.json -> /project1/feedback_trails
```

Over MCP, exactly as an agent: validate the spec offline (`td_validate_build`),
build it live in one call (`build_network`, one Ctrl+Z), then verify — errors,
a captured frame, motion across real frames, median frame cost — and finally
show the user: their network editor is pointed at the output with a status-bar
message, and the output's viewer window is opened and screenshotted so the agent
sees what they see. Measured: 11 operators in ~350 ms, 0 errors, animating,
~3-4% of a 60 fps frame. See
[examples/README.md](examples/README.md).

## Verify in TouchDesigner — the major rule

**Doing beats guessing.** TD_MCP is built to be driven by an AI agent, so it is
verified the way an agent uses it, against a real TouchDesigner. Anyone working
on the project — human or agent — may open the test project at any moment to
check a behaviour, a parameter name or a fix:

```bash
python scripts/td_live.py launch   # open ../TD_MCP_TESTS/TEST-01/NewProject.toe, known token
python scripts/td_live.py deploy   # after editing bridge/: copy into TEST-01 + safe restart
python scripts/live_smoke.py       # MCP stdio client -> td-mcp-live/offline -> bridge -> TD
python scripts/td_live.py call describe_op_type '{"op_type": "lfoCHOP"}'
python scripts/td_live.py monitor 165hz   # TD + its viewers on that screen, not yours
python scripts/td_live.py close    # work done: save the project and quit TouchDesigner
```

Close what you open: `screenshot` closes the viewer window it opened (the
window going away tells the user the step is done), and a finished session
ends with `td_live.py close`.

`live_smoke.py` builds, inspects, captures and cleans up a scratch network
through the same MCP tools an agent calls (30 checks; `--offline-only` without
TD). It found three bugs in 1.13 that the unit suite could not. Rules for
agents working in this repo are in [AGENTS.md](AGENTS.md).

## Tests

```bash
uv run pytest                       # full suite — 1,057 passing
uv run python -m tests.test_rag     # retrieval fusion + version/per-source
uv run python -m tests.test_mcp_server
```

`tests/fake_remote_mcp.py` is a tiny stdio MCP server the fusion test uses to exercise the
multi‑process path without a real external install.

**`tests/td_double.py` is a deterministic TouchDesigner API double**, plus a
loader that execs the *entire* shipped bridge against it — so the ~1,200 lines
of `_do_*` handlers are covered, not just the pure-helper block. It models the
API as TouchDesigner documents it, which is the point: the error accessors are
methods returning strings, `COMP.create` refuses an unknown opType and takes
the type first, `findChildren(depth=N)` matches an *exact* depth, and
`POP.points()` accepts slicing arguments and ignores them. A handler that
mistakes any of those fails here instead of in a live session. When a handler
needs a new corner of the TD API, model that corner rather than loosening the
test.

---

## License

MIT. See [`LICENSE`](LICENSE). Note: `TrueFiasco/TD_Builder_alpha` (a source of the hybrid‑RAG
idea) is **AGPL‑3.0** — the *techniques* here are reimplemented, not copied, so MIT stays clean.
