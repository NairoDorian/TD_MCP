# Troubleshooting

Ordered by how often each one bites. Every symptom here has been seen.

---

## The bridge

### "connection refused" from the live server

The bridge is not running. In TouchDesigner's Textport:

```python
op('/project1/td_mcp/bridge').module.status()
```

| Result | Meaning | Fix |
|---|---|---|
| `{'running': True, 'port': 9980, ...}` | Bridge is up | The problem is the client — check the port and token match |
| `{'running': False, ...}` | Loaded, not started | Toggle `Active` on the component, or pulse `Restart Bridge` |
| `AttributeError: module has no attribute 'status'` | The DAT is **empty** | See below |
| `op(...) is None` | No component | Run `bridge/bootstrap.py` |

### "cannot embed the bridge … `td_mcp_bridge.py` is not readable"

The builder could not find `td_mcp_bridge.py`. The path in the message is the
giveaway: `C:\project1\...` is not a folder on your disk — it is a
TouchDesigner *operator path* that got run through `os.path.abspath`.

Inside a Text DAT, `__file__` is the DAT's operator path (`/project1/bootstrap`),
not a file. Under `exec(open(...).read())` it is usually undefined. Versions
before v1.12.0 trusted it either way.

Fix — any one of these:

```python
# Textport: name the repo first
TD_MCP_REPO = r'C:/path/to/TD_MCP'
exec(open(TD_MCP_REPO + '/bridge/bootstrap.py').read())
```

- **Or** drag `bridge/bootstrap.py` into the network and right-click →
  **Run Script**. TouchDesigner sets the DAT's **File** parameter, and the
  builder reads the path from there. Nothing to configure.
- **Or**, if you pasted the script into a Text DAT: a Textport variable is not
  visible in a DAT's module namespace. Edit the `REPO` line in the
  CONFIGURATION block near the top of the DAT, then Run Script.

On current versions this failure prints the folders it searched and the exact
lines to run instead.

### "'td.ParCollection' object has no attribute 'pulse'"

A builder older than v1.12.0 setting a parameter TouchDesigner does not have.
The Parameter Execute DAT's pulse toggle is **`onpulse`**. Re-run the current
`bridge/bootstrap.py`; it will fix the existing component in place.

If you see this for some *other* parameter name, the current builder reports it
as a warning and leaves the component otherwise wired:

```
[td_mcp] WARNING: 1 parameter(s) could not be set:
[td_mcp]   control.par.something = True -> ... has no attribute 'something'
```

Please report that with your TouchDesigner build number — parameter names do
occasionally move between builds.

### `set_parameters` said ok but nothing changed

You are on a version before v1.12.0. An unknown parameter name was a silent
skip, so a misspelling returned `{"ok": true, "applied": {}}`. Current versions
return `ok: false` with the unknown names and a close match for each.

Check the real name with `td_docs_parameter` — one call, and the habit worth
forming. See [AGENT_GUIDE.md](AGENT_GUIDE.md#the-five-rules).

### "NameError: name 'StreamableHTTPMixin' is not defined" / "Module compilation error. See /project1/td_mcp/bridge"

Your `td_mcp_bridge.py` predates v1.12.0. That file imported a module from
the `td_mcp` package, which is never importable inside TouchDesigner — the
bridge could not compile in any TD session, ever. Every callback on the
component then reports `Module compilation error` because they all reach for
the bridge module.

Update `td_mcp_bridge.py` (pull the repo, or copy the current file wherever
you dragged it from) and pulse **Restart Bridge**. In `link` mode
TouchDesigner has already reloaded the DAT from disk.

If you see a *different* `NameError` or `ImportError` in the bridge DAT, that
is a new instance of the same class of bug: the bridge must import nothing
from `td_mcp`. `scripts/check_bridge_stale.py` now refuses such imports, and
the test double blocks them — please report it with the line number.

### Every tool answers `'td.Undo' object does not support the context manager protocol`

Your `td_mcp_bridge.py` predates the first live session (v1.12.0). It wrapped
every call in `with ui.undo:`; TouchDesigner's `Undo` object has
`startBlock`/`endBlock` and no context-manager protocol, so nothing ever
reached a handler. Update the bridge file and pulse **Restart Bridge**.

### After editing the bridge, `Restart Bridge` says "address already in use"

A bridge older than v1.12.0. In link mode TD recompiles the module but the old
server thread keeps the port, and the old `stop()` did not know about it.
Current versions adopt a previous load's server. To recover on an old bridge:
restart TouchDesigner, or run in the Textport

```python
import threading
from http.server import HTTPServer
for t in threading.enumerate():
    s = getattr(getattr(t, "_target", None), "__self__", None)
    if isinstance(s, HTTPServer): s.shutdown(); s.server_close()
```

then pulse **Restart Bridge**.

### "TouchDesigner objects cannot be accessed outside the main thread"

A bridge older than v1.12.0's second live pass, or a component whose
`startup` Execute DAT predates the pump. Every request used to run its
handler on the HTTP thread; TouchDesigner refuses that and warns the
application may terminate. **Save your project**, then re-run
`bridge/bootstrap.py` (drag → Run Script): it rewrites the `startup` DAT with
the Frame Start pump and restarts the bridge.

### "main thread did not service the request: the main-thread pump has never run"

The bridge is running but nothing is draining its queue. The component's
`startup` Execute DAT needs **Frame Start** on and the current callback code —
re-run `bridge/bootstrap.py`. If you installed the bridge by hand (no
component), add an Execute DAT with Frame Start on whose `onFrameStart`
calls `op('bridge').module.pump()`.

### "… the main-thread pump last ran Ns ago"

Either TouchDesigner's main thread is stalled (a modal dialog, a long cook),
or you edited `td_mcp_bridge.py` in link mode: TD recompiled the DAT, the
new module owns the pump, and the still-running old server is queueing into
a module nobody drains. Pulse **Restart Bridge**.

### `set_parameters` says "unknown parameter: opacity (did you mean opacity?)"

The parameter exists and the suggestion proves it. A bridge older than the
first live session tested membership with `'name' in node.par`, which is
always False in TouchDesigner (ParCollection has no name-based `in`). Update
`td_mcp_bridge.py` and pulse **Restart Bridge**.

### `read_chop` fails with "'builtin_function_or_method' object is not iterable"

Same vintage: `node.chans` is a method in TouchDesigner. Update the bridge.

### `capture_viewport` reports `is_black` for a render that is clearly not

`TOP.numpyArray()` returns 0..1 floats; bridges before the first live
session compared them against 0..255 thresholds. Update the bridge. If it
persists on a current bridge, check the operator actually cooks — a
parameter error (a bad expression) makes a TOP output nothing, which *is*
black.

### The client sees "[WinError 10053] An established connection was aborted"

A refused request (wrong token, bad Host) answered before its body was read;
Windows resets the socket. Fixed in the current bridge; on an old one, the
status was 401 or 403 — check the token and the Host header.

### An MCP client shows the live server's tools but none of the offline server's

`tools/list` on `td-mcp-offline` answered *"Handler returned an invalid
result"* before v1.12.0: every tool declared `inputSchema` as a bare property
map where MCP requires a JSON Schema object. Nothing was visible to any
client. Upgrade.

### Every wiring read comes back empty (`inputs: []`, no DOT edges)

A bridge older than the deep MCP pass. `Connector.connections` holds
*connectors*; the operator is `connector.owner`, and the `outputOP` /
`inputOP` the bridge read do not exist -- inside a bare `except`, that read as
"no connections". Update `td_mcp_bridge.py` and pulse **Restart Bridge**.

### `import_recipe` fails with "Unknown operator type. Value:'constant'"

The recipe was exported by an old bridge that recorded `OP.type` (the short
form) instead of `OP.OPType`. Re-export it with the current bridge.

### The bridge stops answering after the timeline is paused

Pausing stops frames, and the main-thread pump runs on Frame Start. **Press
Play in TouchDesigner** and the queued request is answered immediately. Current
versions refuse `timeline pause` for exactly this reason (`force=true`
overrides) and name the pause in the stall message. If you paused by hand,
only the keyboard can undo it -- the bridge cannot answer the call that would.

### `execute_python` says "forbids 'eval'" for code that has no eval in it

A bridge older than v1.12.0 matched the forbidden builtins as *substrings*, so
`par.eval()` -- reading any parameter -- was refused, as was any identifier
containing `open`, `compile` or `exec`. Update `td_mcp_bridge.py`. Current
versions parse the code and match names, so `par.eval()` is fine and only a
bare `eval(...)` is refused.

### The `bridge` DAT is empty

Almost always a `link`-mode component whose file moved. The Text DAT's **File**
parameter points at a `td_mcp_bridge.py` that is not there, so TouchDesigner
loads an empty DAT — and the failure surfaces as `no module named start`, which
points nowhere near the real cause.

- Check the DAT's **File** parameter resolves to a real path.
- Moving the repo? Re-run `bridge/bootstrap.py` to rewrite the path.
- Sending the project to someone else? Rebuild in `embed` mode:
  `TD_MCP_MODE = 'embed'` before the `exec(...)`.

### The port is already in use

Nine projects in this ecosystem default to 9980 or 9981. If you have another
TouchDesigner MCP bridge installed, they collide.

Change **Port** on the component (the parameter callback bounces the bridge for
you) and set `TD_MCP_PORT` to match in your client config. `td_discover` finds
running bridges on the local machine.

### "unknown tool: …"

Your component is older than your server. The bridge is installed by *pasting*
or *embedding*, so it drifts invisibly: everything works until the server calls
a tool added after the paste.

The live server warns on the first call:

```
TouchDesigner bridge is v1.11.0 but this server is v1.12.0. Tools added since
the bridge was installed will fail as unknown tools.
```

- **`link` mode:** pulse `Restart Bridge`. TD has already reloaded the file.
- **`embed` mode:** re-run `bridge/bootstrap.py`.
- Confirm with `describe_td_tools` → `bridge_version`.

### 401 Unauthorized

The token does not match. The bridge mints a random one at every start unless
you pin it.

```python
op('/project1/td_mcp/bridge').module.status()['auth_token']
```

or pulse **Print Auth Token** on the component. Put that value in
`TD_MCP_AUTH_TOKEN` in your client config. To stop it changing every restart,
set the component's **Auth Token** parameter.

### A tool answers "execute_python is disabled" / "code route denied"

**Allow Python Execution** is off. It gates every route that hands TouchDesigner
caller-supplied code — not just `execute_python`, but parameter expressions,
`bind_parameter_expression`, DAT text, a DAT's `file` parameter, creating
Script/Execute/GLSL/Expression operators, `exec_node_method`, and any
`import_recipe` blueprint carrying one of those.

Turn it on if you trust the client. See [SECURITY.md](SECURITY.md).

### The component does nothing when dragged in

If you built the `.tox` yourself with an older builder, its Execute DAT may not
have **Create** enabled. A dragged-in `.tox` never fires `Start` — only `Create`
— so it sits inert until the next TouchDesigner restart.

Enable `Create` on the `startup` Execute DAT, or rebuild the `.tox` with the
current `bridge/bootstrap.py`.

---

## Documentation and retrieval

### Every doc tool says "documentation not indexed"

`chunks.jsonl` was never built. It is gitignored on purpose:

```bash
uv run python -m td_mcp.kb.build_kb
```

The retriever degrades gracefully to an empty index rather than crashing, which
is correct — and also why this is easy to miss.

### Answers are thin, or an operator is missing

You are on the 1,091-chunk curated corpus. Ingest the offline mirror
TouchDesigner already installed and it becomes 9,602 chunks (~17 s, no network):

```bash
uv run python -m td_mcp.kb.wiki_ingest --offline-help "C:/Program Files/Derivative/TouchDesigner/Config/OfflineHelp"
```

Recall @k=5 goes 0.991 → 1.000. Check with `td_kb_stats`.

### "KB integrity receipt mismatch"

Something appended to `chunks.jsonl` outside `build_kb`, so the receipt no
longer matches and the retriever refuses the whole KB rather than trusting a
half-written index. Rebuild:

```bash
uv run python -m td_mcp.kb.build_kb
```

### Dense retrieval is not running

It is opt-in. It needs the `[rag]` extra *and* the flag:

```bash
uv pip install -e ".[rag]"
TD_MCP_DENSE=1 uv run td-mcp-offline --mcp
```

Without both, you get BM25 — which is the tuned default path, not a fallback.

---

## The servers

### The client shows no tools

1. Is the command right? `uv run --project <abs-path> td-mcp-offline --mcp`
2. Does it start by hand? Run that exact command — an import error prints to
   stderr, which most clients hide.
3. Absolute paths only. `~` and relative paths fail under most clients.

### Non-ASCII text arrives mangled, or the server dies mid-request

Locale-codepage stdio. MCP sends UTF-8 JSON-RPC; Python binds the standard
streams to the locale codepage — CP1252 on Western Windows, US-ASCII on a
GUI-launched macOS process. An em dash arrives with the wrong character count,
and one undecodable byte can kill the read loop.

Fixed in v1.12.0 (`td_mcp/util/stdio.py`, pinned from `main()`). If you see it,
you are on an older version.

### A client refuses read-only tools

Check `TD_MCP_MAX_RISK` and `TD_BUILDER_LIVE_READONLY`. Before v1.12.0 the
offline tools had no explicit risk class and inherited the `WRITE_ADDITIVE`
fallback, so a read-only policy refused plain documentation lookups. Upgrade.

---

## Building and verifying

### `build_and_verify` reports no errors but the operator is red

You are on a version before v1.12.0, where the bridge read `node.errors` as an
attribute rather than calling the method. `get_errors` always failed and every
scanner reported clean networks. Upgrade — this is the bug that release exists
for. See [DIAGNOSTICS.md](DIAGNOSTICS.md).

### A GLSL TOP is broken and nothing reports it

GLSL compile errors appear on **none** of `errors()`, `warnings()` or
`scriptErrors()` — TouchDesigner writes them to the auto-docked Info DAT.
`get_errors` reads it as `shader_errors`; if you are seeing an empty list on a
visibly broken shader, confirm `include_shader` was not set to `false`.

### A DAT's callback raises and the tools call it clean

That is `scriptErrors`, the third surface. Same fix as above.

### An operator stays badged after the code is correct

Script errors persist until explicitly cleared. One cook inside an extension
re-init window badges a node indefinitely.

```
clear_script_errors(path)
```

Then re-verify. If it returns, it is real.

### The render looks right but nothing moves

Use `observe` instead of `capture_viewport`. A single-frame verdict cannot tell
"renders correctly" from "renders correctly and is frozen" — the most common
silent failure in a feedback or audio-reactive network.

`is_static: null` means too few frames were captured to judge, not "fine".

### `read_pop` refuses to return values

Deliberate. `POP.points(attr, start, count)` accepts both slicing arguments and
**ignores them**, so there is no cheap partial read — a 4,096-point "slice" off
a 160k POP is *slower* than reading all of it, and a full readback costs about
four frames at 60 fps. Metadata is always free. Raise `max_points` if the stall
is acceptable.

### `import_recipe` creates nodes but wires nothing

Before v1.12.0, `export_recipe` wrote source *paths* into `inputs` while the
importer looked them up by *name*, and the failed lookup was a silent
`continue`. Upgrade; the current importer accepts both spellings and reports
anything it could not resolve in `unwired`.

### A TDN checkpoint lost my GLSL / callbacks

Before v1.12.0, TDN had no field for DAT text at all. Upgrade, and prefer
`snapshot_network` (a real `.tox`) for anything you cannot afford to lose. See
[TDN_FORMAT.md](TDN_FORMAT.md).

---

## Installation

### `td-mcp-install install --yes` exits 50

Not an error. `50` is `requires_restart`: the bridge is staged and the
TouchDesigner half is up to you. Do the step it prints, then
`td-mcp-install verify`.

### `status` says `drifted`

A staged artifact no longer matches its recorded SHA-256 — usually a hand-edited
file in `~/.td_mcp`. Re-run `install --yes`.

### `verify` says `directly_usable: false`

Read the `checks` array; it names the first thing that failed, in order:
receipt present → artifacts match → KB built → a real read-only probe answers.

---

## Still stuck

Collect this before filing anything:

```bash
td-mcp-install status --json
td-mcp-install verify --json
uv run python -c "import td_mcp; print(td_mcp.__version__)"
uv run python scripts/check_bridge_stale.py
```

```python
# TouchDesigner Textport
import sys; print(sys.version, app.build)
op('/project1/td_mcp/bridge').module.status()
```
