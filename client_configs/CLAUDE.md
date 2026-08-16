# td-mcp for claude

Local-first TouchDesigner MCP: an offline doc/RAG server plus a live
bridge client. Query docs offline; control a running TD over the bridge.

## Tools
- `td_docs_search`
- `td_docs_operator`
- `td_docs_parameter`
- `td_build_network`
- `create_node`
- `set_parameters`
- `connect_nodes`
- `build_and_verify`
- `scan_network`
- `capture_viewport`

## Rules
- Prefer `td_docs_*` for facts before guessing parameter names.
- Use `build_and_verify` for create->verify->preview; it returns a
  recovery hint on failure so you can self-correct.
- Wrap batches in `batch` and rely on the bridge's undo (Ctrl+Z).
- Respect risk tiers: `delete_node`/`execute_python` are DESTRUCTIVE.