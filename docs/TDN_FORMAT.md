# TDN — TouchDesigner Network format

Version **2.1**. A diffable YAML serialisation of a TouchDesigner network, so a
build is reviewable, git-diffable and restorable.

```yaml
format: tdn
version: '2.1'
generator: td-mcp
network_path: /project1
operators:
- name: noise1
  type: Noise TOP
  position: [0, 0]
  parameters: {period: 4.0}
- name: level1
  type: Level TOP
  position: [200, 0]
  inputs: [noise1]
  expressions: {opacity: absTime.seconds % 1}
- name: glsl1_pixel
  type: Text DAT
  text: |
    out vec4 fragColor;
    void main() { fragColor = vec4(1.0); }
  text_source: embedded
```

---

## Operator fields

| Field | Notes |
|---|---|
| `name`, `type` | Required. `type` accepts a display name or an opType. |
| `position`, `size`, `color` | Layout. |
| `comment`, `tags` | `tags` also carries `tdn_exclude:<par>` markers. |
| `parameters` | Non-default **constant** values. |
| `expressions` | Parameters in EXPRESSION or BIND mode, as source. |
| `text`, `text_source` | A DAT's authored content. `text_source` is `embedded`, `file` or `empty`. |
| `inputs` | Source operator names, by input index. |
| `flags`, `custom_pars` | |
| `excluded_parameters` | What `tdn_exclude` omitted — recorded, deliberately. |
| `children` | Nested operators. |

---

## Four rules, each bought with a bug

These come from `Embody/docs/reports/tdn-exporter-fidelity-moonshine.md` and
from applying it to this codebase. Read that report before extending TDN.

### 1. Any doubt embeds

A DAT's text is omitted **only** when a file already holds it. Never as an
optimisation, never behind a general "include content" flag.

```python
dat_text_policy(text, file_backed=False, externalized=False)
# ("", ...)                → (False, "empty")
# ("code", file_backed)    → (False, "file")     # duplicating it helps nobody
# ("code", unbacked)       → (True,  "embedded") # the export is the ONLY copy
```

Embody shipped an `include_dat_content` flag that defaulted off and dropped the
content of *every* DAT — including ones whose code existed nowhere else. A
COMP could export 185 operators and zero lines of code, so a crash took every
shader and callback with it.

`td-mcp` had it worse: TDN had **no field for DAT text at all**, so
`checkpoint()` round-tripped a network while discarding every line of GLSL and
every callback, unconditionally. Fixed in v1.12.0.

> For anything you cannot afford to lose, prefer `snapshot_network` — a real
> `.tox`, which preserves everything TouchDesigner knows about the component.

### 2. Expressions are configuration; their values are not

A parameter in EXPRESSION mode is non-default, so a value-only exporter writes
down whatever it happened to evaluate to and the rebuilt network is **dead** —
the animation is gone, replaced by one frame of it.

`expressions` travels separately, and on import expressions are applied *after*
constants so an expression always wins over a stale captured value.

### 3. Every field participates in the diff

`diff_tdn` compares `type`, `position`, `size`, `color`, `comment`, `tags`,
`inputs`, `flags`, `text`, `expressions` **and** `parameters`.

It used to compare `parameters` and `type` and nothing else — so **a rewiring
diffed as `is_equal: True`**, in a format whose entire selling point is
diffability. A move, a retag and an edited shader were equally invisible.

Operators are indexed by **path**, not bare name. Two operators called `noise1`
under different parents are two operators; keying on the name collapsed them
into one, reporting phantom changes between unrelated nodes and missing real
ones.

### 4. Runtime state is marked, and the marking is visible

Tag an operator `tdn_exclude:<parname>` and that parameter's constant value is
omitted from the export:

```yaml
- name: moonbeam_out
  type: Moonbeam TOP
  tags: ['tdn_exclude:Port']
  parameters: {Active: 1}          # Port: 17361 omitted
  excluded_parameters: [Port]      # ← and said so
```

Three real leaks motivated this: an authoring machine's absolute asset path, a
port chosen at runtime by probing a ladder, and live window geometry — all
committed to git, all changing per session.

Two design points matter:

- **The tag lives on the operator**, not in this package's source. It therefore
  reaches parameters on non-COMP operators — which is exactly where `file` and
  `Port` live — without anyone patching `td-mcp`.
- **`excluded_parameters` is written into the file.** A silently omitted
  parameter is indistinguishable from one sitting at its default. A typo'd name
  is reported in `excluded_parameters_unknown` and warned about, because the
  worst outcome is the leak staying while the author believes it is fixed.

Expressions survive exclusion: the expression says *how* the value is derived
and is configuration; the constant it currently evaluates to is the session
state worth losing.

---

## Paths

```python
resolve_output_path("touch/net.tdn", project_folder)  # → <project>/touch/net.tdn
relative_to_project(path, project_folder)             # → "touch/net.tdn" or None
```

A relative `output_file` resolves against **TouchDesigner's current working
directory**, not the project root. Embody's exporter wrote a 903 KB
whole-project dump one directory deeper than intended, then failed to track it —
`Path.relative_to` cannot relate a relative path to an absolute one — and the
`ValueError` was swallowed by a broad `except` and logged as a warning while the
export reported success. The file sat unreferenced in git for three days.

`relative_to_project` returns `None` for a file outside the project rather than
raising. "This is outside the project" is a specific situation deserving a
specific message, not an exception to swallow.

---

## Two more traps, not yet hit here

`td-mcp`'s TDN does not serialise parameter **sequences** yet. When it does,
these are the rules:

**Sequence block counts double-declare wiring.** A `mergePOP` with 7 wired
inputs exported `sequences: input: [{}] × 7`. On import, the *list length is the
numBlocks instruction*, and it was applied **before** wiring — so the operator
briefly had 7 declared inputs and nothing connected, latching a `No input POP`
error that resurfaced on **every project open**. Omit wiring-derived sequences
entirely (`inputs:` is the source of truth), probe real default block counts
from a throwaway instance rather than assuming 1, and apply block counts *after*
wiring.

**Untouched boilerplate is not authored content.** Several hundred lines of
TouchDesigner's own default `onValueChange`/`onFocus` stubs got embedded because
"has content" was standing in for "has authored content". Compare against the
default template for the type: byte-identical means nothing was authored, so
there is nothing to protect.

---

## A process note

From the same report, and worth knowing on its own: creating and destroying a
scratch COMP through an MCP triggered a **bulk TDN re-export** that overwrote
unrelated edits on disk and baked live session values into committed files. An
apparently read-only investigation can rewrite files as a side effect of
touching the network at all.

---

## API

```python
from td_mcp import tdn

net  = tdn.new_network("/project1", operators=[...])
text = tdn.export_network(net)              # applies tag exclusions
back = tdn.import_network(text)
diff = tdn.diff_tdn(text_a, text_b)         # added / removed / changed / is_equal

path = tdn.checkpoint("/project1", operators)   # ~/.td_mcp/tdn/*.tdn
net  = tdn.restore_checkpoint(path)
```

`export_network` ignores volatile headers (`build`, `generator`, `td_build`,
`exported_at`, `source_file`) when diffing, so an unchanged network re-exported
yields an empty diff and git textconv stays quiet.

---

## See also

- [TESTING.md](TESTING.md) — `tests/test_tdn_fidelity.py` pins every rule here
- [TOOLS.md](TOOLS.md#state-and-code) — `snapshot_network` vs TDN checkpoints
- `Embody/docs/reports/tdn-exporter-fidelity-moonshine.md` — the source report
