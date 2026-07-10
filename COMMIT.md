# Before You Commit

A short, repeatable checklist to run **before every `git commit`** so the
`td-mcp` repo stays clean, accurate, and consistent. Treat it as a gate: if any
step fails or reveals drift, fix it *before* committing.

---

## 1. Regenerate the code-free summary

The repository ships a single, code-free overview (`SUMMARY.md`). Always
regenerate it after adding, renaming, or deleting files so it reflects the
current tree and per-file descriptions:

```bash
uv run python scripts/generate_summary.py
```

> `SUMMARY.md` intentionally contains **no source code** — only the file list,
> architecture, the directory tree, and a 1–2 line description of each file.
> For a full source pack (all code inlined) run `npx repomix --config
> repomix.config.json` manually; that is *not* part of the default flow and its
> output (`repomix-output.md`) is gitignored.

---

## 2. Full documentation pass (all `.md` files)

Do a **complete pass over every Markdown file** and edit them so they match the
*current* state of the code. This is the most common source of drift, so be
thorough:

- `README.md`, `ARCHITECTURE.md`, `HOW_TO_USE.md`, `CHANGELOG.md`,
  `SUMMARY.md`, `skills/td-building/SKILL.md`, and the brainstorm/docs
  (`TD_MCP_Master_Plan.md`, `TouchDesigner_MCP_Servers.md`, `TouchDesigner_Links.md`).
- Verify, and **fix if wrong**:
  - tool counts / tool names (offline = 40, live = 39) match what the servers
    actually register;
  - file paths and the layout tree match the real directory structure;
  - numbers (chunk counts, recall/metrics, versions) match reality;
  - no hardcoded absolute paths (use `<REPO_DIR>` / relative links);
  - no `file:///` links or machine-specific usernames;
  - cross-references between docs resolve.
- If you added/removed/renamed a module, update the layout tree **and** the
  per-file description in `SUMMARY.md` (step 1) accordingly.

---

## 3. Bump the version (single source of truth)

The version lives in exactly **two** places and `scripts/bump_version.py` keeps
them aligned in one command:

```bash
uv run python scripts/bump_version.py 1.9.0
```

This updates `pyproject.toml` (`version = "..."`) **and** the top
`CHANGELOG.md` heading (`## [x.y.z] - YYYY-MM-DD`). Runtime code reads the
version from the installed package via `td_mcp.__version__` (which itself comes
from `pyproject.toml`), so **no `.py` file needs editing** when you bump.

- Only bump if the change is user-visible.
- The script prints a warning if any other file still hardcodes the old version
  — review those (build artifacts like `uv.lock` / `PKG-INFO` update themselves).
- Ensure any new importable package (e.g. `td_mcp.tools`) is listed under
  `[tool.setuptools] packages`, and keep `dependencies` / optional-extras
  (`mcp`, `rag`, `scrape`) accurate.

---

## 4. Add a `CHANGELOG.md` entry

`scripts/bump_version.py` already wrote the top heading
(`## [x.y.z] - YYYY-MM-DD`) when you bumped. Fill in its body directly under
that heading, grouped by category (`Added` / `Changed` / `Fixed` / `Cleaned`).
Mention the summary regeneration and any doc edits so the history is
self-explanatory. If you skipped the bump, add the new heading by hand and keep
it identical to the `pyproject.toml` version.

---

## 5. Run the tests

```bash
uv run pytest
```

Fix or intentionally document any failure. A red suite should block the commit.

---

## 6. Review the diff, then commit & push

```bash
git status                       # confirm only intended files are staged
git diff --stat                  # sanity-check the scope
git add <specific files>         # avoid blanket `git add -A`
git commit -m "type: short summary

- bullet 1
- bullet 2"
git push origin main
```

Good-commit hygiene:

- **Stage explicitly** — never `git add -A` / `git add .`; generated/large
  artifacts (`embeddings.jsonl`, `repomix-output.md`, `.venv/`, build dirs) are
  gitignored and must not be committed.
- **One concern per commit** when possible; keep messages imperative and scoped.
- Verify `SUMMARY.md` and docs are regenerated and consistent *before* the
  commit, not after.

---

### One-line reminder

> Bump version (`scripts/bump_version.py`) → proofread **all** `.md` files →
> `pytest` → explicit stage → commit → push.
