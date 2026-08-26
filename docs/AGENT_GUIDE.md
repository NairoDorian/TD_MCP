# Using td-mcp as an agent

Written for the model driving these tools, and for whoever is writing its system
prompt. The advice is specific to TouchDesigner and mostly counter-intuitive.

---

## The five rules

1. **Never guess a parameter name.** `td_docs_parameter` costs one call. A wrong
   name costs a failed write, a confusing error, and a retry.
2. **Build with `build_and_verify`, not `create_node`.** One returns a path and
   tells you nothing; the other reads all four error surfaces, captures the
   viewport, and scores the result.
3. **Author offline before touching a live session.** `td_build_network` →
   `td_validate_build` → `td_self_heal` runs with TouchDesigner closed. Fix the
   description first; materialise it once.
4. **"No errors" is not "working".** Check `is_static`. A frozen render passes
   every error check.
5. **Cite what you assert.** Every doc hit carries `Page § Section` + a URL. If
   you cannot cite it, say you are guessing.

---

## Which server to call

| You want | Server | Needs TD |
|---|---|---|
| What parameters does Noise TOP have? | offline | no |
| Design an audio-reactive network | offline | no |
| Is this network valid? What score? | offline | no |
| Create it in my project | **live** | yes |
| Why is this operator red? | **live** | yes |
| What is this CHOP actually outputting? | **live** | yes |

Prefer offline. It is faster, safer, has no session to break, and its answers
are reproducible.

---

## The build loop

```
td_docs_search / td_docs_operator      ← what exists, what it is called
        ↓
td_build_network                       ← a TDN description; types validated
        ↓
td_validate_build → td_score_build     ← is it well-formed? how good?
        ↓
td_self_heal                           ← repair, re-assess (offline, free)
        ↓
build_and_verify        ← LIVE: create → set → all four error surfaces
        ↓                       → viewport verdict → offline validate/score/heal
observe                                ← is it actually moving?
```

Everything above `build_and_verify` is free and reversible. Spend your effort
there.

### Reading the result

`build_and_verify` returns:

```jsonc
{
  "created":  { "ok": true, "path": "/project1/glsl1" },
  "errors":   { "errorCount": 1, "hasErrors": true,
                "shader_errors": ["0(12) : error C1008: undefined variable \"half\""],
                "diagnostics": [{ "kind": "shader", ... }] },
  "preview":  { "verdict": { "is_black": true, ... } },
  "offline_validation": "{ \"score\": { \"score\": 43, \"grade\": \"D\", ... } }"
}
```

Read `diagnostics[].kind` **before** deciding what to fix:

| `kind` | Fix |
|---|---|
| `cook` | Wiring or a parameter — check inputs first |
| `script` | Python in a DAT callback. Fix, `clear_script_errors`, re-verify |
| `shader` | The GLSL source, not the network |
| `warning` | Usually ignorable; never a failure |

---

## Verifying, properly

Four things can be wrong, and they need four different checks:

| Wrong | Detected by |
|---|---|
| Won't cook | `errors` |
| Callback raises | `script_errors` |
| Shader won't compile | `shader_errors` |
| Renders nothing | `capture_viewport` → `is_black` / `is_flat` |
| **Renders correctly, frozen** | `observe` → `is_static` |

The last one has no error of any kind. Every check passes and the piece is dead.
On anything animated — feedback, audio-reactive, noise over time — call
`observe` before you say it works.

`is_static: null` means too few frames to judge. Unknown, not fine.

---

## Reading data without drowning

Readers reduce by default. Do not reach for the raw form out of habit.

```
read_chop("/project1/audio1")
→ { "chan1": { "count": 600, "min": -0.82, "max": 0.91, "mean": 0.01, "std": 0.31 } }
```

Four numbers answer "what did this chain do to my data". Six hundred samples do
not, and cost a thousand times more context.

- **`diff_chop`** after inserting a filter — it reports what changed, which is
  the actual question.
- **`read_dat`** gives per-column stats plus edge rows; `format="rows"` when you
  genuinely need the table.
- **`read_pop`** gives attribute metadata free and *refuses* value readback above
  a point ceiling. That is not a limitation to work around: `POP.points()`
  accepts `start`/`count` and ignores them, so there is no cheap partial read.
- **`get_non_default_params`** instead of `get_parameters` on a large network.

---

## Working safely

**Every mutation is already wrapped in `ui.undo`.** One Ctrl+Z reverts a whole
batch. Use `batch` for related changes so they undo together.

**`snapshot_network` before anything risky.** It writes a real `.tox` and
survives the undo stack. Prefer it over a TDN checkpoint when code matters — a
`.tox` preserves DAT content unconditionally.

**Respect a refusal.** `execute_python is disabled` or `code route denied` means
the operator turned off Python execution deliberately. Do not route around it
with a parameter expression or a DAT write — those are the same gate, and they
will refuse too. Say what you would need and why.

**`delete_node` is destructive** and refuses protected paths. Confirm before
deleting anything you did not create in this session.

---

## Session memory

`td_memory_save` after a substantive result; `td_memory_recall` at the start of
related work. It is SQLite FTS5 and survives restarts, which is the point — the
operator's project conventions, the port they use, the fact that their LED wall
is 96×32, are all things worth not asking twice.

`td_history_recall` searches session memory *and* the technique store together.

---

## Trust and citation

Answers carry a source. Use it.

- **Cite the page.** "Per *Noise TOP § Parameters*, `period` defaults to 1."
- **Heed family disambiguation.** "Noise" is four operators — TOP, CHOP, SOP,
  POP — with different parameters. A hit will warn you; do not flatten it.
- **Heed version gating.** Operators appear and disappear across builds.
  `td_docs_version_compat` gives per-operator `addedIn`/`removedIn`. POPs are TD
  2025+.
- **Say when you are guessing.** If the KB has no answer, that is a real answer.
  A fabricated parameter name costs the user a debugging session.

---

## Anti-patterns

| Don't | Do |
|---|---|
| Guess a parameter name from another host app | `td_docs_parameter` |
| `create_node` then assume it worked | `build_and_verify` |
| Trust a scanner reporting zero errors | Confirm it read `script_errors` |
| Dump 4,096 CHOP samples to "have a look" | `read_chop` stats, then `diff_chop` |
| Call it done on a still frame | `observe` |
| Retry a refused code route via another route | They are the same gate |
| Re-derive the network every turn | `td_memory_recall`, `export_recipe` |
| Fix a script error and re-read | `clear_script_errors`, *then* re-read |

---

## A worked example

> *"Make me an audio-reactive feedback thing."*

```
1  td_memory_recall("audio reactive feedback")     ← have we done this before?
2  td_docs_workflow("audio reactive feedback")     ← the operator chain
3  td_build_audio_reactive(...)                    ← a TDN description
4  td_validate_build(spec)                         ← 2 warnings
5  td_self_heal(spec)                              ← repaired, score 94 (A)
   ── nothing has touched TouchDesigner yet ──
6  build_and_verify("/project1", "audiodeviceinCHOP")   ← materialise
7  … batch(create + connect for the rest)
8  observe("/project1/out1", frames=5)             ← is_static: false ✓
9  td_memory_save("audio-reactive feedback rig", recipe)
```

Steps 1–5 are free, reversible and fast. By step 6 the description is already
known-good, so a live failure is a real one — a missing audio device, a build
difference — not a typo you could have caught offline.

---

## See also

- [TOOLS.md](TOOLS.md) — every tool, with risk classes
- [DIAGNOSTICS.md](DIAGNOSTICS.md) — why "no errors" is not "working"
- [SECURITY.md](SECURITY.md) — what the exec gate covers
- `skills/td-building/SKILL.md` — the Claude Code skill
