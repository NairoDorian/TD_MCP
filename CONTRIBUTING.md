# Contributing

Thanks for wanting to improve **td-mcp**. This is a local-first TouchDesigner
MCP toolkit; contributions should keep the offline-first, pure-Python,
fully-testable spirit of the project.

Read [`docs/TESTING.md`](docs/TESTING.md) before your first change. It explains
the TouchDesigner API double, which is the single most important convention
here.

## Before you open a PR

Follow the checklist in [`COMMIT.md`](COMMIT.md). In short:

1. **Regenerate the code-free summary** — `uv run python scripts/generate_summary.py`.
2. **Proofread every `.md` file** and edit it so it matches the current code
   (tool counts, layout tree, numbers, paths, cross-links).
3. Keep `pyproject.toml` in sync (version + packages + dependencies). Bump
   `BRIDGE_VERSION` in `bridge/td_mcp_bridge.py` to match — `test_version_truth`
   and `scripts/check_bridge_stale.py` both enforce it.
4. Add a `CHANGELOG.md` entry under the appropriate `Added` / `Changed` /
   `Fixed` / `Cleaned` category.
5. Run `uv run pytest` **and** `uv run python scripts/check_bridge_stale.py`.
6. Stage explicitly, write a clear commit message, and push.

## Guidelines

### Code

- Everything in `td_mcp/` stays pure Python (stdlib + optional `mcp` /
  `networkx` / `sentence-transformers`) and unit-testable without a running
  TouchDesigner.
- Retrieval upgrades (dense / HyDE / rerank) are **opt-in and lazy** — never
  force a download or a heavy dependency on the base install.
- `bridge/*.py` run inside TouchDesigner: zero-dependency, standard library
  only, no imports from `td_mcp`.
- Keep `SUMMARY.md` free of source code.

### The bridge has exactly one implementation

`bridge/td_mcp_bridge.py` is the only place bridge logic lives.
`bridge/bootstrap.py` *builds a component that references it* and must never
carry a `_DISPATCH` table or `_do_*` handlers of its own.

This is enforced by `check_bridge_stale.py` and
`test_bootstrap.py::test_no_forked_bridge_logic`, and the rule exists because
`bootstrap.py` held a forked copy of the whole bridge until v1.12.0. It drifted
to 39 tools against the bridge's 46, three reversed `COMP.create` calls, and
every blind error read Wave 12 fixed — and nothing compared the two copies.

### Adding a tool

A tool has to land on **four** surfaces or the parity tests fail:

1. `bridge/td_mcp_bridge.py` — the `_do_*` handler and a `_DISPATCH` entry
2. `td_mcp/server_live.py` — a `TDClient` method, a `TOOL_REGISTRY` entry and a
   `_TOOL_META` schema
3. `td_mcp/tools/risk.py` — an **explicit** risk class. Never rely on the
   fallback: a capability reaching the model without anyone deciding what it may
   do is how an unreviewed tool ships.
4. `tests/` — a handler-level test against the double

If the tool hands TouchDesigner caller-supplied code by any route — a parameter
expression, a DAT's text, a DAT's `file` parameter — it is `DESTRUCTIVE` and it
must respect `ALLOW_EXEC`. See [`docs/SECURITY.md`](docs/SECURITY.md).

### Touching the TouchDesigner API

**Check the real signature first**, in `TD HELP/*.htm` or the online docs.
Return type and method-vs-attribute both matter, and getting either wrong fails
in a way that hides itself — `op.errors` is a *method returning a string*, and
reading it as an attribute yields a truthy bound method that makes an `or []`
fallback useless and a `list()` call raise.

Then model that corner in `tests/td_double.py`, faithfully, including the
awkward parts. Do not loosen the double to make a handler pass. If TouchDesigner
does something surprising, put the surprise in a comment — that comment is why
the next person does not reintroduce the bug.

### Writing tests

- Name the failure, not the function:
  `test_traceback_counts_as_one_error_not_one_per_line`.
- Say *why* in the docstring when the behaviour is surprising.
- Assert the honest value. `is_static is None` is a real outcome and deserves
  its own test; collapsing it into `False` erases the distinction the feature
  exists for.
- A test that documents a bug should say so, so nobody "simplifies" it back into
  the bug.

### Documentation

Every user-visible change names the `docs/` page that now describes it, or is
declared internal in the changelog. A feature nobody can find is a feature that
does not exist.

Where a doc explains a design decision, **say what it cost**. The pages here are
useful precisely because they record the bug that motivated each rule.
