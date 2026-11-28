"""Read a saved TouchDesigner project (.toe) or component (.tox) with
TouchDesigner closed.

TouchDesigner ships ``toeexpand``, which unpacks a ``.toe``/``.tox`` into a
tree of small files, one group per operator. This module drives it on a
*copy* (``toeexpand`` writes beside its input) and parses that tree, so an
agent can inspect, search, compare and sanity-check a project file without a
running TouchDesigner -- and without ever touching the original.

Nothing here is documented by Derivative. Every rule below was measured on
TouchDesigner 2025.33230 (Windows): a network built for the purpose and saved
from the test project, then all 768 components that ship with the install
(489 OP Snippets + 279 Palette, 93,153 operators). ``python -m
td_mcp.toe_reader --selftest DIR`` re-runs that measurement on a new build.
The idea of reading projects this way comes from grigabyte/td-atlas (MIT),
whose format notes were the map; the facts were re-derived here, and two of
them came out differently on this build (table header order, see
:func:`read_table_payload`).

Measured grammar
----------------
``<name>.n``      ``FAMILY:type`` then ``tile x y w h``, ``flags = k v ...``,
                  ``inputs { index <tab>sibling }``, ``color r g b``, and
                  optionally ``comment``, ``tags``, ``dock``, ``exports``,
                  ``dict`` (pickled storage -- never unpickled here), ``view``.
                  ``type`` is ``OP.type`` (``geo``), not ``OP.OPType``
                  (``geometryCOMP``); the parameter catalog maps one to the
                  other.
``<name>.parm``   ``name flags constant [expression] [bind]`` between ``?``
                  lines. The low two bits of the flags word are the mode;
                  ``0x10`` = an expression is stored, ``0x200`` = a bind is
                  stored, and those bits -- not the shape of the line -- say
                  how many fields follow. Only parameters that differ from
                  default (plus every parameter of sequence blocks past the
                  first). A field is bare, or double-quoted when it is empty
                  or holds white space; ``\\`` and ``"`` are backslash-escaped
                  in both forms, and a field with a non-ASCII character is
                  preceded by a UTF-8 byte order mark.
``<name>.cparm``  ``pages N names...`` then one definition per custom
                  parameter (type code, name, label, ..., page, order).
``<name>.text``   ``2\\n*`` + uint32 ``1, rows, cols, 1`` + one cell.
``<name>.table``  ``1\\n*`` + uint32 ``1, rows, cols, 0`` + row-major cells.
                  A cell is ``uint32 tag, uint32 length, bytes``; a text DAT
                  is a 1 x 1 table (an empty one may have no cell at all).
``<name>.network`` a COMP's own input wires (``compinputs``).

Pure standard library; ``toeexpand`` is only needed to *expand*. An already
expanded ``*.dir`` tree loads anywhere.
"""

from __future__ import annotations

import difflib
import glob
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: The low two bits of a `.parm` flags word are the parameter's mode, in
#: ``ParMode`` order. Measured over 806,313 shipped parameters: every mode-1
#: line carries an expression, all but one mode-3 line a bind expression, and
#: no mode-2 line carries either (its value arrives from a CHOP export).
PAR_MODES = ("constant", "expression", "export", "bind")
#: "An expression is stored" -- NOT "the parameter is in expression mode".
#: 22,394 shipped parameters are constants that keep an inactive expression
#: (writing a value over an expression switches the mode and keeps the text).
FLAG_EXPRESSION = 0x10
#: "A bind expression is stored."
FLAG_BIND = 0x200
#: One more value follows, in every sample a repeat of the bind expression.
FLAG_EXTRA_VALUE = 0x200000

#: Saved UI state that looks like a parameter (which page is showing).
_UI_STATE_PAR = re.compile(r"(pageindex|stdswitcher)\d*$")

#: `flags =` words that are editor state, not something a reader cares about.
_EDITOR_FLAGS = {"parlanguage", "picked", "current", "showDocked", "compare"}

#: Low byte of a `.cparm` type code -> parameter style, measured by saving one
#: custom parameter from every ``Page.append*`` method on 2025.33230. Anything
#: else is reported as its hex code.
_CPARM_BASE = {0x01: "Float", 0x02: "Int", 0x03: "Toggle", 0x04: "Str",
               0x05: "Pulse", 0x06: "Object", 0x07: "SOP", 0x08: "MAT",
               0x09: "CHOP", 0x0A: "TOP", 0x0B: "DAT", 0x0C: "PanelCOMP",
               0x0D: "COMP", 0x0E: "OP", 0x0F: "Menu", 0x10: "File",
               0x11: "Folder", 0x12: "StrMenu", 0x13: "Momentary",
               0x14: "Python", 0x15: "Header", 0x17: "POP", 0x18: "Sequence",
               0x19: "FileSave", 0x1A: "TOPMulti"}
#: Bits 8-11 of the type code are the number of components, bits 12-15 how the
#: components are named: ``Pos`` with scheme 3 and three components is
#: ``Posx Posy Posz``; scheme 1 numbers them (``Size1 Size2``) -- and is what
#: a one-component parameter carries too. Bit 31 is "starts a section".
#: (Float 0x2E101101, RGB 0x2E102301, XYZ 0x2E103301, UV 0x2E104201,
#: WH 0x2E105201, Float size 2 0x2E101201.)
_CPARM_SUFFIXES = {1: "1234", 2: "rgba", 3: "xyzw", 4: "uvw", 5: "wh"}
_CPARM_TUPLE_STYLE = {(2, 3): "RGB", (2, 4): "RGBA", (3, 2): "XY", (3, 3): "XYZ",
                      (3, 4): "XYZW", (4, 2): "UV", (4, 3): "UVW", (5, 2): "WH"}

_PAYLOAD_HEADER = 27
_BOM = "\ufeff"


class ToeReaderError(Exception):
    """A project file could not be expanded or read."""


# --------------------------------------------------------------------------
# toeexpand
# --------------------------------------------------------------------------
def find_toeexpand() -> Optional[str]:
    """Path to TouchDesigner's ``toeexpand``, or None.

    Order: ``TD_MCP_TOEEXPAND``, beside ``TD_EXE``, the newest install under
    Program Files (Windows) or /Applications (macOS), then ``PATH``.
    """
    exe = "toeexpand.exe" if os.name == "nt" else "toeexpand"
    explicit = os.environ.get("TD_MCP_TOEEXPAND")
    if explicit and os.path.isfile(explicit):
        return explicit
    td_exe = os.environ.get("TD_EXE")
    if td_exe:
        beside = os.path.join(os.path.dirname(td_exe), exe)
        if os.path.isfile(beside):
            return beside
    patterns = []
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"),
                     r"C:\Program Files"):
            if base:
                patterns.append(os.path.join(base, "Derivative", "TouchDesigner*", "bin", exe))
    elif sys.platform == "darwin":
        patterns.append("/Applications/TouchDesigner*.app/Contents/MacOS/toeexpand")
    else:
        patterns.append("/opt/derivative/touchdesigner*/bin/toeexpand")
    found: List[str] = []
    for pat in patterns:
        found.extend(glob.glob(pat))
    if found:
        return sorted(set(found))[-1]
    return shutil.which("toeexpand")


def cache_root() -> Path:
    base = os.environ.get("TD_MCP_HOME")
    return (Path(base) if base else Path.home() / ".td_mcp") / "cache" / "toe"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _prune(root: Path, keep: int) -> None:
    try:
        entries = sorted((p for p in root.iterdir() if p.is_dir()),
                         key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for stale in entries[keep:]:
        shutil.rmtree(stale, ignore_errors=True)


def expand(file: str, cache_dir: Optional[str] = None, refresh: bool = False,
           keep: int = 32, timeout: float = 600.0) -> str:
    """Expand ``file`` into the cache and return the ``*.dir`` path.

    The file is **copied** first: ``toeexpand`` writes ``<name>.dir`` and
    ``<name>.toc`` beside its input, and the original must stay untouched.
    The cache key is the file's SHA-256, so an unchanged file expands once.
    ``toeexpand`` exits 1 on success (measured), so success is the output
    directory existing, never the exit code.
    """
    src = Path(file).expanduser()
    if not src.is_file():
        raise ToeReaderError("no such file: %s" % file)
    if src.suffix.lower() not in (".toe", ".tox"):
        raise ToeReaderError("not a .toe or .tox file: %s" % src.name)
    root = Path(cache_dir) if cache_dir else cache_root()
    slot = root / _sha256(src)[:20]
    out = slot / (src.name + ".dir")
    if out.is_dir() and not refresh:
        try:
            os.utime(slot, None)
        except OSError:
            pass
        return str(out)
    tool = find_toeexpand()
    if not tool:
        raise ToeReaderError(
            "toeexpand not found. It ships in TouchDesigner's bin folder; set "
            "TD_MCP_TOEEXPAND to its full path, or TD_EXE to TouchDesigner's executable.")
    shutil.rmtree(slot, ignore_errors=True)
    slot.mkdir(parents=True, exist_ok=True)
    copy = slot / src.name
    shutil.copy2(src, copy)
    try:
        proc = subprocess.run([tool, copy.name], cwd=str(slot), capture_output=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        shutil.rmtree(slot, ignore_errors=True)
        raise ToeReaderError("toeexpand failed on %s: %s" % (src.name, exc)) from exc
    try:
        copy.unlink()  # the expansion is what is kept; the copy doubled the cache
    except OSError:
        pass
    if not out.is_dir():
        said = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()
        shutil.rmtree(slot, ignore_errors=True)
        raise ToeReaderError("toeexpand produced nothing for %s%s"
                             % (src.name, ": " + said[:300] if said else ""))
    _prune(root, keep)
    return str(out)


# --------------------------------------------------------------------------
# File grammars
# --------------------------------------------------------------------------
def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def _read_text_file(path: Path) -> str:
    try:
        return _decode(path.read_bytes())
    except OSError:
        return ""


def _scan_field(s: str, i: int) -> Tuple[str, int, bool]:
    """One field of ``s`` from ``i`` -> ``(value, next index, closed)``.

    A field is a bare run up to white space, or a double-quoted string that
    may hold white space and newlines. In both forms ``\\\\`` is a backslash
    and ``\\"`` a double quote (measured: ``back\\slash`` is saved as
    ``back\\\\slash``, ``a"b`` as ``a\\"b``, neither quoted). ``closed`` is
    False when a quoted field runs off the end -- the value continues on the
    next line of the file.

    Not ``shlex``: single quotes are content (``op('a').par.x`` must survive).
    """
    n = len(s)
    while i < n and s[i] in " \t":
        i += 1
    if i < n and s[i] == _BOM:
        i += 1
    if i >= n:
        return "", n, True
    out = []
    if s[i] == '"':
        i += 1
        while i < n:
            ch = s[i]
            if ch == "\\" and i + 1 < n and s[i + 1] in '"\\':
                out.append(s[i + 1])
                i += 2
                continue
            if ch == '"':
                return "".join(out), i + 1, True
            out.append(ch)
            i += 1
        return "".join(out), n, False
    while i < n and s[i] not in " \t":
        ch = s[i]
        if ch == "\\" and i + 1 < n and s[i + 1] in '"\\':
            out.append(s[i + 1])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out), i, True


def _lex_field(s: str, i: int) -> Tuple[str, int]:
    """One field of ``s`` from ``i`` -> ``(value, next index)``."""
    value, j, _closed = _scan_field(s, i)
    return value, j


def _unquote(value: str) -> str:
    """A whole string as one field: outer quotes, BOM and escapes removed.

    Text that is not one field (two bare words) is returned as it stands.
    """
    v = value.strip()
    if not v:
        return ""
    field, j, _closed = _scan_field(v, 0)
    if v[j:].strip():
        return v[len(_BOM):] if v.startswith(_BOM) else v
    return field


def _tokens(s: str) -> List[str]:
    out, i, n = [], 0, len(s)
    while True:
        while i < n and s[i] in " \t":
            i += 1
        if i >= n:
            return out
        tok, i = _lex_field(s, i)
        out.append(tok)


def parse_parm_line(line: str) -> Optional[Dict[str, Any]]:
    """``name flags constant [expression] [bind]`` -> a parameter record.

    The flags word decides how many values follow: reading it off the shape
    of the line turns ``colorr 515 1 parent.X.par.Color1r`` (bind mode) into
    a constant of ``"1 parent.X.par.Color1r"``. It also holds the mode
    (``flags & 3``): ``label 16 trails me.name`` is the *constant* ``trails``
    with a leftover expression, not an expression.
    """
    name, sep, rest = line.partition(" ")
    if not sep or not name:
        return None
    flags_s, _, tail = rest.partition(" ")
    try:
        flags = int(flags_s)
    except ValueError:
        return None
    rec: Dict[str, Any] = {"name": name, "flags": flags, "mode": PAR_MODES[flags & 3]}
    keys = ["value"] + [k for k, bit in (("expr", FLAG_EXPRESSION), ("bind", FLAG_BIND),
                                         ("extra", FLAG_EXTRA_VALUE)) if flags & bit]
    i = 0
    for k, key in enumerate(keys):
        if k == len(keys) - 1:
            # The last field owns the rest of the line. It is one field in
            # every measured file; text that is not is kept as it stands.
            rec[key] = _unquote(tail[i:])
        else:
            rec[key], i = _lex_field(tail, i)
    return rec


def _value_is_open(line: str) -> bool:
    """True when a `.parm` record ends inside a double-quoted field.

    A quoted field may hold newlines (a multi-line Text COMP string, an
    annotation body): the record then runs on until the quote closes. Rare --
    three of the 85,744 shipped `.parm` files have such a record.
    """
    _name, _, rest = line.partition(" ")
    flags_s, _, tail = rest.partition(" ")
    try:
        int(flags_s)
    except ValueError:
        return False
    i, n = 0, len(tail)
    while i < n:
        _value, i, closed = _scan_field(tail, i)
        if not closed:
            return True
    return False


def split_parm_records(text: str) -> List[str]:
    """The raw records of a `.parm` body: one per parameter, a multi-line
    quoted value kept whole, the ``?`` fences and blank lines dropped.

    Lines are split on ``\\n`` only -- a value may hold a carriage return or
    another character ``str.splitlines`` would break on.
    """
    lines = text.split("\n")
    out = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        i += 1
        if not line or line == "?":
            continue
        while i < n and _value_is_open(line):
            line += "\n" + lines[i]
            i += 1
        out.append(line)
    return out


def _parse_legacy_parms(lines: List[str]) -> List[Dict[str, Any]]:
    """The pre-099 grammar some shipped components still carry:
    ``{`` / ``version 0.8`` / ``name<tab>(<tab>values...<tab>)`` / ``}``.
    It records values only, so every parameter reads as a constant."""
    out = []
    for line in lines:
        name, _, rest = line.partition("\t")
        parts = rest.split("\t")
        if not name or len(parts) < 2 or parts[0] != "(" or parts[-1] != ")":
            continue
        values = [_unquote(v) for v in parts[1:-1]]
        out.append({"name": name, "flags": 0, "mode": "constant", "legacy": True,
                    "value": values[0] if len(values) == 1 else " ".join(values)})
    return out


def is_legacy_parms(text: str) -> bool:
    """True for the pre-099 ``{ version 0.8 ... }`` grammar."""
    return text.lstrip("\n").startswith("{")


def parse_parms(text: str) -> List[Dict[str, Any]]:
    if is_legacy_parms(text):
        return _parse_legacy_parms(text.splitlines())
    out = []
    for line in split_parm_records(text):
        rec = parse_parm_line(line)
        if rec is not None:
            out.append(rec)
    return out


def parse_cparms(text: str) -> Dict[str, Any]:
    """Custom parameter pages and definitions.

    The first line is ``pages <count> <names...>`` -- the number is a count,
    not a flags word. A definition line is ``typecode Name Label ... page
    order``; the columns between label and page are not decoded (their layout
    varies with the type), so only name, label, style, page and menu entries
    are returned.
    """
    pages: List[str] = []
    pars: List[Dict[str, Any]] = []
    for line in text.splitlines():
        if not line or line == "?":
            continue
        toks = _tokens(line)
        if not toks:
            continue
        if toks[0] == "pages":
            pages = toks[2:]
            continue
        if len(toks) < 3:
            continue
        try:
            code = int(toks[0])
        except ValueError:
            continue
        rec: Dict[str, Any] = {"name": toks[1], "label": toks[2]}
        base = _CPARM_BASE.get(code & 0xFF)
        size = (code >> 8) & 0xF
        scheme = (code >> 12) & 0xF
        rec["style"] = _CPARM_TUPLE_STYLE.get((scheme, size), base) if base in ("Float", "Int") \
            else (base if base else "0x%X" % (code & 0xFFFF))
        if base in ("Float", "Int") and size > 1:
            rec["size"] = size
            suffixes = _CPARM_SUFFIXES.get(scheme, "")[:size]
            if len(suffixes) == size:
                rec["names"] = [toks[1] + ch for ch in suffixes]
        if code & 0x80000000:
            rec["section"] = True  # starts a new section on its page
        # The page is the last token that names one of this COMP's pages.
        page_at = None
        for idx in range(len(toks) - 1, 2, -1):
            if toks[idx] in pages:
                page_at = idx
                break
        if page_at is not None:
            rec["page"] = toks[page_at]
            after = toks[page_at + 1:]
            if base == "Menu" and len(after) >= 2:
                try:
                    count = int(after[1])
                    pairs = after[2:2 + 2 * count]
                    rec["menu"] = pairs[0::2]
                    labels = pairs[1::2]
                    if labels and labels != rec["menu"]:
                        rec["menu_labels"] = labels
                except ValueError:
                    pass
        pars.append(rec)
    return {"pages": pages, "pars": pars}


def read_text_payload(data: bytes) -> str:
    """DAT text: 27-byte prologue whose last uint32 is the payload length.

    The prologue is a table header in disguise -- ``2\\n*`` then uint32
    ``1, rows=1, cols=1, 1`` and one cell (``tag, length``); 14,689 of the
    14,720 shipped files carry tag 2, the rest tag 1 or no cell. Scanning for
    the first newline instead is right on the first file anyone tries and
    wrong whenever the length's low byte is not 0x0A.
    """
    if data[1:3] != b"\n*":
        return _decode(data)
    if len(data) < _PAYLOAD_HEADER:
        return ""  # an empty DAT is a 19-byte header with no length word
    (length,) = struct.unpack(">I", data[23:27])
    body = data[_PAYLOAD_HEADER:]
    if length <= len(body):
        body = body[:length]
    return _decode(body)


def read_table_payload(data: bytes) -> List[List[str]]:
    """Table DAT cells, row-major.

    Measured on a 3-row x 2-column table saved from 2025.33230: after
    ``1\\n*`` come uint32 ``1, rows, cols, 0`` and then each cell as
    ``uint32 tag, uint32 length, bytes``. (Rows before columns; the six-word
    prologue of ``.text`` files does not apply to tables.)
    """
    if len(data) < 19 or data[:3] != b"1\n*":
        return []
    _one, rows, cols, _pad = struct.unpack(">4I", data[3:19])
    if rows * cols > 4_000_000:
        return []
    pos, cells = 19, []
    total = rows * cols
    while len(cells) < total and pos + 8 <= len(data):
        _tag, length = struct.unpack(">2I", data[pos:pos + 8])
        pos += 8
        cells.append(_decode(data[pos:pos + length]))
        pos += length
    if len(cells) < total:
        cells.extend([""] * (total - len(cells)))
    return [cells[r * cols:(r + 1) * cols] for r in range(rows)]


def _block(lines: List[str], i: int) -> Tuple[List[str], int]:
    """Lines of a ``{ ... }`` block starting at or after ``lines[i]``."""
    n = len(lines)
    while i < n and lines[i].strip() != "{":
        if lines[i].strip():
            return [], i
        i += 1
    i += 1
    body = []
    while i < n and lines[i].strip() != "}":
        body.append(lines[i])
        i += 1
    return body, i + 1


def parse_node(text: str) -> Dict[str, Any]:
    """One ``.n`` file -> family, type, tile, flags, inputs and annotations."""
    lines = text.split("\n")  # not splitlines(): a comment may hold a \r
    node: Dict[str, Any] = {"family": "", "type": "", "flags": {}, "inputs": []}
    if not lines or not lines[0].strip():
        return node
    family, _, short = lines[0].strip().partition(":")
    node["family"], node["type"] = family, short
    i = 1
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        key, _, rest = s.partition(" ")
        i += 1
        if key == "tile":
            try:
                x, y, w, h = (int(float(v)) for v in rest.split()[:4])
                node["x"], node["y"], node["w"], node["h"] = x, y, w, h
            except ValueError:
                pass
        elif key == "flags":
            toks = rest.split("=", 1)[-1].split()
            for j in range(0, len(toks) - 1, 2):
                value = toks[j + 1]
                node["flags"][toks[j]] = (True if value == "on" else False if value == "off"
                                          else int(value) if value.lstrip("-").isdigit() else value)
        elif key == "inputs":
            body, i = _block(lines, i)
            for entry in body:
                index, _, source = entry.partition("\t")
                index, source = index.strip(), source.strip()
                if source and index.isdigit():
                    node["inputs"].append((int(index), source))
        elif key == "exports":
            body, i = _block(lines, i)
            names = [e.strip().strip('"') for e in body]
            node["exports"] = [e for e in names if e]
        elif key == "color":
            try:
                node["color"] = [round(float(v), 4) for v in rest.split()[:3]]
            except ValueError:
                pass
        elif key == "comment":
            rest = line.partition(" ")[2]  # unstripped: a quoted comment may end in spaces
            while not _scan_field(rest, 0)[2] and i < len(lines):
                rest += "\n" + lines[i]
                i += 1
            node["comment"] = _unquote(rest)
        elif key == "tags":
            node["tags"] = _tokens(rest)[2:]
        elif key == "dock":
            node["dock"] = rest.strip()
        elif key == "dict":
            node["has_storage"] = True
        elif key == "end":
            break
    return node


def parse_network(text: str) -> List[Tuple[int, str, str]]:
    """A COMP's ``.network`` file: ``[(input index, sibling source, inner In op)]``."""
    lines = text.splitlines()
    out: List[Tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        if line.strip() == "compinputs":
            body, _ = _block(lines, i + 1)
            j = 0
            while j < len(body):
                index, _, source = body[j].partition("\t")
                if index.strip().isdigit() and source.strip():
                    inner = body[j + 1].strip() if j + 1 < len(body) else ""
                    out.append((int(index.strip()), source.strip(), inner))
                    j += 3
                else:
                    j += 1
            break
    return out


# --------------------------------------------------------------------------
# Catalog bridge (operator type names, parameter styles)
# --------------------------------------------------------------------------
def _catalog():
    try:
        from td_mcp.kb import param_catalog
        return param_catalog if param_catalog.available() else None
    except Exception:  # noqa: BLE001 - the reader works without a catalog
        return None


def op_type_for(family: str, short: str) -> str:
    """``('COMP', 'geo')`` -> ``geometryCOMP``.

    The file stores ``OP.type``; ``COMP.create`` wants ``OP.OPType``. They
    differ for about one operator in six (``comp`` -> ``compositeTOP``,
    ``eval`` -> ``evaluateDAT``), and the short form is not a reliable prefix,
    so the catalog -- which recorded both from a running build -- decides.
    """
    cat = _catalog()
    if cat is not None:
        hit = cat.resolve_op_type(short, family)
        if hit:
            return hit
    return "%s%s" % (short, family)


def _sequence_spec(name: str, known: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The catalog entry for a later block of a sequence parameter.

    The catalog records block 0 (``const0name``, flagged ``sequence``); a
    Constant CHOP with three channels also saves ``const1name`` and
    ``const2name``, which are the same parameter in another block.
    """
    m = re.fullmatch(r"(.*?)(\d+)(\D*)", name)
    if not m:
        return None
    spec = known.get("%s0%s" % (m.group(1), m.group(3)))
    return spec if spec and spec.get("sequence") else None


def _coerce(value: Any, style: Optional[str]) -> Any:
    """A stored constant as the value a tool call would send."""
    if not isinstance(value, str):
        return value
    if style == "Toggle":
        if value in ("on", "1"):
            return True
        if value in ("off", "0"):
            return False
        return value
    numeric = style in ("Float", "Int", "XY", "XYZ", "XYZW", "WH", "UV", "UVW", "RGB", "RGBA")
    if numeric or style is None:
        try:
            if re.fullmatch(r"-?\d+", value):
                return int(value)
            if numeric or re.fullmatch(r"-?(\d+\.\d*|\.\d+)(e[-+]?\d+)?", value, re.I):
                return float(value)
        except ValueError:
            pass
    return value


# --------------------------------------------------------------------------
# Project model
# --------------------------------------------------------------------------
class Node:
    """One operator of an expanded project. Heavy parts load on first use."""

    __slots__ = ("path", "name", "parent", "base", "_n", "_parms", "_cparms",
                 "_text", "_table", "children", "op_type")

    def __init__(self, path: str, base: Path, parent: Optional[str]):
        self.path = path
        self.name = path.rsplit("/", 1)[-1]
        self.parent = parent
        self.base = base  # file path without extension
        self._n: Optional[Dict[str, Any]] = None
        self._parms: Optional[List[Dict[str, Any]]] = None
        self._cparms: Optional[Dict[str, Any]] = None
        self._text: Optional[str] = None
        self._table: Optional[List[List[str]]] = None
        self.children: List[str] = []
        self.op_type = ""

    def _file(self, ext: str) -> Path:
        return self.base.with_name(self.base.name + ext)

    @property
    def n(self) -> Dict[str, Any]:
        if self._n is None:
            self._n = parse_node(_read_text_file(self._file(".n")))
        return self._n

    @property
    def family(self) -> str:
        return self.n.get("family", "")

    @property
    def is_comp(self) -> bool:
        return self.family == "COMP"

    @property
    def parms(self) -> List[Dict[str, Any]]:
        if self._parms is None:
            f = self._file(".parm")
            self._parms = parse_parms(_read_text_file(f)) if f.is_file() else []
        return self._parms

    @property
    def cparms(self) -> Dict[str, Any]:
        if self._cparms is None:
            f = self._file(".cparm")
            self._cparms = (parse_cparms(_read_text_file(f)) if f.is_file()
                            else {"pages": [], "pars": []})
        return self._cparms

    @property
    def text(self) -> Optional[str]:
        if self._text is None:
            f = self._file(".text")
            if not f.is_file():
                return None
            try:
                self._text = read_text_payload(f.read_bytes())
            except OSError:
                return None
        return self._text

    @property
    def table(self) -> Optional[List[List[str]]]:
        if self._table is None:
            f = self._file(".table")
            if not f.is_file():
                return None
            try:
                self._table = read_table_payload(f.read_bytes())
            except OSError:
                return None
        return self._table

    def comp_inputs(self) -> List[Tuple[int, str, str]]:
        f = self._file(".network")
        return parse_network(_read_text_file(f)) if f.is_file() else []


class Project:
    """An expanded ``.toe``/``.tox``: operators by absolute path."""

    def __init__(self, directory: str, source: Optional[str] = None):
        self.dir = Path(directory)
        if not self.dir.is_dir():
            raise ToeReaderError("not an expanded project directory: %s" % directory)
        self.source = source or re.sub(r"\.dir$", "", self.dir.name)
        self.kind = "tox" if self.source.lower().endswith(".tox") else "toe"
        self.nodes: Dict[str, Node] = {}
        self.roots: List[str] = []
        self.build = self._read_build()
        self._scan()

    def _read_build(self) -> Dict[str, str]:
        info: Dict[str, str] = {}
        for line in _read_text_file(self.dir / ".build").splitlines():
            key, _, value = line.partition(" ")
            if key in ("version", "build", "osname", "time"):
                info[key] = value.strip()
        for line in _read_text_file(self.dir / ".start").splitlines():
            key, _, value = line.partition(" ")
            if key in ("cookrate", "realtime"):
                info[key] = value.strip()
        return info

    def _scan(self) -> None:
        stack: List[Tuple[Path, Optional[str]]] = [(self.dir, None)]
        while stack:
            folder, parent = stack.pop()
            try:
                names = sorted(e.name for e in os.scandir(folder)
                               if e.is_file() and e.name.endswith(".n"))
            except OSError:
                continue
            for fname in names:
                stem = fname[:-2]
                path = "%s/%s" % (parent or "", stem)
                node = Node(path, folder / stem, parent)
                self.nodes[path] = node
                if parent is None:
                    self.roots.append(path)
                else:
                    self.nodes[parent].children.append(path)
                sub = folder / stem
                if sub.is_dir():
                    stack.append((sub, path))
        for node in self.nodes.values():
            node.op_type = op_type_for(node.family, node.n.get("type", ""))

    # -- navigation --------------------------------------------------------
    def node(self, path: str) -> Optional[Node]:
        if not path or path == "/":
            return None
        return self.nodes.get("/" + path.strip("/"))

    def children(self, path: Optional[str]) -> List[Node]:
        if not path or path == "/":
            return [self.nodes[p] for p in self.roots]
        node = self.node(path)
        return [self.nodes[p] for p in node.children] if node else []

    def walk(self, path: Optional[str] = None, depth: Optional[int] = None) -> Iterable[Node]:
        """Operators under ``path`` (itself excluded), breadth by level."""
        level = self.children(path)
        d = 1
        while level and (depth is None or d <= depth):
            nxt: List[Node] = []
            for node in level:
                yield node
                nxt.extend(self.nodes[p] for p in node.children)
            level = nxt
            d += 1

    # -- derived facts -----------------------------------------------------
    def inputs_of(self, node: Node) -> List[Dict[str, Any]]:
        """Wires into ``node`` with absolute source paths.

        A source written ``comp/out1`` is the COMP ``comp`` seen through its
        Out operator ``out1``; the wire belongs to the COMP.
        """
        parent = node.parent or ""
        wires = []
        for index, source in node.n.get("inputs", []):
            head, _, via = source.partition("/")
            rec: Dict[str, Any] = {"index": index, "from": "%s/%s" % (parent, head)}
            if via:
                rec["via_out"] = via
            wires.append(rec)
        for index, source, inner in node.comp_inputs():
            head, _, via = source.partition("/")
            rec = {"index": index, "from": "%s/%s" % (parent, head)}
            if via:
                rec["via_out"] = via
            if inner:
                rec["to_in"] = inner
            wires.append(rec)
        return wires

    def custom_styles(self, node: Node) -> Dict[str, str]:
        styles: Dict[str, str] = {}
        for p in node.cparms["pars"]:
            # Pos -> Posx Posy Posz, Tint -> Tintr Tintg Tintb, Size -> Size1 Size2
            for name in p.get("names") or ():
                styles[name] = p["style"]
            styles[p["name"]] = p["style"]
        return styles

    def params_of(self, node: Node) -> Dict[str, Any]:
        """Non-default parameters: ``{name: value | {"expr": ...} | {"bind": ...}}``."""
        cat = _catalog()
        known = {}
        if cat is not None:
            info = cat.op_info(node.op_type)
            known = (info or {}).get("by_name") or {}
        custom = self.custom_styles(node) if node.is_comp else {}
        out: Dict[str, Any] = {}
        for p in node.parms:
            name = p["name"]
            if _UI_STATE_PAR.match(name):
                continue
            style = (known.get(name) or _sequence_spec(name, known) or {}).get("style") \
                or custom.get(name)
            if p["mode"] == "expression":
                out[name] = {"expr": p.get("expr", "")}
            elif p["mode"] == "bind":
                out[name] = {"bind": p.get("bind", "")}
            elif p["mode"] == "export":
                # Driven by a CHOP export; the stored constant is what it
                # falls back to when the export goes away.
                out[name] = {"export": True, "value": _coerce(p["value"], style)}
            else:
                out[name] = _coerce(p["value"], style)
        return out


def load(file_or_dir: str, cache_dir: Optional[str] = None, refresh: bool = False) -> Project:
    """A :class:`Project` from a ``.toe``/``.tox`` (expanded on demand) or an
    already expanded ``*.dir`` directory."""
    p = Path(file_or_dir).expanduser()
    if p.is_dir():
        return Project(str(p))
    return Project(expand(str(p), cache_dir=cache_dir, refresh=refresh), source=p.name)


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------
def _flags_view(node: Node) -> List[str]:
    """Flags that are on and say something about behaviour."""
    out = []
    for key, value in node.n.get("flags", {}).items():
        if key in _EDITOR_FLAGS:
            continue
        if key == "viewer":
            if value:
                out.append("viewer")
        elif key == "cook":
            if value is False:
                out.append("cooking_off")
        elif value is True or (isinstance(value, int) and not isinstance(value, bool) and value):
            out.append(key)
    return out


def describe(project: Project, node: Node, params: bool = True, text: str = "none",
             max_text: int = 4000) -> Dict[str, Any]:
    """One operator as a compact record. ``text``: none | head | full."""
    n = node.n
    rec: Dict[str, Any] = {"path": node.path, "type": node.op_type}
    if "x" in n:
        rec["position"] = [n["x"], n["y"]]
    wires = project.inputs_of(node)
    if wires:
        rec["inputs"] = wires
    flags = _flags_view(node)
    if flags:
        rec["flags"] = flags
    for key in ("comment", "tags", "dock", "exports"):
        if n.get(key):
            rec[key] = ("%s/%s" % (node.parent or "", n[key])) if key == "dock" else n[key]
    if n.get("has_storage"):
        rec["has_storage"] = True
    if params:
        values = project.params_of(node)
        if values:
            rec["params"] = values
    custom = node.cparms
    if custom["pars"]:
        rec["custom_pages"] = custom["pages"]
        rec["custom_pars"] = custom["pars"] if params else [p["name"] for p in custom["pars"]]
    body = node.text
    if body is not None:
        rec["text_lines"] = body.count("\n") + (1 if body and not body.endswith("\n") else 0)
        if text == "full" or (text == "head" and len(body) <= max_text):
            rec["text"] = body if len(body) <= 200_000 else body[:200_000]
        elif text == "head":
            rec["text"] = body[:max_text]
            rec["text_truncated"] = len(body)
    table = node.table
    if table is not None:
        rec["table_shape"] = [len(table), len(table[0]) if table else 0]
        if text != "none":
            rec["table"] = table[:50] if text == "head" else table
    if node.children:
        rec["children"] = len(node.children)
    return rec


def summarize(project: Project, top: int = 12) -> Dict[str, Any]:
    """What is in the file: build, size, families, commonest types, the
    top-level networks, where the code lives and what it points at on disk."""
    families: Counter = Counter()
    types: Counter = Counter()
    code_lines = dats_with_text = expressions = binds = 0
    external: List[Dict[str, str]] = []
    custom_comps = 0
    for node in project.nodes.values():
        families[node.family] += 1
        types[node.op_type] += 1
        body = node.text
        if body:
            dats_with_text += 1
            code_lines += body.count("\n") + 1
        if node.is_comp and node.cparms["pars"]:
            custom_comps += 1
        for p in node.parms:
            if p["mode"] == "expression":
                expressions += 1
            elif p["mode"] == "bind":
                binds += 1
            if p["name"] in _FILE_PARS and p["mode"] == "constant" and p.get("value"):
                if len(external) < 200:
                    external.append({"path": node.path, "par": p["name"], "value": p["value"]})
    roots = []
    for root in project.children(None):
        roots.append({"path": root.path, "type": root.op_type,
                      "operators": sum(1 for _ in project.walk(root.path))})
    return {
        "file": project.source, "kind": project.kind, "saved_with": project.build,
        "operators": len(project.nodes), "by_family": dict(families.most_common()),
        "top_types": dict(types.most_common(top)),
        "roots": roots,
        "dats_with_text": dats_with_text, "text_lines": code_lines,
        "expressions": expressions, "binds": binds,
        "comps_with_custom_parameters": custom_comps,
        "external_files": external,
    }


#: Parameters that name a file or folder on disk.
_FILE_PARS = {"file", "externaltox", "folder", "rootfolder", "savefile",
              "tox", "moviefile", "audiofile", "imagefile", "fontfile"}


def read_network(project: Project, path: Optional[str] = None, depth: int = 1,
                 params: bool = True, text: str = "none", max_ops: int = 150) -> Dict[str, Any]:
    """The operators under ``path`` (``/`` = the file's roots), ``depth`` levels down."""
    if path and path != "/" and project.node(path) is None:
        close = difflib.get_close_matches("/" + path.strip("/"), list(project.nodes), n=5, cutoff=0.5)
        return {"ok": False, "error": "no operator at %s in %s" % (path, project.source),
                "did_you_mean": close, "roots": project.roots}
    out: Dict[str, Any] = {"ok": True, "file": project.source, "saved_with": project.build,
                           "path": ("/" + path.strip("/")) if path and path != "/" else "/"}
    target = project.node(path) if path and path != "/" else None
    if target is not None:
        out["operator"] = describe(project, target, params=params, text=text)
    ops, total = [], 0
    for node in project.walk(path, depth=max(1, int(depth or 1))):
        total += 1
        if len(ops) < max_ops:
            ops.append(describe(project, node, params=params, text=text))
    out["operators"] = ops
    out["count"] = total
    if total > len(ops):
        out["truncated"] = "%d of %d operators shown; narrow `path` or lower `depth`" % (len(ops), total)
    return out


def grep(project: Project, pattern: str, scope: str = "all", path: Optional[str] = None,
         ignore_case: bool = True, max_matches: int = 80) -> Dict[str, Any]:
    """Search what no file search can reach: the code and tables inside DATs,
    parameter values and expressions, names and comments.

    ``scope``: all | text | params | names | tables.
    """
    try:
        rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        return {"ok": False, "error": "bad regular expression: %s" % exc}
    want = (lambda s: scope in ("all", s))
    prefix = ("/" + path.strip("/") + "/") if path and path != "/" else None
    hits: List[Dict[str, Any]] = []
    total = 0

    def add(rec):
        nonlocal total
        total += 1
        if len(hits) < max_matches:
            hits.append(rec)

    for node in project.nodes.values():
        if prefix and not (node.path + "/").startswith(prefix):
            continue
        if want("names") and rx.search(node.name):
            add({"path": node.path, "type": node.op_type, "where": "name"})
        if want("names") and node.n.get("comment") and rx.search(node.n["comment"]):
            add({"path": node.path, "where": "comment", "match": node.n["comment"][:200]})
        if want("text"):
            body = node.text
            if body:
                for lineno, line in enumerate(body.splitlines(), 1):
                    if rx.search(line):
                        add({"path": node.path, "where": "text", "line": lineno,
                             "match": line.strip()[:240]})
        if want("params"):
            for p in node.parms:
                for key in ("value", "expr", "bind"):
                    value = p.get(key)
                    if isinstance(value, str) and value and rx.search(value):
                        add({"path": node.path, "where": "param", "par": p["name"],
                             "field": key, "mode": p["mode"], "match": value[:240]})
        if want("tables"):
            table = node.table
            if table:
                for r, row in enumerate(table):
                    for c, cell in enumerate(row):
                        if cell and rx.search(cell):
                            add({"path": node.path, "where": "table", "row": r, "col": c,
                                 "match": cell[:240]})
    out: Dict[str, Any] = {"ok": True, "file": project.source, "pattern": pattern,
                           "count": total, "matches": hits}
    if total > len(hits):
        out["truncated"] = "%d of %d matches shown" % (len(hits), total)
    return out


def _text_diff(a: str, b: str, limit: int = 60) -> List[str]:
    lines = list(difflib.unified_diff(a.splitlines(), b.splitlines(), "before", "after",
                                      lineterm="", n=1))
    if len(lines) > limit:
        lines = lines[:limit] + ["... %d more diff lines" % (len(lines) - limit)]
    return lines


def diff(a: Project, b: Project, path: Optional[str] = None, max_items: int = 100) -> Dict[str, Any]:
    """What changed between two saves, by meaning: operators added, removed,
    retyped, rewired, re-parameterised, re-flagged, and DAT code edits.
    Operators that were only moved are counted apart, so a tidy-up cannot
    bury a real change."""
    prefix = ("/" + path.strip("/")) if path and path != "/" else ""

    def scoped(project):
        return {p: n for p, n in project.nodes.items()
                if not prefix or p == prefix or p.startswith(prefix + "/")}

    na, nb = scoped(a), scoped(b)
    added = sorted(set(nb) - set(na))
    removed = sorted(set(na) - set(nb))
    changed: List[Dict[str, Any]] = []
    moved: List[str] = []
    for p in sorted(set(na) & set(nb)):
        x, y = na[p], nb[p]
        rec: Dict[str, Any] = {}
        if x.op_type != y.op_type:
            rec["retyped"] = [x.op_type, y.op_type]
        wa = [(w["index"], w["from"]) for w in a.inputs_of(x)]
        wb = [(w["index"], w["from"]) for w in b.inputs_of(y)]
        if wa != wb:
            rec["rewired"] = {"before": ["%d<-%s" % w for w in wa], "after": ["%d<-%s" % w for w in wb]}
        pa, pb = a.params_of(x), b.params_of(y)
        if pa != pb:
            delta = {}
            for key in sorted(set(pa) | set(pb)):
                if pa.get(key) != pb.get(key):
                    delta[key] = {"before": pa.get(key, "<default>"), "after": pb.get(key, "<default>")}
            rec["params"] = delta
        fa, fb = sorted(_flags_view(x)), sorted(_flags_view(y))
        if fa != fb:
            rec["flags"] = {"before": fa, "after": fb}
        ta, tb = x.text, y.text
        if (ta or "") != (tb or ""):
            rec["text"] = _text_diff(ta or "", tb or "")
        if (x.table or None) != (y.table or None):
            rec["table"] = {"before_shape": [len(x.table or []), len((x.table or [[]])[0])],
                            "after_shape": [len(y.table or []), len((y.table or [[]])[0])]}
        ca = [(c["name"], c.get("style")) for c in x.cparms["pars"]]
        cb = [(c["name"], c.get("style")) for c in y.cparms["pars"]]
        if ca != cb:
            rec["custom_pars"] = {"added": [n for n, _ in cb if n not in dict(ca)],
                                  "removed": [n for n, _ in ca if n not in dict(cb)]}
        if rec:
            rec["path"] = p
            changed.append(rec)
        elif (x.n.get("x"), x.n.get("y")) != (y.n.get("x"), y.n.get("y")):
            moved.append(p)
    out: Dict[str, Any] = {
        "ok": True, "before": a.source, "after": b.source,
        "summary": {"added": len(added), "removed": len(removed), "changed": len(changed),
                    "moved_only": len(moved)},
        "added": [{"path": p, "type": nb[p].op_type} for p in added[:max_items]],
        "removed": [{"path": p, "type": na[p].op_type} for p in removed[:max_items]],
        "changed": changed[:max_items],
        "moved_only": moved[:max_items],
    }
    if a.build.get("build") != b.build.get("build"):
        out["saved_with"] = {"before": a.build.get("build"), "after": b.build.get("build")}
    return out


def check(project: Project, path: Optional[str] = None, max_items: int = 60) -> Dict[str, Any]:
    """Hold a saved project against the parameter catalog of the installed
    build: operator types it does not know, parameter names it does not have
    (renamed or removed since the file was saved), menu values outside the
    menu, and the files on disk the project depends on."""
    cat = _catalog()
    if cat is None:
        return {"ok": False, "error": "the parameter catalog is not available in this install"}
    prefix = ("/" + path.strip("/")) if path and path != "/" else ""
    unknown_types: Counter = Counter()
    unknown_pars: List[Dict[str, Any]] = []
    bad_menus: List[Dict[str, Any]] = []
    missing_files: List[Dict[str, Any]] = []
    checked = 0
    for node in project.nodes.values():
        if prefix and not (node.path == prefix or node.path.startswith(prefix + "/")):
            continue
        checked += 1
        info = cat.op_info(node.op_type)
        if info is None:
            unknown_types["%s:%s" % (node.family, node.n.get("type", ""))] += 1
            continue
        known = info.get("by_name") or {}
        custom = project.custom_styles(node) if node.is_comp else {}
        for p in node.parms:
            name = p["name"]
            if _UI_STATE_PAR.match(name) or name in custom:
                continue
            spec = known.get(name) or _sequence_spec(name, known)
            if spec is None:
                # A custom parameter keeps its value in .parm too; without a
                # .cparm (a clone, a replicant) the capital first letter is
                # the only sign, and TouchDesigner enforces that convention.
                if name[:1].isupper():
                    continue
                if len(unknown_pars) < max_items:
                    unknown_pars.append({"path": node.path, "type": node.op_type, "par": name,
                                         "suggest": cat.suggest(node.op_type, name, 3)})
                continue
            if (p["mode"] == "constant" and spec.get("style") == "Menu" and spec.get("menu")
                    and p["value"] not in spec["menu"] and len(bad_menus) < max_items):
                bad_menus.append({"path": node.path, "type": node.op_type, "par": name,
                                  "value": p["value"], "valid": spec["menu"][:12]})
            if (name in _FILE_PARS and p["mode"] == "constant" and p.get("value")
                    and len(missing_files) < max_items):
                value = p["value"]
                if os.path.isabs(value) and not os.path.exists(value):
                    missing_files.append({"path": node.path, "par": name, "value": value})
    return {
        "ok": True, "file": project.source, "saved_with": project.build.get("build"),
        "catalog_build": cat.build(), "operators_checked": checked,
        "unknown_types": dict(unknown_types.most_common(max_items)),
        "unknown_parameters": unknown_pars,
        "invalid_menu_values": bad_menus,
        "missing_files": missing_files,
        "clean": not (unknown_types or unknown_pars or bad_menus or missing_files),
    }


def to_spec(project: Project, path: str) -> Dict[str, Any]:
    """The network inside ``path`` as a build spec -- the format
    ``td_validate_build`` checks and the live ``build_network`` creates.

    One level: the children of ``path``, wired by name. A child COMP is
    emitted as an empty COMP of its type (its own network is another call).
    """
    comp = project.node(path)
    if comp is None:
        return {"ok": False, "error": "no operator at %s" % path}
    kids = project.children(path)
    names = {k.path: k.name for k in kids}
    operators = []
    skipped = []
    for k in kids:
        rec: Dict[str, Any] = {"name": k.name, "type": k.op_type}
        if "x" in k.n:
            rec["position"] = [k.n["x"], k.n["y"]]
        wires = sorted(project.inputs_of(k), key=lambda w: w["index"])
        if wires:
            slots: List[Optional[str]] = [None] * (wires[-1]["index"] + 1)
            for w in wires:
                slots[w["index"]] = names.get(w["from"])
            rec["inputs"] = slots
        values = project.params_of(k)
        if values:
            rec["parameters"] = values
        flags = {f: True for f in _flags_view(k) if f in ("bypass", "display", "render", "lock", "viewer")}
        if flags:
            rec["flags"] = flags
        if k.text is not None and not k.n.get("dock"):
            rec["text"] = k.text
        if k.n.get("dock"):
            skipped.append(k.name)  # docked DATs are created with their host
            continue
        operators.append(rec)
    return {"ok": True, "name": comp.name, "source": "%s:%s" % (project.source, comp.path),
            "operators": operators, "connections": [],
            "skipped_docked": skipped,
            "nested_comps": [k.name for k in kids if k.children]}


# --------------------------------------------------------------------------
# Self-measurement
# --------------------------------------------------------------------------
def selftest(directory: str) -> Dict[str, Any]:
    """Re-measure the grammar over every expanded project under ``directory``.

    Reports what a new build would break: `.parm` lines whose value count
    disagrees with the flags word, payloads whose length field is off, table
    files that do not consume exactly, operator types the catalog cannot name.
    """
    stats: Counter = Counter()
    odd: Dict[str, List[str]] = {"parm": [], "text": [], "table": [], "type": []}
    unknown_types: Counter = Counter()
    for root, _dirs, files in os.walk(directory):
        if ".dir" not in root:
            continue
        for fname in files:
            full = os.path.join(root, fname)
            if fname.endswith(".parm"):
                body = _read_text_file(Path(full))
                records = parse_parms(body)
                stats["parm_records"] += len(records)
                if records and records[0].get("legacy"):
                    stats["parm_legacy_files"] += 1
                    continue
                raw = [ln for ln in body.splitlines() if ln and ln != "?"]
                continued = sum(str(r.get(k, "")).count("\n") for r in records
                                for k in ("value", "expr", "bind", "extra"))
                if len(records) + continued != len(raw):
                    stats["parm_lines_unaccounted"] += abs(len(raw) - len(records) - continued)
                    if len(odd["parm"]) < 12:
                        odd["parm"].append(os.path.relpath(full, directory)[-110:])
                stats["parm_continued_lines"] += continued
                for rec in records:
                    stats["parm_" + rec["mode"]] += 1
                    if rec["flags"] & FLAG_EXTRA_VALUE:
                        stats["parm_extra_value_bit"] += 1
                    if rec["name"][:1].isupper():
                        stats["parm_capitalised"] += 1
                        if rec["flags"] & 0x4000000:
                            stats["parm_capitalised_with_0x4000000"] += 1
                    elif rec["flags"] & 0x4000000:
                        stats["parm_lowercase_with_0x4000000"] += 1
            elif fname.endswith(".text"):
                data = Path(full).read_bytes()
                stats["text_files"] += 1
                ok = (len(data) >= _PAYLOAD_HEADER and data[:3] == b"2\n*"
                      and struct.unpack(">I", data[23:27])[0] == len(data) - _PAYLOAD_HEADER)
                if len(data) == 19 and data[:3] == b"2\n*":
                    stats["text_empty"] += 1
                elif not ok:
                    stats["text_length_mismatch"] += 1
                    if len(odd["text"]) < 12:
                        odd["text"].append("%s head=%r" % (os.path.relpath(full, directory)[-90:], data[:27]))
            elif fname.endswith(".table"):
                data = Path(full).read_bytes()
                stats["table_files"] += 1
                if len(data) >= 19 and data[:3] == b"1\n*":
                    _one, rows, cols, _pad = struct.unpack(">4I", data[3:19])
                    pos, cells = 19, 0
                    while cells < rows * cols and pos + 8 <= len(data):
                        _tag, length = struct.unpack(">2I", data[pos:pos + 8])
                        pos += 8 + length
                        cells += 1
                    if cells == rows * cols and pos == len(data):
                        stats["table_exact"] += 1
                        continue
                stats["table_inexact"] += 1
                if len(odd["table"]) < 12:
                    odd["table"].append("%s head=%r" % (os.path.relpath(full, directory)[-90:], data[:19]))
            elif fname.endswith(".n"):
                stats["nodes"] += 1
                node = parse_node(_read_text_file(Path(full)))
                cat = _catalog()
                if cat is not None and cat.resolve_op_type(node["type"], node["family"]) is None:
                    unknown_types["%s:%s" % (node["family"], node["type"])] += 1
    return {"stats": dict(stats), "unknown_types": dict(unknown_types.most_common(40)),
            "odd": {k: v for k, v in odd.items() if v}}


def _main(argv: List[str]) -> int:
    if len(argv) >= 2 and argv[0] == "--selftest":
        print(json.dumps(selftest(argv[1]), indent=1))
        return 0
    if not argv:
        print(__doc__)
        return 0
    started = time.time()
    project = load(argv[0])
    view = summarize(project)
    view["loaded_in_s"] = round(time.time() - started, 2)
    print(json.dumps(view, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
