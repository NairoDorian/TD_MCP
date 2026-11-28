# Saved projects (`.toe` / `.tox`): read and edit with TouchDesigner closed

`td_project_read`, `td_project_grep`, `td_project_diff` and
`td_project_check` read a saved project or component **with TouchDesigner
closed**; `td_project_edit` and `td_project_extract` change one into a **new**
file (see [Editing](#editing)). They answer the questions an agent has before
it opens a file, and after a session that changed one:

| Ask | Call |
|---|---|
| What is in this file? | `td_project_read(file)` |
| What does `/project1/scene` contain, wired how? | `td_project_read(file, path="/project1/scene", depth=2)` |
| Where is `onValueChange` / this channel name used? | `td_project_grep(file, "onValueChange")` |
| What did I change since the last save? | `td_project_diff(before, after)` |
| Will this old project's names still work on this build? | `td_project_check(file)` |
| Rebuild that network somewhere else | `td_project_read(file, path=..., format="spec")` -> `build_network` |
| Change parameters / code / wires in it without opening TD | `td_project_edit(file, edits=[...])` |
| Point every media path at a new folder | `td_project_edit(file, edits=[{"op": "relink", "from": "C:/old", "to": "D:/new"}])` |
| Make a reusable component out of one COMP | `td_project_extract(file, path="/project1/scene")` |
| Start a component from a spec, no TD at all | `td_project_edit(new="fx", out="fx.tox", edits=[{"op": "build", ...}])` |

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
- **Fields** are bare, or double-quoted when they are empty or hold white
  space. In both forms `\\` is a backslash and `\"` a double quote --
  `back\slash` is saved as `back\\slash`, `a"b` as `a\"b` (not quoted). A
  field holding a non-ASCII character is preceded by a UTF-8 byte order mark
  (`U0 67108928 <BOM>café`). Measured by saving 26 hand-picked strings
  (quotes, backslashes, tabs, newlines, CR LF, CJK, a lone `"`), then over
  the shipped files: the writer re-renders all 806,301 non-legacy records
  byte for byte.
- A quoted field may span lines (multi-line Text COMP strings, annotation
  bodies): three shipped files have such a record.
- Six shipped files still use the pre-099 grammar (`{` / `version 0.8` /
  `name<TAB>(<TAB>values<TAB>)` / `}`); they are read as constants.

**DAT text** is a 1 x 1 table in disguise: `2\n*`, then big-endian uint32
`1, rows=1, cols=1, 1` and one cell `tag 2, length, bytes`. Eight shipped
empty DATs have no cell at all (a 19-byte file); TouchDesigner 2025 writes an
empty one with a zero-length cell. (Another project's guide calls the length a
uint16 at bytes 25-26: right only while the text is under 64 KB.)

**Tables** start with `1\n*` and uint32 `1, rows, cols, 0`, then each cell as
`uint32 tag, uint32 length, bytes`, row-major. Rows come **before** columns
(checked on a 3-row x 2-column table saved for the purpose); all 3,981 shipped
tables are consumed exactly by this layout.

**Wiring** is by sibling name. A COMP's output is written `inner/out1` -- the
COMP seen through its Out operator -- and a COMP's own inputs live in
`<comp>.network` (`compinputs { index<TAB>source / <TAB>In operator / <TAB>family }`).

**Custom parameters**: `.cparm` opens with `pages <count> <names...>` (the
number is a page count), then one definition per parameter whose first column
is a type code, measured by saving one parameter from every `Page.append*`
method:

- the low byte is the style: 0x01 Float, 0x02 Int, 0x03 Toggle, 0x04 Str,
  0x05 Pulse, 0x06 Object, 0x07 SOP, 0x08 MAT, 0x09 CHOP, 0x0A TOP, 0x0B DAT,
  0x0C PanelCOMP, 0x0D COMP, 0x0E OP, 0x0F Menu, 0x10 File, 0x11 Folder,
  0x12 StrMenu, 0x13 Momentary, 0x14 Python, 0x15 Header, 0x17 POP,
  0x18 Sequence, 0x19 FileSave, 0x1A TOPMulti;
- bits 8-11 are the number of components and bits 12-15 how they are named:
  1 numbered (`Size1 Size2`), 2 `rgba`, 3 `xyzw`, 4 `uvw`, 5 `wh` -- so
  `appendXYZ('Pos')` is `0x2E103301` and its values are `Posx Posy Posz`
  (the 1.15 reader called that "a Float of 3" and missed the naming);
- bit 31 marks a parameter that starts a new section.

A menu definition lists its entries after the page name. The values are saved
in `.parm` like any other parameter, with flags `0x04000040` when a script set
them.

**Saved UI state that looks like a parameter** -- `pageindex`, `pageindex1`,
`stdswitcher1` -- is skipped.

## Editing

`td_project_edit(file, edits, out=None, dry_run=False, new=None)` applies a
list of edits to a **copy** of the expansion and packs it with TouchDesigner's
own `toecollapse` into a **new** file. The rules it keeps:

- **The source is never written**, and no existing file is replaced: `out`
  defaults to `<name>.edited.toe` beside the source (`-2`, `-3`... if taken).
  `toecollapse` itself would rename an existing file to `.bkp1` -- the editor
  collapses in a private folder and hands the result over with an exclusive
  create.
- **All or nothing.** The first edit that cannot be applied stops the run;
  the answer names it (`failed_edit`) and says `nothing_written`.
- **Proved before delivery.** `toecollapse` is deterministic and
  `toeexpand(toecollapse(tree)) == tree` (measured, byte for byte, `.build`
  included). The produced file is expanded again and compared with the edited
  tree; any difference and nothing is delivered. The answer then lists what
  changed, as `td_project_diff` sees it.
- **The `.toc` is the file list.** A file in the tree that is not listed is
  dropped by `toecollapse` without a word; the editor rebuilds the list and
  refuses to pack a tree that disagrees with it.
- **Untouched records keep their bytes**; edited ones are rendered the way
  TouchDesigner renders them (fields, escapes and BOM above). A new
  parameter line gets the flags TouchDesigner writes for a scripted change:
  `0` built-in, `0x04000040` custom.
- **The catalog checks every name and value** the way the live
  `set_parameters` does: unknown names get close matches, a Menu token must
  be one of its tokens (TouchDesigner resets the menu to its first item
  otherwise), toggles take booleans, numbers are clamped to the hard limits
  and the clamp is reported. A StrMenu takes any text.
- **Writes keep TouchDesigner's semantics**: a value written over an
  expression makes the parameter a constant and keeps the text, inactive; a
  value over an export is the fallback the export overrides; `null` puts a
  parameter back to its default (the line is removed). Naming a later block
  of a sequence (`const3name`) grows it when the file opens, and the blocks
  skipped take block-0 defaults -- a skipped `const2name` repeats `chan1`, and
  the answer says so.

| Edit | Does |
|---|---|
| `set_params {path, params}` | values, `{"expr"}`, `{"bind"}`, `{"mode": "constant", "val"}`, `null` = default, tuplets as lists (`"t": [0, 1, 0]`) |
| `reset_params {path, names}` | back to default |
| `set_text {path, text}` / `replace_text {path, old, new, count}` | DAT text, whole or one unique occurrence |
| `set_table {path, rows}` / `set_cells {path, cells}` | Table DAT; a cell is `{row, col, value}`, row and col by index or by the header |
| `replace {pattern, replacement, scope, path, regex}` | across code, values, expressions, binds, tables, comments |
| `relink {from, to, path}` | file parameters whose path starts with a folder (backslash and slash alike) |
| `set_flags {path, flags}` | bypass, display, render, viewer, pickable, expose, cooking (COMP) |
| `set_node {path, position, color, comment, tags}` | |
| `connect {from, to, input}` / `disconnect {to, input or from}` | siblings of one family; the input index is checked against the type's maximum |
| `rename {path, name}` | files, `.toc`, sibling wires, docks and a COMP's `.network` follow; the places that still *say* the old name (expressions, code) are listed |
| `delete {path}` | with its docked DATs; wires into it are removed and listed |
| `add {parent, name, type, params, inputs, flags, text, table, dock}` | a new operator, with the flags `COMP.create` gives it (a Geometry COMP renders and displays) |
| `build {parent, spec}` | the `td_validate_build` / `build_network` spec |
| `insert {parent, file, name}` | a saved `.tox` dropped into a COMP |
| `set_project {cookrate, realtime}` | project settings of a `.toe` |

`td_project_extract(file, path)` saves one COMP as its own `.tox`; the
answer lists parameters inside it that point outside (absolute paths).
`new="name"` starts from an empty Base COMP instead of a file.

What it refuses, and why:

- **lock** -- a locked operator's frozen output is a separate `.data` file
  the editor does not produce; and a locked DAT's content.
- **COMPs whose function is the network TouchDesigner builds inside them**
  (Button, Slider, Table, Field, Animation, Annotate, FBX, glTF, USD...) --
  added to a file they would be empty shells. Create them in TouchDesigner,
  or `insert` a saved `.tox` of one.
- **wires to or from a COMP's own connectors** (they map to its In/Out
  operators by position) and across networks.
- custom parameter *definitions* (`.cparm` columns are not fully decoded).

Added operators do not get what the editor gives them in TouchDesigner: a
Script CHOP/SOP/TOP/DAT has no docked callbacks DAT, a GLSL TOP or MAT no
shader DATs, a Geometry COMP no torus. The answer says so for each.

Verified in TouchDesigner (`scripts/live_toe_edit.py`, 18 checks): a
component built and saved by TouchDesigner, edited offline, loaded back --
constants, expressions, an untouched bind following its edited master,
escaped and non-ASCII custom strings, a menu, an XYZ, a grown sequence, DAT
text, a table cell, a rename, a new wire, an added Blur TOP and a Geometry
COMP rendering a built box, no errors. A `.toe` edited offline (cook rate,
an added operator, a changed constant) opened as the project, with the bridge
running from it. `python -m td_mcp.toe_writer --selftest <dir>` re-renders
every record of a set of expansions and counts differences.

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
  and defaults of custom parameters are not reported, and the editor cannot
  create or change custom parameter definitions.
- `dict` (operator storage) is a pickle and is never unpickled; its presence
  is reported as `has_storage`.
- `to_spec` emits one network level; nested COMPs are listed under
  `nested_comps` and read with another call. Docked DATs are created by their
  host and are skipped.
