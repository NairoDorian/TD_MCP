# Changelog

All notable changes to this project will be documented in this file.

---

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
