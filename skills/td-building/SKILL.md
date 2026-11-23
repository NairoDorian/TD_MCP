---
name: td-building
description: Cross-cutting TouchDesigner knowledge for any build through TD_MCP - pull-based cooking, Python essentials, the main-thread rule, and field-verified API traps (TD 2025.33230). Load alongside td-workflow.
---

# TouchDesigner building — AI assistant skill

You are helping someone build a TouchDesigner (TD) network. TD is node-graph
creative-coding software by Derivative. Operator families: TOP (images), CHOP
(channels/animation), SOP (CPU geometry), POP (GPU points/particles), DAT
(data/text), MAT (materials), COMP (containers/3D). Start from `td-workflow`.

## Philosophy

- TD is **pull-based**: nodes cook only when their output is requested. Never
  force-cook unless you need a snapshot.
- Prefer CHOPs + expressions over Python for time-varying values.
- Always look up parameter names before setting them: `describe_op_type` (live)
  or `td_describe_op_type` / `td_docs_parameter` (offline) return the exact names
  probed from a running TouchDesigner. Never guess a parameter name.
- TD_MCP already wraps each tool call in one undo step. In your own Python, the API
  is `ui.undo.startBlock('name')` ... `ui.undo.endBlock()` -- `ui.undo` is NOT a
  context manager.

## Operator families

- **TOP** — texture/image operators (Blur, Noise, Level, Feedback, GLSL).
- **CHOP** — channel/animation operators (LFO, Audio Device In, Math, Filter).
- **SOP** — CPU geometry (Box, Sphere, Transform, Noise).
- **POP** — GPU particles, build 2023.10000+.
- **DAT** — data/text (Table, Text, Script, Execute, Web Client).
- **COMP** — networks/UI (Container, Geometry, Camera, Light, Render).

## Critical conventions

- Parameter identifiers are lowercase, no spaces: `par.file`, not `filename`.
- `op('x').par.file = 'path'` — the classic Movie File In TOP example.
- A CHOP→TOP takes its source via the `chop` parameter, not an input wire.
- Geometry COMP's create type is `geometryCOMP`, not `geoCOMP`.
- Feedback TOP needs an input wired to initialize its buffer.
- GLSL TOP uniforms go on the **Vectors** page (runtime), not Constants page
  (compile-time only, fails silently).

## Python essentials

```python
op('noise1')                 # relative path
op('/project1/noise1')       # absolute path
op('x').par.roughness.eval() # current value
op('x').par.roughness = 0.5  # direct set
op('x').par.roughness.expr = "me.time.seconds"
with ui.undo: op('x').par.value0 = 1  # undo-safe mutation
op('x').store('key', value)  # persistent per-node state
op('x').fetch('key')
```

## Threading — never call `run()` off the main thread

TouchDesigner's global `run()` is main-thread-only. Calling it from a worker
thread does **not** raise (Derivative-confirmed 2026-08-17): it corrupts TD
state silently and crashes minutes later, far from the call site, so the
traceback you eventually get points at innocent code.

```python
# WRONG — the exception you want never arrives; the crash does.
def worker():
    run("op('moviefilein1').par.play = 1")     # corrupts TD from a thread
threading.Thread(target=worker).start()

# RIGHT — hand the work to the main thread and let TD drain it.
_pending = []
def worker():
    _pending.append("op('moviefilein1').par.play = 1")   # thread-safe append
# ...then in a CHOP/DAT Execute callback, which runs on the main thread:
def onFrameStart(frame):
    while _pending:
        run(_pending.pop(0))
```

`execute_python` and `set_dat_text` statically lint what you submit and return
a `THREADING WARNING` when a thread target reaches `run()`. The write still
lands — treat the warning as a defect to fix, not as noise.

## Common mistakes to avoid

- Calling `run()` from a thread target (see above) — it fails silently, later.
- Calling `par.val` on an expression-driven parameter — use `.eval()`.
- Storing operator references long-term (they go stale on delete/rename).
- Using `exec_python` when a structured tool (`set_parameters`,
  `bind_parameter_expression`) exists.
- Writing expressions via `exec_python` — use `bind_parameter_expression`
  which reports eval errors.
- Forgetting `ui.undo` around multi-step mutations.

## Field-verified API traps (TD 2025.33230)

- `Par.default` **never sets the value** — after `appendFloat` +
  `p.default = 5.0` the par still evaluates 0.0, and a later `min`/`clampMin`
  clamps it to the min, not the default. Set `p.val = p.default`, only on the
  create branch of get-or-create.
- The **Extension Object parameter is a constant-mode Str par** whose VALUE is
  Python code. Never set its `.expr`: the extension comes back `None` with
  `extensionsReady` True and `errors()` empty; the only trace is a
  `SyntaxError ... Context:(Extension 1)` on `scriptErrors()` (kind `script`).
- `findChildren(depth=N)` matches **exactly** depth N — `depth=8` returns
  nothing for an op at depth 1, a silent all-clear. Walk a subtree with no
  depth argument or `maxDepth=N`.
- A **Timer CHOP** ships with `cycle` off and `cyclelimit` on at `maxcycles`
  4 — a periodic timer stops after four ticks unless you turn `cycle` on and
  clear the limit.
- A **CHOP Execute DAT is a per-frame demand source**: its watched CHOP cooks
  every frame whether or not anything else pulls it. Conversely, an Execute
  DAT's `framestart` toggle is OFF by default — a frame driver that was never
  enabled demands nothing.
- `allowCooking = False` is **COMP-only** (it raises `tdError` on a
  TOP/CHOP/SOP/MAT; `= True` is accepted anywhere, so a mixed-type gate passes
  its enable pass and aborts on disable). Gate non-COMPs with `bypass`, or
  remove the demand.
- A custom **CPlusPlus operator** (compiled `.dll`) is the route when a Python
  operator cannot hold real-time rates — see
  [docs/CPP_CUSTOM_OPERATORS.md](../../docs/CPP_CUSTOM_OPERATORS.md) for the
  full SDK pattern, threading model and PluginBuilder build workflow.

## Docs & references

- Docs: https://docs.derivative.ca
- Python: https://docs.derivative.ca/TouchDesigner_Python_Classes
- Use `td_docs_search`, `td_describe_op_type`, `td_docs_parameter`,
  `td_docs_version_compat`, `td_docs_experimental` for grounded answers.
- Family and workflow skills: `td-workflow` (start here), `td-top-family`,
  `td-chop-family`, `td-pop-family`, `td-sop-render`, `td-dat-python`, `td-glsl`,
  `td-performance`, `td-network-layout`.
