# Upstream sync — 2026-08-26

> **Status: implemented.** Wave 12 shipped as **v1.12.0** on 2026-08-26 —
> see [`CHANGELOG.md`](CHANGELOG.md) and `IMPROVEMENT_PLAN.md` item 28. Tests
> went 389 → 593 passing. Two findings below were corrected once the work was
> under way: section 3.6 (the SDK servers had already migrated) and item 12 of
> the work list (no per-write project-wide sweep exists). Executing the
> handlers for the first time also surfaced two bugs this report did not
> predict: `COMP.create` called with its arguments reversed, and `import_recipe`
> resolving wires by a key `export_recipe` never wrote.

Every repo under `github-mcp/` was fetched from its GitHub upstream and
fast-forwarded. This file records **what moved**, **what that means for
`TD_MCP`**, and the resulting work items. Companion to
[`IMPROVEMENT_PLAN.md`](IMPROVEMENT_PLAN.md), which this appends Wave 12 to.

Baseline at time of writing: `td-mcp` **v1.11.0**, `pytest` **389 passed, 6 skipped**.

---

## 1. Sync result

| | count |
|---|---|
| Repos fetched | 27 |
| Repos that had upstream commits | **2** |
| Repos already at upstream HEAD | 25 |
| Repos newly cloned (were missing) | **4** |

### 1.1 Repos that moved

| Repo | Range | Commits | Headline |
|---|---|---|---|
| `Embody` (dylanroscover) | `66e9478 -> 74faf52` (v6.0.252 -> **v6.0.280**) | 42 | 13,616 insertions. Script/GLSL error surfaces, CHOP/POP readers, TDN fidelity fixes, project PyEnv v1, updater deadlocks |
| `dcc-mcp-touchdesigner` | `0ab736c -> c991513` (0.1.1 -> **0.1.2**) | 5 | **Install SOP v1** — an agent-first, machine-readable install lifecycle |

### 1.2 Repos newly cloned

Two were already listed as "not yet cloned" in `../README.md`; two are new to the
ecosystem search.

| Folder | Repo | Why it matters |
|---|---|---|
| `touchdesigner-mcp-johnsabath` | [johnsabath/touchdesigner-mcp](https://github.com/johnsabath/touchdesigner-mcp) (12 stars) | Zero-dependency self-contained `.tox` on `:9988`, Streamable HTTP on a Web Server DAT, **13 one-word tools**, `observe` returns **PNG *or animated GIF***, `render` produces **MP4 with audio**, DOT network map, ships a Claude Code skill |
| `touchdesigner-mcp-superdwayne` | [superdwayne/Touchdesigner-mcp](https://github.com/superdwayne/Touchdesigner-mcp) (6 stars) | A **real, working `.mcpb` bundle** with a spec-shaped `manifest.json` |
| `hermes-skill-touchdesigner-mcp` | [mahsan07/hermes-skill-touchdesigner-mcp](https://github.com/mahsan07/hermes-skill-touchdesigner-mcp) | Docs-first agent skill: operating contract, `SAFETY.md`, "expected evidence" per step |
| `touchdesigner-mcp-froussio` | [froussio/touchdesigner-mcp](https://github.com/froussio/touchdesigner-mcp) | FastMCP + Banana API bridge aimed at live performance |

### 1.3 Housekeeping

18 reference repos had a **deleted (but tracked) `LICENSE`** in their working
tree. Restored via `git checkout -- LICENSE`. This matters: the library's premise
is that these repos are *studied, not copied*, and `TD_Builder_alpha` is AGPL-3.0.
Licence files must be present and readable.

---

## 2. Embody v6.0.252 -> v6.0.280 — what to take

Embody is the closest peer to `TD_MCP` (live bridge + diffable TDN + in-TD
extension), so its bug reports are the most transferable in the ecosystem. Six of
these findings land directly on `TD_MCP` code.

### 2.1 TouchDesigner reports errors on **three** surfaces, not two

> *"An operator could sit red in the network while every Envoy error tool called
> it clean."* — Embody v6.0.265
>
> *"GLSL compile errors are visible at last: `op.errors()` reports '(none)' for a
> shader that failed to compile."* — Embody v6.0.264

The three surfaces are `errors()`, `warnings()`, **`scriptErrors()`** (DAT
`onCook` tracebacks, Script SOP/CHOP/TOP, Replicator callbacks), plus GLSL
compile text which appears in **none** of them — it is written to the
auto-docked **Info DAT** (find it by `dat.type == 'info'`, *not* by name; TD
keeps the old dock name after a host rename).

**`TD_MCP` is worse off than Embody was.** See section 3.1 — its bridge does not
read any of them correctly.

### 2.2 Reads must be reductions, never dumps

`get_chop_data` reduces a CHOP to per-channel `min/max/mean/std` and can diff it
against another operator. `get_dat_content(format='stats')` returns per-column
stats plus edge rows — measured **17x smaller** than a full dump. `get_pop_data`
returns POP attribute metadata for free and gates value readback on point count,
because **`POP.points(attr, start, count)` accepts and silently ignores both
slicing arguments** — reading 4,096 points off a 160k POP measured *slower*
(80 ms) than reading all of them.

### 2.3 Cost discipline for anything that runs per-write

- **Reading an Info DAT cooks it.** A project-wide shader scan cooked ~213
  docked operators and took **3.36 s** against a 0.25 s budget, tripping a
  one-way disable latch that silently killed the whole feature for the session.
  Fix: scan only the operators the write actually touched; walk a touched dock up
  to its host so editing `<name>_pixel` still reports its host's compile error.
- **Fingerprinting every parameter in one frame** blocked the main thread
  0.5–1 s (28–60 dropped frames). Fix: frame-chunk under an **8 ms** budget;
  keep the programmatic path synchronous for callers that need completion.
- Measured budgets that passed: script-error merge **11 ms** project-wide,
  shader pass **2.7 ms** over 1,920 ops.

### 2.4 `findChildren(depth=N)` matches an **exact** relative depth

`CatalogManager._patchComp` walked `findChildren(depth=-1)` and therefore matched
nothing — an entire cross-build repair path was a silent no-op with zero test
coverage. Any recursive search must avoid assuming `depth` means "recurse", or
walk `children` explicitly.

### 2.5 stdio must be pinned to UTF-8, from `main()`

MCP clients send UTF-8 JSON-RPC, but Python binds `sys.stdin` to the **locale
codepage** — CP1252 on Western Windows, US-ASCII on a GUI-launched macOS process.
A 5-character probe containing an em dash arrived as 7 characters, and one bad
byte could kill the read loop. Pin in `main()`, **never at import** — the in-TD
tests exec the bridge inside TouchDesigner, where an import-time pin would mutate
TD's own streams.

### 2.6 TDN exporter fidelity — four findings

From `Embody/docs/reports/tdn-exporter-fidelity-moonshine.md` (2026-08-24). Read
it in full before touching `td_mcp/tdn/`.

1. **Sequence block counts double-declare wiring.** A `mergePOP` with 7 wired
   inputs exported `sequences: input: [{}] x 7`. On import the *list length is
   the numBlocks instruction*, and it was applied **before** wiring — so the
   operator briefly had 7 declared inputs and nothing connected, latching a
   `No input POP` error that resurfaced on **every project open**. Fixes: omit
   wiring-derived sequences entirely (`inputs:` is the source of truth), probe
   real default block counts from a throwaway instance, and apply block counts
   *after* wiring.
2. **No way to mark a parameter as runtime state.** Committed `.tdn` files leaked
   an authoring machine's absolute asset path, a runtime-negotiated port
   (`Port: 17361`, chosen by probing a ladder), and live window geometry.
   Embody's answer is a `tdn_exclude:<parname>` **tag on the operator** — it
   reaches non-COMP operators, lives in the project's own network, ships in the
   exported file as the visible record, and a typo'd parameter name WARNs. A
   silently omitted parameter is indistinguishable from a default one, so the
   omission must be visible.
3. **Untouched TD boilerplate classified as authored content.** Several hundred
   lines of default `onValueChange`/`onFocus` stubs were embedded. Fix: compare
   the DAT's text against the default template for its type — byte-identical
   means nothing was authored.
4. **A relative `output_file` writes outside the project and then fails to
   track.** It resolves against TouchDesigner's cwd, produced a 903 KB
   whole-project dump one directory deeper than intended, and `relative_to()`
   raised into a broad `except` that logged a WARNING while the export
   "succeeded". Fix: anchor to `project.folder` when not `is_absolute()`, and
   narrow the `except` around the tracking step.

Process note from the same report: *creating and destroying a scratch COMP
through the MCP triggered a bulk TDN re-export that overwrote unrelated edits on
disk*. An apparently read-only investigation can rewrite committed files as a
side effect of touching the network at all.

### 2.7 The rest, worth knowing

- **`include_dat_content=False` threw away authored code.** The flag now means
  "skip what a file already holds", never "discard": a file-backed DAT is
  omitted, an unbacked one is **always** embedded. *Any doubt embeds.* Five
  pre-existing tests had pinned the old contract and were reframed, not deleted.
- **PyEnv v1** — Embody authors `TDPyEnvManagerContext.yaml` beside the `.toe`
  so TouchDesigner's own startup helper links `.venv` **before any COMP cooks**,
  making module-level venv imports work in extensions that construct first.
  Foreign contexts are never touched; deleting the file opts out durably; the
  file is auto-removed while the venv needs reinstall so TD never links stale
  wheels.
- **"Bundled" means `sys.base_prefix`, not any `site-packages` on `sys.path`.**
  `sys.path` only ever grows, so opening a second project in one TD session
  leaves the first project's venv on it — and Embody was crediting TouchDesigner
  with packages it had never heard of.
- **`urlopen(timeout=...)` bounds one socket read, not the transfer.** A
  connection that trickles bytes never times out and never finishes. Give the
  download a **wall-clock budget**; give every phase a ceiling a later attempt
  uses to clear a dead latch; make a leftover crash sentinel resolvable in place
  rather than advice that cannot work.
- **A release-process rule worth copying:** every user-visible change in a
  release diff must name the `docs/` page that now describes it, or be explicitly
  declared internal.

---

## 3. What this means for `TD_MCP` — verified findings

Each item below was checked against this repo's source, not inferred.

### 3.1 The live bridge cannot read TouchDesigner errors at all — CRITICAL

TouchDesigner's own docs (`TD HELP/OP_Class.htm`) are unambiguous:

```
errors(recurse=False)            -> str
warnings(recurse=False)          -> str
scriptErrors(recurse=False)      -> str
clearScriptErrors(recurse=False, error='*') -> None
```

They are **methods returning strings**. The bridge treats `node.errors` as an
iterable **attribute** at six sites:

| Site | Code | Effect in real TD |
|---|---|---|
| `bridge/td_mcp_bridge.py:782-783` | `list(getattr(node, "errors", []) or [])` | a bound method is truthy, so `list(method)` raises `TypeError: 'method' object is not iterable`. `get_errors` **always fails** |
| `bridge/td_mcp_bridge.py:750,763` | same, inside `try/except: before = []` | `bind_parameter_expression`'s `op_errors` is **always empty** — its entire "eval-error reporting" promise is dead |
| `bridge/td_mcp_bridge.py:846` | `try: [str(e) for e in node.errors] except: pass` | `scan_network` reports **zero errors, always** |
| `bridge/td_mcp_bridge.py:972` | same | `get_node` reports **zero errors, always** |
| `bridge/td_mcp_bridge.py:1210` | same | `validate_network` reports **zero errors, always** |

Because the HTTP dispatcher wraps handlers (`bridge/td_mcp_bridge.py:1546`,
`1828`), the failure surfaces as `{"ok": false, "error": "'method' object is not
iterable"}` rather than a crash — and the four `except: pass` sites surface as
*silence*, which is worse.

Everything downstream inherits it: `build_and_verify`
(`bridge/td_mcp_bridge.py:875`) calls `_do_get_errors`, and `td_validate_live` ->
`score_build` -> `td_self_heal` are fed by it. **The self-healing pipeline is
currently healing against an empty error list.**

Correct shape, incorporating section 2.1:

```python
def _op_diagnostics(node, recurse=False):
    def _lines(fn):
        try:
            return [l for l in (fn(recurse) or "").splitlines() if l.strip()]
        except Exception:
            return []
    return {
        "errors":       _lines(node.errors),
        "warnings":     _lines(node.warnings),
        "scriptErrors": _lines(node.scriptErrors),   # third surface
        "shaderErrors": _info_dat_errors(node),      # GLSL: dat.type == 'info'
    }
```

`errorCount`/`hasErrors` should then mean *"what the network shows red"*, and a
traceback should ride back on the very write that caused it. Pair with
`clearScriptErrors` as a documented recovery action — script errors persist until
explicitly cleared, so one cook inside an extension re-init window badges a node
long after the code is correct.

### 3.2 The in-TD half of the bridge has zero test coverage — CRITICAL

`tests/bridge_pure.py` slices only the block between `# BEGIN PURE HELPERS` and
`# END PURE HELPERS` (`bridge/td_mcp_bridge.py:400-486`) and execs it. The file
is **2,191 lines**. Every `_do_*` handler that touches the TD API — roughly
1,200 lines, including all five sites in 3.1 — is never executed by any test.

`tests/test_parity.py` checks that tool *names* match across surfaces and
`scripts/check_bridge_stale.py` checks the generated file is fresh; neither
executes a handler. This is exactly the blind spot 3.1 lives in.

**dcc-mcp's deterministic TD API double is the fix**, and it is already named in
`IMPROVEMENT_PLAN.md` section 1 as an idea to steal — it was simply never
scheduled. A double that models `errors()`/`warnings()`/`scriptErrors()` as
*methods returning strings* would have failed 3.1 on the first run.

### 3.3 TDN loses authored code, expressions, and rewiring — HIGH

`td_mcp/tdn/__init__.py` (151 lines) has:

- **No DAT text field of any kind.** `checkpoint()` and `restore_checkpoint()`
  therefore round-trip a network while dropping every line of GLSL, every
  callback, every Script DAT. This is Embody's v6.0.264 bug ("TDN export could
  silently throw away every line of code it was meant to preserve") in its
  permanent form. `snapshot_network` is safe — it uses `.tox` — but the TDN path
  is not.
- **`diff_tdn` compares only `parameters` and `type`.** `inputs` is never
  compared, so **a rewiring change diffs as `is_equal: True`** in a format whose
  entire selling point is diffability. `position`, `flags`, `tags` and `children`
  are likewise invisible.
- **`_index_ops` keys by bare `name`**, flattening children into one namespace,
  so two operators named `noise1` under different parents collide.

`_do_export_recipe` (`bridge/td_mcp_bridge.py:1312`) captures `p.val` for
non-default parameters only. A parameter in EXPRESSION mode is non-default, so
its **expression is exported as a frozen constant** — the recipe rebuilds a dead
network. `p.mode`/`p.expr` are never read on any export path (the only `.expr`
writes are at `:1062` and `:754`).

Adopt section 2.6's four findings *before* extending TDN, plus a
`tdn_exclude:<par>` tag and the "any doubt embeds" rule for DAT content.

### 3.4 Data readers dump instead of reduce — HIGH

| Tool | Line | Today | Should be (section 2.2) |
|---|---|---|---|
| `read_chop` | `1563` | first *N* raw samples per channel | per-channel `min/max/mean/std`, `numSamples`, optional diff vs another op |
| `read_dat` | `1589` | first *N* raw rows | `format='stats'` — per-column stats + edge rows |
| `read_top` | `1575` | `width`/`height` only | keep |
| POP reader | — | **does not exist** | metadata free; gate value readback on point count; do **not** pass `start`/`count` to `POP.points()` and expect slicing |

`IMPROVEMENT_PLAN.md` section 7.4 (token-efficient reads) was marked done via
`get_non_default_params`; the reader half is still open.

### 3.5 The `.mcpb` bundle would not install — MEDIUM

`td_mcp/bundle.py` writes a member named **`server.json`** with a bespoke schema
(`{"name","version","description","command","args","format":"mcpb"}`). The
MCP-bundle format expects a root **`manifest.json`** carrying
`dxt_version`/`manifest_version` and a `server` block with `type`,
`entry_point` and `mcp_config` — as `touchdesigner-mcp-superdwayne/manifest.json`
demonstrates for a shipping 1.3 MB bundle. Open roadmap item **#17** is
therefore further from done than it reads.

### 3.6 Protocol version is stale in the in-TD transport — MEDIUM

**Corrected after implementation.** This section originally read "the migration
has not been applied", which overstated it: `MCP_2026_MIGRATION.md` records that
`server_live.py` and `server_offline.py` *did* migrate to SDK v2 and the
2026-07-28 protocol in Phase 2, and that `streamable_http.py` — the standalone
transport pasted into TouchDesigner, which does not use the MCP SDK — was
deliberately deferred (section 4 of that document).

What was not deliberate: `td_mcp/streamable_http.py:169` hard-coded
`"protocolVersion": "2024-11-05"` into every `initialize` reply regardless of
what the client requested, and advertised a `logging` capability that
2026-07-28 deprecates. A client opening with 2026-07-28 was told 2024-11-05 and
left to decide whether to downgrade, for a transport that can serve either.
Fixed in v1.12.0 by negotiating; the deferred session-layer removal stands.

### 3.7 Doc drift — LOW

`README.md` and `IMPROVEMENT_PLAN.md` describe **v1.9.0 / 84 tools**;
`pyproject.toml` says **1.11.0** and the KB is now 9,602 chunks. Adopt Embody's
release rule (section 2.7): every user-visible change names the doc page that
describes it, or is declared internal.

---

## 4. What to take from the newly cloned repos

### 4.1 `johnsabath/touchdesigner-mcp` — verbs, and motion

- **A 13-verb surface**: `run`, `inspect`, `set`, `create`, `wire`, `read`,
  `write`, `edit`, `list`, `docs`, `observe`, `render`, `map`. `TD_MCP` exposes
  39 live tools; a *thin verb facade* over them costs little and materially cuts
  tool-choice tokens.
- **`observe` returns a PNG *or an animated GIF*.** `TD_MCP`'s
  `capture_viewport` verdicts (`is_black`/`is_flat`) are single-frame, so they
  cannot distinguish "renders correctly" from "renders correctly and is
  frozen" — the most common silent failure in a feedback or audio-reactive
  network. A multi-frame capture plus an inter-frame-difference verdict
  (`is_static`) closes that hole and feeds `score_build` directly.
- **`render`** produces MP4 via TD-native recording, with audio.
- **`edit`** does find/replace inside a TextDAT. `TD_MCP` only has whole-text
  `set_dat_text`; a surgical edit pairs naturally with the SHA-256 guard already
  there.
- **`create`** takes position, params, expressions **and** wiring in one call.

### 4.2 `dcc-mcp-touchdesigner` 0.1.2 — Install SOP v1

This is the answer to open items **#12 / #17 / section 7.3**, and it is designed
for an agent rather than a human:

- Verbs `install | status | verify | uninstall | upgrade`, every one accepting
  `--json`; mutation verbs require `--yes`; `--dry-run` never writes.
- A **machine-readable plan** with `schema_version`, per-step status, resolved
  interpreter and `site_packages`, `bootstrap_sha256`, and a receipt at
  `~/.dcc-mcp/receipts/touchdesigner.json`.
- **Distinct exit codes per stage** — `0` ok, `10` preflight, `20` acquire,
  `30` install, `40` verify, `50` requires_restart.
- The honest part: the CLI **cannot** create a project-owned Execute DAT or
  restart the host, so instead of pretending, it returns exactly one
  machine-executable `next_steps` entry containing a `file_edit` for the agent to
  apply, plus a `why`. *"The CLI does not pretend that a project-owned DAT or
  host restart can be completed from an external process."*
- `verify.directly_usable` is only true when staged artifacts match the receipt,
  the target interpreter imports both packages, no bootstrap error is recorded,
  **and** a read-only probe tool succeeds.
- `install.md` is written to be fetched raw by an agent before it changes an
  installation.

`scripts/install_all.py` should be reshaped to this contract.

### 4.3 `superdwayne` — a working `.mcpb` manifest

Concrete reference for 3.5.

### 4.4 `mahsan07/hermes-skill-touchdesigner-mcp` — evidence-first skill docs

Documentation-first packaging: operating contract, safety boundaries,
**expected evidence per step**, and an explicit refusal to claim a runtime it
does not bundle. Worth mirroring in `skills/td-building/SKILL.md`, which
currently teaches usage but does not define what "done" looks like.

---

## 5. Wave 12 — proposed work items

Ordered by *what unblocks the most*. Items 1-3 are correctness; everything below
them is currently being validated against a broken signal.

| # | Item | Where | Size |
|---|---|---|---|
| 1 | **Fix `_op_diagnostics`**: call `errors()`/`warnings()`/`scriptErrors()` as methods, split on newlines, add GLSL `shaderErrors` from the Info DAT (`dat.type == 'info'`), scope the shader pass to touched ops. Fix all 6 sites. Expose `clearScriptErrors` as a recovery action. | `bridge/td_mcp_bridge.py:750,763,782,846,972,1210` | M |
| 2 | **Deterministic TD API double** plus a handler-level test tier, so `_do_*` stops being untested. Model `errors()` as a **method returning a string**. | new `tests/td_double.py` | M |
| 3 | **Re-verify the heal loop** end to end once 1-2 land: `build_and_verify -> td_validate_live -> score_build -> td_self_heal` against a network with a real cook error, a real traceback, and a real GLSL compile failure. | `tests/test_live_offline_loop.py` | S |
| 4 | **TDN fidelity**: DAT content with "any doubt embeds"; `p.mode`/`p.expr` on every export path; `diff_tdn` comparing `inputs`/`position`/`flags`/`children`; path-qualified `_index_ops` keys; `tdn_exclude:<par>` tag; anchor relative `output_file` to `project.folder`. | `td_mcp/tdn/__init__.py`, `bridge:1312` | L |
| 5 | **Reader reductions**: `read_chop` to per-channel stats; `read_dat` `format='stats'`; new `read_pop` (metadata free, readback gated by point count, no slicing assumptions). | `bridge:1563,1589` | M |
| 6 | **`observe` with motion**: multi-frame capture plus an `is_static` verdict, fed into `score_build`. | `bridge`, `td_mcp/vision.py`, `td_mcp/scoring.py` | M |
| 7 | **Install SOP v1**: reshape `install_all.py` into `install/status/verify/uninstall/upgrade`, `--json`/`--yes`/`--dry-run`, staged receipt with SHA-256, stage-specific exit codes, and `next_steps` `file_edit` entries for what a CLI cannot do. | `scripts/install_all.py` | L |
| 8 | **Fix the `.mcpb` manifest** to the real bundle schema; verify by installing it. Then PyPI. Closes #17. | `td_mcp/bundle.py` | S |
| 9 | **Pin stdio to UTF-8 in `main()`**, never at import. | `bridge/td_mcp_bridge.py`, `td_mcp/server_live.py` | S |
| 10 | **Audit every recursive walk** for the `findChildren(depth=N)` exact-depth trap. | bridge-wide | S |
| 11 | **Apply the MCP 2026-07-28 migration** already documented: bump `protocolVersion`, drop the session layer for stateless Streamable HTTP. | `td_mcp/streamable_http.py:169` | M |
| 12 | **Main-thread budget discipline**: frame-chunk any project-wide sweep under ~8 ms; keep programmatic calls synchronous; never let a per-write scan cook the whole project. | bridge-wide | M |
| 13 | **Thin verb facade** (`run/inspect/set/create/wire/read/write/edit/list/docs/observe/render/map`) over the existing 39 live tools. | `td_mcp/server_live.py` | M |
| 14 | **Doc-truth pass**: v1.11.0 everywhere, real tool counts, 9,602 chunks; adopt the release rule that every user-visible change names its docs page. | `README.md`, `IMPROVEMENT_PLAN.md` | S |
| 15 | **PyEnv-style dependency story** for the optional `[rag]` extra inside TouchDesigner, using TD's own pre-cook venv linking. "Bundled" must mean `sys.base_prefix`. | new | L |

---

## 6. Reproducing this sync

```bash
cd github-mcp
for d in */ ; do d="${d%/}"; [ -d "$d/.git" ] || continue
  git -C "$d" fetch --all --tags --prune
  git -C "$d" pull --ff-only
done
```
