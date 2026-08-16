# TouchDesigner MCP Servers — Catalog & Brainstorm

> A living catalog of every TouchDesigner (TD) MCP (Model Context Protocol) server found on GitHub, with descriptions, architecture, strengths/weaknesses, and a brainstorm on how to merge the best ideas into one local-first "super server" with a documentation/RAG query layer.
> Companion notes: see `TouchDesigner_Links.md` (docs/Python) and `TD_MCP_Master_Plan.md` (merge design).

---

## 0. How to find more

- **GitHub search (use the browser / Playwright to render JS results):** https://github.com/search?q=touchdesigner%20mcp&type=repositories
- Related community index: https://github.com/monkeymonk/awesome-touchdesigner
- Referenced upstream lib: https://github.com/satoruhiga/claude-touchdesigner (helper code that several servers build on)

> **Note on "use MCP Playwright":** Most of these servers expose a local HTTP endpoint (Streamable HTTP / JSON-RPC) while TD is running. You can drive/inspect them headlessly with a browser-automation tool (Playwright) or `curl` — e.g. `curl http://127.0.0.1:9980/api/info`. Endpoints collected in §3.

---

## 1. At-a-glance comparison

| Repo | Stars | Lang | Type | Live TD control? | Knowledge base | Notable |
|------|------:|------|------|------------------|----------------|---------|
| [8beeeaaat/touchdesigner-mcp](https://github.com/8beeeaaat/touchdesigner-mcp) | 422 | TS | Bridge | ✅ WebServer DAT | small | Most-starred, mature, semver compat |
| [dylanroscover/Embody](https://github.com/dylanroscover/Embody) | 135 | TS | Bridge + format | ✅ | TDN (YAML) | Diffable networks, 2005 tests |
| [404dotzero/twozero-td-mcp](https://github.com/404dotzero/twozero-td-mcp) | 21 | TS | Plugin bridge | ✅ (twozero) | twozero | Spatial markers, multi-instance |
| [Pantani/tdmcp](https://github.com/Pantani/tdmcp) | 20 | TS | Bridge + KB | ✅ | 629 ops / 68 py | 355 tools, create→verify→preview |
| [johnsabath/touchdesigner-mcp](https://github.com/johnsabath/touchdesigner-mcp) | 7 | Py | Bridge (.tox) | ✅ | tiny | Self-contained `.tox`, Streamable HTTP |
| [bottobot/touchdesigner-mcp-server](https://github.com/bottobot/touchdesigner-mcp-server) | 69 | JS | **Docs only** | ❌ | 661 ops / 214 py | Zero-config doc RAG, no TD needed |
| [axysar/touchdesigner-agent-mcp](https://github.com/axysar/touchdesigner-agent-mcp) | 0 | Py | Bridge | ✅ | small | Undo safety, `td://` resources, vision |
| [familienak-tech/Touchdesigner---MCP-server](https://github.com/familienak-tech/Touchdesigner---MCP-server) | 2 | Py/JS | Mega vision | ✅ | yes | Show-control, LED mapping, media servers |
| [TrueFiasco/TD_Builder_alpha](https://github.com/TrueFiasco/TD_Builder_alpha) | 0 | Py | Offline builder + bridge | ✅ (optional) | local RAG | Key-free, eval-gated 636/636 |
| [superdwayne/Touchdesigner-mcp](https://github.com/superdwayne/Touchdesigner-mcp) | 6 | Py+JS | Bridge + proxy | ✅ embedded `.tox` | small | One-click `.mcpb`, natural names, workflow presets |
| [benoitliard/touch-mcp](https://github.com/benoitliard/touch-mcp) | 4 | Py (FastMCP) | Bridge (WebSocket) | ✅ `.tox` | small | 37 tools, `td_batch`, auto-reconnect |
| [cacheflowe/td-docs-mcp](https://github.com/cacheflowe/td-docs-mcp) | 4 | Py | **Docs only** | ❌ | live-scraped | Crawl4AI crawler + cleaner, fuzzy search |
| [NairoDorian/TD_MCP](https://github.com/NairoDorian/TD_MCP) | — | Py | **Ours — ultimate** | ✅ 39 live + 45 offline | 1,091 chunks + 661 ops + 214 py | Two-server split, hybrid RAG (recall 0.966), self-healing, TDN, show-control/LED, memory + observability, hardened bridge |

---

## 2. Detailed descriptions

### 8beeeaaat/touchdesigner-mcp — *the reference implementation*
- **Approach:** MCP server (Node/TS) ↔ TouchDesigner **WebServer DAT** bridge (`mcp_webserver_base.tox`, port **9981**).
- **Tools (13):** `create_td_node`, `delete_td_node`, `exec_node_method`, `execute_python_script`, `get_module_help`, `get_td_class_details`, `get_td_classes`, `get_td_info`, `get_td_node_errors`, `get_td_node_parameters`, `get_td_nodes`, `update_td_node_parameters`.
- **Prompts (3):** Search node, Node connection, Check node errors.
- **Strengths:** Most adopted, semantic-version compat check between server & TD API, good connection-error guidance, MIT, 34 releases.
- **Weaknesses:** No built-in RAG/doc KB; relies on model's memory.

### dylanroscover/Embody — *best engineering & version-control story*
- **Three parts:** **Envoy** MCP server (53 tools, port **9870**, HTTP), **TDN** (TouchDesigner Network format = human-readable YAML you can `git diff`), and **externalization** (tag any COMP/DAT → files on disk, auto-restored on open).
- **Strengths:** Networks become text → real diff/branch/restore; auto-generates `AGENTS.md`/`CLAUDE.md`; `ui.undo` wrapping; recovery hints on errors; `capture_top` quality verdict (catches empty renders); **90 test suites / 2,005 tests**; multi-client (Claude/Codex/Gemini/Cursor/Copilot).
- **Weaknesses:** Large, TD-2025+ only; externalization adds project structure.

### 404dotzero/twozero-td-mcp — *slickest UX, runs inside a TD plugin*
- **Approach:** MCP server built into the **twozero** plugin (https://www.404zero.com/twozero, by 404.zero). Drop `twozero.tox`, enable MCP (port **40404**, configurable).
- **Spatial markers:** `*here in TD` = current network, `*this op in TD` = selected op — unambiguous pointers that force the agent to use live context. `study this project` cold-start tour.
- **Multi-instance:** one base URL, twozero routes to the right TD.
- **Strengths:** Best prompt ergonomics; CSRF/origin hardening; built-in "shots" (contextual knowledge injected into tool responses); localization (RU/EN).
- **Weaknesses:** twozero is a 3rd-party plugin (commercial ecosystem, MCP part is free); newer (v0.1.1).

### Pantani/tdmcp (MindDesigner) — *best "artist asks, network appears" loop*
- **Approach:** Node server + TD **bridge** (`tdmcp_bridge_package.tox`, port **9980**); one-click `.mcpb` for Claude Desktop.
- **Knowledge:** embedded reference of **629 operators, 68 Python classes**, workflow patterns, GLSL techniques, tutorials.
- **Tools:** **355** across 3 layers — artist generators (`create_feedback_network`, `create_audio_reactive`, `create_particle_system`) → building blocks → atomic CRUD. Auto left→right layout.
- **Loop:** create → verify (errors) → **preview** (thumbnail) so the AI sees its own work.
- **Optional Creative RAG:** local Ollama-backed artwork/technique search (opt-in).
- **Strengths:** Most complete "vibey" UX; Obsidian vault + session memory integrations; docs site (pantani.github.io/tdmcp).
- **Weaknesses:** 355 tools can bloat context; bridge listens on all interfaces (secure with token).

### johnsabath/touchdesigner-mcp — *cleanest single-file drop-in*
- **Approach:** self-contained `td_mcp_server.tox` (port **9988**), MCP Streamable HTTP, **no external deps**, Python 100%.
- **Tools:** `run` (exec Python), `inspect`, `set`, `create` (one-call params+wire), `wire`, `read`/`write`/`edit` (DATs), `list`, `docs` (param/menu/default lookup), `observe` (PNG/GIF capture), `render` (MP4), `map` (network as DOT graph).
- **Bonus:** ships a **Claude Code skill** (`.claude/skills/touchdesigner-building`) that teaches the agent GLSL/feedback workflows.
- **Strengths:** Zero-config, tiny, great example set (monolith, black hole, infinite machine).

### bottobot/touchdesigner-mcp-server — *best pure documentation RAG (no TD needed)*
- **Approach:** **Docs-only** stdio MCP server (`@bottobot/td-mcp`). Local JSON, **no TouchDesigner required**.
- **Coverage:** **661 operators** (CHOP 170, TOP 147, SOP 113, POP 102, DAT 75, COMP 41, MAT 13), **214 Python API classes / 1,674+ methods**, 14 tutorials, version system (099→2025), experimental builds (POPs, Vulkan), 7 experimental-technique categories, 16 GLSL patterns, network templates, workflow patterns.
- **Tools (21):** `get_operator`, `search_operators`, `compare_operators`, `suggest_workflow`, `get_operator_connections`, `get_network_template`, `get_python_api`, `search_python_api`, `get_glsl_pattern`, `get_experimental_techniques`, version tools, etc.
- **Strengths:** Zero-config, version-aware search, great for keeping the LLM honest about current TD (the "models fall back to old knowledge" problem).
- **Weaknesses:** Cannot touch a live project — pair it with a bridge server.

### axysar/touchdesigner-agent-mcp — *safest bridge*
- **Approach:** dual-process (Python client ↔ WebServer DAT on **9981**); plain-Python TD side (auditable, no opaque `.tox`).
- **Tools (25):** CRUD, `execute_python_script`, `td_viewport` (vision), `td_scaffold`, `td_connect`/`td_layout` (family-validated wiring, no overlaps), `td_glsl`, `td_save/load_tox`.
- **Resources (4):** `td://chop/{path}`, `td://node/{path}`, `td://errors/{path}`, `td://project/info` (live streaming).
- **Strengths:** **Undo safety** (`ui.undo` wraps every mutation → Ctrl+Z), progress reporting, auto-generated auth token, CORS protection. Explicitly extends 8beeeaaat + satoruhiga.
- **Weaknesses:** Young (0 stars), less doc KB.

### familienak-tech/Touchdesigner---MCP-server (TD-Codex Ultimate) — *most ambitious scope*
- **Approach:** REST + MCP ("enhanced_server.js" on port 3000). Bundles Runtime engine, Knowledge engine, Compiler (blueprint generation), Validator, plus:
  - **Vision feedback** (screenshot capture + self-correction)
  - **Performance analyzer** (GPU/CPU bottleneck detection)
  - **Show control**: Art-Net, sACN, OSC, MIDI, LTC/MTC timecode
  - **LED & pixel mapping**: LED wall/strip, voxel grid, universe management
  - **Media-server bridges**: Millumin, Resolume, Notch, Disguise
- **Strengths:** Targets real production/live-event workflows others ignore.
- **Weaknesses:** Very early (2 stars, 1 commit), broad scope risks unfinished pieces.

### TrueFiasco/TD_Builder_alpha — *best offline + eval-gated correctness*
- **Approach:** **Two servers** — `td-builder` (offline, 17 tools, no TD) and `td-builder-live` (21 tools, needs TD). **Key-free** (no API keys).
- **KB:** local vector store + `all-MiniLM-L6-v2` embeddings, **hybrid retrieval (dense + BM25 + reranker)**; topics tagged `rag`.
- **Eval gate:** offline-generated operators re-expanded through TD's `toeexpand` and diffed → **636/636 build-token-exact**; retrieval benchmark (recall@k/MRR/nDCG).
- **Strengths:** Proves correctness with a published eval harness; generates real `.toe/.tox` offline.
- **Weaknesses:** AGPL-3.0 (copyleft — matters if you ship a modified network service); young.

### NairoDorian/TD_MCP — *the ultimate merged server (this repo)*
- **v1.9+ status:** no longer a placeholder. **84 MCP tools** (45 offline `td_*` + 39 live), two-server split, hybrid RAG with recall@5 ≈ 0.966, self-healing pipeline, TDN YAML, show-control/LED/media-server planners, SQLite memory + observability, technique memory cycle, bounded ops, exec-mode tiers, SHA-256 DAT concurrency, bridge staleness guard, name-integrity eval, KB integrity receipt, adaptive HyDE, operator-aware intent boost, freshness banner, L0/L1/L2 doc tiering, gotcha templates, source registry, show-readiness checklist, param-family expansion, conversation compaction, pre-turn injection, activity log, structured exec results, `get_parameter_info`, `bind_parameter_expression`.
- **Safety:** auth, DNS-rebind, origin pinning, `ui.undo`, protected paths, `TD_MCP_ALLOW_EXEC`, `TD_MCP_EXEC_MODE` tiers, traceback redaction.
- **The one to run:** absorbs every feature below plus the ones added since.

### superdwayne/Touchdesigner-mcp — *best "just works" installer*
- **Approach:** a Python server **embedded in a self-contained `.tox`** (port **8053**) + a Node.js **proxy** (stdio/SSE) between Claude and TD. Ships a one-click `touchdesigner-mcp-1.0.0.mcpb` Claude Desktop bundle (1.3 MB).
- **Install paths:** Plugin Installer (creates reusable `td_mcp_server` COMP), Auto-Bootstrap (Execute DAT auto-starts on open), or Manual (paste server into a Text DAT).
- **Smart UX:** **natural type names** (`"webcam"`, `"blur"`, `"mic"`) resolved with family suffixes; **auto-connect** to same-family sibling; **workflow presets** (`audio_experience`, `interactive_installation`, `render_scene`); timeline + CHOP-export + custom COMP params + node styling.
- **Tools:** create/delete/list, get/set/set-many params, execute Python, connect/auto-connect, build_workflow, layout, show preview, timeline control, CHOP export, custom params, node styling.
- **Safety:** `TD_MCP_PROTECTED_PATHS` (comma list) shields critical ops from deletion; graceful skip of ops missing in a given TD build.
- **Strengths:** Lowest friction install; great for non-technical artists. **Weaknesses:** Python-in-TD server + Node proxy = two moving parts; 8053 collides with nothing major but is unusual.

### benoitliard/touch-mcp — *most tools + batching*
- **Approach:** Python **FastMCP** server (stdio) ↔ **persistent WebSocket** to a `TouchMCPBridge.tox` in TD (port **9980**, same as tdmcp — don't run both unmodified). `pip install touch-mcp`.
- **Tools (37):** nodes (create/delete/list/get/copy/rename/find/errors/flags), parameters (get/set/info/expression/pulse), connections, **data readers** (`td_read_chop/top/sop/dat`, `td_write_dat`), scripts (`td_execute_script`, `td_class_list/detail`, `td_module_help`), timeline, render (`td_screenshot`, `td_export_render`), project (`td_project_info/save`), layout, and **`td_batch`** (bundle many ops into one round-trip).
- **Strengths:** Highest tool count; WebSocket + batching = fast large-network builds; auto-reconnect survives TD restarts; full Python access. **Weaknesses:** younger (4★); 9980 port clash with tdmcp.

### cacheflowe/td-docs-mcp — *live-scraping doc RAG*
- **Approach:** **Docs-only** stdio MCP (uv) that **scrapes `docs.derivative.ca` at build time** with Crawl4AI into markdown, then serves it. Complements bottobot's *static* JSON with *fresh* docs.
- **Crawler (3 stages, resumable):** (1) operator category index pages (TOPs/CHOPs/DATs/SOPs/POPs/MATs/COMPs), (2) Python base classes, (3) operator-specific Python classes (`BlurTOP_Class`…). Flags: `-c`, `--limit`, `--classes-only`, `--skip-classes`, `--retry-failed`.
- **Cleaner:** strips wiki artifacts, fixes headers, relativizes anchors, formats params as bullets, routes general pages to `General/`.
- **Tools:** `search_touchdesigner_docs`, `read_operator_doc`, `list_categories`, `get_python_class`. Ships `CLAUDE.md`, `skills/TD_SKILLS.md`, and per-client config samples.
- **Strengths:** Always-current docs; explicit "look up before coding" guidance that kills param-name/callback-signature hallucination. **Weaknesses:** Needs a crawl run (network) before first use; no live TD control.

---

## 3. Endpoint / port map (for Playwright / curl testing)

| Server | Default port | Transport |
|--------|-------------:|-----------|
| 8beeeaaat / axysar | 9981 | WebServer DAT (HTTP) |
| Pantani tdmcp | 9980 | bridge (HTTP) |
| benoitliard touch-mcp | 9980 (WebSocket) | WebSocket → stdio proxy (⚠️ clashes with tdmcp) |
| superdwayne | 8053 (+ Node proxy 8050 SSE) | embedded `.tox` HTTP + proxy |
| dylanroscover Embody (Envoy) | 9870 | HTTP (`/mcp`) |
| johnsabath | 9988 | Streamable HTTP (`/mcp`) |
| twozero | 40404 (configurable) | HTTP (`/mcp`) |
| familienak TD-Codex | 3000 | REST + MCP |
| bottobot / cacheflowe | n/a | stdio (docs only) |
| TrueFiasco | internal | stdio (offline) / TD (live) |

Quick health check example: `curl http://127.0.0.1:9980/api/info` (tdmcp) or `curl http://127.0.0.1:9870/mcp` (Embody).

---

## 4. Brainstorm: the TD-MCP idea & a documentation query system

### 4.1 The core problem these servers solve
LLMs "know" TouchDesigner from old training data (pre-POPs, wrong param names) and **hallucinate**. Two failure modes:
1. **Wrong ops/params** → broken networks.
2. **Forgetting to use the MCP** → falls back to guesswork (bottobot calls this out explicitly).

The fix is a **tight create → verify → preview loop** plus **always-on, version-accurate doc retrieval**.

### 4.2 Two complementary server classes
- **Bridge servers** (8beeeaaat, Embody, tdmcp, johnsabath, axysar, twozero, TrueFiasco-live): actually mutate a running TD.
- **Doc/RAG servers** (bottobot, TrueFiasco-offline, tdmcp's embedded KB): answer "what's the right operator/param?" without TD.

**Insight:** the ideal server is *both* — a bridge that carries its own doc-retrieval so the agent never guesses.

### 4.3 Documentation / knowledge query system (RAG) — design sketch
A local-first TD knowledge base that any MCP server can query:

- **Sources (scrape once, cache locally):**
  - `docs.derivative.ca` operator pages (all families) + Python class pages.
  - `learn.derivative.ca` curriculum examples.
  - Bundled `POPs Examples`, OP Snippets, Palette.
- **Chunking:** one chunk per operator (params, tips, connectors) + one per Python class/method + one per tutorial section.
- **Index (local, no cloud):**
  - Dense vectors: `all-MiniLM-L6-v2` (proven by TrueFiasco).
  - Lexical: BM25 (great for exact param/op names).
  - **Hybrid** (dense + BM25 + reranker) — TrueFiasco measured 0.86 → 0.93.
- **Version awareness:** tag every chunk with the TD build it applies to (bottobot already does this well) so queries can filter `version >= 2025`.
- **Tools exposed:**
  - `td_docs_search(query, family?, version?)` → hybrid retrieval.
  - `td_docs_operator(name)` → full param + connector spec.
  - `td_docs_python(class_or_method)` → signature + members.
  - `td_docs_glsl(pattern)` / `td_docs_template(name)` → paste-ready snippets.
- **Honesty guard:** every code-gen tool response appends the relevant doc chunk (twozero's "shots" pattern) so the model is anchored to truth.

### 4.4 What "best of all" would combine
See `TD_MCP_Master_Plan.md` for the concrete merged design. In short, steal:
- **Embody's** TDN diffable format + undo + recovery hints.
- **tdmcp's** create→verify→preview loop + 355-tool breadth + embedded KB.
- **bottobot's** version-aware doc RAG (no TD needed).
- **axysar's** undo safety + `td://` live resources + auth.
- **twozero's** spatial markers (`*here`/`*this`) + multi-instance routing + shots.
- **johnsabath's** zero-dep `.tox` drop-in + skill file.
- **TrueFiasco's** key-free offline build + eval gate + hybrid RAG.
- **familienak's** show-control / LED / media-server modules.
- **superdwayne's** one-click `.mcpb` installer + natural-name resolution + workflow presets.
- **benoitliard's** 37-tool breadth + `td_batch` + WebSocket auto-reconnect.
- **cacheflowe's** live Crawl4AI doc crawler/cleaner (fresh docs) + fuzzy search.
- **8beeeaaat's** maturity + semver compat + community trust.

---

## 5. Recommendations (which to actually run today)
- **Want to just build visuals by talking?** → `Pantani/tdmcp` or `dylanroscover/Embody`.
- **Want git-diffable, reviewable networks?** → `Embody` (TDN).
- **Want the LLM to stop hallucinating params?** → run `bottobot/touchdesigner-mcp-server` *alongside* any bridge.
- **Want a safe, auditable bridge?** → `axysar/touchdesigner-agent-mcp`.
- **Want zero-config drop-in?** → `johnsabath/touchdesigner-mcp`.
- **Want to build without TD open / offline?** → `TrueFiasco/TD_Builder_alpha`.
- **Want the smoothest installer for a non-tech artist?** → `superdwayne/Touchdesigner-mcp` (one-click `.mcpb`).
- **Want the most tools + batching for big networks?** → `benoitliard/touch-mcp` (free port 9980 clash with tdmcp — change one).
- **Want always-fresh scraped docs (not a frozen snapshot)?** → `cacheflowe/td-docs-mcp` (run the crawler once).
- **Already using 404.zero tools?** → `twozero-td-mcp`.

---

## 6. FAQ / Troubleshooting

**Q: The MCP shows up in my client but "no tools" / the agent ignores it.**
A: Many servers are *passive* — the LLM only calls them when your prompt signals TD context ("I'm in a TouchDesigner DAT execute script…"). Use cacheflowe's trick: explicitly ask it to *look up* the operator/Python class before writing code. With a bridge, verify TD is actually running and the bridge `.tox` started (check its Textport for "started successfully").

**Q: Port already in use / "Address already in use".**
A: Two servers share **9980** (tdmcp & benoitliard) and several use 998x. Pick non-colliding ports: tdmcp `9980`, benoitliard `9980`→ change to e.g. `9975`, Embody `9870`, johnsabath `9988`, twozero `40404`, superdwayne `8053`. Restart TD after changing the bridge param.

**Q: Claude can't connect but TD server is up.**
A: Open `http://127.0.0.1:<port>/api/status` (or `/api/info`) in a browser — if that 200s, the problem is the client config path (e.g. wrong `index.js` path in `claude_desktop_config.json`) or you forgot to restart the client. For stdio servers, the `command`/`args` must point at the real installed binary.

**Q: The agent keeps hallucinating param names.**
A: Run a **doc/RAG server alongside the bridge** (bottobot or cacheflowe). The model's training data predates POPs and many 2023+ params. Force version-aware lookups.

**Q: A mutation broke my project.**
A: Prefer servers with `ui.undo` wrapping (Embody, axysar) so one Ctrl+Z reverts a whole agent batch. Set `TD_MCP_PROTECTED_PATHS` (superdwayne) or equivalent on critical COMPs. Disable `exec`/`execute_python` on untrusted prompts.

**Q: Which is "the best one"?**
A: There isn't one. Use **two**: a bridge you like (tdmcp/Embody/benoitliard) **+** a doc server (bottobot/cacheflowe). The merged `td-mcp` design in `TD_MCP_Master_Plan.md` is the attempt to make that one tool.

---

## 7. Combined local-first stack (quickstart)

Run a **bridge** + a **doc RAG** side-by-side (the "best of both"):
1. Install a doc server: `npm i -g @bottobot/td-mcp` (zero-config, no TD) **or** `uv sync && uv run td-docs-mcp` after crawling `docs.derivative.ca`.
2. Install a bridge: e.g. drop `tdmcp_bridge_package.tox` (Pantani) into `/project1`, set port 9980, start it; configure the Node/Python MCP proxy in your client.
3. In the client config, register **both** MCP servers. Now the agent can *build* (bridge) and *verify against truth* (doc RAG).
4. Harden: token auth, localhost-only, `TD_MCP_ALLOW_EXEC=0` when you don't need RCE, undo-wrapped mutations.
5. (Optional) Add `td-mcp` (this repo's scaffold, see `TD_MCP_Master_Plan.md`) to fold the RAG *into* the bridge so every code-gen response is doc-anchored.

---

## 8. Raw URLs

```
# Search
https://github.com/search?q=touchdesigner%20mcp&type=repositories

# Servers
https://github.com/8beeeaaat/touchdesigner-mcp
https://github.com/dylanroscover/Embody
https://github.com/404dotzero/twozero-td-mcp
https://github.com/Pantani/tdmcp
https://github.com/johnsabath/touchdesigner-mcp
https://github.com/bottobot/touchdesigner-mcp-server
https://github.com/axysar/touchdesigner-agent-mcp
https://github.com/familienak-tech/Touchdesigner---MCP-server
https://github.com/TrueFiasco/TD_Builder_alpha
https://github.com/NairoDorian/TD_MCP
https://github.com/superdwayne/Touchdesigner-mcp
https://github.com/benoitliard/touch-mcp
https://github.com/cacheflowe/td-docs-mcp

# twozero
https://twozero.ai/docs/mcp
https://www.404zero.com/twozero
https://www.404zero.com/pisang/twozero.tox

# Related
https://github.com/monkeymonk/awesome-touchdesigner
https://github.com/satoruhiga/claude-touchdesigner
```
