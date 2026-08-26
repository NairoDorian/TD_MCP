# What goes *into* TouchDesigner

> **Short answer:** run `bridge/bootstrap.py` once inside TouchDesigner. It
> builds a `/td_mcp` Base COMP containing three operators, and can save that
> COMP as `td_mcp.tox` — which is then the drag-and-drop file for everyone
> else. The `.tox` is a **build artifact**, not the source.

The MCP server runs *outside* TouchDesigner, as a normal Python process your AI
client launches. It has no way to reach into a running TD session on its own.
Something has to live *inside* the session, listen on a port, and perform the
work on TouchDesigner's main thread. That something is the **bridge**.

This page covers what the bridge is, how it gets into your project, why it is
packaged the way it is, and how every other project in the ecosystem solves the
same problem.

---

## Contents

- [The component](#the-component)
- [Getting it into your project](#getting-it-into-your-project)
- [`link` vs `embed`](#link-vs-embed)
- [Parameters](#parameters)
- [Why a builder script and not a checked-in `.tox`](#why-a-builder-script-and-not-a-checked-in-tox)
- [How every other project does it](#how-every-other-project-does-it)
- [Why td-mcp does not use a Web Server DAT](#why-td-mcp-does-not-use-a-web-server-dat)
- [Building the `.tox` for distribution](#building-the-tox-for-distribution)

---

## The component

```
/project1/td_mcp                 Base COMP  — custom page "TD MCP"
├── bridge      Text DAT         the bridge source (linked or embedded)
├── startup     Execute DAT      Start / Create / Exit / Frame Start → starts, stops, pumps
└── control     Parameter Exec   reacts to the COMP's own parameters
```

Three operators, and each one earns its place:

**`bridge` (Text DAT)** holds `bridge/td_mcp_bridge.py` — ~2,200 lines that
implement all 46 tools, the HTTP/SSE/WebSocket transports, auth, the exec gate
and the undo wrapping. A Text DAT is importable as a Python module via
`DAT.module`, which is how `startup` reaches `start()`.

**`startup` (Execute DAT)** applies the component's parameters as environment
variables and then starts the bridge. It fires on **Start** *and* on **Create**.
Both matter, and for different reasons:

- `Start` runs when TouchDesigner launches — the case where your project already
  contains the component and you just opened it.
- `Create` runs when the operator is created — which is what happens when
  someone drags the `.tox` into a network. **A dragged-in `.tox` never sees
  `Start`.** Without `Create`, a drag-and-drop install would sit there looking
  inert until the next restart, which is the single most confusing thing a
  drop-in component can do.

`Exit` stops the bridge so TouchDesigner does not leave a listening socket
behind on close.

**`Frame Start` is the part that makes the bridge legal.** TouchDesigner
objects may only be touched from its main thread, and the bridge's HTTP
server runs on a thread of its own. So a request never runs its handler
where it arrives: it is queued, and `onFrameStart` calls
`bridge.module.pump()` — on the main thread, once per frame, under an 8 ms
budget — which runs the handler and hands the result back. Latency is at
most one frame. Without this callback every tool call fails fast with *"the
main-thread pump has never run"*; before it existed, TouchDesigner answered
every call with *"objects cannot be accessed outside the main thread"* and
warned the application might terminate.

**`control` (Parameter Execute DAT)** makes the component operable from its
parameter panel rather than only from the Textport. Toggling `Active` starts and
stops it; changing `Port`, `Token`, `Allow Python Execution` or `Protected
Paths` bounces a running bridge, because a live bridge is holding the *old*
value and a panel that shows the new one while the old one is in effect is worse
than one that shows nothing.

### Order matters in `startup`

The bridge reads its entire configuration from `os.environ` **at import time**,
and a Text DAT is imported lazily on first `.module` access. So the environment
has to be set *before* that first access:

```python
def start_bridge():
    comp = me.parent()
    _apply_env()                    # ← must come first
    comp.op('bridge').module.start()
```

Set the environment afterwards and the bridge is already running on the
defaults, with your parameters displayed but not applied. `tests/test_bootstrap.py`
pins this ordering.

---

## Getting it into your project

### Option A — drag `bootstrap.py` in, then Run Script (easiest)

Drag `bridge/bootstrap.py` from your file manager into a TouchDesigner network.
TouchDesigner creates a **file-backed Text DAT** — its **File** parameter is the
path you dragged from, which is exactly the folder `td_mcp_bridge.py` lives in.
Right-click the DAT → **Run Script**.

Nothing to configure: the builder reads its own DAT's File parameter to locate
the bridge. Delete the DAT afterwards if you like; the `/td_mcp` component it
created is independent of it.

### Option B — the Textport

Open the Textport (**Alt+T**, or *Dialogs → Textport and DATs*) and run:

```python
TD_MCP_REPO = r'C:/path/to/TD_MCP'
exec(open(TD_MCP_REPO + '/bridge/bootstrap.py').read())
```

That is the whole install. It prints the component path, the mode it chose, the
port and the auth token. Re-running it is safe — it updates the existing
component in place and preserves any parameter values you have set.

> **Why two lines and not one?** Neither `exec(open(...).read())` nor a Text DAT
> gives the script a usable `__file__`. The first inherits the Textport's
> globals, where it is undefined; the second sets it to the DAT's *operator
> path*, so `os.path.abspath('/project1/bootstrap')` becomes
> `C:\project1ootstrap` and every lookup beside it names a folder that never
> existed. Naming `TD_MCP_REPO` first puts a real path in the globals the script
> can see. (Field-reported and fixed in v1.12.0 — the builder now also searches
> its own DAT's File parameter, the project folder and the working directory,
> and refuses with instructions rather than a misleading "not readable".)

If you pasted the script into a Text DAT instead of dragging it, a Textport
variable is **not** visible in the DAT's own module namespace — edit the `REPO`
line in the CONFIGURATION block near the top of the DAT.

To point it somewhere other than `/project1`, or to change the port:

```python
TD_MCP_TARGET = '/project1/tools'
TD_MCP_PORT = 9765
TD_MCP_REPO = r'C:/path/to/TD_MCP'
exec(open(TD_MCP_REPO + '/bridge/bootstrap.py').read())
```

### Option C — drag and drop a `.tox`

Once someone has built `td_mcp.tox` (see
[Building the `.tox`](#building-the-tox-for-distribution)), it is a normal
TouchDesigner component: drag it from Explorer/Finder into any network and it
starts itself. This is the nicest experience and the one most projects ship —
but somebody has to run TouchDesigner once to produce the file.

### Option D — by hand

If you would rather see every step, or you are working in a locked-down project:

1. Create a **Base COMP**, name it `td_mcp`.
2. Inside it, create a **Text DAT** named `bridge`.
   - *Linked:* set its **File** parameter to
     `<repo>/bridge/td_mcp_bridge.py` and turn on **Sync to File**.
   - *Embedded:* paste the contents of that file into the DAT.
3. In the Textport: `op('/project1/td_mcp/bridge').module.start()`

That gets you a running bridge with no parameters and no auto-start — fine for a
one-off session. The builder exists so you do not have to keep doing this.

### The CLI half

`td-mcp-install` stages the bridge and writes a receipt, then tells you to do
the TouchDesigner step above:

```bash
td-mcp-install install --yes     # exits 50 = requires_restart, by design
td-mcp-install verify --json     # after you have created the component
```

It exits **50**, not 0, because an external process genuinely cannot create an
operator inside a running TouchDesigner project. See
[INSTALL.md](INSTALL.md#install-sop-v1) for the full lifecycle.

---

## `link` vs `embed`

The `bridge` Text DAT can carry the source two ways, and the builder picks
automatically unless you tell it otherwise.

| | `link` | `embed` |
|---|---|---|
| How | `File` parameter + **Sync to File** | source pasted into the DAT |
| Updating the bridge | save the `.py`, TD reloads it | re-run the builder |
| Moves to another machine | breaks unless the `.py` moves too | works anywhere |
| Diffable in git | yes — the `.py` is the file | only inside the `.toe` |
| Chosen when | the source file is readable (default) | saving a `.tox` (forced) |

**`link` is the development mode.** TouchDesigner watches the file and reloads
the DAT whenever it changes, so the edit loop is: save the `.py`, toggle
`Restart` on the component, done. No copy-paste, and git sees every change.

**`embed` is the distribution mode.** A `.tox` is meant to travel to machines
that have never seen this repository. A file-linked DAT whose target does not
exist there loads as an *empty* DAT, and an empty bridge DAT fails with `no
module named start` — a message that points nowhere near the real problem. So
saving a `.tox` always embeds; `resolve_mode()` enforces it and a test pins it.

A note on relative paths in `link` mode: TouchDesigner resolves a DAT's `File`
parameter relative to the project folder. The builder writes an **absolute**
path, which is correct for a repo checked out anywhere. If you want a portable
project, put the `.py` inside your project folder — TD will then store the path
relative and the project stays movable.

---

## Parameters

All on the component's **TD MCP** page.

| Parameter | Type | Default | What it does |
|---|---|---|---|
| **Active** | Toggle | On | Starts/stops the bridge. On by default so a dropped-in `.tox` just works. |
| **Port** | Int | `9980` | The port the bridge listens on (loopback only). |
| **Auth Token** | Str | *empty* | Leave empty and the bridge mints a random token at start and prints it. Set one to pin it across restarts. |
| **Allow Python Execution** | Toggle | On | Gates *every* route that hands TouchDesigner caller-supplied code — see below. |
| **Protected Paths** | Str | *empty* | Comma-separated paths the bridge refuses to delete or run code inside. |
| **Restart Bridge** | Pulse | — | Stop and start, picking up any changed source or parameter. |
| **Print Auth Token** | Pulse | — | Re-prints the current token to the Textport. |

### About **Allow Python Execution**

It defaults **On**, matching the bridge's own documented default. Understand
what it covers before deciding: it is not just `execute_python`. TouchDesigner
runs caller-supplied Python from many places, and the gate closes all of them
together — parameter expressions, `bind_parameter_expression`, a DAT's text, a
DAT's `file` parameter, creating Script/Execute/GLSL/Expression operators,
`exec_node_method`, and any `import_recipe` blueprint carrying one of those.

Turn it **Off** when the MCP client is not fully trusted, or when you are
running a show and want the network read-mostly. The bridge still creates,
wires, reads and inspects; it just stops being a Python execution route. Two
tool classes are refused outright in that mode and say so, rather than failing
obscurely.

The bridge is loopback-only and requires a bearer token in every request
(compared with `hmac.compare_digest`), with Origin pinning and DNS-rebind
protection. That is the reason On is a defensible default — not a reason to
leave it on if you do not need it.

---

## Why a builder script and not a checked-in `.tox`

A `.tox` is **opaque compressed binary**. Confirm it for yourself:

```bash
$ head -c 4 td_mcp_server.tox | xxd
00000000: 0000 0001
```

Which means:

- It cannot be **diffed**. A code review of a `.tox` change shows "binary files
  differ".
- It cannot be **built** without a running TouchDesigner, so no CI can
  regenerate it, and no staleness guard can prove it matches the source.
- It cannot be **inspected** before you run it. Dragging a `.tox` from the
  internet into your project executes whatever its Execute DATs contain.

So `td-mcp` treats the script as the source of truth and the `.tox` as a build
artifact — the same relationship as source and compiled binary. You are welcome
to commit the artifact for convenience; you regenerate it whenever the bridge
changes.

This is not a novel opinion. mintiasaikoh's README says the same thing about
their own component, in as many words: *"`mcp_bridge.tox` is the output of
running this script"*.

### The fork this replaced

Until v1.12.0, `bridge/bootstrap.py` was not a builder — it contained a
**second, complete copy of the bridge**, pasted in and then left to rot. By the
time it was found it had drifted to **39 tools against the bridge's 46**, held
`COMP.create` calls with the arguments reversed at three sites, and carried
every blind `node.errors` read that [Wave 12](../UPSTREAM_SYNC_2026-08-26.md#31-the-live-bridge-cannot-read-touchdesigner-errors--critical)
fixed. Anyone who ran it installed a broken bridge.

Nothing caught it, because `scripts/check_bridge_stale.py` only ever compared
`td_mcp_bridge.py` against the live registry. It now also fails if any other
file under `bridge/` declares a `_DISPATCH` table or `_do_*` handlers, and
`tests/test_bootstrap.py::test_no_forked_bridge_logic` asserts the same thing.
**One bridge, one copy.**

---

## How every other project does it

Surveyed across the 30 repositories in `github-mcp/` (2026-08-26). Thirteen ship
a binary `.tox` or `.toe`; the rest ask you to paste something.

### Strategy 1 — ship a binary `.tox`, drag and drop

The best user experience, and the most common.

| Project | Artifact | Size | Port |
|---|---|---|---|
| `johnsabath/touchdesigner-mcp` | `td_mcp_server.tox` | 20 KB | 9988 |
| `mrinalghosh/touchdesigner-mcp` | `td_component/touchdesigner_mcp.tox` | 32 KB | 9980 |
| `benoitliard/touch-mcp` | `td/touch_mcp.tox` | 12 KB | 9980 |
| `mintiasaikoh/touchdesigner-mcp` | `td/mcp_bridge.tox` | 4.3 KB | 9981 |
| `8beeeaaat/touchdesigner-mcp` | `td/mcp_webserver_base.tox` | 4 KB | 9981 |
| `wangpolanshan/touchdesigner-mcp` | `outputs/TD_MCP_Bridge.tox` | — | 9980 |
| `dreamrec/TDPilot` | `td_component/tdpilot.tox` | 60 KB | — |
| `TrueFiasco/TD_Builder_alpha` | `MCP/td-webserver/mcp_webserver_base.tox` | — | — |
| `familienak-tech` | `td-bridge/mcp_webserver_base.tox` | — | 3000 |
| `dylanroscover/Embody` | `release/Embody-v6.0.280.tox` | 1.6 MB | 9870 |

johnsabath's is explicitly **self-contained** — "no file paths to configure" —
i.e. embedded, not linked. `aliphi` and `froussio` go further and ship a whole
`.toe` project file.

### Strategy 2 — ship a generator, build the `.tox` from it

The pattern `td-mcp` follows.

- **`mintiasaikoh/touchdesigner-mcp`** — `td/setup_mcp.py` (11.8 KB). Paste into
  a Text DAT and *Run Script*, or paste into the Textport. Creates
  `/mcp_bridge` with a Web Server DAT and a callbacks Text DAT. Their README
  states the shipped `.tox` is the saved output of this script, and offers both
  paths side by side.
- **`benoitliard/touch-mcp`** — `td/BUILD_TOX.md`, a five-step manual build:
  create the Base COMP, add a Web Server DAT on 9980, paste `callbacks.py` into
  a Text DAT, paste `command_router.py` into a second Text DAT with **Module on
  Demand** enabled, add a `log` Table DAT. The generator written out as prose.

### Strategy 3 — a stub DAT that loads real files from disk

Exactly the shape you described — a small node whose DATs reference `.py` files
next to the project.

- **`dcc-mcp/dcc-mcp-touchdesigner`** ships
  `resources/bootstrap.py.tmpl` plus an `execute_dat.py`. Its installer stages
  them to `~/.dcc-mcp/touchdesigner/` and then emits a machine-readable
  `next_steps` entry telling the agent to create *one* Execute DAT with Start
  and Exit callbacks whose body imports the staged bootstrap. The DAT in the
  project is a stub; the real code lives on disk and is version-checked by
  SHA-256 in a receipt.
- **`superdwayne/Touchdesigner-mcp`** — `td_mcp_bootstrap.py` +
  `td_mcp_plugin_installer.py`, same idea.

This is the most *maintainable* shape — updating the server updates the code
without touching anyone's `.toe` — and the most fragile if a path moves. `td-mcp`
gives you it as **`link` mode**, with `embed` as the portable alternative.

### Strategy 4 — paste the server into a Text DAT

What `td-mcp` used to require, and what several thin bridges still do
(`rickengineroom`, and the manual fallback in most of the above). It works and
needs no artifact, but "paste 2,200 lines into a DAT" is not an install
procedure, and it gives you no way to tell whether the paste is current.

### What almost everyone shares

Every HTTP bridge in the library except `td-mcp` and `michaelslain` is built on
a **Web Server DAT**: TouchDesigner's own operator that listens on a port and
calls back into Python. Ports cluster on 9980/9981; `johnsabath` uses 9988,
`Embody` 9870, `twozero` 40404, `familienak` 3000, `michaelslain` a TCP/NDJSON
socket on 9005.

---

## Why td-mcp does not use a Web Server DAT

`td-mcp`'s bridge runs its own `http.server.HTTPServer` on a daemon thread
rather than hosting a Web Server DAT. That is a deliberate trade, and the
first live session presented the bill in full.

**What it buys.** One transport implementation serving plain JSON-RPC,
Streamable HTTP, SSE **and** WebSocket from the same process, plus the chat UI
at `GET /`. A Web Server DAT gives you HTTP request callbacks; the SSE and
WebSocket paths would each need their own scaffolding. It also means the
whole bridge is one file with no operator wiring to get wrong — which is what
makes `link` mode possible at all.

**What it costs.** Requests arrive on a worker thread, and TouchDesigner
objects cannot be touched from any thread but the main one. Not "should not":
cannot. TouchDesigner refuses with *"objects cannot be accessed outside the
main thread"* and warns the application may terminate. `run()` is not a way
back either — called off the main thread it does not raise, it corrupts TD
silently (Embody v6.0.251, Derivative-confirmed).

**How it is paid.** The sanctioned TouchDesigner pattern, which is what every
background-thread component in the palette does: a queue, drained from the
main thread. The HTTP thread enqueues a job and waits; the component's
`startup` Execute DAT calls `pump()` on every Frame Start and runs the job
there. The handler executes exactly where a Web Server DAT callback would
have — one frame later at most. A call already on the main thread (a tool
calling a tool, the Textport) runs inline, so nothing waits on its own queue,
and a missing or stalled pump fails fast with a message naming the fix
rather than hanging.

A Web Server DAT remains the safest starting point for a bridge of your own:
its callbacks run on the main thread and this whole class of problem does not
exist. `td-mcp` took the other road for transport breadth and pays with the
pump — which is now tested by driving the real handler over a real socket
from the wrong thread.

---

## Building the `.tox` for distribution

On a machine with TouchDesigner:

```python
# Textport
TD_MCP_SAVE_TOX = 1
TD_MCP_REPO = r'C:/path/to/TD_MCP'
exec(open(TD_MCP_REPO + '/bridge/bootstrap.py').read())
```

This forces `embed` mode, builds the component, and writes `td_mcp.tox` next to
your project file. The Textport prints the path.

Then verify before you publish it:

1. **Open a fresh TouchDesigner project** — not the one you built it in.
2. Drag `td_mcp.tox` into the network. It should start immediately (that is the
   `Create` callback earning its keep) and print a token to the Textport.
3. Check the component's parameters look right.
4. Point an MCP client at it and call `describe_td_tools`. Confirm
   `bridge_version` matches the package version — if it does not, the `.tox`
   was built from a stale checkout.

**Rebuild the `.tox` whenever `bridge/td_mcp_bridge.py` changes.** An embedded
copy is a snapshot, and unlike `link` mode nothing will tell you it has gone
stale except a version mismatch warning from the live server — which is exactly
why that warning exists.

---

## See also

- [INSTALL.md](INSTALL.md) — the full install lifecycle, both halves
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — when the bridge will not start or connect
- [ECOSYSTEM.md](ECOSYSTEM.md) — the 30-project survey this page draws on
- [ARCHITECTURE.md](../ARCHITECTURE.md) — how the two servers and the bridge fit together
