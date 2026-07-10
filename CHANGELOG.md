# Changelog

All notable changes to this project will be documented in this file.

---

## [1.7.1] - 2026-07-10

Repository cleanup and a code-free summary generator.

### Added
- **Code-free repository summary generator** (`scripts/generate_summary.py`) — walks the
  repo and emits `SUMMARY.md` (overview, architecture, directory tree, and a short
  description of every tracked file) with **no source code inlined**. A `repomix.config.json`
  is provided for anyone who wants a full source pack via `npx repomix` (off by default).
- **`LICENSE`** (MIT) — previously only referenced in the docs.
- **`td_mcp/tools/__init__.py`** — explicit package marker; `td_mcp.tools` added to the
  `pyproject.toml` `packages` list.

### Changed
- `README.md` rewritten: accurate layout tree, full 40/39 tool catalog, relative doc
  links, fixed the stale chunk count (216 → 1,091) and recall figure.
- `HOW_TO_USE.md` hardcoded absolute repo paths replaced with `<REPO_DIR>`.
- `pyproject.toml` version bumped `0.1.0` → `1.7.0` to match this changelog.

### Cleaned
- `.gitignore` extended (`repomix-output.md`, `.td_mcp/`).

---

## [1.7.0] - 2026-07-10

Third inspiration sweep — orchestration / continuity / packaging ideas from the
survey, plus exposing them as offline MCP tools.

### Added
- **Multi-instance discovery (twozero)**: `td_mcp.discover` — `discover_instances()`
  probes known TD-MCP ports (9980/9988/9870/8053/40404/8765) with an injectable
  probe, so an agent can pick which open TD to target. Exposed as `td_discover`.
- **Session memory (tdmcp Obsidian / AI session memory, Embody)**: `td_mcp.memory`
  — local JSONL memory of interaction turns with keyword-overlap `recall()` and
  `summarize()`. Gives an agent continuity across sessions with no vector DB.
  Exposed as `td_memory_save` / `td_memory_recall`.
- **Spatial pointer resolver (twozero `*here`/`*this`)**: `td_mcp.spatial` —
  `resolve_pointer()` / `resolve_args()` turn `*here` (current network) and
  `*this op` (selected operator) into concrete paths for the live client.
- **Progress reporting (touchdesigner_agent_mcp `report_progress`)**:
  `td_mcp.progress` — structured `{step, total, percent, label}` updates for long
  builds.
- **Project bundling `.mcpb` (tdmcp multi-client packaging)**: `td_mcp.bundle` —
  `package()` zips the project with a `server.json` manifest (excluding
  `.venv`/`__pycache__`/etc.), `read_manifest()` reads it back.
- **Recipe scaffolding from a network** (tdmcp `scaffoldRecipeFromNetwork`):
  `td_scaffold_recipe` turns a TDN/operator description into a reusable recipe
  blueprint via `recipe_vault.draft_recipe_from_chain`.
- **4 new offline MCP tools** (READ_ONLY): `td_discover`, `td_memory_save`,
  `td_memory_recall`, `td_scaffold_recipe`. Offline server now exposes 35 tools.
- **Tests**: `tests/test_discover.py`, `tests/test_memory.py`, `tests/test_spatial.py`,
  `tests/test_progress.py`, `tests/test_bundle.py`, `tests/test_offline_more_tools.py`
  (19 new cases; suite now 137 passing).

## [1.6.0] - 2026-07-10

Second full inspiration sweep — more high-value, implementable ideas, plus
exposing them as **callable offline MCP tools** so an agent can actually use
them.

### Added
- **Macro recorder / replay (tdmcp)**: `td_mcp.macro` — `MacroRecorder`
  records tool calls (tool/args/result), dedupes, serializes, and replays via
  any dispatcher (e.g. the live `batch`).
- **Version compatibility + error cache (8beeeaaat)**: `td_mcp.compat` —
  `check_compat()` (MAJOR=error / MINOR=warning / PATCH=tolerated) and an
  `ErrorCache` (TTL) so a flapping bridge doesn't spam identical transport
  errors.
- **Expert prompts (TD_Builder `get_expert_prompt`)**: `td_mcp.prompts` — phase
  personas (td_designer, network_builder, td_glsl_expert, td_python_expert,
  ui_expert, critic) routed per build phase (plan/build/self_improve).
- **Performance analyzer (TD-Codex)**: `td_mcp.perf` — classifies fps, ranks
  slowest cooks, and emits concrete optimization suggestions from a snapshot.
- **Media-server connectors (TD-Codex)**: `td_mcp.showcontrol.media_server()`
  plans Millumin / Resolume / Notch / Disguise / QLab / MadMapper connectors
  (transport + endpoint + target operator).
- **7 new offline MCP tools** (all READ_ONLY, risk-tiered): `td_glsl_pattern`,
  `td_network_template`, `td_expert_prompt`, `td_compat_check`, `td_score_build`,
  `td_mediaserver`, `td_analyze_performance`. The offline server now exposes 31
  tools.
- **Tests**: `tests/test_macro.py`, `tests/test_compat.py`, `tests/test_prompts.py`,
  `tests/test_perf.py`, `tests/test_offline_new_tools.py` (25 new cases, now 118
  total passing).

## [1.5.0] - 2026-07-10

Full inspiration sweep across the entire `github-mcp` ecosystem — implementing
the remaining high-value, implementable ideas (not the placeholder/mock stubs).

### Added
- **Vision / histogram caption (tdmcp `captionTop`)**: `td_mcp.vision` —
  `analyze_pixels()` computes deterministic pixel stats (`mean_luma`,
  `near_black_fraction`, `saturation_mean`, `classification`) and a viewport
  verdict (`is_black`/`is_flat`/`fully_transparent`/`pass`) with NO vision LLM
  required; `caption_from_stats()` emits a text caption + `viewport_verdict()`
  bridges to the agent loop. A blind agent can now "see" the render.
- **Build scoring + self-repair loop (tdmcp `scoreBuild`/`repairNetwork`)**:
  `td_mcp.scoring` — `score_build()` grades a network 0..100 (A–F) from
  validity, typed/wired completeness and corpus backing; `repair_network()`
  iteratively applies `auto_repair` until clean (the `autoRepairLoop`).
- **Token-efficient logs (Embody discipline)**: `td_mcp.tools.logs` — a bounded
  `LogRing` plus `attach_piggyback()` that only attaches WARNING/ERROR `_logs`
  to a result, keeping successful runs quiet within context budgets.
- **GLSL pattern library + network templates (bottobot `get_glsl_pattern` /
  `get_network_template`)**: `td_mcp.glsl_patterns` — 6 named, paste-ready
  fragment shaders and 4 ready-to-build network templates (audio-reactive,
  feedback, render-scene, LED wall) emitting TDN operator lists.
- **Per-client config + skill generation (Embody / twozero)**: `td_mcp.config_gen`
  — generates the exact `.mcp.json` / `claude_desktop_config.json` and a tailored
  `CLAUDE.md` skill doc for td-mcp's two servers, for a one-step client install.
- **TDN idle auto-checkpoint (Embody)**: `tdn.checkpoint()` / `restore_checkpoint()`
  persist a git-diffable `.tdn` snapshot to disk (volatile headers ignored by
  `diff_tdn`) for crash recovery / auto-restore on project open.
- **Security hardening — Origin pinning (Embody)**: `server_live` now enforces a
  configured `TD_MCP_ALLOWED_ORIGINS` allowlist (Host-pinning / CSRF defense) on
  top of the existing DNS-rebind Host check, in the Streamable-HTTP server.
- **Tests**: `tests/test_vision.py`, `tests/test_scoring.py`, `tests/test_logs.py`,
  `tests/test_glsl.py`, `tests/test_config_gen.py` (23 new cases, all passing).

## [1.4.0] - 2026-07-10

Agent self-healing & build-quality improvements, inspired by surveying the
full `github-mcp` ecosystem (Embody, tdmcp, TD_Builder_alpha, touch_mcp,
twozero_td_mcp, bottobot, …).

### Added
- **Recovery hints (Embody)**: `td_mcp.tools.recovery` — a pure error-signature
  catalog that returns `{cause, action, next_tools}` for every common TD/bridge
  failure (unreachable bridge, node-not-found, unknown param, family mismatch,
  cook error, read-only/auth blocks, …). Wired into `TDClient._call` so **every
  failed live tool response now carries a `recovery` block**, letting an agent
  self-correct instead of retrying blindly.
- **Staged build validator + auto-repair (TD_Builder 5-stage + tdmcp
  `autoRepairLoop`)**: `td_mcp.validation` — `validate_build()` runs five staged
  passes (schema → semantic → reference → logical → td_rules/family-compat),
  `suggest_repairs()` turns findings into actions, and `auto_repair()` returns a
  corrected description (drops dangling inputs/connections, auto-names unnamed
  nodes, removes typeless nodes). Corpus-aware: uses real operator families and
  parameter schemas when available.
- **Layout lint (Embody)**: `td_mcp.tools.layout` — `lint_layout()` flags nodes
  at (0,0), overlaps, zero-size, stray/orphan docks, and unnamed operators;
  `placement_hint()` gives a deterministic next-free position to stop the
  classic node pile-up.
- **Recipe vault upgrade (tdmcp)**: `recipe_vault` now stores tdmcp-style
  design-file metadata (`difficulty`, `td_version_min`, `technique`) and ships
  `draft_recipe_from_chain()` to auto-skeleton a reusable blueprint from an
  operator chain. Listings surface the new fields.
- **Tests**: `tests/test_recovery.py`, `tests/test_validation.py`,
  `tests/test_layout.py`, `tests/test_recipe.py` (23 new cases, all passing).

## [1.3.0] - 2026-07-10

Improvements informed by reviewing sibling TouchDesigner MCP projects
(`github-mcp/Embody` Envoy, `DOCUMENTATION/`) in the parent folder.

### Fixed
- **Bridge `do_GET`/`do_POST` were defined twice** (`td_mcp_bridge.py`). The
  second (stub) definitions shadowed the real ones, silently killing SSE
  streaming and root JSON-RPC — two headline features advertised by the
  Streamable-HTTP transport. Removed the dead stubs so the real handlers
  (WebSocket upgrade, SSE, `/` JSON-RPC, chat UI, status, resources) are active.
  Added `tests/test_scan_ws.py::test_single_do_get_post` to lock this in.
- **CORS `*` wildcard was still live despite the docstring claiming it was
  removed for CSRF** (`td_mcp_bridge.py` `_send` + `do_OPTIONS`). Replaced with
  `_cors_origin()`, which only echoes back a *loopback* Origin and downgrades
  everything else to `http://127.0.0.1`. Added
  `tests/test_scan_ws.py::test_cors_reflects_loopback_blocks_external`.
- **Undefined `debug` helper** (`td_mcp_bridge.py`): `start()`/`stop()`
  referenced `debug(...)` which was never defined, raising `NameError` on a
  second `start()` or on `stop()`. Added a `debug` log helper.
- **Redundant duplicate WebSocket-upgrade check** in the bridge `do_GET`.

### Changed
- **`skills/td-building/SKILL.md`** gained a *Connectivity (do this first)*
  section and a *Self-correction* section distilled from Embody's
  `td-connectivity` / `mcp-safety` rules: check reachability first, resolve
  `*here`/`*this`, treat `capture_viewport` quality verdicts as the source of
  truth for visual work, and follow `recovery_hints` instead of retrying.

---

## [1.2.0] - 2026-07-10

### Fixed
- **Rerank score misassignment**: `ParallelRetriever` now keeps each doc's own
  RRF score attached through the cross-encoder reordering (previously scores
  were reassigned by position, so the wrong score landed on the wrong doc after
  reranking). Added `tests/test_rag.py::test_rerank_keeps_scores`.
- **Streamable-HTTP `tools/list` exposed empty schemas**: the HTTP MCP server
  now emits the full input schema, description, and risk annotations for every
  tool (previously it sent `{"properties": {}}` with `fn.__doc__` which was
  `None` for the lambda-registered tools, making HTTP clients blind to tool
  parameters).
- **Legacy CLI was dead**: `td-mcp-live` subcommands (`status`, `create`,
  `set`, `exec`, `list`, `batch`, `read`, `scan`, `find`, ...) were
  commented out, so the README quick-start commands did nothing. Reimplemented
  a typed argparse CLI that dispatches to the bridge client.

### Changed
- **Unified tool metadata**: both the stdio and Streamable-HTTP servers now
  derive their tool descriptions, input schemas, and risk annotations from a
  single `_TOOL_META` source, eliminating duplicated schema definitions.
- **Risk classification** (`td_mcp/tools/risk.py`) now covers all 39 live
  bridge tools, so `READ_ONLY` / `WRITE_ADDITIVE` / `DESTRUCTIVE` hints are
  consistent across the live server, the offline server, and any bridge policy
  enforcement (`TD_MCP_MAX_RISK`, `TD_MCP_ALLOW_EXEC`, `TD_BUILDER_LIVE_READONLY`).

### Cleaned
- Removed a duplicated `_json_loads` helper in `server_offline.py`.

---

## [1.1.0] - 2026-07-09

### Added
- **Live Project RAG (Network Context Scanner)**: Implemented `_do_scan_network()` in `bridge/td_mcp_bridge.py`. Recursively walks the active TouchDesigner COMP graph up to configurable `depth`, collecting node paths, types, wiring inputs, non-default parameters, and cook errors into a structured JSON snapshot.
- **Agent Context Injection**: Updated `bridge/td_mcp_agent.py` to automatically call `scan_network` at the start of every chat session and inject the live topology into the LLM system prompt so the model is always aware of what already exists in the project.
- **`scan_network` MCP Tool**: Registered the new tool in `td_mcp/server_live.py` (`list_tools` schema + `call_tool` handler + `TDClient.scan_network` method).
- **Natively Embedded Chat UI Panel** (`bridge/chat_ui.html`): A self-contained, zero-dependency HTML/CSS/JS chat interface with a premium glassmorphic dark-mode design. Features include: provider selector (Ollama / Gemini / OpenAI), persistent credential storage via `localStorage`, a live network node sidebar panel, health status indicator, and a full multi-step autonomous agent loop communicating directly with the bridge.
- **Chat UI served on `GET /`**: Dropped a Web Render TOP at `http://localhost:9980/` loads the chat panel directly inside TouchDesigner.
- **Pure-Python WebSocket Bi-directional Streaming**: Implemented RFC 6455 WebSocket support in `bridge/td_mcp_bridge.py` with no external dependencies — includes handshake key derivation (`_ws_handshake_key`), frame encoder (`_ws_make_frame`), frame decoder (`_ws_read_frame`), token-authenticated connection upgrade, and a tool-dispatch loop (`_handle_websocket`) that streams results back in real time.
- **New Test Module** (`tests/test_scan_ws.py`): 6 tests covering WebSocket RFC 6455 handshake key derivation, small/medium payload frame roundtrips, close frame encoding, offline `scan_network` graceful failure, and agent tool schema validation.

### Changed
- Bridge `do_OPTIONS` now returns proper CORS headers for cross-origin access from the Chat UI.
- `Access-Control-Allow-Origin` updated to `*` to allow the embedded HTML panel to communicate freely on localhost.

---

## [1.0.0] - 2026-07-09

This is the initial release of the TouchDesigner MCP (TD_MCP) "Ultimate" Super-Server, uniting the best features of the TouchDesigner MCP ecosystem (including parallel-RAG search, live network control, and autonomous in-app building) under a clean, unified structure.

### Added
- **Live stdio MCP Server**: Graduated `td_mcp.server_live` from a CLI client to a fully compliant standard stdio Model Context Protocol (MCP) server (via `--mcp` flag) exposing 16 live control tools (e.g. `create_node`, `set_parameters`, `get_errors`, `capture_viewport`).
- **Autonomous in-TouchDesigner Agent**: Created `bridge/td_mcp_agent.py`, a zero-dependency OpenAI-compatible agent script designed to run natively inside TouchDesigner Text DATs. It supports 13 function-calling tool schemas and interacts with local Ollama, Gemini, or OpenAI to edit networks autonomously.
- **Spatial Context Pointers**: Implemented `*here` and `*this` path resolution inside the TouchDesigner bridge `bridge/td_mcp_bridge.py` utilizing the TouchDesigner Python `ui` API at runtime.
- **New Unit Tests**:
  - `tests/test_live_server.py` to verify the live server's wiring and tool registration.
  - `tests/test_agent.py` to assert autonomous agent schemas compile and fail gracefully when offline.
- **Documentation**: Created `HOW_TO_USE.md` mapping out installation, starting the bridge, configuring Claude/Cursor clients, and using the autonomous agent loop.

### Fixed
- **Setuptools Packaging**: Fixed a build error by removing the non-existent `"td_mcp.bridge"` from `packages` in `pyproject.toml` (since `bridge` is a top-level workspace directory and not a python subpackage).

### Changed
- **Dependencies**: Upgraded all project dependencies in `uv.lock` to their latest compatible versions via `uv lock --upgrade`.
- **Test Suite**: Expanded test coverage to 30 tests (all passing).
- **README Updates**: Updated `README.md` to document the unified layout, live MCP server setups, new tools, and updated recall metrics (0.966).
