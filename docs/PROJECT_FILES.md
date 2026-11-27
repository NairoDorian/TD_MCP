# Reading saved projects (`.toe` / `.tox`)

`td_project_read`, `td_project_grep`, `td_project_diff` and
`td_project_check` read a saved project or component **with TouchDesigner
closed**. They answer the questions an agent has before it opens a file, and
after a session that changed one:

| Ask | Call |
|---|---|
| What is in this file? | `td_project_read(file)` |
| What does `/project1/scene` contain, wired how? | `td_project_read(file, path="/project1/scene", depth=2)` |
| Where is `onValueChange` / this channel name used? | `td_project_grep(file, "onValueChange")` |
| What did I change since the last save? | `td_project_diff(before, after)` |
| Will this old project's names still work on this build? | `td_project_check(file)` |
| Rebuild that network somewhere else | `td_project_read(file, path=..., format="spec")` -> `build_network` |

## How it works

TouchDesigner ships `toeexpand` (and `toecollapse`) in its `bin` folder. It
unpacks a container into a directory of small text files, one group per
operator. The reader:

1. copies the file into `~/.td_mcp/cache/toe/<sha256>/` -- `toeexpand` writes
   beside its input, so the original is never touched, and an unchanged file
   is expanded once;
2. runs `toeexpand` there (it **exits 1 on success**; success is the output
   directory appearing);
3. parses the tree with the standard library.

`toeexpand` is found through `TD_MCP_TOEEXPAND`, beside `TD_EXE`, or in the
newest TouchDesigner install. An already-expanded `*.dir` folder can be passed
instead of a file, and needs no TouchDesigner at all. The cache keeps the 32
most recently used expansions.

## The format, as measured

Nothing here is documented by Derivative. Every rule was measured on
TouchDesigner 2025.33230 (Windows): a network built for the purpose in the
TEST-01 project, saved as a `.tox` and expanded, then **all 768 components
that ship with the install** (489 OP Snippets, 279 Palette components:
93,153 operators, 806,313 parameter records, 14,720 DAT texts, 3,981 tables).
`python -m td_mcp.toe_reader --selftest <dir of expansions>` re-runs the
measurement on a new build. grigabyte/td-atlas (MIT) published the first map
of this format; it was the starting point, and two of its rules came out
differently here (marked below).

| File | Holds |
|---|---|
| `<op>.n` | `FAMILY:type`, `tile x y w h`, `flags = name value ...`, `inputs { index<TAB>sibling }`, `color r g b`; optionally `comment`, `tags`, `dock`, `exports`, `dict` (pickled storage) and `view` |
| `<op>.parm` | parameters that differ from default, between `?` lines |
| `<op>.cparm` | custom parameter pages and definitions |
| `<op>.text` | DAT text |
| `<op>.table` | Table DAT cells |
| `<op>.network` | a COMP's own input wires |

**Type names are `OP.type`, not `OP.OPType`.** A Composite TOP is `TOP:comp`,
a Geometry COMP `COMP:geo`, an Evaluate DAT `DAT:eval`. The parameter catalog
recorded both names from a running build, so each operator is reported with
the opType `create_node` accepts.

**A `.parm` line is `name flags constant [expression] [bind]`**, and the flags
word decides everything:

- `flags & 3` is the **mode**, in `ParMode` order: 0 constant, 1 expression,
  2 export, 3 bind. Over the 806,313 shipped records every mode-1 line
  carries an expression and every mode-3 line but one a bind expression.
- `0x10` means *an expression is stored* -- **not** that the parameter is in
  expression mode (td-atlas reads it as the mode). 22,394 shipped parameters
  are constants that keep an inactive expression: writing a value over an
  expression switches the mode and keeps the text, and it is saved that way.
  The Out TOP in the TEST-01 demo saves `label 16 trails me.name` -- the label
  is the constant `trails`.
- `0x200` means a bind expression is stored; `0x200000` adds one more value
  (in every sample a repeat of the bind expression).
- How many values follow is decided by those bits, never by the shape of the
  line: `colorr 515 1 parent.X.par.Color1r` is the constant `1` bound to
  `parent.X.par.Color1r`.
- Single quotes are content (`op('null_ctrl')['chan1']` must survive), so no
  shell-style splitter.
- A quoted constant may span lines (multi-line Text COMP strings, annotation
  bodies): 51 lines in the shipped examples.
- Six shipped files still use the pre-099 grammar (`{` / `version 0.8` /
  `name<TAB>(<TAB>values<TAB>)` / `}`); they are read as constants.

**DAT text** starts with `2\n*` and six big-endian uint32, the last of them the
payload length. An empty DAT is a 19-byte header with no length word.

**Tables** start with `1\n*` and uint32 `1, rows, cols, 0`, then each cell as
`uint32 tag, uint32 length, bytes`, row-major. Rows come **before** columns
(checked on a 3-row x 2-column table saved for the purpose); all 3,981 shipped
tables are consumed exactly by this layout.

**Wiring** is by sibling name. A COMP's output is written `inner/out1` -- the
COMP seen through its Out operator -- and a COMP's own inputs live in
`<comp>.network` (`compinputs { index<TAB>source / <TAB>In operator / <TAB>family }`).

**Custom parameters**: `.cparm` opens with `pages <count> <names...>` (the
number is a page count), then one definition per parameter whose first column
is a type code: the low byte is the style (0x01 Float, 0x02 Int, 0x03 Toggle,
0x04 Str, 0x05 Pulse, 0x0E OP, 0x0F Menu), bits 12-15 the component count
(`Pos` XYZ is a Float of 3). A menu definition lists its entries after the
page name. Their values are saved in `.parm` like any other parameter.

**Saved UI state that looks like a parameter** -- `pageindex`, `pageindex1`,
`stdswitcher1` -- is skipped.

## What `td_project_check` reports

It holds every operator against the catalog of the installed build: types the
build does not have (`SOP:font`, `DAT:web`, `POP:force` in older shipped
examples), parameter names it does not have (with close matches; custom
parameters, which start with a capital, are skipped), constant menu values
outside the menu, and absolute file paths missing on this machine.
TouchDesigner migrates many old names when it loads a file -- the findings
matter when names are copied into new work.

## Limits

- `.cparm` columns between the label and the page are not decoded, so ranges
  and defaults of custom parameters are not reported.
- `dict` (operator storage) is a pickle and is never unpickled; its presence
  is reported as `has_storage`.
- `to_spec` emits one network level; nested COMPs are listed under
  `nested_comps` and read with another call. Docked DATs are created by their
  host and are skipped.
