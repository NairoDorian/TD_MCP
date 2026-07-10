# Contributing

Thanks for wanting to improve **td-mcp**! This is a local-first TouchDesigner MCP
toolkit; contributions should keep the offline-first, pure-Python, fully-testable
spirit of the project.

## Before you open a PR

Follow the checklist in [`COMMIT.md`](COMMIT.md). In short:

1. **Regenerate the code-free summary** — `uv run python scripts/generate_summary.py`.
2. **Proofread every `.md` file** and edit it so it matches the current code
   (tool counts, layout tree, numbers, paths, cross-links).
3. Keep `pyproject.toml` in sync (version + packages + dependencies).
4. Add a `CHANGELOG.md` entry under the appropriate `Added` / `Changed` / `Fixed` /
   `Cleaned` category.
5. Run `uv run pytest` and make sure the suite is green.
6. Stage explicitly, write a clear commit message, and push.

## Guidelines

- Everything in `td_mcp/` must stay pure Python (stdlib + optional `mcp` /
  `networkx` / `sentence-transformers`) and unit-testable without a running
  TouchDesigner.
- Retrieval upgrades (dense / HyDE / rerank) are **opt-in and lazy** — never force
  a download or a heavy dependency on the base install.
- `bridge/*.py` are designed to be pasted into TouchDesigner Text DATs — keep them
  zero-dependency and self-contained.
- Keep `SUMMARY.md` free of source code; it is a list/architecture/per-file overview.
