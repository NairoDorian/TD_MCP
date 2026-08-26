# The four error surfaces

> **TouchDesigner reports errors in four places, and three of them are invisible
> to the obvious API call.** If your agent only reads `op.errors()`, it will
> call a visibly broken network clean.

This is the single most important thing to understand about automating
TouchDesigner, and it is why `td-mcp` v1.12.0 exists.

---

## The surfaces

| # | Surface | API | What lands here |
|---|---|---|---|
| 1 | Cook errors | `op.errors(recurse=False) → str` | "Cannot cook", "No input", missing file |
| 2 | Warnings | `op.warnings(recurse=False) → str` | Resolution mismatch, deprecation |
| 3 | **Script errors** | `op.scriptErrors(recurse=False) → str` | DAT `onCook` tracebacks, Script SOP/CHOP/TOP, Replicator callbacks, extension `__init__` |
| 4 | **GLSL compile errors** | *none* — read the docked **Info DAT** | Shader compile failures |

Surfaces 3 and 4 are where the most common real failures live. A DAT whose
`onCook` raises is the single most frequent thing that goes wrong in a
TouchDesigner project, and it does not appear in `errors()`. A GLSL TOP that
failed to compile appears in **none** of the three accessors — TouchDesigner
emits a vague warning and writes the actual compiler output into the Info DAT it
auto-docks to the operator.

---

## They are methods, and they return strings

```python
errors(recurse=False)                        -> str
warnings(recurse=False)                      -> str
scriptErrors(recurse=False)                  -> str
clearScriptErrors(recurse=False, error='*')  -> None
```

Not attributes. Not lists. This matters more than it looks:

```python
# WRONG — and it fails in a way that hides itself
errors = list(getattr(node, "errors", []) or [])
```

A bound method is **truthy**, so `or []` never fires, and `list(<method>)`
raises `TypeError: 'method' object is not iterable`. Wrap that in
`except: pass` — as read-only scanners naturally do — and the operator reports
**zero errors, forever**.

`td-mcp` shipped exactly this at six call sites for its entire life before
v1.12.0. `get_errors` always failed; `scan_network`, `get_node` and
`validate_network` always reported clean networks; `bind_parameter_expression`'s
"eval-error reporting" was structurally unable to return anything. And because
`build_and_verify → td_validate_live → score_build → td_self_heal` is fed by
that signal, **the self-healing pipeline was healing against an empty list**.

The correct shape:

```python
def _diag_call(node, method_name, recurse=False):
    accessor = getattr(node, method_name, None)
    if not callable(accessor):
        return []
    try:
        return group_diagnostics(accessor(recurse))   # call it, then split
    except Exception:
        return []
```

---

## One traceback is one error

The accessors return a *single newline-joined string*. Splitting naively on
newlines turns one Python traceback into a dozen "errors", so a single failing
`onCook` comes back looking like a catastrophe:

```
Traceback (most recent call last):     ← 1
  File "<DAT>", line 3                 ← 2
    x = y                              ← 3
NameError: name 'y' is not defined     ← 4
```

`group_diagnostics()` keeps a traceback whole: it opens at
`Traceback (most recent call last):` and closes on the next non-indented line,
which is the exception line that terminates it. Two tracebacks are two errors;
four plain messages are four errors.

---

## GLSL: read the docked Info DAT, by type

TouchDesigner docks an Info DAT to operators that have extra diagnostics to
report. For a GLSL TOP, that DAT holds the compiler output.

```python
def _info_dat_for(node):
    for docked in (getattr(node, "docked", None) or []):
        if docked.type == "info":
            return docked
    return None
```

**Find it by `dat.type == 'info'`, never by name.** TouchDesigner keeps the
*old* dock name after the host operator is renamed, so name-based discovery
silently stops working exactly when someone tidies up a project — the worst
possible time for error reporting to quietly break.

`shader_error_lines()` then filters the DAT's text: an Info DAT carries plenty
of routine telemetry (`Resolution: 1280 x 720`, `Cook Time: 0.4`, `Errors: 0`)
and only lines that actually read as a compile failure are diagnostics. A
healthy shader yields `[]`.

### Reading an Info DAT *cooks* it

This is not free. Embody measured a project-wide shader pass at **3.36 s**
against a 0.25 s budget, which tripped a one-way disable latch and silently
killed their whole error footer for the session.

So `td-mcp`:

- scopes the shader pass to the operator a write actually touched
  (`build_and_verify`),
- lets any caller skip it (`get_errors(..., include_shader=False)`),
- walks `dock` **up** to the host, so editing `<name>_pixel` still reports the
  host's compile error.

An explicit `get_errors` call may walk, because that is a deliberate request
rather than a per-write tax.

---

## Script errors persist until cleared

`scriptErrors` is sticky. One cook inside an extension re-initialisation window
— where the extension object is briefly the previous one — badges a node long
after the code is correct again.

For an agent this is a trap: it fixes the code, re-reads, still sees the
traceback, and "fixes" working code. Hence:

```
clear_script_errors(path, recurse=False)
```

Call it after you believe you have fixed something, *then* re-verify. If the
error comes back, it is real.

---

## What `td-mcp` returns

```jsonc
{
  "ok": true,
  "path": "/project1/glsl1",

  // Flat list for back-compat: cook + script + shader, i.e. what is red.
  "errors": ["Cannot cook", "RuntimeError: nope", "0(2) : error C7011"],

  // Broken out by surface.
  "cook_errors":   ["Cannot cook"],
  "script_errors": ["RuntimeError: nope"],
  "shader_errors": ["0(2) : error C7011: implicit cast"],
  "warnings":      ["deprecated"],

  // Tagged, for a caller that wants to reason about kind.
  "diagnostics": [
    { "message": "Cannot cook",   "severity": "error",   "kind": "cook" },
    { "message": "RuntimeError…", "severity": "error",   "kind": "script" },
    { "message": "0(2) : …",      "severity": "error",   "kind": "shader" },
    { "message": "deprecated",    "severity": "warning", "kind": "warning" }
  ],

  "error_count": 3, "warning_count": 1,
  "errorCount": 3, "hasErrors": true
}
```

`errorCount` and `hasErrors` mean **"what the network shows red"**. A warning is
not a failure and never counts toward them.

---

## How diagnostics reach the score

Live diagnostics are not derivable from a network *description* — a DAT whose
`onCook` raises is a perfectly well-formed description. So they are passed
explicitly into scoring:

```
build_and_verify
   ├─ get_errors           →┐
   ├─ capture_viewport     →┤
   └─ td_validate_live ────┴→ score_build → td_self_heal
```

`score_build` weighs them:

| Finding | Penalty | Why |
|---|---|---|
| Cook error | −12 each | Visible in the obvious place |
| **Script error** | −15 each | TD reports it on no accessor most people read |
| **Shader error** | −15 each | TD reports it on *none* of them |
| Live warning | −4 each | Not a failure |
| Black viewport | −20 | Renders nothing |
| Flat viewport | −10 | One solid colour |
| **Static viewport** | −15 | Renders correctly and never changes |

Script and shader errors are weighted *above* cook errors deliberately: both
routinely go unnoticed, so a scorer that treats them as equal understates how
broken the network is.

Both edges of this loop were cut before v1.12.0 — `build_and_verify` captured
`get_errors` and never passed it on, and handed the whole `capture_viewport`
envelope where its inner `verdict` was expected, so `is_black` read as absent
and no viewport penalty could fire either.

---

## The fifth thing that can be wrong: nothing moves

Not an error surface, but the same class of blind spot. A feedback or
audio-reactive network that renders one correct frame and then freezes has:

- no cook errors,
- no script errors,
- no shader errors,
- a viewport that is neither black nor flat.

Every check passes and the piece is dead. `observe` catches it by comparing 8×8
luminance grids across frames. A scalar mean will not do — a pattern that
scrolls across the canvas holds its mean *exactly* constant while every pixel
changes.

`is_static` is `None` when fewer than two frames were captured. Unknown, not
false: claiming a network animates on the strength of one frame is the error
being fixed.

---

## Checklist for an agent

1. `build_and_verify` rather than `create_node` — it reads all four surfaces.
2. Non-zero `errorCount`? Read `diagnostics[].kind` before deciding what to fix.
   A `shader` error means the GLSL, not the wiring.
3. Fixed a script error? `clear_script_errors`, then re-verify.
4. Clean but suspicious? `observe` — it may be frozen.
5. Never trust a scanner that reports zero errors without having read
   `script_errors`.

---

## See also

- [TOOLS.md](TOOLS.md#diagnostics) — the tool signatures
- [TESTING.md](TESTING.md) — the TD API double that makes this class of bug fail loudly
- [../UPSTREAM_SYNC_2026-08-26.md](../UPSTREAM_SYNC_2026-08-26.md#31-the-live-bridge-cannot-read-touchdesigner-errors--critical) — how it was found
- Embody v6.0.264 / v6.0.265 — the upstream changelogs that pointed at it
