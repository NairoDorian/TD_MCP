# Installing td-mcp

There are **two halves**, and they fail independently:

| Half | What it is | Needs TouchDesigner? |
|---|---|---|
| **The servers** | `td-mcp-offline` and `td-mcp-live`, Python processes your AI client launches | No |
| **The bridge** | a component *inside* your `.toe` that the live server talks to | Yes |

The offline server is fully useful on its own — documentation, RAG, network
authoring, validation, scoring — with TouchDesigner closed. The live server is
a client for the bridge and does nothing until the bridge is running.

> **If you only read one thing:** run `td-mcp-install install --yes`, then run
> the two-line Textport snippet below in TouchDesigner,
> then `td-mcp-install verify`.

---

## Contents

- [Requirements](#requirements)
- [1. The Python side](#1-the-python-side)
- [2. The TouchDesigner side](#2-the-touchdesigner-side)
- [3. Register with your AI client](#3-register-with-your-ai-client)
- [4. Verify](#4-verify)
- [Install SOP v1](#install-sop-v1)
- [Environment variables](#environment-variables)
- [Upgrading](#upgrading)
- [Uninstalling](#uninstalling)

---

## Requirements

- **Python 3.11** — match TouchDesigner's own interpreter. TD 2025 ships 3.11;
  check with `app.pythonExecutable` and `sys.version` in the Textport.
- **TouchDesigner 2022+** for the bridge (2025.30000+ recommended; POP tools
  need 2025).
- **[uv](https://docs.astral.sh/uv/)** — optional but assumed throughout.

Do not mix Python minor versions between the server and TouchDesigner, and do
not replace TD's bundled NumPy unless you own the consequences.

---

## 1. The Python side

```bash
git clone https://github.com/NairoDorian/TD_MCP.git td-mcp
cd td-mcp

# Pin the interpreter to TouchDesigner's:
uv venv --python 3.11
uv pip install -e ".[mcp]"

# Build the knowledge base — 1,091 curated chunks, ~2s:
uv run python -m td_mcp.kb.build_kb
```

`chunks.jsonl` is gitignored and **must be built**. Without it the retriever
degrades gracefully to an empty index and every doc tool answers "documentation
not indexed" — it will not crash, but it will not help either.

### Optional extras

```bash
uv pip install -e ".[rag]"      # sentence-transformers + networkx: dense vectors, reranking, knowledge graph
uv pip install -e ".[scrape]"   # requests + beautifulsoup4: the doc crawler
uv pip install -e ".[test]"     # pytest + networkx: enough to run the suite
```

Dense retrieval is **opt-in** and off unless you set `TD_MCP_DENSE=1`. The
default path is pure-Python BM25 and needs no torch.

### The full offline knowledge base

TouchDesigner ships a complete offline mirror of `docs.derivative.ca`. Point
`td-mcp` at it and the KB grows from 1,091 curated chunks to **9,602** — no
network, no API keys, ~17 seconds:

```bash
uv run python -m td_mcp.kb.wiki_ingest --offline-help "C:/Program Files/Derivative/TouchDesigner/Config/OfflineHelp"
```

Retrieval recall goes from 0.991 to **1.000 @k=5**. See [RAG.md](RAG.md).

---

## 2. The TouchDesigner side

Open the Textport (**Alt+T**) and run:

```python
TD_MCP_REPO = r'C:/path/to/td-mcp'
exec(open(TD_MCP_REPO + '/bridge/bootstrap.py').read())
```

That builds a `/td_mcp` component and starts the bridge. It prints:

```
[td_mcp] component ready at /project1/td_mcp (mode=link)
[td_mcp] bridge DAT is synced to C:/path/to/td-mcp/bridge/td_mcp_bridge.py
[td_mcp] listening on http://127.0.0.1:9980
[td_mcp] auth token: xK9_pR2mQvN4tL8sW1hY6bZ3
```

**Copy that token** — your MCP client needs it.

Other routes (drag-and-drop `.tox`, fully manual, or building a `.tox` for
distribution) are in
[TOUCHDESIGNER_COMPONENT.md](TOUCHDESIGNER_COMPONENT.md#getting-it-into-your-project).

**Save your project.** The component lives in the `.toe`; if you do not save,
you do this again next session.

---

## 3. Register with your AI client

Generated config files are written to `client_configs/` by the installer. The
block itself:

```json
{
  "mcpServers": {
    "td-mcp-offline": {
      "command": "uv",
      "args": ["run", "--project", "/abs/path/to/td-mcp", "td-mcp-offline", "--mcp"]
    },
    "td-mcp-live": {
      "command": "uv",
      "args": ["run", "--project", "/abs/path/to/td-mcp", "td-mcp-live", "--mcp"],
      "env": { "TD_MCP_AUTH_TOKEN": "<the token from step 2>" }
    }
  }
}
```

| Client | Config file |
|---|---|
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` · `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Claude Code | `.mcp.json` in the project, or `claude mcp add` |
| Cursor | `%USERPROFILE%\.cursor\mcp.json` · `~/.cursor/mcp.json` |
| VS Code / Copilot | `.vscode/mcp.json` |
| Codex | `~/.codex/config.toml` |

Register **only `td-mcp-offline`** if you want documentation and offline
authoring without TouchDesigner running. It is the smaller, safer surface.

### Streamable HTTP instead of stdio

```bash
uv run td-mcp-live --http --port 8765
```

Then point an HTTP-capable client at `http://127.0.0.1:8765/mcp`. Stateless,
SDK v2, no `Mcp-Session-Id`.

---

## 4. Verify

```bash
td-mcp-install verify --json
```

```
td-mcp verify: ok
  [     ok] receipt-present - ~/.td_mcp/receipts/touchdesigner.json
  [     ok] artifacts-match-receipt
  [     ok] knowledge-base-built - td_mcp/kb/chunks.jsonl
  [     ok] offline-probe - td_docs_search answered
```

Then in TouchDesigner, confirm the live half:

```python
op('/project1/td_mcp/bridge').module.status()
# {'running': True, 'port': 9980, 'auth_token': '...', 'allow_exec': True, ...}
```

And from your agent, call `describe_td_tools`. If `bridge_version` does not
match the package version, your component is stale — re-run the builder.

---

## Install SOP v1

`td-mcp-install` is the agent-facing lifecycle, adapted from
`dcc-mcp-touchdesigner`. Every verb takes `--json`; mutating verbs require
`--yes`; `--dry-run` never writes.

```bash
td-mcp-install install --dry-run --json    # the full plan, nothing written
td-mcp-install install --yes               # stage bridge + client configs
td-mcp-install status --json               # absent | installed | drifted | outdated
td-mcp-install verify --json               # is this actually usable?
td-mcp-install uninstall --yes             # remove what the receipt lists
```

### Exit codes

| Code | Meaning | What to do |
|---|---|---|
| `0` | ok | — |
| `10` | preflight failed | wrong Python, or the bridge source is missing |
| `20` | acquire failed | the plan carried no artifacts (internal) |
| `30` | install failed | could not write to `~/.td_mcp` |
| `40` | verify failed | staged, but not usable yet — read the `checks` |
| `50` | **requires_restart** | staged successfully; do the TouchDesigner step |

**A successful first install exits 50, not 0.** That is the point. An external
process cannot create a Text DAT inside a running TouchDesigner project and
cannot restart the host, so the installer returns exactly one
machine-executable `next_steps` entry instead of claiming success:

```json
{
  "id": "create-bridge-dat",
  "description": "Create a Text DAT named `td_mcp_bridge` ... then run op('td_mcp_bridge').module.start()",
  "file_edit": { "path": "touchdesigner://current-project/text-dat/td_mcp_bridge",
                 "action": "create",
                 "content_file": "~/.td_mcp/bridge/td_mcp_bridge.py" },
  "verify_with": "td-mcp-install verify --json",
  "why": "The bridge runs inside TouchDesigner as a Text DAT. An external process cannot create an operator in a running project, and cannot save or restart it."
}
```

### The receipt

`~/.td_mcp/receipts/touchdesigner.json` records a SHA-256 for every staged
artifact. `status` re-hashes them, so a hand-edited staged file reports
`drifted` rather than passing as installed, and `verify` refuses. `uninstall`
removes only what the receipt lists — a stray file you put in `~/.td_mcp`
stays.

---

## Environment variables

### Bridge (set inside TouchDesigner, or via the component's parameters)

| Variable | Default | Meaning |
|---|---|---|
| `TD_MCP_PORT` | `9980` | Listening port (loopback only) |
| `TD_MCP_AUTH_TOKEN` | auto | Bearer token; auto-generated and printed if unset |
| `TD_MCP_ALLOW_EXEC` | `1` | `0` closes **every** code-bearing route |
| `TD_MCP_EXEC_MODE` | `on` | `off` is equivalent to `TD_MCP_ALLOW_EXEC=0` |
| `TD_MCP_PROTECTED_PATHS` | *empty* | Comma-separated paths shielded from delete/RCE |
| `TD_MCP_MAX_BATCH` | `64` | Cap on ops per `batch` call |
| `TD_MCP_MAX_DEPTH` | `4` | Cap on recursion depth for scans |
| `TD_MCP_MAX_SAMPLES` | `4096` | Cap on CHOP samples / POP point readback |
| `TD_MCP_MAX_ROWS` | `10000` | Cap on DAT rows |
| `TD_MCP_MAX_DAT_BYTES` | `1048576` | Cap on a DAT write |

### Servers

| Variable | Default | Meaning |
|---|---|---|
| `TD_MCP_MODE` | — | `mcp` to force MCP mode (same as `--mcp`) |
| `TD_MCP_HOST` / `TD_MCP_PORT` | `127.0.0.1` / `9980` | Where the live server looks for the bridge |
| `TD_MCP_DENSE` | unset | `1` enables dense vectors (needs `[rag]`) |
| `TD_MCP_WIKI_DB` | unset | Path to a `touchdesigner-wiki-rag` SQLite index to fuse |
| `TD_MCP_HOME` | `~/.td_mcp` | Where the installer stages artifacts |
| `TD_MCP_ALLOWED_ORIGINS` | *empty* | Exact Origin pinning for the HTTP server |

---

## Upgrading

```bash
git pull
uv pip install -e ".[mcp]"
uv run python -m td_mcp.kb.build_kb     # if the corpus changed
td-mcp-install upgrade --yes
```

Then **update the component in TouchDesigner**:

- **`link` mode** — nothing to do. TD reloads the DAT from disk; pulse
  `Restart Bridge` to pick it up in a live session.
- **`embed` mode** — re-run `bridge/bootstrap.py`, or rebuild and re-drag the
  `.tox`.

The live server compares the bridge's stamped `BRIDGE_VERSION` against its own
version on the first call and warns if they differ, because a stale pasted-in
bridge otherwise shows up only as an "unknown tool" error on a tool added after
the paste.

---

## Uninstalling

```bash
td-mcp-install uninstall --yes
```

Removes the staged bridge, the generated client configs and the receipt. It
does **not** remove the component from your `.toe` — that lives in your project
file, which this process does not own. Delete `/project1/td_mcp` yourself and
save.

---

## See also

- [TOUCHDESIGNER_COMPONENT.md](TOUCHDESIGNER_COMPONENT.md) — what the component is and why
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — when it does not work
- [TOOLS.md](TOOLS.md) — the full tool reference
- [SECURITY.md](SECURITY.md) — the exec gate, auth, protected paths
