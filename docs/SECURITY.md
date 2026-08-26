# Security model

The bridge opens a port inside your TouchDesigner session and lets a language
model drive it. This page is what protects that, and what does not.

---

## Threat model

**In scope.** A local port reachable by any process or web page on the machine;
a compromised or careless MCP client; an agent that follows a malicious
instruction embedded in content it read.

**Out of scope.** An attacker with local code execution already — they can
attach to TouchDesigner directly. Nothing here is a sandbox.

**The honest summary:** with `Allow Python Execution` on, an MCP client that
holds the bearer token can run arbitrary Python inside TouchDesigner, which
means arbitrary code as your user. That is what the tool is *for*. The controls
below make that a deliberate choice rather than an accident.

---

## Transport

**Loopback only.** `HTTPServer(("127.0.0.1", port))`. Not reachable off-machine
without an explicit tunnel you set up yourself.

**Bearer token on every request.** Auto-generated with `secrets.token_urlsafe(24)`
at start and printed to the Textport; compared with `hmac.compare_digest` so a
wrong token takes constant time. Pin it via the component's **Auth Token**
parameter or `TD_MCP_AUTH_TOKEN` if you want it stable across restarts.

**CORS is loopback-reflecting, not `*`.** A wildcard let any page that could
persuade your browser to hit `127.0.0.1` reach the bridge. Only a `localhost` /
`127.0.0.1` / `[::1]` Origin is echoed; anything else gets a fixed loopback
value.

**DNS-rebind protection.** The `Host` header must be a loopback name. Pin exact
origins with `TD_MCP_ALLOWED_ORIGINS`.

**WebSocket requires an auth handshake** before any frame is dispatched.

---

## The exec gate

`TD_MCP_ALLOW_EXEC=0` (or the component's **Allow Python Execution** toggle, or
`TD_MCP_EXEC_MODE=off`).

**Disabling `execute_python` is not the same as disabling code execution.**
TouchDesigner runs caller-supplied Python from many places, and before this gate
existed, "exec is off" was not a claim the bridge could honour. The gate closes
all of these together:

| Route | Why it is code |
|---|---|
| `execute_python` | Obvious |
| Parameter expressions | TD evaluates them as Python |
| `bind_parameter_expression` | Same |
| A DAT's text | *Is* the program for an Execute/Script DAT |
| A DAT's `file` parameter | Loads that file's contents as the DAT's text |
| Creating Script/Execute/Expression/GLSL operators | Creates a code host |
| `exec_node_method` | Calls arbitrary methods |
| `import_recipe` | Blueprints can carry any of the above |
| Type-specific filter params (`groupSOP.filter`, `textCOMP.customformatting`, …) | Evaluated as Python on those types |

Detection matches on the **trailing identifier**, so `scriptCHOP`,
`glslmultiTOP` and `textDAT` all resolve without an operator-type table.

With the gate closed, refused calls say so explicitly and name the route. The
bridge still creates, wires, reads, inspects and lays out — it simply stops
being a Python execution route.

Even with the gate open, a fixed set of builtins is always refused:
`__import__`, `open`, `exec`, `eval`, `compile`, `globals`, `locals`.

These are matched as **names**, by parsing the submitted code -- not as
substrings of the source. That distinction is the difference between a usable
tool and a broken one: `par.eval()` is the commonest call in TouchDesigner
Python, and a substring check refused every script that read a parameter. It
is also the difference between a guard-rail and security theatre, since
`getattr(__builtins__, 'ev' + 'al')` defeats a substring check trivially.
Treat this as what it is: a stop against an obvious mistake. It is not a
sandbox, and this page has never claimed one.

---

## Risk classes

Every tool carries an explicit, reviewed class — enforced by tests, because a
capability reaching the model without anyone deciding what it may do is how an
unreviewed tool ships.

```bash
TD_MCP_MAX_RISK=WRITE_ADDITIVE   # refuse DESTRUCTIVE
TD_BUILDER_LIVE_READONLY=1       # everything becomes READ_ONLY
```

The four `DESTRUCTIVE` tools are `delete_node`, `execute_python`,
`set_dat_text` and `bind_parameter_expression`. The last two are there because
they hand TouchDesigner caller-supplied Python by another name; classifying
either below `execute_python` would let a max-risk policy admit code it meant to
block.

---

## Protected paths

```bash
TD_MCP_PROTECTED_PATHS=/project1/show,/project1/output
```

Listed paths and their descendants are refused for delete and for code-bearing
routes. Set this to your show-critical containers before handing an agent the
keys during a run.

---

## Bounded operations

Caps enforced server-side, so a caller cannot balloon your context or freeze the
bridge:

| Variable | Default | Bounds |
|---|---|---|
| `TD_MCP_MAX_BATCH` | 64 | ops per `batch` |
| `TD_MCP_MAX_PARAMS` | 256 | params per set |
| `TD_MCP_MAX_DEPTH` | 4 | recursion depth |
| `TD_MCP_MAX_SAMPLES` | 4096 | CHOP samples / POP points |
| `TD_MCP_MAX_ROWS` | 10000 | DAT rows |
| `TD_MCP_MAX_DAT_BYTES` | 1 MiB | DAT write |

---

## Undo and concurrency

**Every tool call runs inside a named undo block** (`ui.undo.startBlock`/`endBlock`, on the main thread). One Ctrl+Z reverts a whole
agent batch. This is the most practical safety property here — an agent that
does something unwanted is one keystroke from undone.

**`snapshot_network`** writes a real `.tox` that survives the undo stack, for
anything you cannot afford to lose.

**`set_dat_text` takes `expected_sha256`.** Pass the hash of the current
contents and a concurrent edit is refused rather than clobbered.

---

## The threading hazard

Requests arrive on the bridge's HTTP thread, and TouchDesigner objects cannot
be touched from any thread but the main one — TD refuses, and warns the
application may terminate. So the HTTP thread never runs a handler. It queues
a job; the component's Execute DAT drains the queue on Frame Start, on the
main thread, and the handler runs there. See
[TOUCHDESIGNER_COMPONENT.md](TOUCHDESIGNER_COMPONENT.md#why-td-mcp-does-not-use-a-web-server-dat).

One hazard survives that design and is worth knowing about: TouchDesigner's
global `run()` called off the main thread **does not raise** — it corrupts
TouchDesigner silently. Submitted Python (`execute_python`, DAT text) now
runs on the main thread, but code *it* starts on a thread of its own could
still call `run()`. The bridge statically lints submitted Python for that and
returns a `THREADING WARNING` on the write that introduced it. Advisory: the
write still lands, because a false positive that blocks a legitimate write is
worse than a warning. `subprocess.run` and every other `x.run()` are
deliberately not matched.

---

## Errors

Tracebacks are **redacted** before leaving the process: user paths are stripped
(`C:/Users/alice/...` → `~/...`) so an error message does not leak a home
directory into a transcript. Snapshot and restore paths are sandboxed to a
snapshots directory with symlink-aware containment checks.

---

## Supply chain

**No network at runtime.** The default install makes no outbound requests: the
corpus ships with the repo, and the 9,602-chunk KB is built from the offline
mirror TouchDesigner already installed on your disk.

**Optional extras are opt-in.** `sentence-transformers` (and torch) come only
with `[rag]`, and dense scoring additionally requires `TD_MCP_DENSE=1`.

**The install receipt** records a SHA-256 per staged artifact.
`td-mcp-install status` re-hashes them, so a modified staged file reports
`drifted` rather than passing as installed, and `verify` refuses.

---

## Deploying to a show

1. `TD_MCP_ALLOW_EXEC=0` unless you are actively authoring.
2. `TD_MCP_PROTECTED_PATHS` covering every show-critical container.
3. Pin **Auth Token** so it survives restarts, and keep it out of git.
4. `TD_MCP_MAX_RISK=WRITE_ADDITIVE`, or `TD_BUILDER_LIVE_READONLY=1` during the
   run itself.
5. Register **only `td-mcp-offline`** on machines that do not need live control.
6. Turn the component's **Active** toggle off when you are not using it. A
   bridge that is not listening cannot be reached.

---

## Reporting a vulnerability

Open a GitHub issue for anything non-sensitive. For something exploitable,
contact the maintainer privately first — describe the class of problem rather
than posting a working exploit.
