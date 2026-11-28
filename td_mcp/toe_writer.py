"""Edit a saved TouchDesigner project (.toe) or component (.tox) with
TouchDesigner closed -- and never touch the file that was given.

``toe_reader`` expands a *copy* of a project with TouchDesigner's own
``toeexpand`` and reads the tree. This module changes that tree and packs it
back with ``toecollapse`` into a **new** file: parameters (values, expressions,
binds), DAT text and tables, wires, flags, names, whole operators, whole
networks from a build spec, a component lifted out of a project or dropped
into one.

Nothing here is documented by Derivative. Every rule was measured on
TouchDesigner 2025.33230 (Windows) by setting a state in TouchDesigner, saving
it, expanding the file and reading the bytes -- then proven the other way:
:func:`selftest` re-renders every parameter record of the 768 components that
ship with the install (806,313 of them) and compares it with what TouchDesigner
wrote, and ``scripts/live_toe_edit.py`` opens edited files in TouchDesigner
and reads every change back.

Measured rules the writer keeps
-------------------------------
* A `.parm` field is bare, or double-quoted when it is empty or holds white
  space. ``\\`` and ``"`` are backslash-escaped in both forms. A field with a
  non-ASCII character is preceded by a UTF-8 byte order mark.
* The flags word: bits 0-1 the mode, ``0x10`` an expression is stored,
  ``0x200`` a bind is stored. A line that exists keeps every other bit
  (``0x80`` = disabled, ``0x100`` = read-only were identified; the rest are
  carried untouched). A new line gets what TouchDesigner writes for a scripted
  change: ``0`` built-in, ``0x04000040`` custom.
* Writing a value over an expression switches the mode and keeps the text,
  as TouchDesigner does.
* DAT text is a 1 x 1 table: ``2\\n*`` + uint32 ``1, 1, 1, 1`` + cell
  ``(2, length, bytes)``. A table is ``1\\n*`` + ``1, rows, cols, 0`` + cells.
* ``<name>.toc`` lists every file of the tree; a file that is not listed is
  dropped by ``toecollapse`` without a word, so the list is rebuilt and checked.
* ``toecollapse`` is deterministic and ``expand(collapse(tree)) == tree``. The
  writer relies on that as its last check: the produced file is expanded again
  and must match the edited tree byte for byte before it is handed over.

What it refuses, and why
------------------------
Locking an operator (the frozen output lives in a ``.data`` file this module
does not produce), changing a locked DAT's content, creating custom parameter
*definitions* (the ``.cparm`` columns are not fully decoded), and wires to or
from a COMP's own connectors in the general case. Each refusal says what to do
instead.

Pure standard library. ``toeexpand`` / ``toecollapse`` ship in TouchDesigner's
``bin`` folder and are needed to read and to write a file.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from td_mcp import toe_reader as tr

BOM = "﻿"

#: What TouchDesigner 2025 writes for a custom parameter set by a script
#: (``Fset 67108928 0.75``): bit 26 and ``0x40``. Built-in parameters get 0.
CUSTOM_BASE_FLAGS = 0x04000040
MODE_BITS = {"constant": 0, "expression": 1, "export": 2, "bind": 3}

#: The header TouchDesigner writes at the top of every component's `.toc`
#: (identical in all 768 shipped components). A project's `.toc` has none.
TOX_TOC_HEADER = "# 4 0 0 0 1"

#: Naming a later block of a sequence parameter grows the sequence when the
#: file is opened; refuse runaway growth the way the live bridge does.
MAX_SEQUENCE_GROWTH = 64

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_WS = " \t\n\r"

#: Own-file extensions in the order TouchDesigner lists them in a `.toc`.
_EXT_ORDER = {".n": 0, ".cparm": 1, ".parm": 2}

#: Statement order inside a `.n` file, as TouchDesigner writes it.
_N_ORDER = {"v": 0, "comment": 1, "tags": 2, "tile": 3, "flags": 4, "inputs": 5,
            "color": 6, "dock": 7}

#: User-facing flag -> (key in the `.n` flags line, value when on, value when off).
#: ``None`` means "the pair is absent". Measured: `bypass on`, `viewer 1`,
#: `cook off` for a COMP with cooking disabled, `expose off`.
_FLAGS = {
    "bypass": ("bypass", "on", None),
    "display": ("display", "on", None),
    "render": ("render", "on", None),
    "viewer": ("viewer", "1", None),
    "pickable": ("pickable", "on", None),
    "expose": ("expose", None, "off"),
    "cooking": ("cook", None, "off"),
    "allowCooking": ("cook", None, "off"),
    "clone_immune": ("cloneImmune", "on", None),
    "cloneImmune": ("cloneImmune", "on", None),
}

_NUMERIC_STYLES = {"Float", "Int", "XY", "XYZ", "XYZW", "WH", "UV", "UVW", "RGB", "RGBA"}
_NO_VALUE_STYLES = {"Pulse", "Momentary", "Header"}
_FILE_STYLES = {"File", "Folder", "FileSave"}


class ToeEditError(Exception):
    """An edit could not be applied. Nothing is written when one is raised."""

    def __init__(self, message: str, **extra: Any):
        super().__init__(message)
        self.extra = extra


# --------------------------------------------------------------------------
# Formatting (the inverse of toe_reader's grammars)
# --------------------------------------------------------------------------
def format_field(value: str) -> str:
    """One `.parm` / `.n` field exactly as TouchDesigner writes it."""
    body = value.replace("\\", "\\\\").replace('"', '\\"')
    if value == "" or any(ch in value for ch in _WS):
        body = '"' + body + '"'
    if any(ord(ch) > 127 for ch in value):
        body = BOM + body
    return body


def format_number(value: Any) -> str:
    """A number the way a `.parm` constant holds it (``1``, ``0.25``, ``-1.5``)."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    f = float(value)
    if math.isnan(f) or math.isinf(f):
        raise ToeEditError("not a finite number: %r" % (value,))
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return "%.10g" % f


def format_parm(rec: Dict[str, Any]) -> str:
    """A parameter record -> its line. The flags word decides the fields."""
    flags = int(rec["flags"])
    parts = [rec["name"], str(flags), format_field(str(rec.get("value", "")))]
    if flags & tr.FLAG_EXPRESSION:
        parts.append(format_field(str(rec.get("expr", ""))))
    if flags & tr.FLAG_BIND:
        parts.append(format_field(str(rec.get("bind", ""))))
    if flags & tr.FLAG_EXTRA_VALUE:
        parts.append(format_field(str(rec.get("extra", ""))))
    return " ".join(parts)


def text_payload(text: str) -> bytes:
    """A `.text` file: a 1 x 1 table whose one cell is the text."""
    body = text.encode("utf-8")
    return b"2\n*" + struct.pack(">6I", 1, 1, 1, 1, 2, len(body)) + body


def table_payload(rows: List[List[str]]) -> bytes:
    """A `.table` file, row-major, every row padded to the widest."""
    cols = max((len(r) for r in rows), default=0)
    out = [b"1\n*" + struct.pack(">4I", 1, len(rows) if cols else 0, cols, 0)]
    if cols:
        for row in rows:
            for c in range(cols):
                cell = (row[c] if c < len(row) else "").encode("utf-8")
                out.append(struct.pack(">2I", 2, len(cell)) + cell)
    return b"".join(out)


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return format_number(value)
    return str(value)


def _read(path: Path) -> str:
    """File text, byte-exact on the way back (``surrogateescape``)."""
    return path.read_bytes().decode("utf-8", "surrogateescape")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8", "surrogateescape"))


# --------------------------------------------------------------------------
# `.parm` files: untouched records go back byte for byte
# --------------------------------------------------------------------------
class ParmFile:
    """One operator's `.parm`. Only records that were edited are re-rendered."""

    def __init__(self, path: Path):
        self.path = path
        self.exists = path.is_file()
        self.dirty = False
        self.legacy = False
        self.records: List[Dict[str, Any]] = []
        if not self.exists:
            return
        text = _read(path)
        if tr.is_legacy_parms(text):
            self.legacy = True
            return
        for raw in tr.split_parm_records(text):
            rec = tr.parse_parm_line(raw)
            if rec is None:
                rec = {"name": None}
            rec["raw"] = raw
            self.records.append(rec)

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        for rec in self.records:
            if rec.get("name") == name:
                return rec
        return None

    def names(self) -> List[str]:
        return [r["name"] for r in self.records if r.get("name")]

    def put(self, rec: Dict[str, Any], order: Optional[Dict[str, int]] = None) -> None:
        """Replace the record of that name, or insert it where the operator's
        own parameter order puts it (``order``), else at the end."""
        rec.pop("raw", None)
        rec["mode"] = tr.PAR_MODES[rec["flags"] & 3]
        self.dirty = True
        for i, old in enumerate(self.records):
            if old.get("name") == rec["name"]:
                self.records[i] = rec
                return
        if order and rec["name"] in order:
            rank = order[rec["name"]]
            for i, old in enumerate(self.records):
                if order.get(old.get("name"), -1) > rank:
                    self.records.insert(i, rec)
                    return
        self.records.append(rec)

    def remove(self, name: str) -> bool:
        for i, old in enumerate(self.records):
            if old.get("name") == name:
                del self.records[i]
                self.dirty = True
                return True
        return False

    def render(self) -> str:
        lines = ["?"]
        for rec in self.records:
            lines.append(rec["raw"] if "raw" in rec else format_parm(rec))
        lines.append("?")
        return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# `.n` files: statements this module does not know are carried as they are
# --------------------------------------------------------------------------
class NodeFile:
    """One operator's `.n`: type, tile, flags, inputs, colour, comment, tags, dock."""

    def __init__(self, path: Optional[Path] = None, text: Optional[str] = None):
        self.path = path
        body = text if text is not None else _read(path)
        self.lines = body.split("\n")
        if self.lines and self.lines[-1] == "":
            self.lines.pop()
        self.dirty = False

    # -- statements --------------------------------------------------------
    def _statements(self) -> List[Tuple[str, int, int]]:
        """``[(keyword, first line, one past the last line)]`` after the type line."""
        out = []
        i, n = 1, len(self.lines)
        while i < n:
            line = self.lines[i]
            key = line.strip().partition(" ")[0]
            j = i + 1
            if key == "comment":
                rest = line.partition(" ")[2]
                while not tr._scan_field(rest, 0)[2] and j < n:
                    rest += "\n" + self.lines[j]
                    j += 1
            elif j < n and self.lines[j].strip() == "{":
                while j < n and self.lines[j].strip() != "}":
                    j += 1
                j = min(n, j + 1)
            out.append((key, i, j))
            i = j
        return out

    def _span(self, key: str) -> Optional[Tuple[int, int]]:
        for k, i, j in self._statements():
            if k == key:
                return i, j
        return None

    def _put(self, key: str, new_lines: Optional[List[str]]) -> None:
        """Replace statement ``key`` (``None`` removes it) or insert it in
        TouchDesigner's order."""
        span = self._span(key)
        self.dirty = True
        if span is not None:
            self.lines[span[0]:span[1]] = new_lines or []
            return
        if not new_lines:
            return
        rank = _N_ORDER.get(key, 50)
        at = len(self.lines)
        for k, i, _j in self._statements():
            if k == "end" or _N_ORDER.get(k, 50) > rank:
                at = i
                break
        self.lines[at:at] = new_lines

    # -- accessors ---------------------------------------------------------
    @property
    def kind(self) -> Tuple[str, str]:
        family, _, short = self.lines[0].strip().partition(":")
        return family, short

    def flags(self) -> List[List[str]]:
        span = self._span("flags")
        if span is None:
            return []
        toks = self.lines[span[0]].split("=", 1)[-1].split()
        return [[toks[i], toks[i + 1]] for i in range(0, len(toks) - 1, 2)]

    def set_flag(self, key: str, value: Optional[str]) -> None:
        pairs = self.flags()
        found = False
        for pair in list(pairs):
            if pair[0] == key:
                found = True
                if value is None:
                    pairs.remove(pair)
                else:
                    pair[1] = value
        if not found and value is not None:
            at = next((i for i, p in enumerate(pairs) if p[0] == "parlanguage"), len(pairs))
            pairs.insert(at, [key, value])
        self._put("flags", ["flags =  " + " ".join("%s %s" % (k, v) for k, v in pairs)])

    def tile(self) -> List[int]:
        span = self._span("tile")
        if span is None:
            return [0, 0, 130, 90]
        try:
            return [int(float(v)) for v in self.lines[span[0]].split()[1:5]]
        except ValueError:
            return [0, 0, 130, 90]

    def set_tile(self, x: int, y: int) -> None:
        _x, _y, w, h = (self.tile() + [130, 90])[:4]
        self._put("tile", ["tile %d %d %d %d" % (int(x), int(y), w, h)])

    def inputs(self) -> List[Tuple[int, str]]:
        span = self._span("inputs")
        out = []
        if span is None:
            return out
        for line in self.lines[span[0] + 2:span[1] - 1]:
            index, _, source = line.partition("\t")
            if index.strip().isdigit() and source.strip():
                out.append((int(index.strip()), source.strip()))
        return out

    def set_inputs(self, wires: Iterable[Tuple[int, str]]) -> None:
        wires = sorted(wires)
        if not wires:
            self._put("inputs", None)
            return
        self._put("inputs", ["inputs", "{"] + ["%d \t%s" % w for w in wires] + ["}"])

    def set_color(self, rgb: List[float]) -> None:
        self._put("color", ["color " + " ".join(format_number(round(float(c), 4)) for c in rgb[:3]) + " "])

    def set_comment(self, text: Optional[str]) -> None:
        self._put("comment", ["comment " + format_field(text)] if text else None)

    def set_tags(self, tags: List[str]) -> None:
        self._put("tags", ["tags 0 %d %s" % (len(tags), " ".join(format_field(t) for t in tags))]
                  if tags else None)

    def dock(self) -> Optional[str]:
        span = self._span("dock")
        return self.lines[span[0]].partition(" ")[2].strip() if span else None

    def set_dock(self, host: Optional[str]) -> None:
        self._put("dock", ["dock " + host] if host else None)

    def render(self) -> str:
        return "\n".join(self.lines) + "\n"


def new_node_text(family: str, short: str, x: int = 0, y: int = 0,
                  flags: Optional[List[List[str]]] = None) -> str:
    """The `.n` of a fresh operator, as a script-created one is saved."""
    w, h = (160, 130) if family == "COMP" else (130, 90)
    pairs = list(flags or []) + [["parlanguage", "0"]]
    return "\n".join([
        "%s:%s" % (family, short),
        "tile %d %d %d %d" % (int(x), int(y), w, h),
        "flags =  " + " ".join("%s %s" % (k, v) for k, v in pairs),
        "color 0.67 0.67 0.67 ",
        "end",
    ]) + "\n"


# --------------------------------------------------------------------------
# toecollapse
# --------------------------------------------------------------------------
def find_toecollapse() -> Optional[str]:
    """``toecollapse`` beside ``toeexpand`` (or ``TD_MCP_TOECOLLAPSE``)."""
    explicit = os.environ.get("TD_MCP_TOECOLLAPSE")
    if explicit and os.path.isfile(explicit):
        return explicit
    expand = tr.find_toeexpand()
    if expand:
        folder, name = os.path.split(expand)
        beside = os.path.join(folder, name.replace("toeexpand", "toecollapse"))
        if os.path.isfile(beside):
            return beside
    return shutil.which("toecollapse")


def work_root() -> Path:
    return tr.cache_root().parent / "edit"


def _run_tool(tool: str, name: str, cwd: Path, timeout: float = 600.0) -> str:
    try:
        proc = subprocess.run([tool, name], cwd=str(cwd), capture_output=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToeEditError("%s failed: %s" % (os.path.basename(tool), exc)) from exc
    return (proc.stdout or b"").decode("utf-8", "replace") + (proc.stderr or b"").decode("utf-8", "replace")


# --------------------------------------------------------------------------
# Workspace: an expanded copy being edited
# --------------------------------------------------------------------------
class Workspace:
    """A private, editable expansion of one project file.

    ``source`` is expanded through the reader's cache and that tree is
    *copied* here, so the cache stays the untouched "before" picture and the
    original file is never opened for writing. ``Workspace.new`` starts from
    an empty component instead.
    """

    def __init__(self, source: Optional[str] = None, root: Optional[str] = None):
        self.root = Path(root) if root else work_root() / uuid.uuid4().hex[:12]
        self.root.mkdir(parents=True, exist_ok=True)
        self.source: Optional[Path] = None
        self.before: Optional[tr.Project] = None
        self.header: List[str] = []
        self.toc: List[str] = []
        self._project: Optional[tr.Project] = None
        self._parms: Dict[str, ParmFile] = {}
        self._nodes: Dict[str, NodeFile] = {}
        self.notes: List[str] = []
        if source is None:
            self.name = ""
            self.dir = self.root
            return
        src = Path(source).expanduser()
        if src.is_dir():
            # An already expanded tree (``name.toe.dir`` beside ``name.toe.toc``).
            if not src.name.endswith(".dir"):
                raise ToeEditError("not an expanded project directory: %s" % src)
            before_dir = src
            self.source = None
            self.name = src.name[:-4]
        else:
            before_dir = Path(tr.expand(str(src)))
            self.source = src
            self.name = src.name
        self.before = tr.Project(str(before_dir), source=self.name)
        self.dir = self.root / (self.name + ".dir")
        shutil.copytree(before_dir, self.dir)
        toc_file = before_dir.parent / (self.name + ".toc")
        if not toc_file.is_file():
            raise ToeEditError("the expansion of %s has no table of contents" % self.name)
        self._load_toc(_read(toc_file))

    @classmethod
    def new(cls, name: str = "component", root: Optional[str] = None,
            build: Optional[str] = None) -> "Workspace":
        """An empty Base COMP called ``name``, to be saved as ``<name>.tox``."""
        if not _NAME.match(name or ""):
            raise ToeEditError("not a usable component name: %r (letters, digits, underscore)" % (name,))
        ws = cls(None, root=root)
        ws.name = name + ".tox"
        ws.dir = ws.root / (ws.name + ".dir")
        ws.dir.mkdir(parents=True)
        cat = tr._catalog()
        build = build or (cat.build() if cat is not None else None) or "2025.33230"
        osname = "Windows" if os.name == "nt" else ("macOS" if sys.platform == "darwin" else "Linux")
        _write(ws.dir / ".build", "version 099\nbuild %s\ntime %s\nosname %s\nosversion 10\n"
               % (build, time.strftime("%a %b %d %H:%M:%S %Y"), osname))
        _write(ws.dir / (name + ".n"), new_node_text("COMP", "base"))
        ws.header = [TOX_TOC_HEADER]
        ws.toc = [".build", name + ".n"]
        return ws

    # -- table of contents --------------------------------------------------
    def _load_toc(self, text: str) -> None:
        for line in text.split("\n"):
            line = line.rstrip("\r")
            if not line:
                continue
            if line.startswith("#") and not self.toc:
                self.header.append(line)
            else:
                self.toc.append(line)

    def toc_text(self) -> str:
        return "\n".join(self.header + self.toc) + "\n"

    @staticmethod
    def base_of(path: str) -> str:
        return path.strip("/")

    def own_files(self, base: str) -> List[str]:
        """`.toc` entries that are files of the operator at ``base`` itself."""
        n = len(base)
        return [e for e in self.toc
                if e.startswith(base + ".") and "/" not in e[n:] and e[:n] == base]

    def subtree(self, base: str) -> List[str]:
        return [e for e in self.toc if e.startswith(base + "/")]

    def ensure_file(self, base: str, ext: str) -> None:
        """List ``base + ext`` in the `.toc`, where TouchDesigner would."""
        entry = base + ext
        if entry in self.toc:
            return
        own = self.own_files(base)
        if not own:
            raise ToeEditError("no operator at /%s" % base)
        rank = _EXT_ORDER.get(ext, 9)
        after = own[0]
        for e in own:
            if _EXT_ORDER.get(e[len(base):], 9) <= rank:
                after = e
        self.toc.insert(self.toc.index(after) + 1, entry)

    def drop_file(self, base: str, ext: str) -> None:
        entry = base + ext
        if entry in self.toc:
            self.toc.remove(entry)
        f = self.dir / entry
        if f.is_file():
            f.unlink()

    # -- model --------------------------------------------------------------
    @property
    def project(self) -> tr.Project:
        if self._project is None:
            self.flush()
            self._project = tr.Project(str(self.dir), source=self.name)
        return self._project

    def invalidate(self) -> None:
        self._project = None

    def refresh(self, path: str) -> None:
        """Files of the operator at ``path`` changed: write pending models and
        let its lazily read parts load again (cheaper than a full re-scan)."""
        self.flush()
        if self._project is None:
            return
        node = self._project.nodes.get(path)
        if node is None:
            self._project = None
            return
        node._n = node._parms = node._cparms = node._text = node._table = None
        node.op_type = tr.op_type_for(node.family, node.n.get("type", ""))

    def register(self, path: str) -> None:
        """A new operator's `.n` was written at ``path``: add it to the model."""
        self.flush()
        if self._project is None:
            return
        parent = path.rsplit("/", 1)[0] or None
        node = tr.Node(path, self.dir / self.base_of(path), parent)
        self._project.nodes[path] = node
        if parent is None:
            self._project.roots.append(path)
        else:
            self._project.nodes[parent].children.append(path)
        node.op_type = tr.op_type_for(node.family, node.n.get("type", ""))

    def node(self, path: Any) -> Optional[tr.Node]:
        if not isinstance(path, str) or not path.strip("/"):
            return None
        return self.project.node(path)

    def need(self, edit: Dict[str, Any], key: str = "path") -> tr.Node:
        path = edit.get(key)
        if not isinstance(path, str) or not path.strip():
            raise ToeEditError("`%s` is required: the operator's full path inside the file" % key)
        if any(ch in path for ch in "*?["):
            raise ToeEditError("`%s` must name one operator, not a pattern: %s" % (key, path))
        node = self.node(path)
        if node is None:
            import difflib
            close = difflib.get_close_matches("/" + path.strip("/"), list(self.project.nodes), n=5, cutoff=0.5)
            raise ToeEditError("no operator at %s" % path, did_you_mean=close,
                               roots=list(self.project.roots))
        return node

    def parm_file(self, node: tr.Node) -> ParmFile:
        pf = self._parms.get(node.path)
        if pf is None:
            pf = self._parms[node.path] = ParmFile(self.dir / (self.base_of(node.path) + ".parm"))
        return pf

    def node_file(self, node_or_path: Any) -> NodeFile:
        path = node_or_path if isinstance(node_or_path, str) else node_or_path.path
        nf = self._nodes.get(path)
        if nf is None:
            nf = self._nodes[path] = NodeFile(self.dir / (self.base_of(path) + ".n"))
        return nf

    def flush(self) -> None:
        """Write edited `.parm` / `.n` models to the tree."""
        for path, pf in self._parms.items():
            if not pf.dirty:
                continue
            base = self.base_of(path)
            if pf.records:
                _write(pf.path, pf.render())
                self.ensure_file(base, ".parm")
            elif pf.exists:
                # Every line removed: TouchDesigner still lists an empty file
                # for an operator that had one, so keep it listed.
                _write(pf.path, pf.render())
            pf.exists = pf.path.is_file()
            pf.dirty = False
        for nf in self._nodes.values():
            if nf.dirty and nf.path is not None:
                _write(nf.path, nf.render())
                nf.dirty = False

    def forget(self) -> None:
        """Drop cached file models (after files were renamed or removed)."""
        self.flush()
        self._parms.clear()
        self._nodes.clear()
        self.invalidate()

    # -- consistency -----------------------------------------------------------
    def check_tree(self) -> None:
        """`.toc` and tree must agree: an unlisted file would vanish silently."""
        self.flush()
        listed = set(self.toc)
        if len(listed) != len(self.toc):
            dup = sorted({e for e in self.toc if self.toc.count(e) > 1})
            raise ToeEditError("internal: duplicate table-of-contents entries: %s" % dup[:5])
        on_disk = set()
        for folder, _dirs, files in os.walk(self.dir):
            for f in files:
                on_disk.add(Path(folder, f).relative_to(self.dir).as_posix())
        missing = sorted(listed - on_disk)
        extra = sorted(on_disk - listed)
        if missing or extra:
            raise ToeEditError("internal: table of contents and tree disagree",
                               listed_but_missing=missing[:8], present_but_unlisted=extra[:8])

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


# --------------------------------------------------------------------------
# What a parameter is (catalog, custom definitions, the file itself)
# --------------------------------------------------------------------------
def _type_info(node: tr.Node) -> Optional[Dict[str, Any]]:
    cat = tr._catalog()
    return cat.op_info(node.op_type) if cat is not None else None


def _custom_specs(node: tr.Node) -> Dict[str, Dict[str, Any]]:
    """Custom parameters of a COMP by component name (``Posx``), with style,
    menu and position."""
    out: Dict[str, Dict[str, Any]] = {}
    for index, p in enumerate(node.cparms["pars"]):
        spec = {"style": p.get("style"), "custom": True, "index": index, "group": p["name"]}
        if p.get("menu"):
            spec["menu"] = p["menu"]
        for name in p.get("names") or [p["name"]]:
            out[name] = spec
    return out


def _block_of(name: str, known: Dict[str, Any]) -> Optional[Tuple[str, int, Dict[str, Any]]]:
    """``const3value`` -> ``('const', 3, spec of const0value)`` for a sequence
    parameter of a later block, else None."""
    m = re.fullmatch(r"(.*?)(\d+)(\D*)", name)
    if not m:
        return None
    spec = known.get("%s0%s" % (m.group(1), m.group(3)))
    if not spec or not spec.get("sequence"):
        return None
    return spec["sequence"], int(m.group(2)), spec


def _block_name(name0: str, index: int) -> str:
    """``const0name`` -> ``const2name`` (the block number is the last digit run)."""
    m = re.fullmatch(r"(.*?)0(\D*)", name0)
    return "%s%d%s" % (m.group(1), index, m.group(2)) if m else name0


class _Pars:
    """Everything known about one operator's parameters, for validation."""

    def __init__(self, ws: Workspace, node: tr.Node):
        self.node = node
        self.info = _type_info(node)
        self.known: Dict[str, Any] = (self.info or {}).get("by_name") or {}
        self.custom = _custom_specs(node) if node.is_comp else {}
        self.file = ws.parm_file(node)
        self._rank: Dict[str, int] = {}
        for i, p in enumerate((self.info or {}).get("pars") or []):
            self._rank[p["name"]] = i * 1000
        base = (len(self._rank) + 1) * 1000
        for name, spec in self.custom.items():
            self._rank.setdefault(name, base + spec["index"] * 10)

    def rank(self, name: Optional[str]) -> int:
        if name is None:
            return -1
        if name in self._rank:
            return self._rank[name]
        block = _block_of(name, self.known)
        if block is not None:
            _seq, index, spec = block
            return self._rank.get(spec["name"], 0) + min(index, 999)
        return -1

    def spec(self, name: str) -> Optional[Dict[str, Any]]:
        if name in self.known:
            return self.known[name]
        block = _block_of(name, self.known)
        if block is not None:
            return block[2]
        return self.custom.get(name)

    def is_custom(self, name: str) -> bool:
        return name in self.custom or (name not in self.known and name[:1].isupper())

    def tuplets(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for p in (self.info or {}).get("pars") or []:
            if p.get("tuplet"):
                out.setdefault(p["tuplet"], []).append(p["name"])
        groups: Dict[str, List[str]] = {}
        for name, spec in self.custom.items():
            groups.setdefault(spec["group"], []).append(name)
        for group, names in groups.items():
            if len(names) > 1:
                out.setdefault(group, names)
        return out

    def suggestions(self, name: str) -> List[str]:
        import difflib
        cat = tr._catalog()
        out = list(cat.suggest(self.node.op_type, name, 5)) if cat is not None and self.info else []
        pool = list(self.custom) + self.file.names()
        for hit in difflib.get_close_matches(name, pool, n=4, cutoff=0.6):
            if hit not in out:
                out.append(hit)
        return out[:6]

    def block_count(self, seq: str) -> int:
        """Blocks of sequence ``seq`` the file holds (at least one)."""
        top = 0
        for name in self.file.names():
            block = _block_of(name, self.known)
            if block is not None and block[0] == seq:
                top = max(top, block[1])
        return top + 1


def _default_text(spec: Optional[Dict[str, Any]]) -> str:
    """The constant a new expression/bind line carries (TouchDesigner writes
    the value the parameter had: its default)."""
    if not spec:
        return "0"
    style = spec.get("style")
    default = spec.get("default")
    if style == "Toggle":
        return "on" if default in (True, 1, "on") else "off"
    if default is None:
        if style in ("Menu", "StrMenu") and spec.get("menu"):
            return str(spec["menu"][0])
        return "0" if style in _NUMERIC_STYLES else ""
    if isinstance(default, (int, float)):
        return format_number(default)
    return str(default)


def _constant_text(name: str, value: Any, spec: Optional[Dict[str, Any]],
                   notes: List[str], force: bool) -> str:
    """A tool-call value as the text a `.parm` constant holds."""
    style = (spec or {}).get("style")
    if style in _NO_VALUE_STYLES:
        raise ToeEditError("%s is a %s parameter: it has no value to store" % (name, style))
    if isinstance(value, (list, tuple, dict)):
        raise ToeEditError("%s takes one value (got %s); a tuplet takes a list under the "
                           "tuplet's own name" % (name, type(value).__name__))
    if style == "Toggle":
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("on", "1", "true", "yes"):
                return "on"
            if low in ("off", "0", "false", "no"):
                return "off"
            raise ToeEditError("%s is a toggle: pass true or false, not %r" % (name, value))
        return "on" if value else "off"
    if style in ("Menu", "StrMenu"):
        menu = list((spec or {}).get("menu") or [])
        if isinstance(value, bool):
            value = int(value)
        if isinstance(value, int) and menu:
            if not 0 <= value < len(menu):
                raise ToeEditError("%s: menu index %d is outside 0..%d" % (name, value, len(menu) - 1),
                                   valid=menu[:40])
            return str(menu[value])
        text = _cell_text(value)
        if style == "Menu" and menu and text not in menu and not force:
            import difflib
            raise ToeEditError(
                "%s: %r is not one of this menu's tokens (TouchDesigner would silently reset the "
                "menu to its first item)" % (name, text), valid=menu[:40],
                did_you_mean=difflib.get_close_matches(text, menu, n=3, cutoff=0.5))
        return text
    if style in _NUMERIC_STYLES:
        if isinstance(value, str):
            try:
                value = float(value) if not re.fullmatch(r"-?\d+", value.strip()) else int(value)
            except ValueError:
                raise ToeEditError("%s is numeric: %r is not a number. For an expression pass "
                                   "{\"expr\": \"...\"}" % (name, value)) from None
        number = float(value)
        if style == "Int" and number != int(number):
            raise ToeEditError("%s is an integer parameter: %r is not whole" % (name, value))
        clamp = (spec or {}).get("clamp") or [None, None]
        lo, hi = (list(clamp) + [None, None])[:2]
        if lo is not None and number < lo:
            notes.append("%s clamped %s -> %s (the parameter's minimum)" % (name, format_number(value), format_number(lo)))
            value = lo
        elif hi is not None and number > hi:
            notes.append("%s clamped %s -> %s (the parameter's maximum)" % (name, format_number(value), format_number(hi)))
            value = hi
        if style == "Int":
            value = int(float(value))
        return format_number(value)
    return _cell_text(value)


# --------------------------------------------------------------------------
# Edit operations. Each takes the workspace and one edit and returns a receipt.
# --------------------------------------------------------------------------
def _expand_tuplets(pars: _Pars, params: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    tuplets = None
    for name, value in params.items():
        if isinstance(value, (list, tuple)):
            if tuplets is None:
                tuplets = pars.tuplets()
            members = tuplets.get(name)
            if not members:
                raise ToeEditError("%s got a list, but it is not a tuplet of %s"
                                   % (name, pars.node.op_type), tuplets=sorted(tuplets)[:30])
            if len(value) > len(members):
                raise ToeEditError("%s has %d components (%s); got %d values"
                                   % (name, len(members), ", ".join(members), len(value)))
            for member, v in zip(members, value):
                if v is not None:
                    out[member] = v
        else:
            out[name] = value
    return out


def _e_set_params(ws: Workspace, e: Dict[str, Any], force: bool) -> Dict[str, Any]:
    node = ws.need(e)
    params = e.get("params") if e.get("params") is not None else e.get("parameters")
    if not isinstance(params, dict) or not params:
        raise ToeEditError("`params` must be an object {name: value | {\"expr\": ...} | {\"bind\": ...}}")
    pars = _Pars(ws, node)
    if pars.file.legacy:
        raise ToeEditError("%s stores its parameters in the pre-099 grammar; open and save it in "
                           "TouchDesigner once, then edit" % node.path)
    notes: List[str] = []
    written: Dict[str, Any] = {}
    grown: Dict[str, List[int]] = {}
    for name, value in _expand_tuplets(pars, params).items():
        if not isinstance(name, str) or not name:
            raise ToeEditError("parameter names must be strings")
        spec = pars.spec(name)
        existing = pars.file.get(name)
        if spec is None and existing is None and not force:
            raise ToeEditError(
                "%s has no parameter %r" % (node.op_type, name), did_you_mean=pars.suggestions(name),
                hint="td_describe_op_type lists the exact names; pass force=true only for a "
                     "name this build's catalog does not know")
        block = _block_of(name, pars.known)
        if block is not None:
            seq, index, _spec0 = block
            have = pars.block_count(seq)
            if index >= have:
                if index - have + 1 > MAX_SEQUENCE_GROWTH:
                    raise ToeEditError("%s would grow sequence %r from %d to %d blocks; more than "
                                       "%d at once is refused" % (name, seq, have, index + 1,
                                                                 MAX_SEQUENCE_GROWTH))
                grown[seq] = [have, max(index + 1, grown.get(seq, [0, 0])[1])]
        if value is None or (isinstance(value, dict) and value.get("default") is True):
            if pars.file.remove(name):
                written[name] = "<default>"
            else:
                notes.append("%s was already at its default" % name)
            continue
        rec = dict(existing) if existing is not None else {
            "name": name, "flags": CUSTOM_BASE_FLAGS if pars.is_custom(name) else 0,
            "value": _default_text(spec)}
        flags = int(rec["flags"])
        if isinstance(value, dict):
            unknown = set(value) - {"expr", "bind", "mode", "val", "value"}
            if unknown:
                raise ToeEditError("%s: unknown key(s) %s. Use {\"expr\": ...}, {\"bind\": ...} or "
                                   "{\"mode\": \"constant\", \"val\": ...}" % (name, sorted(unknown)))
            style = (spec or {}).get("style")
            if style in _NO_VALUE_STYLES:
                raise ToeEditError("%s is a %s parameter: it has no value to store" % (name, style))
            constant = value.get("val", value.get("value"))
            if constant is not None:
                rec["value"] = _constant_text(name, constant, spec, notes, force)
            if "expr" in value:
                if not isinstance(value["expr"], str) or not value["expr"].strip():
                    raise ToeEditError("%s: `expr` must be a non-empty string" % name)
                rec["expr"] = value["expr"]
                flags = (flags & ~3) | MODE_BITS["expression"] | tr.FLAG_EXPRESSION
                written[name] = {"expr": value["expr"]}
            elif "bind" in value:
                if not isinstance(value["bind"], str) or not value["bind"].strip():
                    raise ToeEditError("%s: `bind` must be a non-empty string" % name)
                rec["bind"] = value["bind"]
                if flags & tr.FLAG_EXTRA_VALUE:
                    rec["extra"] = value["bind"]
                flags = (flags & ~3) | MODE_BITS["bind"] | tr.FLAG_BIND
                written[name] = {"bind": value["bind"]}
            else:
                mode = value.get("mode", "constant")
                if mode not in ("constant", "expression", "bind"):
                    raise ToeEditError("%s: mode must be constant, expression or bind" % name)
                if mode == "expression" and not flags & tr.FLAG_EXPRESSION:
                    raise ToeEditError("%s has no stored expression to switch to; pass {\"expr\": ...}" % name)
                if mode == "bind" and not flags & tr.FLAG_BIND:
                    raise ToeEditError("%s has no stored bind to switch to; pass {\"bind\": ...}" % name)
                flags = (flags & ~3) | MODE_BITS[mode]
                written[name] = {"mode": mode, "val": rec["value"]} if mode == "constant" else {"mode": mode}
            mode_arg = value.get("mode")
            if mode_arg and ("expr" in value or "bind" in value) and mode_arg == "constant":
                flags &= ~3  # store the text, stay constant
                written[name]["mode"] = "constant"
        else:
            was = tr.PAR_MODES[flags & 3]
            rec["value"] = _constant_text(name, value, spec, notes, force)
            flags &= ~3
            written[name] = rec["value"]
            if was == "expression":
                notes.append("%s was an expression; it is now the constant %s and the text "
                             "is kept, inactive" % (name, rec["value"]))
            elif was == "bind":
                notes.append("%s was bound; in the file a constant replaces the bind (a live "
                             "write would have gone through to the bind master)" % name)
            elif was == "export":
                notes.append("%s was driven by a CHOP export; the constant is what it falls "
                             "back to, and the export will take over again when it cooks" % name)
                flags |= MODE_BITS["export"]
        rec["flags"] = flags
        pars.file.put(rec)
        if existing is None:
            _reposition(pars, name)
    out: Dict[str, Any] = {"op": "set_params", "path": node.path, "type": node.op_type, "set": written}
    if grown:
        out["sequences_grown"] = {k: {"from": v[0], "to": v[1]} for k, v in grown.items()}
        for seq, (have, top) in grown.items():
            # Measured: a Constant CHOP given only const3name/const3value opens
            # with four blocks, and the skipped block 2 is named 'chan1' -- the
            # block-0 default -- so two channels share a name.
            written_blocks = {b[1] for b in (_block_of(n, pars.known) for n in written) if b and b[0] == seq}
            skipped = [i for i in range(have, top) if i not in written_blocks]
            names = sorted(p["name"] for p in pars.known.values()
                           if p.get("sequence") == seq and p.get("style") == "Str"
                           and p.get("default") not in (None, ""))
            msg = ("naming block %d grows sequence %r from %d to %d blocks when TouchDesigner opens "
                   "the file; parameters not written here take their block-0 defaults"
                   % (top - 1, seq, have, top))
            if skipped:
                msg += " -- block%s %s %s left entirely at defaults" % (
                    "s" if len(skipped) > 1 else "", ", ".join(map(str, skipped)),
                    "are" if len(skipped) > 1 else "is")
                if names:
                    msg += (", so %s would repeat the default %r: set it in the same call"
                            % (", ".join(_block_name(n, skipped[0]) for n in names),
                               pars.known[names[0]].get("default")))
            notes.append(msg)
    if notes:
        out["notes"] = notes
    ws.refresh(node.path)
    return out


def _reposition(pars: _Pars, name: str) -> None:
    """Move a newly appended record to where the operator's parameter order
    puts it (only when every record around it has a known place)."""
    records = pars.file.records
    if not records or records[-1].get("name") != name or "raw" in records[-1]:
        return
    rank = pars.rank(name)
    if rank < 0:
        return
    rec = records.pop()
    for i, old in enumerate(records):
        if pars.rank(old.get("name")) > rank:
            records.insert(i, rec)
            return
    records.append(rec)


def _dat_text_target(ws: Workspace, node: tr.Node, creating: bool = True) -> Path:
    if node.family != "DAT":
        raise ToeEditError("%s is a %s, not a DAT: it holds no text" % (node.path, node.op_type))
    if node.n.get("flags", {}).get("lock"):
        raise ToeEditError("%s is locked: its content is frozen in a separate file this editor "
                           "does not write. Unlock it in TouchDesigner, save, then edit" % node.path)
    base = ws.dir / ws.base_of(node.path)
    text_file = base.with_name(base.name + ".text")
    if text_file.is_file():
        return text_file
    if base.with_name(base.name + ".table").is_file():
        raise ToeEditError("%s is a table: use set_table or set_cells" % node.path)
    if node.n.get("type") != "text" or not creating:
        raise ToeEditError("%s (%s) stores no text in the file -- its content is computed or "
                           "comes from its inputs" % (node.path, node.op_type))
    return text_file


def _file_sync_note(ws: Workspace, node: tr.Node) -> Optional[str]:
    for p in ws.parm_file(node).records:
        if p.get("name") == "file" and (p.get("value") or p.get("expr")):
            return ("%s loads from %s; with Sync to File or Load on Start on, TouchDesigner "
                    "replaces this edit with the file's content" % (node.name, p.get("value") or p.get("expr")))
    return None


def _e_set_text(ws: Workspace, e: Dict[str, Any], force: bool) -> Dict[str, Any]:
    node = ws.need(e)
    text = e.get("text")
    if not isinstance(text, str):
        raise ToeEditError("`text` must be a string")
    target = _dat_text_target(ws, node)
    text = text.replace("\r\n", "\n")
    before = tr.read_text_payload(target.read_bytes()) if target.is_file() else ""
    target.write_bytes(text_payload(text))
    ws.ensure_file(ws.base_of(node.path), ".text")
    ws.refresh(node.path)
    out = {"op": "set_text", "path": node.path, "lines": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
           "was_lines": before.count("\n") + (1 if before and not before.endswith("\n") else 0)}
    note = _file_sync_note(ws, node)
    if note:
        out["notes"] = [note]
    return out


def _e_replace_text(ws: Workspace, e: Dict[str, Any], force: bool) -> Dict[str, Any]:
    node = ws.need(e)
    old, new = e.get("old"), e.get("new")
    if not isinstance(old, str) or not old or not isinstance(new, str):
        raise ToeEditError("`old` (non-empty) and `new` must be strings")
    target = _dat_text_target(ws, node, creating=False)
    body = tr.read_text_payload(target.read_bytes())
    found = body.count(old)
    want = e.get("count", 1)
    if found == 0:
        raise ToeEditError("`old` does not occur in %s" % node.path,
                           hint="read the text with td_project_read(text='full') and copy it exactly")
    if want and found != int(want):
        raise ToeEditError("`old` occurs %d time(s) in %s, expected %d; add context to make it "
                           "unique or pass count=0 for every occurrence" % (found, node.path, int(want)))
    target.write_bytes(text_payload(body.replace(old, new)))
    ws.refresh(node.path)
    out = {"op": "replace_text", "path": node.path, "replaced": found}
    note = _file_sync_note(ws, node)
    if note:
        out["notes"] = [note]
    return out


def _table_target(ws: Workspace, node: tr.Node) -> Path:
    if node.family != "DAT":
        raise ToeEditError("%s is a %s, not a DAT" % (node.path, node.op_type))
    if node.n.get("flags", {}).get("lock"):
        raise ToeEditError("%s is locked: unlock it in TouchDesigner, save, then edit" % node.path)
    base = ws.dir / ws.base_of(node.path)
    table_file = base.with_name(base.name + ".table")
    if not table_file.is_file() and node.n.get("type") != "table":
        raise ToeEditError("%s (%s) stores no table in the file" % (node.path, node.op_type))
    return table_file


def _e_set_table(ws: Workspace, e: Dict[str, Any], force: bool) -> Dict[str, Any]:
    node = ws.need(e)
    rows = e.get("rows")
    if not isinstance(rows, list) or any(not isinstance(r, (list, tuple)) for r in rows):
        raise ToeEditError("`rows` must be a list of rows, each a list of cells")
    target = _table_target(ws, node)
    cells = [[_cell_text(c) for c in row] for row in rows]
    target.write_bytes(table_payload(cells))
    ws.ensure_file(ws.base_of(node.path), ".table")
    ws.refresh(node.path)
    return {"op": "set_table", "path": node.path,
            "shape": [len(cells), max((len(r) for r in cells), default=0)]}


def _e_set_cells(ws: Workspace, e: Dict[str, Any], force: bool) -> Dict[str, Any]:
    node = ws.need(e)
    cells = e.get("cells")
    if not isinstance(cells, list) or not cells:
        raise ToeEditError("`cells` must be a list of {row, col, value}")
    target = _table_target(ws, node)
    table = tr.read_table_payload(target.read_bytes()) if target.is_file() else []
    if not table:
        raise ToeEditError("%s is empty: use set_table" % node.path)

    def index(key, labels, what):
        if isinstance(key, bool) or not isinstance(key, (int, str)):
            raise ToeEditError("%s must be an index or a header name" % what)
        if isinstance(key, int):
            if not 0 <= key < len(labels):
                raise ToeEditError("%s %d is outside 0..%d" % (what, key, len(labels) - 1))
            return key
        if key not in labels:
            raise ToeEditError("no %s named %r" % (what, key), have=labels[:30])
        return labels.index(key)

    changed = []
    for c in cells:
        if not isinstance(c, dict) or "value" not in c:
            raise ToeEditError("each cell is {row, col, value}")
        r = index(c.get("row"), [row[0] for row in table], "row")
        k = index(c.get("col"), list(table[0]), "col")
        table[r][k] = _cell_text(c["value"])
        changed.append([r, k])
    target.write_bytes(table_payload(table))
    ws.refresh(node.path)
    return {"op": "set_cells", "path": node.path, "changed": changed}


def _scoped(ws: Workspace, path: Optional[str]) -> List[tr.Node]:
    if not path or path == "/":
        return list(ws.project.nodes.values())
    root = ws.node(path)
    if root is None:
        raise ToeEditError("no operator at %s" % path, roots=list(ws.project.roots))
    prefix = root.path + "/"
    return [n for n in ws.project.nodes.values() if n.path == root.path or n.path.startswith(prefix)]


def _e_replace(ws: Workspace, e: Dict[str, Any], force: bool) -> Dict[str, Any]:
    pattern, replacement = e.get("pattern"), e.get("replacement")
    if not isinstance(pattern, str) or not pattern or not isinstance(replacement, str):
        raise ToeEditError("`pattern` (non-empty) and `replacement` must be strings")
    scope = e.get("scope", "all")
    scopes = set(scope if isinstance(scope, list) else [scope])
    valid = {"all", "text", "values", "expressions", "binds", "tables", "comments"}
    if not scopes <= valid:
        raise ToeEditError("scope must be among %s" % sorted(valid))
    everything = "all" in scopes
    flags = re.IGNORECASE if e.get("ignore_case") else 0
    try:
        rx = re.compile(pattern if e.get("regex") else re.escape(pattern), flags)
    except re.error as exc:
        raise ToeEditError("bad regular expression: %s" % exc) from None
    repl = replacement if e.get("regex") else replacement.replace("\\", "\\\\")
    per_node: Dict[str, int] = {}
    skipped: List[str] = []
    total = 0

    def sub(text):
        try:
            return rx.subn(repl, text)
        except re.error as exc:
            raise ToeEditError("bad replacement: %s" % exc) from None

    for node in _scoped(ws, e.get("path")):
        count = 0
        locked = bool(node.n.get("flags", {}).get("lock"))
        base = ws.dir / ws.base_of(node.path)
        if (everything or "text" in scopes) and node.family == "DAT":
            f = base.with_name(base.name + ".text")
            if f.is_file():
                body = tr.read_text_payload(f.read_bytes())
                new, k = sub(body)
                if k and locked:
                    skipped.append(node.path)
                elif k:
                    f.write_bytes(text_payload(new))
                    count += k
        if (everything or "tables" in scopes) and node.family == "DAT":
            f = base.with_name(base.name + ".table")
            if f.is_file():
                table = tr.read_table_payload(f.read_bytes())
                k_total = 0
                for row in table:
                    for i, cell in enumerate(row):
                        row[i], k = sub(cell)
                        k_total += k
                if k_total and locked:
                    skipped.append(node.path)
                elif k_total:
                    f.write_bytes(table_payload(table))
                    count += k_total
        fields = [key for key, name in (("value", "values"), ("expr", "expressions"), ("bind", "binds"))
                  if everything or name in scopes]
        if fields:
            pf = ws.parm_file(node)
            if not pf.legacy:
                for rec in list(pf.records):
                    if rec.get("name") is None or tr._UI_STATE_PAR.match(rec["name"]):
                        continue
                    new_rec, hit = dict(rec), 0
                    for key in fields:
                        if isinstance(rec.get(key), str) and rec.get(key):
                            new_rec[key], k = sub(rec[key])
                            hit += k
                            if key == "bind" and k and rec.get("extra") == rec.get("bind"):
                                new_rec["extra"] = new_rec["bind"]
                    if hit:
                        pf.put(new_rec)
                        count += hit
        if (everything or "comments" in scopes) and node.n.get("comment"):
            new, k = sub(node.n["comment"])
            if k:
                ws.node_file(node).set_comment(new)
                count += k
        if count:
            per_node[node.path] = count
            total += count
    if not total and not e.get("allow_none"):
        raise ToeEditError("nothing matched %r in scope %s" % (pattern, sorted(scopes)),
                           hint="td_project_grep shows where a pattern occurs; pass allow_none=true "
                                "to make no-match a no-op", skipped_locked=skipped[:10])
    for touched in per_node:
        ws.refresh(touched)
    out: Dict[str, Any] = {"op": "replace", "replaced": total, "operators": len(per_node),
                           "per_operator": dict(list(per_node.items())[:60])}
    if skipped:
        out["skipped_locked"] = skipped[:20]
    return out


def _norm_path(value: str) -> str:
    return value.replace("\\", "/")


def _is_file_par(name: str, spec: Optional[Dict[str, Any]]) -> bool:
    return name in tr._FILE_PARS or (spec or {}).get("style") in _FILE_STYLES


def _e_relink(ws: Workspace, e: Dict[str, Any], force: bool) -> Dict[str, Any]:
    src, dst = e.get("from"), e.get("to")
    if not isinstance(src, str) or not src.strip() or not isinstance(dst, str):
        raise ToeEditError("`from` (a folder or file prefix) and `to` must be strings")
    old = _norm_path(src).rstrip("/")
    new = _norm_path(dst).rstrip("/")
    case = bool(e.get("case_sensitive"))
    changed: List[Dict[str, str]] = []
    in_expressions = 0
    key = old if case else old.lower()
    for node in _scoped(ws, e.get("path")):
        pars = None
        pf = ws.parm_file(node)
        if pf.legacy:
            continue
        for rec in list(pf.records):
            name = rec.get("name")
            if name is None or not isinstance(rec.get("value"), str):
                continue
            for field in ("expr", "bind"):
                text = rec.get(field)
                if isinstance(text, str) and (old in _norm_path(text) if case
                                              else key in _norm_path(text).lower()):
                    in_expressions += 1
            if tr.PAR_MODES[rec["flags"] & 3] != "constant" or not rec["value"]:
                continue
            value = _norm_path(rec["value"])
            probe = value if case else value.lower()
            if not (probe == key or probe.startswith(key + "/")):
                continue
            if pars is None:
                pars = _Pars(ws, node)
            if not _is_file_par(name, pars.spec(name)) and not e.get("any_parameter"):
                continue
            rest = value[len(old):]
            new_value = (new + rest) if new else rest.lstrip("/")
            new_rec = dict(rec)
            new_rec["value"] = new_value
            pf.put(new_rec)
            changed.append({"path": node.path, "par": name, "from": rec["value"], "to": new_value})
    if not changed and not e.get("allow_none"):
        raise ToeEditError("no file parameter starts with %s" % src,
                           hint="td_project_read's summary lists external_files; "
                                "td_project_check lists the missing ones",
                           found_in_expressions=in_expressions)
    for touched in {c["path"] for c in changed}:
        ws.refresh(touched)
    out: Dict[str, Any] = {"op": "relink", "changed": len(changed), "parameters": changed[:60]}
    if in_expressions:
        out["notes"] = ["%d expression(s) also mention %s and were left alone; use replace "
                        "with scope='expressions' to change them" % (in_expressions, src)]
    return out


def _flag_pairs(flags: Dict[str, Any], node: tr.Node) -> List[Tuple[str, Optional[str]]]:
    out = []
    for name, value in flags.items():
        if name == "lock":
            raise ToeEditError(
                "lock cannot be changed offline: a locked operator's frozen output is stored in a "
                "separate file this editor does not write. Lock or unlock it in TouchDesigner")
        if name not in _FLAGS:
            raise ToeEditError("unknown flag %r" % name, valid=sorted(set(_FLAGS) - {"allowCooking", "cloneImmune"}))
        if not isinstance(value, bool):
            raise ToeEditError("flag %s takes true or false" % name)
        key, on, off = _FLAGS[name]
        if key == "cook" and not node.is_comp:
            raise ToeEditError("cooking can be switched off on a COMP only; %s is a %s (use bypass)"
                               % (node.path, node.op_type))
        if key in ("display", "render") and node.family not in ("SOP", "POP", "COMP"):
            raise ToeEditError("%s has no %s flag (%s)" % (node.path, key, node.op_type))
        out.append((key, on if value else off))
    return out


def _e_set_flags(ws: Workspace, e: Dict[str, Any], force: bool) -> Dict[str, Any]:
    node = ws.need(e)
    flags = e.get("flags")
    if not isinstance(flags, dict) or not flags:
        raise ToeEditError("`flags` must be an object, e.g. {\"bypass\": true}")
    nf = ws.node_file(node)
    for key, value in _flag_pairs(flags, node):
        nf.set_flag(key, value)
    ws.refresh(node.path)
    return {"op": "set_flags", "path": node.path, "flags": dict(flags)}


def _e_set_node(ws: Workspace, e: Dict[str, Any], force: bool) -> Dict[str, Any]:
    node = ws.need(e)
    nf = ws.node_file(node)
    done = {}
    if e.get("position") is not None:
        pos = e["position"]
        if not isinstance(pos, (list, tuple)) or len(pos) != 2:
            raise ToeEditError("`position` is [x, y] in network units")
        nf.set_tile(int(pos[0]), int(pos[1]))
        done["position"] = [int(pos[0]), int(pos[1])]
    if e.get("color") is not None:
        rgb = e["color"]
        if not isinstance(rgb, (list, tuple)) or len(rgb) != 3:
            raise ToeEditError("`color` is [r, g, b], each 0..1")
        nf.set_color([float(c) for c in rgb])
        done["color"] = [float(c) for c in rgb]
    if "comment" in e:
        nf.set_comment(e["comment"] or None)
        done["comment"] = e["comment"] or ""
    if "tags" in e:
        tags = e["tags"] or []
        if not isinstance(tags, list) or any(not isinstance(t, str) or not t for t in tags):
            raise ToeEditError("`tags` is a list of non-empty strings")
        nf.set_tags(tags)
        done["tags"] = tags
    if not done:
        raise ToeEditError("set_node takes position, color, comment and/or tags")
    ws.refresh(node.path)
    out = {"op": "set_node", "path": node.path}
    out.update(done)
    return out


def _sibling(ws: Workspace, parent: Optional[str], name: str) -> Optional[tr.Node]:
    return ws.node("%s/%s" % (parent or "", name))


def _wire_endpoints(ws: Workspace, e: Dict[str, Any]) -> Tuple[tr.Node, tr.Node]:
    src, dst = ws.need(e, "from"), ws.need(e, "to")
    if src.parent != dst.parent:
        raise ToeEditError(
            "%s and %s are in different networks; a wire joins siblings only. Put a Select "
            "operator of the right family beside the destination and point it at the source"
            % (src.path, dst.path))
    if src.path == dst.path:
        raise ToeEditError("an operator cannot feed itself")
    if src.is_comp or dst.is_comp:
        raise ToeEditError(
            "wiring a COMP's own connectors offline is not supported (they map to its In/Out "
            "operators by position); wire the operators inside it, or do this one in TouchDesigner")
    if src.family != dst.family:
        raise ToeEditError("%s is a %s and %s a %s: a wire joins operators of one family (convert "
                           "with a 'to' operator, e.g. choptoTOP)" % (src.path, src.family, dst.path, dst.family))
    return src, dst


def _max_inputs(node: tr.Node) -> Optional[int]:
    info = _type_info(node)
    return info.get("max_inputs") if info else None


def _e_connect(ws: Workspace, e: Dict[str, Any], force: bool) -> Dict[str, Any]:
    src, dst = _wire_endpoints(ws, e)
    index = e.get("input", e.get("to_input", 0))
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ToeEditError("`input` is the destination's 0-based input index")
    if e.get("from_output", 0) not in (0, None):
        raise ToeEditError("only output 0 can be wired offline")
    limit = _max_inputs(dst)
    if limit is not None and limit == 0:
        raise ToeEditError("%s (%s) takes no inputs" % (dst.path, dst.op_type))
    if limit is not None and index >= limit:
        raise ToeEditError("%s (%s) has %d input(s): index %d is out of range"
                           % (dst.path, dst.op_type, limit, index))
    nf = ws.node_file(dst)
    wires = dict(nf.inputs())
    replaced = wires.get(index)
    wires[index] = src.name
    nf.set_inputs(wires.items())
    ws.refresh(dst.path)
    out = {"op": "connect", "from": src.path, "to": dst.path, "input": index}
    if replaced and replaced != src.name:
        out["replaced"] = "%s/%s" % (dst.parent or "", replaced)
    return out


def _e_disconnect(ws: Workspace, e: Dict[str, Any], force: bool) -> Dict[str, Any]:
    dst = ws.need(e, "to")
    nf = ws.node_file(dst)
    wires = dict(nf.inputs())
    if e.get("from"):
        src = ws.need(e, "from")
        hits = [i for i, s in wires.items() if s.partition("/")[0] == src.name]
    else:
        index = e.get("input", e.get("to_input"))
        if not isinstance(index, int) or isinstance(index, bool):
            raise ToeEditError("disconnect takes `to` plus `input` (index) or `from`")
        hits = [index] if index in wires else []
    if not hits:
        raise ToeEditError("no such wire into %s" % dst.path,
                           inputs={str(i): s for i, s in sorted(wires.items())})
    removed = {i: wires.pop(i) for i in hits}
    nf.set_inputs(wires.items())
    ws.refresh(dst.path)
    return {"op": "disconnect", "to": dst.path,
            "removed": {str(i): "%s/%s" % (dst.parent or "", s) for i, s in removed.items()}}


def _check_new_name(ws: Workspace, parent: Optional[str], name: Any) -> str:
    if not isinstance(name, str) or not _NAME.match(name):
        raise ToeEditError("not a usable operator name: %r (letters, digits and underscore, "
                           "not starting with a digit)" % (name,))
    if _sibling(ws, parent, name) is not None:
        raise ToeEditError("%s/%s already exists" % (parent or "", name))
    return name


def _siblings(ws: Workspace, node: tr.Node) -> List[tr.Node]:
    return [n for n in ws.project.children(node.parent) if n.path != node.path]


def _rewrite_network_file(path: Path, old: str, new: Optional[str], inner: bool) -> int:
    """Rename (or blank, ``new=None``) a name inside a COMP's `.network` file.

    ``inner`` False: ``old`` is a sibling source feeding the COMP
    (``index<tab>source``). ``inner`` True: ``old`` is one of the COMP's own In
    operators (``<tab>name`` on the line after the source).
    """
    if not path.is_file():
        return 0
    lines = _read(path).split("\n")
    hits = 0
    for i, line in enumerate(lines):
        index, tab, rest = line.partition("\t")
        if not tab:
            continue
        if not inner and index.strip().isdigit():
            head, slash, via = rest.strip().partition("/")
            if head == old:
                lines[i] = "%s\t%s" % (index, (new + slash + via) if new else '""')
                hits += 1
        elif inner and not index.strip() and rest.strip() == old and new:
            lines[i] = "\t" + new
            hits += 1
    if hits:
        _write(path, "\n".join(lines))
    return hits


def _e_rename(ws: Workspace, e: Dict[str, Any], force: bool) -> Dict[str, Any]:
    node = ws.need(e)
    if node.parent is None:
        raise ToeEditError("%s is a root of the file and cannot be renamed offline" % node.path)
    new = _check_new_name(ws, node.parent, e.get("name"))
    old = node.name
    parent_base = ws.base_of(node.parent)
    old_base, new_base = "%s/%s" % (parent_base, old), "%s/%s" % (parent_base, new)
    siblings = _siblings(ws, node)
    is_in_or_out = node.n.get("type") in ("in", "out") and node.family != "COMP"
    grand_siblings = ws.project.children(ws.project.nodes[node.parent].parent) if is_in_or_out else []
    rewired, docked = [], []
    for sib in siblings:
        nf = ws.node_file(sib)
        wires = nf.inputs()
        fixed = [(i, new + s[len(old):] if s.partition("/")[0] == old else s) for i, s in wires]
        if fixed != wires:
            nf.set_inputs(fixed)
            rewired.append(sib.path)
        if nf.dock() == old:
            nf.set_dock(new)
            docked.append(sib.path)
    ws.forget()
    for sib in siblings:
        if sib.is_comp and _rewrite_network_file(
                ws.dir / (ws.base_of(sib.path) + ".network"), old, new, inner=False):
            rewired.append(sib.path)
    if is_in_or_out:
        _rewrite_network_file(ws.dir / (parent_base + ".network"), old, new, inner=True)
        parent_name = node.parent.rsplit("/", 1)[-1]
        for other in grand_siblings:
            nf = ws.node_file(other)
            wires = nf.inputs()
            fixed = [(i, "%s/%s" % (parent_name, new) if s == "%s/%s" % (parent_name, old) else s)
                     for i, s in wires]
            if fixed != wires:
                nf.set_inputs(fixed)
                rewired.append(other.path)
        ws.flush()
    for entry in ws.own_files(old_base):
        ext = entry[len(old_base):]
        (ws.dir / entry).rename(ws.dir / (new_base + ext))
    if (ws.dir / old_base).is_dir():
        (ws.dir / old_base).rename(ws.dir / new_base)
    ws.toc = [new_base + t[len(old_base):] if (t.startswith(old_base + "/") or (
        t.startswith(old_base + ".") and "/" not in t[len(old_base):])) else t for t in ws.toc]
    ws.forget()
    # What still says the old name: expressions, parameters and code are text.
    hits = tr.grep(ws.project, r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(old),
                   scope="all", ignore_case=False, max_matches=12)
    mentions = [m for m in hits.get("matches", []) if m.get("where") in ("param", "text", "table")]
    out: Dict[str, Any] = {"op": "rename", "path": node.path,
                           "new_path": "%s/%s" % (node.parent, new)}
    if rewired:
        out["rewired"] = sorted(set(rewired))
    if docked:
        out["docked_followed"] = docked
    if mentions:
        out["still_mentions_old_name"] = mentions
        out["notes"] = ["parameters, expressions and code are text and do not follow a rename: "
                        "%d place(s) still say %r -- fix them with the replace edit"
                        % (hits.get("count", len(mentions)), old)]
    return out


def _remove_node_files(ws: Workspace, path: str) -> None:
    base = ws.base_of(path)
    for entry in ws.own_files(base):
        f = ws.dir / entry
        if f.is_file():
            f.unlink()
    shutil.rmtree(ws.dir / base, ignore_errors=True)
    keep = []
    n = len(base)
    for t in ws.toc:
        if t.startswith(base + "/") or (t.startswith(base + ".") and "/" not in t[n:]):
            continue
        keep.append(t)
    ws.toc = keep


def _e_delete(ws: Workspace, e: Dict[str, Any], force: bool) -> Dict[str, Any]:
    node = ws.need(e)
    if node.parent is None:
        raise ToeEditError("%s is a root of the file; deleting it would leave nothing to open" % node.path)
    siblings = _siblings(ws, node)
    victims = [node] + [s for s in siblings if s.n.get("dock") == node.name]
    names = {v.name for v in victims}
    removed_ops = sum(1 + sum(1 for _ in ws.project.walk(v.path)) for v in victims)
    unwired = []
    for sib in siblings:
        if sib.name in names:
            continue
        nf = ws.node_file(sib)
        wires = nf.inputs()
        kept = [(i, s) for i, s in wires if s.partition("/")[0] not in names]
        if kept != wires:
            nf.set_inputs(kept)
            unwired.append(sib.path)
    in_out = [v for v in victims if v.n.get("type") in ("in", "out") and v.family != "COMP"]
    parent = ws.project.nodes[node.parent]
    outer = ws.project.children(parent.parent) if in_out else []
    ws.forget()
    for sib in siblings:
        if sib.is_comp and sib.name not in names:
            for name in names:
                if _rewrite_network_file(ws.dir / (ws.base_of(sib.path) + ".network"),
                                         name, None, inner=False):
                    unwired.append(sib.path)
    notes = []
    for v in in_out:
        # The COMP loses a connector: wires that used it from outside go too.
        target = "%s/%s" % (parent.name, v.name)
        for other in outer:
            nf = ws.node_file(other)
            wires = nf.inputs()
            kept = [(i, s) for i, s in wires if s != target]
            if kept != wires:
                nf.set_inputs(kept)
                unwired.append(other.path)
        _drop_comp_input(ws.dir / (ws.base_of(parent.path) + ".network"), v.name)
        notes.append("%s was a connector of %s: that connector is gone, and the ones after it "
                     "move up by one" % (v.name, parent.path))
    ws.flush()
    for v in victims:
        _remove_node_files(ws, v.path)
    ws.forget()
    out: Dict[str, Any] = {"op": "delete", "path": node.path, "operators_removed": removed_ops}
    if len(victims) > 1:
        out["docked_removed"] = [v.path for v in victims[1:]]
    if unwired:
        out["left_unwired"] = sorted(set(unwired))
        notes.append("the file keeps the gap: TouchDesigner's own delete would have joined a "
                     "single-input chain around the removed operator; use connect to do that")
    if notes:
        out["notes"] = notes
    return out


def _drop_comp_input(path: Path, inner_name: str) -> None:
    """Remove the 3-line entry of In operator ``inner_name`` from a `.network`
    file and renumber the entries after it."""
    if not path.is_file():
        return
    lines = _read(path).split("\n")
    out, i, removed = [], 0, False
    while i < len(lines):
        index, tab, _rest = lines[i].partition("\t")
        if (tab and index.strip().isdigit() and i + 2 < len(lines)
                and lines[i + 1].startswith("\t") and lines[i + 2].startswith("\t")):
            entry = lines[i:i + 3]
            i += 3
            if entry[1].strip() == inner_name:
                removed = True
                continue
            if removed:
                entry[0] = "%d \t%s" % (int(index.strip()) - 1, entry[0].partition("\t")[2])
            out.extend(entry)
            continue
        out.append(lines[i])
        i += 1
    if removed:
        _write(path, "\n".join(out))


def _resolve_type(type_name: Any) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    """``noiseTOP`` -> ``('TOP', 'noise', catalog info)``."""
    if not isinstance(type_name, str) or not type_name.strip():
        raise ToeEditError("`type` is required: a TouchDesigner class name such as noiseTOP")
    cat = tr._catalog()
    if cat is None:
        raise ToeEditError("the parameter catalog is not available, so operator types cannot be "
                           "resolved to their file form")
    info = cat.op_info(type_name.strip())
    if info is None:
        raise ToeEditError("unknown operator type %r" % type_name,
                           did_you_mean=cat.candidates(type_name)[:6],
                           hint="td_docs_search / td_describe_op_type find the exact class name")
    return info["family"], info["type"], info


#: Flags a COMP made by ``COMP.create`` starts with, measured on every COMP
#: type of 2025.33230 (all other operators start with none). Without them a
#: Geometry COMP added to a file would neither render nor display.
_FRESH_RENDER = {"actor", "bsolver", "fbx", "geo", "geotext", "gltfin", "gltfout", "usd"}
_FRESH_DISPLAY = {"blend", "bone", "cam", "camblend", "environment", "handle", "light", "null"}
#: COMPs whose function IS the network TouchDesigner builds inside them at
#: creation (number of operators measured): adding one to a file is refused.
_COMPS_BUILT_BY_TOUCHDESIGNER = {"animation": 8, "annotate": 1, "button": 5, "slider": 5,
                                 "field": 6, "table": 12, "bone": 10, "handle": 11,
                                 "bsolver": 2, "fbx": 5, "gltfin": 6, "usd": 6}
#: 3D objects whose built-in children are only their viewport shape.
_COMP_SHAPES = {"cam": 1, "light": 21, "null": 1, "ambient": 1, "environment": 1,
                "blend": 2, "camblend": 1}
#: DAT types that keep their own text in the file (a fresh one is saved with
#: a `.text`); every other DAT computes its content.
_TEXT_DATS = {"text", "execute", "chopexec", "datexec", "opexec", "panelexec",
              "parexec", "pargroupexec"}


def _default_flags(family: str, short: str, info: Optional[Dict[str, Any]]) -> List[List[str]]:
    """Flags a fresh operator of this type is saved with."""
    if family != "COMP":
        return []
    if short in _FRESH_RENDER:
        return [["render", "on"], ["display", "on"], ["pickable", "on"]]
    if short in _FRESH_DISPLAY:
        return [["display", "on"], ["pickable", "on"]]
    return []


def _next_position(ws: Workspace, parent: str) -> List[int]:
    xs = [(k.n.get("x", 0) + k.n.get("w", 130), k.n.get("y", 0)) for k in ws.project.children(parent)]
    if not xs:
        return [0, 0]
    right = max(xs)
    return [int(right[0]) + 70, 0]


def _e_add(ws: Workspace, e: Dict[str, Any], force: bool) -> Dict[str, Any]:
    parent = ws.need(e, "parent")
    if not parent.is_comp:
        raise ToeEditError("%s is a %s; operators live inside COMPs" % (parent.path, parent.op_type))
    name = _check_new_name(ws, parent.path, e.get("name"))
    family, short, info = _resolve_type(e.get("type"))
    pos = e.get("position") or _next_position(ws, parent.path)
    if not isinstance(pos, (list, tuple)) or len(pos) != 2:
        raise ToeEditError("`position` is [x, y] in network units")
    base = "%s/%s" % (ws.base_of(parent.path), name)
    notes: List[str] = []
    op_type = info["op_type"]
    text, table = e.get("text"), e.get("table") if e.get("table") is not None else e.get("rows")
    if text is not None:
        if not isinstance(text, str):
            raise ToeEditError("`text` must be a string")
        if (family, short) == ("DAT", "table"):
            raise ToeEditError("a Table DAT takes `table` (rows), not `text`")
        if family != "DAT" or (short not in _TEXT_DATS and not force):
            raise ToeEditError(
                "`text` is for DATs that store their own text (%s); %s is a %s"
                % (", ".join(sorted(_TEXT_DATS)), name, op_type))
    if table is not None and (family, short) != ("DAT", "table"):
        raise ToeEditError("`table` is for a tableDAT; %s is a %s" % (name, op_type))
    if family == "COMP" and short in _COMPS_BUILT_BY_TOUCHDESIGNER and not force:
        raise ToeEditError(
            "TouchDesigner builds the inner network of a %s when it creates one (%d operators); "
            "added to a file it would be an empty shell. Create it in TouchDesigner, or `insert` "
            "a saved .tox of one" % (op_type, _COMPS_BUILT_BY_TOUCHDESIGNER[short]))
    # The parent's children folder and the node file.
    (ws.dir / ws.base_of(parent.path)).mkdir(parents=True, exist_ok=True)
    _write(ws.dir / (base + ".n"),
           new_node_text(family, short, int(pos[0]), int(pos[1]), _default_flags(family, short, info)))
    parent_base = ws.base_of(parent.path)
    tail = ws.subtree(parent_base)
    anchor = tail[-1] if tail else ws.own_files(parent_base)[-1]
    ws.toc.insert(ws.toc.index(anchor) + 1, base + ".n")
    path = "%s/%s" % (parent.path, name)
    if (family, short) == ("DAT", "table"):
        # TouchDesigner always writes the payload file of a table or text DAT.
        rows = table if table is not None else []
        if not isinstance(rows, list) or any(not isinstance(r, (list, tuple)) for r in rows):
            raise ToeEditError("`table` must be a list of rows")
        (ws.dir / (base + ".table")).write_bytes(table_payload([[_cell_text(c) for c in r] for r in rows]))
        ws.toc.insert(ws.toc.index(base + ".n") + 1, base + ".table")
    elif text is not None or (family, short) == ("DAT", "text"):
        (ws.dir / (base + ".text")).write_bytes(text_payload((text or "").replace("\r\n", "\n")))
        ws.toc.insert(ws.toc.index(base + ".n") + 1, base + ".text")
    ws.register(path)
    receipt: Dict[str, Any] = {"op": "add", "path": path, "type": op_type,
                               "position": [int(pos[0]), int(pos[1])]}
    params = e.get("params") if e.get("params") is not None else e.get("parameters")
    if params:
        res = _e_set_params(ws, {"path": path, "params": params}, force)
        receipt["set"] = res["set"]
        notes.extend(res.get("notes", []))
    nf = ws.node_file(path)
    if e.get("flags"):
        for key, value in _flag_pairs(e["flags"], ws.need({"path": path})):
            nf.set_flag(key, value)
        receipt["flags"] = dict(e["flags"])
    for key in ("comment", "tags", "color"):
        if e.get(key):
            _e_set_node(ws, {"path": path, key: e[key]}, force)
    if e.get("dock"):
        host = _sibling(ws, parent.path, str(e["dock"]))
        if host is None:
            raise ToeEditError("dock host %s/%s does not exist" % (parent.path, e["dock"]))
        nf.set_dock(host.name)
        receipt["dock"] = host.path
    for index, src in enumerate(e.get("inputs") or []):
        if src:
            _e_connect(ws, {"from": "%s/%s" % (parent.path, str(src).rsplit("/", 1)[-1]),
                            "to": path, "input": index}, force)
    if e.get("inputs"):
        receipt["inputs"] = list(e["inputs"])
    made = _MADE_BY_TOUCHDESIGNER.get((family, short))
    if made:
        notes.append(made % {"name": name})
    elif family == "COMP" and short in _COMP_SHAPES:
        notes.append("TouchDesigner puts %d operator(s) inside a %s it creates (its viewport "
                     "shape); added offline it is empty and works the same" % (_COMP_SHAPES[short], op_type))
    if notes:
        receipt["notes"] = notes
    ws.refresh(path)
    return receipt


#: What TouchDesigner creates beside an operator made in the editor or by
#: ``COMP.create`` -- and an operator added to a file does not get. Measured
#: by saving fresh operators and reading the file.
_CALLBACK_NOTE = ("TouchDesigner gives a %s made in the editor a docked Text DAT "
                  "'%%(name)s_callbacks' and points `callbacks` at it; added offline it has "
                  "none -- add a textDAT with dock='%%(name)s' and set `callbacks`")
_MADE_BY_TOUCHDESIGNER = {
    ("CHOP", "script"): _CALLBACK_NOTE % "Script CHOP",
    ("SOP", "script"): _CALLBACK_NOTE % "Script SOP",
    ("TOP", "script"): _CALLBACK_NOTE % "Script TOP",
    ("DAT", "script"): _CALLBACK_NOTE % "Script DAT",
    ("TOP", "glsl"): ("TouchDesigner gives a GLSL TOP made in the editor docked DATs "
                      "'%(name)s_pixel' / '%(name)s_compute' / '%(name)s_info'; added offline it "
                      "has none -- add a textDAT holding the shader and set `pixeldat`"),
    ("MAT", "glsl"): ("TouchDesigner gives a GLSL MAT made in the editor docked DATs "
                      "'%(name)s_vertex' / '%(name)s_pixel'; added offline it has none -- add "
                      "textDATs and set `vdat` / `pdat`"),
    ("COMP", "geo"): ("a Geometry COMP made in the editor contains a torus; added offline it is "
                      "empty -- add the SOP or POP it should render, with display and render flags on"),
}


def _layout(specs: List[Dict[str, Any]], origin: Tuple[int, int] = (0, 0),
            step_x: int = 200, step_y: int = -160) -> Dict[str, List[int]]:
    """Left-to-right positions by wiring depth (the live build_network rule)."""
    by_name = {s.get("name"): s for s in specs if s.get("name")}
    depth: Dict[str, int] = {}

    def depth_of(name, seen=()):
        if name in depth:
            return depth[name]
        if name in seen or name not in by_name:
            return 0
        srcs = [s for s in (by_name[name].get("inputs") or []) if s]
        d = 0 if not srcs else 1 + max(depth_of(s, seen + (name,)) for s in srcs)
        depth[name] = d
        return d

    rows: Dict[int, int] = {}
    out = {}
    for name in by_name:
        d = depth_of(name)
        row = rows.get(d, 0)
        rows[d] = row + 1
        out[name] = [origin[0] + d * step_x, origin[1] + row * step_y]
    return out


def _e_build(ws: Workspace, e: Dict[str, Any], force: bool) -> Dict[str, Any]:
    """A whole network from the spec ``td_validate_build`` checks and the live
    ``build_network`` creates: ``{"operators": [...], "connections": [...]}``."""
    parent = ws.need(e, "parent")
    spec = e.get("spec")
    if not isinstance(spec, dict) or not isinstance(spec.get("operators"), list):
        raise ToeEditError("`spec` must be {\"operators\": [...], \"connections\": [...]}")
    specs = [s for s in spec["operators"] if isinstance(s, dict)]
    if not specs:
        raise ToeEditError("the spec has no operators")
    kids = ws.project.children(parent.path)
    origin = (0, min((k.n.get("y", 0) for k in kids), default=160) - 160) if kids else (0, 0)
    layout = _layout(specs, origin)
    created, notes = [], []
    for s in specs:
        add = {"parent": parent.path, "name": s.get("name"), "type": s.get("type"),
               "position": s.get("position") or layout.get(s.get("name")),
               "params": s.get("parameters") if s.get("parameters") is not None else s.get("params"),
               "flags": s.get("flags"), "text": s.get("text"), "table": s.get("table"),
               "comment": s.get("comment"), "tags": s.get("tags"), "color": s.get("color"),
               "dock": s.get("dock")}
        res = _e_add(ws, {k: v for k, v in add.items() if v is not None}, force)
        created.append(res["path"])
        notes.extend("%s: %s" % (s.get("name"), n) for n in res.get("notes", []))
    wired = 0
    for s in specs:
        for index, src in enumerate(s.get("inputs") or []):
            if src:
                _e_connect(ws, {"from": "%s/%s" % (parent.path, src),
                                "to": "%s/%s" % (parent.path, s["name"]), "input": index}, force)
                wired += 1
    for c in spec.get("connections") or []:
        if not isinstance(c, dict):
            continue
        _e_connect(ws, {"from": "%s/%s" % (parent.path, c.get("from")),
                        "to": "%s/%s" % (parent.path, c.get("to")),
                        "input": int(c.get("to_input", 0)), "from_output": int(c.get("from_output", 0))}, force)
        wired += 1
    out: Dict[str, Any] = {"op": "build", "parent": parent.path, "created": created, "wired": wired}
    if notes:
        out["notes"] = notes
    return out


def _copy_subtree(src_dir: Path, src_toc: List[str], src_base: str,
                  ws: Workspace, dst_base: str, anchor: str) -> int:
    """Copy the operator at ``src_base`` (its files and everything under it)
    from another expansion into this one as ``dst_base``; returns the file count."""
    n = len(src_base)
    entries = [t for t in src_toc
               if t.startswith(src_base + "/") or (t.startswith(src_base + ".") and "/" not in t[n:])]
    at = ws.toc.index(anchor) + 1
    for entry in entries:
        target = dst_base + entry[n:]
        dst = ws.dir / target
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_dir / entry, dst)
        ws.toc.insert(at, target)
        at += 1
    return len(entries)


def _e_insert(ws: Workspace, e: Dict[str, Any], force: bool) -> Dict[str, Any]:
    """Drop a saved component (.tox) into a COMP of this file."""
    parent = ws.need(e, "parent")
    if not parent.is_comp:
        raise ToeEditError("%s is not a COMP" % parent.path)
    file = e.get("file")
    if not isinstance(file, str) or not file.lower().endswith(".tox"):
        raise ToeEditError("`file` must be a .tox component")
    try:
        other_dir = Path(tr.expand(file))
    except tr.ToeReaderError as exc:
        raise ToeEditError(str(exc)) from None
    other = tr.Project(str(other_dir), source=Path(file).name)
    if len(other.roots) != 1:
        raise ToeEditError("%s holds %d root operators; a component has one" % (file, len(other.roots)))
    root = other.nodes[other.roots[0]]
    name = _check_new_name(ws, parent.path, e.get("name") or root.name)
    other_toc = [t for t in _read(other_dir.parent / (Path(file).name + ".toc")).split("\n")
                 if t and not t.startswith("#")]
    (ws.dir / ws.base_of(parent.path)).mkdir(parents=True, exist_ok=True)
    parent_base = ws.base_of(parent.path)
    tail = ws.subtree(parent_base)
    anchor = tail[-1] if tail else ws.own_files(parent_base)[-1]
    dst_base = "%s/%s" % (parent_base, name)
    ws.flush()
    files = _copy_subtree(other_dir, other_toc, root.name, ws, dst_base, anchor)
    ws.forget()
    path = "%s/%s" % (parent.path, name)
    nf = ws.node_file(path)
    nf.set_inputs([])  # wires it had where it was saved from do not exist here
    _blank_comp_inputs(ws.dir / (dst_base + ".network"))
    if e.get("position") is not None:
        nf.set_tile(int(e["position"][0]), int(e["position"][1]))
    else:
        pos = _next_position(ws, parent.path)
        nf.set_tile(pos[0], pos[1])
    ws.flush()
    ws.forget()
    count = 1 + sum(1 for _ in ws.project.walk(path))
    out = {"op": "insert", "path": path, "from": Path(file).name, "operators": count, "files": files}
    saved_with = other.build.get("build")
    here = (ws.before.build.get("build") if ws.before is not None else None)
    if saved_with and here and saved_with != here:
        out["notes"] = ["the component was saved with build %s, this file with %s" % (saved_with, here)]
    return out


def _e_set_project(ws: Workspace, e: Dict[str, Any], force: bool) -> Dict[str, Any]:
    """Project settings kept in `.start` (a .toe only): cook rate, realtime."""
    start = ws.dir / ".start"
    if not start.is_file():
        raise ToeEditError("this file has no project settings (a .tox is a component, not a project)")
    lines = _read(start).split("\n")
    done = {}

    def put(key, value):
        for i, line in enumerate(lines):
            if line.partition(" ")[0] == key:
                lines[i] = "%s %s" % (key, value)
                return
        raise ToeEditError("the project's .start has no %r line" % key)

    if e.get("cookrate") is not None:
        rate = e["cookrate"]
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 1 <= rate <= 1000:
            raise ToeEditError("cookrate is frames per second, 1..1000")
        put("cookrate", format_number(rate))
        done["cookrate"] = rate
    if e.get("realtime") is not None:
        if not isinstance(e["realtime"], bool):
            raise ToeEditError("realtime is true or false")
        put("realtime", "on" if e["realtime"] else "off")
        done["realtime"] = e["realtime"]
    if not done:
        raise ToeEditError("set_project takes cookrate and/or realtime")
    _write(start, "\n".join(lines))
    out = {"op": "set_project"}
    out.update(done)
    return out


def _e_reset_params(ws: Workspace, e: Dict[str, Any], force: bool) -> Dict[str, Any]:
    names = e.get("names")
    if not isinstance(names, list) or not names:
        raise ToeEditError("`names` is the list of parameters to put back to default")
    return _e_set_params(ws, {"path": e.get("path"), "params": {n: None for n in names}}, force)


EDIT_OPS = {
    "set_params": _e_set_params,
    "reset_params": _e_reset_params,
    "set_text": _e_set_text,
    "replace_text": _e_replace_text,
    "set_table": _e_set_table,
    "set_cells": _e_set_cells,
    "replace": _e_replace,
    "relink": _e_relink,
    "set_flags": _e_set_flags,
    "set_node": _e_set_node,
    "connect": _e_connect,
    "disconnect": _e_disconnect,
    "rename": _e_rename,
    "delete": _e_delete,
    "add": _e_add,
    "build": _e_build,
    "insert": _e_insert,
    "set_project": _e_set_project,
}


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------
def _free_name(target: Path) -> Path:
    """``name.edited.toe`` -> itself if free, else ``name.edited-2.toe`` ..."""
    if not target.exists():
        return target
    stem, suffix = target.name[:-len(target.suffix)], target.suffix
    for i in range(2, 10000):
        candidate = target.with_name("%s-%d%s" % (stem, i, suffix))
        if not candidate.exists():
            return candidate
    raise ToeEditError("no free file name beside %s" % target)


def _default_out(source: Path) -> Path:
    stem = source.name[:-len(source.suffix)]
    return _free_name(source.with_name("%s.edited%s" % (stem, source.suffix)))


def _compare_trees(a: Path, b: Path) -> List[str]:
    """Files that differ between two expansions (relative paths, capped)."""
    def listing(root):
        out = {}
        for folder, _dirs, files in os.walk(root):
            for f in files:
                p = Path(folder, f)
                out[p.relative_to(root).as_posix()] = p
        return out

    la, lb = listing(a), listing(b)
    bad = sorted(set(la) ^ set(lb))
    for rel in sorted(set(la) & set(lb)):
        if la[rel].read_bytes() != lb[rel].read_bytes():
            bad.append(rel)
    return bad[:20]


def collapse(ws: Workspace) -> Path:
    """Pack the workspace into ``<root>/<name>`` with ``toecollapse`` and
    prove the result: expanding it again must give the edited tree back."""
    tool = find_toecollapse()
    expand = tr.find_toeexpand()
    if not tool or not expand:
        raise ToeEditError(
            "toecollapse / toeexpand not found. They ship in TouchDesigner's bin folder; set "
            "TD_EXE to TouchDesigner's executable, or TD_MCP_TOEEXPAND / TD_MCP_TOECOLLAPSE")
    ws.check_tree()
    _write(ws.root / (ws.name + ".toc"), ws.toc_text())
    produced = ws.root / ws.name
    if produced.exists():
        produced.unlink()
    said = _run_tool(tool, ws.name, ws.root)
    if not produced.is_file() or produced.stat().st_size == 0:
        raise ToeEditError("toecollapse produced nothing%s" % (": " + said.strip()[:300] if said.strip() else ""))
    verify = ws.root / "verify"
    shutil.rmtree(verify, ignore_errors=True)
    verify.mkdir()
    shutil.copyfile(produced, verify / ws.name)
    _run_tool(expand, ws.name, verify)
    again = verify / (ws.name + ".dir")
    if not again.is_dir():
        raise ToeEditError("the produced file does not expand again; it was not delivered")
    differing = _compare_trees(ws.dir, again)
    if differing:
        raise ToeEditError("the produced file does not hold what was written; it was not delivered",
                           differing_files=differing)
    return produced


def _deliver(produced: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(out, "xb") as dst, open(produced, "rb") as src:
            shutil.copyfileobj(src, dst)
    except FileExistsError:
        raise ToeEditError("%s already exists; this editor never overwrites a file" % out) from None


def _fail(exc: ToeEditError, **more: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": False, "error": str(exc)}
    out.update(exc.extra)
    out.update(more)
    out["nothing_written"] = True
    return out


def apply(ws: Workspace, edits: List[Dict[str, Any]], force: bool = False) -> List[Dict[str, Any]]:
    """Apply ``edits`` to the workspace tree (no file is produced) and return
    one receipt per edit. Raises :class:`ToeEditError` on the first edit that
    cannot be applied, naming it."""
    receipts = []
    for index, e in enumerate(edits):
        if not isinstance(e, dict) or e.get("op") not in EDIT_OPS:
            raise ToeEditError("edit %d: `op` must be one of %s" % (index, ", ".join(sorted(EDIT_OPS))),
                               failed_edit=index)
        try:
            receipts.append(EDIT_OPS[e["op"]](ws, e, bool(force or e.get("force"))))
        except ToeEditError as exc:
            exc.extra.setdefault("failed_edit", index)
            exc.extra.setdefault("op", e["op"])
            exc.extra.setdefault("applied_before_failure", index)
            raise
    ws.flush()
    ws.check_tree()
    return receipts


def edit(file: Optional[str], edits: List[Dict[str, Any]], out: Optional[str] = None,
         dry_run: bool = False, new: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    """Apply ``edits`` to a copy of ``file`` and write the result to a NEW file.

    All or nothing: one edit that cannot be applied stops the run and no file
    is written. The produced file is expanded again and compared with the
    edited tree before it is delivered. ``new="name"`` starts from an empty
    Base COMP instead of a file (the result is ``name.tox``). ``dry_run``
    does everything except deliver the file.

    Returns the receipts of every edit and what changed between the two files
    as ``toe_reader.diff`` sees it.
    """
    if not isinstance(edits, list) or not edits:
        return {"ok": False, "error": "`edits` must be a non-empty list of {\"op\": ...} objects",
                "ops": sorted(EDIT_OPS), "nothing_written": True}
    ws = None
    try:
        if new:
            if file:
                raise ToeEditError("pass `file` or `new`, not both")
            ws = Workspace.new(str(new))
            target = Path(out).expanduser() if out else None
            if target is None and not dry_run:
                raise ToeEditError("`out` is required with `new`: where to write the .tox")
        else:
            if not file:
                raise ToeEditError("`file` is required (or new=\"name\" to start an empty component)")
            src = Path(file).expanduser()
            try:
                ws = Workspace(str(src))
            except tr.ToeReaderError as exc:
                raise ToeEditError(str(exc)) from None
            target = Path(out).expanduser() if out else (None if src.is_dir() else _default_out(src))
            if target is None and not dry_run:
                raise ToeEditError("`out` is required when editing an expanded directory")
        if target is not None:
            want = ".tox" if ws.name.lower().endswith(".tox") else ".toe"
            if target.suffix.lower() != want:
                raise ToeEditError("the result is a %s; `out` must end in %s" % (want, want))
            if target.exists():
                raise ToeEditError("%s already exists; this editor never overwrites a file" % target)
            if ws.source is not None and target.resolve() == ws.source.resolve():
                raise ToeEditError("`out` is the source file; this editor never overwrites")
        receipts = apply(ws, edits, force=force)
        produced = collapse(ws)
        after = tr.Project(str(ws.root / "verify" / (ws.name + ".dir")), source=ws.name)
        result: Dict[str, Any] = {"ok": True, "source": str(file) if file else None,
                                  "edits": receipts, "operators": len(after.nodes)}
        if ws.before is not None:
            d = tr.diff(ws.before, after)
            result["changed"] = {k: d[k] for k in ("summary", "added", "removed", "changed", "moved_only")
                                 if d.get(k)}
        result["verified"] = "the produced file was expanded again and matches the edited tree byte for byte"
        if dry_run:
            result["dry_run"] = True
            result["would_write"] = str(target) if target else None
        else:
            _deliver(produced, target)
            result["out"] = str(target)
            result["bytes"] = target.stat().st_size
            result["next"] = ("open it in TouchDesigner, or inspect it with td_project_read / "
                              "td_project_check; the source file is untouched")
        return result
    except ToeEditError as exc:
        return _fail(exc)
    finally:
        if ws is not None:
            ws.cleanup()


def extract(file: str, path: str, out: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    """Lift one COMP out of a project (or a bigger component) into its own
    ``.tox``, without opening TouchDesigner. The source is untouched."""
    ws = None
    try:
        try:
            src_dir = Path(tr.expand(file))
        except tr.ToeReaderError as exc:
            raise ToeEditError(str(exc)) from None
        src = Path(file).expanduser()
        project = tr.Project(str(src_dir), source=src.name)
        node = project.node(path) if isinstance(path, str) else None
        if node is None:
            raise ToeEditError("no operator at %s in %s" % (path, src.name), roots=list(project.roots))
        if not node.is_comp:
            raise ToeEditError("%s is a %s; only a COMP can be saved as a component" % (node.path, node.op_type))
        target = Path(out).expanduser() if out else _free_name(src.with_name(node.name + ".tox"))
        if target.suffix.lower() != ".tox":
            raise ToeEditError("`out` must end in .tox")
        if target.exists():
            raise ToeEditError("%s already exists; this editor never overwrites a file" % target)
        ws = Workspace(None)
        ws.name = node.name + ".tox"
        ws.dir = ws.root / (ws.name + ".dir")
        ws.dir.mkdir(parents=True)
        shutil.copyfile(src_dir / ".build", ws.dir / ".build")
        ws.header = [TOX_TOC_HEADER]
        ws.toc = [".build"]
        src_toc = [t for t in _read(src_dir.parent / (src.name + ".toc")).split("\n")
                   if t and not t.startswith("#")]
        _copy_subtree(src_dir, src_toc, ws.base_of(node.path), ws, node.name, ".build")
        nf = ws.node_file("/" + node.name)
        had_wires = bool(nf.inputs())
        nf.set_inputs([])
        ws.flush()
        had_wires = _blank_comp_inputs(ws.dir / (node.name + ".network")) or had_wires
        produced = collapse(ws)
        after = tr.Project(str(ws.root / "verify" / (ws.name + ".dir")), source=ws.name)
        result: Dict[str, Any] = {"ok": True, "source": str(file), "path": node.path,
                                  "operators": len(after.nodes),
                                  "verified": "the produced file was expanded again and matches"}
        notes = []
        if had_wires:
            notes.append("the COMP was wired to its neighbours; those wires are not part of a component")
        outside = _outside_references(project, node)
        if outside:
            result["references_outside"] = outside[:20]
            notes.append("%d parameter(s) inside point outside the component (absolute paths or "
                         "parents above it); they will not resolve where it is loaded" % len(outside))
        if notes:
            result["notes"] = notes
        if dry_run:
            result["dry_run"] = True
            result["would_write"] = str(target)
        else:
            _deliver(produced, target)
            result["out"] = str(target)
            result["bytes"] = target.stat().st_size
        return result
    except ToeEditError as exc:
        return _fail(exc)
    finally:
        if ws is not None:
            ws.cleanup()


def _blank_comp_inputs(path: Path) -> bool:
    """Forget which siblings fed a COMP (its `.network` names them): measured,
    an unwired connector is saved as ``index<tab>""``. True if one was wired."""
    if not path.is_file():
        return False
    lines = _read(path).split("\n")
    hit = False
    for i, line in enumerate(lines):
        index, tab, rest = line.partition("\t")
        if tab and index.strip().isdigit() and rest.strip() != '""':
            lines[i] = '%s\t""' % index
            hit = True
    if hit:
        _write(path, "\n".join(lines))
    return hit


def _outside_references(project: tr.Project, comp: tr.Node) -> List[Dict[str, str]]:
    """Constant parameter values inside ``comp`` that are absolute operator
    paths leading out of it."""
    out = []
    prefix = comp.path + "/"
    roots = tuple(r + "/" for r in project.roots) + tuple(project.roots)
    for node in project.walk(comp.path):
        for p in node.parms:
            value = p.get("value")
            if (p["mode"] == "constant" and isinstance(value, str) and value.startswith("/")
                    and value.startswith(roots) and not (value + "/").startswith(prefix)
                    and value in project.nodes):
                out.append({"path": node.path, "par": p["name"], "value": value})
    return out


# --------------------------------------------------------------------------
# Self-measurement
# --------------------------------------------------------------------------
def selftest(directory: str) -> Dict[str, Any]:
    """Prove the writer against files TouchDesigner wrote: re-render every
    `.parm` record, DAT text and table under ``directory`` (a folder of
    expanded ``*.dir`` trees) and count the ones that come out different."""
    from collections import Counter
    stats: Counter = Counter()
    odd: List[str] = []
    for folder, _dirs, files in os.walk(directory):
        if ".dir" not in folder:
            continue
        for fname in files:
            full = Path(folder, fname)
            if fname.endswith(".parm"):
                text = _read(full)
                if tr.is_legacy_parms(text):
                    stats["parm_legacy_files"] += 1
                    continue
                pf = ParmFile(full)
                stats["parm_files"] += 1
                if pf.render() != text:
                    stats["parm_files_not_reproduced"] += 1
                for rec in pf.records:
                    if rec.get("name") is None:
                        stats["parm_unparsed"] += 1
                    elif format_parm(rec) == rec["raw"]:
                        stats["parm_records_identical"] += 1
                    else:
                        stats["parm_records_different"] += 1
                        if len(odd) < 12:
                            odd.append("%s: %r" % (str(full)[-70:], rec["raw"][:120]))
            elif fname.endswith(".text"):
                data = full.read_bytes()
                same = text_payload(tr.read_text_payload(data)) == data
                stats["text_identical" if same else "text_different"] += 1
            elif fname.endswith(".table"):
                data = full.read_bytes()
                same = table_payload(tr.read_table_payload(data)) == data
                stats["table_identical" if same else "table_different"] += 1
            elif fname.endswith(".n"):
                text = _read(full)
                stats["node_identical" if NodeFile(text=text).render() == text else "node_different"] += 1
    return {"stats": dict(stats), "odd": odd}


def _main(argv: List[str]) -> int:
    import json
    if len(argv) >= 2 and argv[0] == "--selftest":
        print(json.dumps(selftest(argv[1]), indent=1))
        return 0
    if len(argv) >= 2:
        edits = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        print(json.dumps(edit(argv[0], edits, out=argv[2] if len(argv) > 2 else None), indent=1))
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
