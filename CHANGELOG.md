# Changelog

All notable changes to this project will be documented in this file.

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
