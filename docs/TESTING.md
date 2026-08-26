# Testing

**803 tests, 803 passing.** `uv run pytest` on a fresh clone, after
`uv run python -m td_mcp.kb.build_kb`.

```bash
uv pip install -e ".[test]"
uv run python -m td_mcp.kb.build_kb    # 8 tests need the KB
uv run pytest -q
uv run python scripts/check_bridge_stale.py
```

---

## The TD API double

`tests/td_double.py` is the most important file in this directory, and it exists
because of a specific failure.

### Why

`tests/bridge_pure.py` slices the block between `# BEGIN PURE HELPERS` and
`# END PURE HELPERS` out of the bridge and execs it — about 90 lines of a
2,191-line file. Every `_do_*` handler, which is to say **everything that
touches TouchDesigner**, was never executed by any test.

That is the blind spot in which the bridge shipped, for its entire life:

- `node.errors` read as an iterable *attribute* at six sites, when
  TouchDesigner documents `errors(recurse=False) → str` — a method. `get_errors`
  raised; four scanners swallowed the exception and reported clean networks.
- `COMP.create(opType, name)` called with the arguments **reversed** at both
  call sites, so every create asked for an operator whose *type* was the
  caller's chosen name.
- `export_recipe` writing source *paths* into `inputs` while `import_recipe`
  looked them up by *name*, so no recipe ever reconnected a wire.

None of these are subtle. All of them were invisible.

### What it does

`build_bridge()` execs the **entire shipped bridge** against a deterministic
model of the TouchDesigner API:

```python
from tests.td_double import build_bridge

br, td = build_bridge()
td.make("noise1", "noiseTOP", errors="Cannot cook: no input")

out = br["_do_get_errors"]("/project1/noise1")
assert out["errorCount"] == 1
```

`br` is the bridge module's globals — every handler and `_DISPATCH` are directly
callable. `td` is the environment: build operators, then inspect what the
handler did to them.

### The rule: model the API as TouchDesigner documents it

This is the whole value of the double. A permissive fake would have accepted
every bug above.

| Modelled | Because |
|---|---|
| `errors()` / `warnings()` / `scriptErrors()` are **methods returning `str`** | Reading them as attributes yields a truthy bound method, so `or []` never fires and `list()` raises |
| `create(opType, name)` **refuses an unknown opType** | A fake that accepts any two strings cannot tell a correct call from a reversed one |
| `create` **auto-names** from the type when `name` is None | TD's own behaviour; a handler inventing names would pass otherwise |
| `findChildren(depth=N)` matches an **exact** relative depth | `depth=-1` matches nothing — it made an entire repair path in Embody a silent no-op |
| `POP.points(attr, start, count)` **ignores** its slicing arguments | TD returns the whole attribute regardless; a handler must not pretend it sliced |
| `par.mode` is a **real enum** | The bridge does `getattr(type(par.mode), "EXPRESSION")`, which only resolves on an enum instance |
| `Connector.connect(target)` accepts an **OP or a connector** | Both spellings appear in TD's own docs and in this codebase |
| `OP.dock` / `OP.docked` | GLSL compile errors live in a docked Info DAT, found by type |
| Appenders return a **ParGroup**, not a parameter | Callers index `[0]`; returning a bare par would let a forgetful caller pass here and fail in TD |
| `import td_mcp` **fails** | It is never on TD's `sys.path`; a bridge that depended on it compiled under pytest and raised NameError in every real session |
| `DAT.module` compiles the text lazily, with `__file__` = the **operator path** | The bridge reads its environment at import; `__file__` is not a file, which sent the builder looking under `C:\project1` |
| Assigning an **unknown parameter raises** | TD says `'td.ParCollection' object has no attribute 'pulse'`; a double that created the attribute let `onpulse` ship as `pulse` |
| **Sync to File** loads the file immediately | Text_DAT.htm says so; link mode depends on it |
| `ui.undo` has `startBlock`/`endBlock` and **no `with` protocol** | The bridge wrote `with ui.undo:` at every dispatch site; a double written from the bridge let every tool call fail in the first real session |
| `project.cookRate`, and the timeline at `root.time` | There is no `project.cook`; the bridge and the double both had one |
| **A frame loop** (`TDEnv.frame_loop()`) drives `onFrameStart` from its own thread | TD objects are main-thread only; the real handler is now exercised over a real socket *from the wrong thread*, the way TouchDesigner will call it |
| `par['name']` is the Par **or None**; a string is never `in node.par` | ParCollection_Class; `'opacity' in node.par` refused every real `set_parameters` |
| `chop.chans()` / `chop.chan()` are **methods** | CHOP_Class; the bound method is what `read_chop` tried to iterate |
| `Par.isDefault` covers value, expression **and** mode | A container's default `resizecomp = me` was exported into every recipe |
| `TOP.numpyArray()` returns **float32 in 0..1** | A healthy Noise TOP (mean 0.46) was judged black by 0..255 thresholds |
| `root` is the root COMP -- **an OP, not a function** | Nine `_op(path) or root()` fallbacks raised "'td.baseCOMP' object is not callable" |
| `clearScriptErrors(recurse=...)` is **keyword-only** | A positional call is refused; every OP diagnostic is now called by keyword |
| `Connector.connections` holds **connectors**; the operator is `.owner` | There is no `outputOP`/`inputOP`; six sites read them and every wiring read came back empty |
| `OP.type` is the short form; **`OP.OPType`** is what `create()` takes | A recipe recording `type` failed to re-import with "Unknown operator type: 'constant'" |
| POP attributes are `pointAttributes`/`primAttributes`/`vertAttributes` **properties** | There is no `attributes()`; `read_pop` reported none for a 252-point sphere |
| A table DAT **re-normalises** its text on assignment | `set_dat_text` hashed what it sent, so every CAS write was refused |
| `ParCollection.__contains__` compares **Par objects**, not names | `'opacity' in node.par` is False even when it exists |
| `par.eval()` is a **method on a Par**, not the `eval` builtin | The exec guard matched substrings and refused every script that read a parameter |

The double has already caught two gaps **in itself** — string `par.mode` and a
too-narrow `connect` — both surfaced by a handler that was correct.

### Extending it

When a handler needs a corner of the TD API the double lacks, **model that
corner**. Do not loosen the test.

1. Check the real signature in `TD HELP/*.htm` or the online docs. Return type
   and method-vs-attribute both matter.
2. Add it to `td_double.py`, faithfully — including the awkward parts.
3. If TouchDesigner does something surprising, put the surprise in a comment.
   That comment is why the next person does not reintroduce the bug.

Operator-type default parameters live in `_DEFAULT_PARS_BY_TYPE`. Only what the
code under test touches is modelled; add a type when a handler needs it.

### What it is not

Not a simulator. It does not cook, render, evaluate expressions, or enforce
TouchDesigner's threading rules. It models the shapes handlers read and the
mutations they perform.

**It cannot prove a handler works in TouchDesigner.** It proves the handler
matches the documented contract, which is a different and much cheaper claim —
and it is the claim that would have caught every bug listed above. The last
mile was walked on 2026-08-26: 37 of 37 smoke calls and a real MCP client
against TouchDesigner 2025.33070, after four passes that each found API
details the double had modelled from the bridge instead of from the docs.
Every one of those is now a row in the table above.

That said, it gets closer than it used to. `TestLiveTransport` and
`TestStart` compile the real bridge as a DAT module with `td_mcp` blocked,
bind a real socket on an ephemeral port, and drive the real handler over
HTTP — the legacy `/mcp` tool call, a 401, a JSON-RPC `initialize`, a
`tools/call` that reaches the double. Three field-reported bugs in a row
(`__file__`, `onpulse`, the `td_mcp` import) were each about what TouchDesigner
*supplies* to a running DAT rather than what its API documents; the double now
models those too.

---

## The suites

| File | What it covers |
|---|---|
| `test_bridge_handlers.py` | All four diagnostic surfaces, traceback grouping, Info DAT discovery, the scanners |
| `test_bridge_readers.py` | Reader reduction contract: CHOP stats, DAT stats, POP ceilings |
| `test_tdn_fidelity.py` | TDN: DAT text, expressions, rewire diffs, exclusions, output paths |
| `test_bootstrap.py` | The component builder — and that the bridge fork stays dead |
| `test_motion.py` | Frame signatures, `is_static`, scoring penalties |
| `test_install_lifecycle.py` | Install SOP: plan/apply/status/verify/uninstall, receipts, exit codes |
| `test_parity.py` | Schema↔handler parity across three surfaces; explicit risk classes |
| `test_argument_parity.py` | **Argument-name** parity, by AST: schema properties = registry lambda keys; client payload keys ⊆ bridge handler keys |
| `test_live_offline_loop.py` | `build_and_verify → validate → score → heal`, end to end |
| `test_rag*.py`, `test_wiki_*.py`, `test_corpus.py` | Retrieval, fusion, ingestion, eval |
| `test_stdio_utf8.py`, `test_protocol_negotiation.py`, `test_version_truth.py` | Transport and version invariants |
| `test_codeguard.py`, `test_provenance.py` | The exec gate; attribution |

---

## The guards that catch drift

Four regression classes are enforced structurally rather than by review:

**Schema↔handler parity** (`test_parity.py`). The live registry, the advertised
`tools/list`, the HTTP metadata surface and the bridge's `_DISPATCH` must match
exactly. A handler the model can see but not call — or the reverse — fails here.
It caught 13 tools missing from `_OFFLINE_DISPATCH` in Wave 10.

**Bridge staleness** (`scripts/check_bridge_stale.py`, run in CI). The bridge's
`_DISPATCH` must match the live registry, `BRIDGE_VERSION` must match
`pyproject.toml`, and **no other file under `bridge/` may declare bridge logic**.
That last check exists because `bootstrap.py` held a forked copy of the entire
bridge until v1.12.0 — 39 tools against 46, three reversed `create()` calls —
and nothing compared the two.

**Explicit risk classes** (`test_parity.py`). Every advertised tool, live and
offline, must carry a reviewed class. The fallback exists as a safety net, not a
policy: a capability reaching the model without anyone deciding what it may do
is how an unreviewed tool ships.

**Version truth** (`test_version_truth.py`). `pyproject.toml`,
`BRIDGE_VERSION` and the runtime `td_mcp.__version__` must agree. They drifted
once — an editable install's metadata sat at 1.10.0 while the source said
1.11.0 — and every session warned that a correct bridge was stale.

---

## Retrieval evaluation

```bash
uv run python -m td_mcp.rag.eval                    # recall@k / MRR / nDCG
uv run python -m td_mcp.rag.eval --name-integrity   # no fabricated operator names
```

**1.000 recall @k=5** with the wiki KB, **0.991** curated-only, zero
version-gating violations. The name-integrity gate is separate and stricter: it
fails if any answer contains an operator name that does not exist in the corpus.

Two things this eval got wrong in the past, both fixed and both worth knowing:
it credited one expectation more than once (a chunk and its `corpus_` twin both
counted), which reported nDCG > 1.0 and masked real misses; and the KB integrity
receipt was invalidated by an ingest path that appended post-build, so the
retriever rejected the entire KB as corrupted.

---

## Writing a test here

- **Name the failure, not the function.**
  `test_traceback_counts_as_one_error_not_one_per_line` beats
  `test_group_diagnostics_2`.
- **Say why in the docstring** when the behaviour is surprising. Most tests in
  `test_bridge_handlers.py` encode something TouchDesigner does that no one
  would guess.
- **Assert the honest value.** `is_static is None` is a real outcome and has its
  own test; collapsing it into `False` would erase the distinction the feature
  exists for.
- **A test that documents a bug should say so**, so the next person does not
  "simplify" it back into the bug.
