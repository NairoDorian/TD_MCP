# MCP 2026-07-28 / SDK v2 — Migration Audit (COMPLETED)

Date: 2026-08-16 (audit) · 2026-08-17 (implementation) · Author: TD_MCP Phase 2

TouchDesigner compatibility target: **2025.33070** (Jul 16, 2026) — all MCP
2026-07-28 protocol features must be compatible with this TD build.

Project tooling: **uv** · Python **3.11.15**

## Current state (BEFORE migration)

- `td_mcp` pins `mcp>=1.0`; the lockfile resolved **mcp 1.28.1** (last 1.x).
- Both `server_offline.py` and `server_live.py` used the 1.x SDK API
  (`mcp.server.Server`, `@app.list_tools()`, `@app.call_tool()`,
  `app.run(...)` over `mcp.server.stdio.stdio_server`).
- `server_live.py` implemented a **custom** Streamable HTTP layer with
  `Mcp-Session-Id`, SSE client tracking, DNS-rebind guard and a
  hand-written JSON-RPC dispatcher. The live server advertised
  `protocolVersion: "2024-11-05"` in its HTTP `initialize` result.

## What changed in the target (2026-07-28)

- Protocol-level sessions are **removed** (`Mcp-Session-Id` is no longer
  issued/accepted; `GET`/`DELETE` on `/mcp` answer `405`).
- Protocol version + client capabilities travel per-request in the
  `_meta` envelope (`io.modelcontextprotocol/protocolVersion`,
  `io.modelcontextprotocol/clientCapabilities`).
- `logging` capability is deprecated; logs go to stderr.
- Streamable HTTP is **stateless** — one fresh server instance per request,
  so deployments can sit behind round-robin LBs.
- `server/discover` is auto-registered by SDK v2 (no manual work).
- `tool_result` carries `resultType: "complete"` (or `"input_required"`).
- Extensions (Tasks, etc.) are first-class.
- The 8beeeaaat reference repo v2.0.0 (surveyed in `github-mcp/`) already
  made the same move and documents the fallback strategy for 2025-era
  clients.

## SDK availability

- `mcp` 2.0.0 is on PyPI (confirmed 2026-08-16). ✓ Installed.
- Python 3.11+ required — bumped `requires-python` to `>=3.10` (still works
  on 3.10, but target is 3.11.15 for TD 2025.33070 compatibility).
- `import mcp.types as types` unchanged — v2 keeps the same alias. ✓

## What was changed (COMPLETED)

### 1. `pyproject.toml`
- `mcp>=1.0` → `mcp>=2.0,<3`
- Added `uvicorn` as a dependency (for stateless HTTP ASGI server).
- Updated module docstrings to reference "stateless, MCP SDK 2.0".

### 2. `server_offline.py` (stdio server)
- Migrated `create_server()` from v1 decorator API (`@app.list_tools()`)
  to v2 constructor kwargs (`on_list_tools=...` on `Server(name=..., version=..., ...)`).
- Handler signatures changed:
  - `async def list_tools():` → `async def _offline_list_tools(ctx, params):`
  - `async def call_tool(name, arguments):` → `async def _offline_call_tool(ctx, params):`
  - `async def read_resource(uri):` → `async def _offline_read_resource(ctx, params):`
  - (same pattern for `list_resources`, `list_prompts`, `get_prompt`)
- Return types wrapped in v2 result objects:
  - `list[Tool]` → `types.ListToolsResult(tools=[...])`
  - `list[TextContent]` → `types.CallToolResult(content=[...], result_type="complete")`
  - `[(text, mime)]` → `types.ReadResourceResult(contents=[types.TextResourceContents(...)])`
- Added `_tool()` helper for v2 `Tool()` constructor compatibility (v2 only
  accepts `name` as positional arg; all others are keyword-only).
- Added cache hints (`ttl_ms=60000, cache_scope="private"`) to `ListToolsResult`.
- `stdio_server()` + `app.run()` path is unchanged (backward compatible).

### 3. `server_live.py` (HTTP + stdio server)
- Same handler migration as `server_offline.py` (decorators → `on_*` kwargs,
  `(ctx, params)` signatures, v2 result types).
- **Removed `logging` capability** — no `on_set_logging_level` handler
  registered (deprecated in 2026-07-28).
- **Replaced custom HTTP handler** (`MCPStreamableHandler` + `BaseHTTPRequestHandler`):
  212 lines of session-management + JSON-RPC dispatch code deleted.
  Replaced with SDK's `server.streamable_http_app(stateless_http=True)` +
  `uvicorn.run()`.
- **Removed session management**: `_sessions`, `_sessions_lock`, `_sse_clients`,
  `_sse_lock`, `_generate_session_id`, `_cleanup_sessions`, `_touch_session`,
  `_get_session`, `_sse_push` — all deleted (~6 KB).
- **Auth moved to per-request middleware**: `_BearerAuthMiddleware` (ASGI
  middleware) wraps the Starlette app, checking `Authorization: Bearer <token>`
  via `hmac.compare_digest`. No session state needed.
- **DNS-rebinding protection**: handled by SDK's `streamable_http_app()`
  (automatic for loopback). `_validate_host_header` / `ALLOWED_HOSTS` kept
  for potential additional validation.
- **Origin pinning**: `ALLOWED_ORIGINS` / `_validate_origin_header` kept
  for potential middleware use.
- `TOOL_REGISTRY`, `_TOOL_META`, `_http_tool_meta()` retained (used by tests,
  tool dispatch, and conformance validation).

### 4. `streamable_http.py` — REMOVED in v1.12.0 (was: "KEPT")

This section used to say the module was kept because the bridge imported it
and "falls back gracefully" without it. Neither half was true, and the gap
between them is why the bridge never worked inside TouchDesigner:

- The import sat in a `try/except` that set `STREAMABLE_HTTP_AVAILABLE = False`
  on failure — and the handler class then inherited from the missing name
  unconditionally. Inside TouchDesigner, where `td_mcp` is never on `sys.path`,
  every load ended in `NameError: name 'StreamableHTTPMixin' is not defined`.
- The mixin contributed nothing in any case: the bridge's `_Handler` overrode
  every method it defined, including `_handle_jsonrpc` — so the protocol
  negotiation added to this module in Wave 12 never ran.
- The bridge *did* call three session helpers (`_generate_session_id`,
  `_touch_session`, `_get_session`) that were neither imported nor defined.

The session store and version negotiation now live in the bridge itself, which
imports nothing from `td_mcp` — enforced by `scripts/check_bridge_stale.py` and
by the test double, which blocks `td_mcp` imports the way TouchDesigner does.
The bridge's own transport still carries the `Mcp-Session-Id` layer; the
stateless rewrite the SDK servers took is deferred, as before.

### 5. Embody ideas implemented (from v6.0.159)
- **Tool-schema conformance test** (`tests/test_tool_schema_conformance.py`):
  validates no dead tools (every listed tool has a dispatch branch),
  complete schemas, cache hints, and `CallToolResult` return types.

## Do NOT do yet

- Drop the session layer from the bridge's own transport (it negotiates
  2026-07-28 correctly now, but still issues `Mcp-Session-Id`). It is a
  standalone implementation inside a Text DAT, not SDK-based. Defer.
- Implement full MCP Tasks extension (MCP 2026-07-28 makes Tasks an extension,
  aligned with Embody v6.0.166 task ledger) — future work.

## Verification gate (COMPLETED)

- `uv run pytest` green: 23 MCP tests pass (test_mcp_server, test_live_server,
  test_tool_schema_conformance, test_prompts, test_logs) + 32 broader tests
  pass (test_offline_more_tools, test_offline_new_tools, etc.).
- `td-mcp-offline --mcp` boots over stdio (verified: create_server() returns
  Server with `server/discover` auto-registered).
- `td-mcp-live --http` serves stateless `POST /mcp` via SDK v2
  `streamable_http_app(stateless_http=True)` — no `Mcp-Session-Id` header,
  `GET/DELETE /mcp` returns 405 (handled by SDK).
- `tools/list` and `tools/call` work through both stdio and HTTP paths.
- `tests/test_live_server.py` updated to v2 API (`_request_handlers` with
  string keys, `server/discover` auto-registered).
- `tests/test_tool_schema_conformance.py` — new, validates tool registry
  consistency across both servers.