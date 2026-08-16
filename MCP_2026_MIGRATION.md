# MCP 2026-07-28 / SDK v2 — Migration Audit (DRAFT)

Date: 2026-08-16 · Author: TD_MCP Phase 2

## Current state

- `td_mcp` pins `mcp>=1.0`; the lockfile resolves **mcp 1.28.1** (last 1.x).
- Both `server_offline.py` and `server_live.py` use the 1.x SDK API
  (`mcp.server.Server`, `@app.list_tools()`, `@app.call_tool()`,
  `app.run(...)` over `mcp.server.stdio.stdio_server`).
- `server_live.py` / `streamable_http.py` implement a **custom** Streamable
  HTTP layer with `Mcp-Session-Id`, SSE client tracking, DNS-rebind guard and
  a hand-written JSON-RPC dispatcher. The live server advertises
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
- The 8beeeaaat reference repo v2.0.0 (surveyed in `github-mcp/`) already
  made the same move and documents the fallback strategy for 2025-era
  clients.

## SDK availability

- `mcp` 2.0.0 is on PyPI (confirmed 2026-08-16).
- Python 3.11+ required (our `requires-python = ">=3.10"` must move to
  `>=3.11` if we adopt 2.0.0 — verify the wheel metadata before deciding;
  if 2.0.0 still supports 3.10, keep the floor).
- 2.0.0 splits types into `mcp-types`; our direct `import mcp.types as types`
  likely moves to `from mcp import types` or `import mcp.types` unchanged,
  TBD by reading the 2.0.0 release notes.

## What must change in TD_MCP when we upgrade

1. **Bump `pyproject.toml`** `mcp>=2.0` (or `mcp>=1.28,<3`) and regenerate
   `uv.lock`.
2. **Offline server (`server_offline.py`)**: audit `create_server()` — the
   1.x decorator API is likely source-compatible for stdio, but the
   `Server(...)` constructor signature may change. Verify
   `app.create_initialization_options()`.
3. **Live HTTP server (`server_live.py` / `streamable_http.py`)**:
   - Replace the custom session store (`_sessions`, `_touch_session`,
     `_get_session`, `Mcp-Session-Id` header handling) with the SDK's
     stateless Streamable HTTP handler.
   - Drop `_handle_sse`'s session auth and SSE-client registry (or replace
     with the SDK's built-in SSE transport).
   - Remove `_send_jsonrpc`/JSON-RPC dispatch in favor of the SDK's
     handler — the current hand-rolled dispatcher is the biggest drift risk.
   - Advertise `protocolVersion: "2026-07-28"` (with the same
     fallback-to-legacy negotiation 8beeeaaat uses, if the SDK exposes it).
4. **Auth**: `TD_MCP_AUTH_TOKEN` Bearer check must move from session-scoped
   to per-request middleware (stateless means no session state to pin the
   token to). Preserve `hmac.compare_digest`.
5. **DNS-rebind / Origin pinning**: keep our `ALLOWED_HOSTS` /
   `ALLOWED_ORIGINS` checks as request middleware wrapping the SDK handler.

## Do NOT do yet

- Do not force mcp 2.0.0 before reading its Python migration notes; the
  stdio path works today on 1.28.1.
- Do not delete `streamable_http.py` until the SDK's stateless handler is
  proven to pass the existing live-server tests.

## Verification gate

- `uv run pytest` green (currently 178 tests + new Phase 2 suites).
- `uv run td-mcp-offline --mcp` still boots over stdio.
- `uv run td-mcp-live --http` serves `POST /mcp` with no `Mcp-Session-Id`
  header and answers `GET/DELETE /mcp` with 405.
- `tools/list` and `tools/call` still work through the live HTTP path
  (extend `tests/test_live_server.py` with a stateless-HTTP smoke test).
