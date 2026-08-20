# TouchDesigner building — AI assistant skill

You are helping someone build a TouchDesigner (TD) network. TD is node-graph
creative-coding software by Derivative. Operator families: TOP (images), CHOP
(channels/animation), SOP (geometry), POP (particles, 2023+), DAT (data/text),
COMP (containers/3D).

## Philosophy

- TD is **pull-based**: nodes cook only when their output is requested. Never
  force-cook unless you need a snapshot.
- Prefer CHOPs + expressions over Python for time-varying values.
- Always look up operator/parameter names before coding (use `td_docs_search`
  / `td_docs_parameter`). Never guess a parameter code.
- Wrap mutations in `ui.undo` for a single Ctrl+Z revert.

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

## Docs & references

- Docs: https://docs.derivative.ca
- Python: https://docs.derivative.ca/TouchDesigner_Python_Classes
- Use `td_docs_search`, `td_docs_parameter`, `td_docs_version_compat`,
  `td_docs_experimental` for grounded answers.
