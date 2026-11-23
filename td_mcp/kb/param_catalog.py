"""Ground-truth operator/parameter catalog, probed from a running TouchDesigner.

The data lives in ``kb/corpus/param_catalog/<FAMILY>.json`` and is produced by
``scripts/probe_param_catalog_td.py`` running inside TouchDesigner: every
operator class in ``families`` instantiated in ``/sys/quiet`` and read back.
It is the one source in the knowledge base whose parameter *names* are the
names ``par.<name>`` accepts -- the scraped corpus keys parameters by label
("Brightness 1") and types all of them float.

Everything here is pure stdlib and read-only; it degrades to "unknown" when
the catalog files are absent (a checkout that never ran the probe).

    >>> resolve_op_type("Level TOP")
    'levelTOP'
    >>> [p["name"] for p in params("levelTOP") if p["name"].startswith("bright")]
    ['brightness1', 'brightness2']
"""

from __future__ import annotations

import difflib
import fnmatch
import glob
import json
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional

CATALOG_DIR = os.path.join(os.path.dirname(__file__), "corpus", "param_catalog")
FAMILIES = ("TOP", "CHOP", "SOP", "POP", "DAT", "MAT", "COMP")

#: Styles that hold an operator reference or a path. Their stored value is the
#: authored text, which is what a set_parameters call should send.
REF_STYLES = {"OP", "COMP", "OBJ", "PanelCOMP", "TOP", "CHOP", "SOP", "DAT",
              "MAT", "POP", "Object", "Python"}
NUMERIC_STYLES = {"Float", "Int", "XY", "XYZ", "XYZW", "WH", "UV", "UVW",
                  "RGB", "RGBA"}


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


@lru_cache(maxsize=1)
def _load() -> Dict[str, Any]:
    ops: Dict[str, Any] = {}
    builds = set()
    for path in sorted(glob.glob(os.path.join(CATALOG_DIR, "*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            continue
        builds.add(str(doc.get("build", "")))
        for name, rec in (doc.get("ops") or {}).items():
            rec = dict(rec)
            rec["op_type"] = name
            rec["by_name"] = {p["name"]: p for p in rec.get("pars") or []}
            ops[name] = rec
    return {"ops": ops, "builds": sorted(b for b in builds if b)}


@lru_cache(maxsize=1)
def _index() -> Dict[str, Any]:
    """Lookup tables: squashed opType, display name and corpus slug -> opType,
    plus short type -> [opTypes] for cross-family ambiguity."""
    exact, short = {}, {}
    for name, rec in _load()["ops"].items():
        fam = rec.get("family", "")
        label = rec.get("label", "")
        for key in (name, f"{label} {fam}", f"{label}_{fam}",
                    f"{rec.get('type', '')}{fam}"):
            exact.setdefault(_squash(key), name)
        short.setdefault(_squash(rec.get("type", "")), []).append(name)
        short.setdefault(_squash(label), [])
        if name not in short[_squash(label)]:
            short[_squash(label)].append(name)
    return {"exact": exact, "short": short}


def available() -> bool:
    return bool(_load()["ops"])


def build() -> Optional[str]:
    """The TouchDesigner build the catalog was probed from ('2025.33230')."""
    builds = _load()["builds"]
    return builds[-1] if builds else None


def op_types(family: Optional[str] = None) -> List[str]:
    ops = _load()["ops"]
    if family:
        fam = family.upper()
        return sorted(n for n, r in ops.items() if r.get("family") == fam)
    return sorted(ops)


def candidates(name: str) -> List[str]:
    """Every opType a name could mean: ``'noise'`` -> four families."""
    if not name or not available():
        return []
    idx = _index()
    key = _squash(name)
    if key in idx["exact"]:
        return [idx["exact"][key]]
    return list(idx["short"].get(key, []))


def resolve_op_type(name: str, family: Optional[str] = None) -> Optional[str]:
    """Map any spelling of an operator type to its opType, or None.

    Accepts the opType (``noiseTOP``), any casing of it, the display name
    (``'GLSL Multi TOP'``), the corpus slug (``glsl_multi_top``) and the short
    type plus a ``family`` hint (``'noise', family='CHOP'``). A bare short type
    that exists in several families returns None -- use :func:`candidates`.
    """
    if not name:
        return None
    hits = candidates(name)
    if family:
        fam = family.upper()
        fam_hits = [h for h in hits if _load()["ops"][h].get("family") == fam]
        if not fam_hits:
            fam_hits = candidates(f"{name}{fam}")
        hits = fam_hits
    return hits[0] if len(hits) == 1 else None


def op_info(op_type: str) -> Optional[Dict[str, Any]]:
    name = resolve_op_type(op_type)
    return _load()["ops"].get(name) if name else None


def params(op_type: str) -> List[Dict[str, Any]]:
    info = op_info(op_type)
    return list(info.get("pars") or []) if info else []


def par(op_type: str, name: str) -> Optional[Dict[str, Any]]:
    info = op_info(op_type)
    return (info.get("by_name") or {}).get(name) if info else None


def display_name(op_type: str) -> Optional[str]:
    info = op_info(op_type)
    return f"{info['label']} {info['family']}" if info else None


def tuplets(op_type: str) -> Dict[str, List[str]]:
    """``{'resolution': ['resolutionw', 'resolutionh'], 't': ['tx','ty','tz'], ...}``."""
    out: Dict[str, List[str]] = {}
    for p in params(op_type):
        tn = p.get("tuplet")
        if tn:
            out.setdefault(tn, []).append(p["name"])
    return out


def schema(op_type: str) -> Dict[str, Dict[str, Any]]:
    """``{par_name: {type, style, default, min, max, menu, ...}}`` keyed by the
    real parameter name. Shape-compatible with ``corpus.param_schema``."""
    out = {}
    for p in params(op_type):
        rng = p.get("range") or [None, None]
        clamp = p.get("clamp") or [None, None]
        out[p["name"]] = {
            "type": p.get("style"),
            "style": p.get("style"),
            "dataType": "number" if p.get("style") in NUMERIC_STYLES else None,
            "label": p.get("label"),
            "page": p.get("page"),
            "default": p.get("default"),
            # min/max are the HARD clamps: a value outside them is clamped by
            # TD. The slider range is advisory and lives under `range`.
            "min": clamp[0],
            "max": clamp[1],
            "range": rng,
            "menu": list(p.get("menu") or []),
            "menu_labels": list(p.get("menu_labels") or []),
            "tuplet": p.get("tuplet"),
            "read_only": bool(p.get("read_only")),
            "animatable": p.get("style") in NUMERIC_STYLES,
        }
    return out


def suggest(op_type: str, key: str, n: int = 5) -> List[str]:
    """Parameter names close to ``key`` by name *or* label."""
    pars = params(op_type)
    if not pars:
        return []
    names = [p["name"] for p in pars]
    by_label = {}
    for p in pars:
        by_label.setdefault(_squash(p.get("label", "")), p["name"])
    out = difflib.get_close_matches(key.lower(), names, n=n, cutoff=0.6)
    lab = by_label.get(_squash(key))
    if lab and lab not in out:
        out.insert(0, lab)
    for hit in difflib.get_close_matches(_squash(key), list(by_label), n=n, cutoff=0.7):
        nm = by_label[hit]
        if nm not in out:
            out.append(nm)
    return out[:n]


def describe(op_type: str, pattern: Optional[str] = None, page: Optional[str] = None,
             include_menus: bool = True, include_help: bool = True,
             include_common: bool = True) -> Dict[str, Any]:
    """Offline twin of the live ``describe_op_type``: what an operator type's
    parameters are called, before it exists. Unknown types get did_you_mean."""
    name = resolve_op_type(op_type)
    if not name:
        options = candidates(op_type)
        out = {"ok": False, "error": f"unknown operator type {op_type!r}"}
        if len(options) > 1:
            out["error"] = f"{op_type!r} is ambiguous across families"
            out["candidates"] = options
        else:
            out["did_you_mean"] = difflib.get_close_matches(
                op_type, op_types(), n=5, cutoff=0.6)
        out["hint"] = ("Type names are TD class names: noiseTOP, lfoCHOP, "
                       "gridPOP, textDAT, pbrMAT, baseCOMP.")
        return out
    info = _load()["ops"][name]
    rows = info.get("pars") or []
    if page:
        rows = [r for r in rows if (r.get("page") or "").lower() == page.lower()]
    elif not include_common:
        rows = [r for r in rows if r.get("page") != "Common"]
    if pattern:
        pat = pattern.lower()
        if not any(ch in pat for ch in "*?["):
            pat = f"*{pat}*"
        rows = [r for r in rows if fnmatch.fnmatchcase(r["name"].lower(), pat)
                or fnmatch.fnmatchcase((r.get("label") or "").lower(), pat)]
    drop = set()
    if not include_menus:
        drop |= {"menu", "menu_labels"}
    if not include_help:
        drop.add("help")
    rows = [{k: v for k, v in r.items() if k not in drop} for r in rows]
    out = {"ok": True, "op_type": name, "display_name": f"{info['label']} {info['family']}",
           "family": info["family"], "pages": info.get("pages"),
           "inputs": [info.get("min_inputs"), info.get("max_inputs")],
           "count": len(rows), "total": len(info.get("pars") or []),
           "parameters": rows, "source": f"live probe, TouchDesigner {build()}"}
    if (pattern or page) and not rows:
        out["hint"] = ("No parameter matched -- the name guess is wrong, not the "
                       "operator. Retry without filters and match by label.")
    return out


# ---------------------------------------------------------------------------
# Sanitizing: the probe records what THIS machine offers, and some of that is
# machine state that must never be committed -- verified in the 2025.33230
# probe: every `localaddress` menu listed the host's IP addresses, audio and
# video device menus carried hardware GUIDs, Screen Grab TOP `source` listed
# the titles of every open window, font menus listed installed fonts, and
# Folder DAT `rootfolder` defaulted to the project's absolute path. Derivative's
# TDMCP drops the same class from its menu catalog ("machine state, like
# device and font menus"). Run after every probe; tests/test_param_catalog.py
# fails if a committed catalog still holds any of it.
# ---------------------------------------------------------------------------
_MACHINE_MENU = re.compile(
    r"\b\d{1,3}(?:\.\d{1,3}){3}\b"      # IPv4 address
    r"|\{[0-9a-fA-F-]{8,}"               # device GUID
    r"|\|\||\\\\\?\\"                    # device id separators / hardware paths
    r"|\b[A-Za-z]:[/\\]"                 # absolute drive path (window titles)
    r"|TouchDesigner 20\d\d")            # TD window title
_MACHINE_MENU_NAMES = {"font"}
_USER_PATH = re.compile(r"[/\\](?:Users|home)[/\\][^/\\]+", re.I)
_TD_INSTALL = re.compile(r"^[A-Za-z]:[/\\]Program Files[/\\]Derivative[/\\]TouchDesigner[^/\\]*",
                         re.I)
_PROBE_NAME = "td_mcp_probe"  # what scripts/probe_param_catalog_td.py names its instances
MACHINE_STATE_NOTE = ("menu depends on this machine (devices, addresses, fonts, "
                      "windows); read it live with describe_op_type")


def sanitize_par(p: Dict[str, Any]) -> bool:
    """Strip machine state from one parameter record in place; True if changed."""
    changed = False
    menu = [str(m) for m in (p.get("menu") or [])]
    labels = [str(m) for m in (p.get("menu_labels") or [])]
    if menu and (p.get("name") in _MACHINE_MENU_NAMES
                 or _MACHINE_MENU.search(" ".join(menu + labels))):
        for key in ("menu", "menu_labels"):
            p.pop(key, None)
        if isinstance(p.get("default"), str) and p["default"] in menu:
            p["default"] = None
        p["machine_state"] = True
        changed = True
    for key in ("default", "declared_default"):
        v = p.get(key)
        if not isinstance(v, str):
            continue
        if v == _PROBE_NAME:
            # The default IS the operator's own name (Out TOP/CHOP `label`):
            # the probe instance was called td_mcp_probe.
            p[key] = "<operator name>"
            changed = True
        elif _USER_PATH.search(v):
            p[key] = "<machine-specific path>"
            p["machine_state"] = True
            changed = True
        elif _TD_INSTALL.match(v):
            p[key] = _TD_INSTALL.sub("<td_install>", v).replace("\\", "/")
            changed = True
    return changed


def sanitize_dir(directory: str = CATALOG_DIR) -> int:
    """Sanitize every family file in ``directory``; returns parameters changed.
    Rewrites in the probe's one-operator-per-line layout."""
    total = 0
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        n = sum(sanitize_par(p) for rec in (doc.get("ops") or {}).values()
                for p in rec.get("pars") or [])
        if n:
            head = {k: v for k, v in doc.items() if k != "ops"}
            names = sorted(doc["ops"])
            lines = ['{"ops": {']
            for i, name in enumerate(names):
                lines.append("%s: %s%s" % (json.dumps(name), json.dumps(
                    doc["ops"][name], sort_keys=True, ensure_ascii=False,
                    separators=(",", ":")), "," if i < len(names) - 1 else ""))
            lines.append("},")
            lines.append(json.dumps(head, sort_keys=True, ensure_ascii=False)[1:-1] + "}")
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(lines) + "\n")
        total += n
    _load.cache_clear()
    _index.cache_clear()
    return total


_CHUNK_MAX = 2400


def _par_line(p: Dict[str, Any]) -> str:
    bits = [p.get("style") or "?"]
    if p.get("menu"):
        bits.append("menu " + "|".join(p["menu"][:14]) + ("|..." if len(p["menu"]) > 14 else ""))
    if p.get("default") not in (None, ""):
        bits.append(f"default {p['default']}")
    line = f"{p['name']} ({p.get('label', '')}; {', '.join(bits)})"
    if p.get("help"):
        line += f": {p['help'][:180]}"
    return line


def catalog_chunks() -> List[Dict[str, Any]]:
    """Retriever chunks carrying the exact parameter names and their help text.

    One chunk per operator per parameter page, split when a page is long, plus
    ONE chunk per family for the Common page that every operator of the family
    shares (instead of 659 copies of it). Category ``parameters``.
    """
    ops = _load()["ops"]
    out: List[Dict[str, Any]] = []
    common_done = set()
    tag = f"TouchDesigner {build()}" if build() else "TouchDesigner"
    for name in sorted(ops):
        rec = ops[name]
        fam, label = rec.get("family"), rec.get("label", name)
        display = f"{label} {fam}"
        pages: Dict[str, List[Dict[str, Any]]] = {}
        for p in rec.get("pars") or []:
            pages.setdefault(p.get("page") or "?", []).append(p)
        for page, rows in pages.items():
            if page == "Common":
                if fam in common_done:
                    continue
                common_done.add(fam)
                title = f"{fam} Common page parameters"
                head = (f"{title}: shared by every {fam} operator (exact parameter "
                        f"names from {tag}).")
                ident, aliases = f"params_{fam}_common", [f"{fam.lower()} common parameters"]
            else:
                title = f"{display} parameters: {page} page"
                head = (f"{display} (`{name}`) parameters on the {page} page, exact "
                        f"names from {tag}.")
                ident, aliases = f"params_{name}_{_squash(page)}", [name.lower(), display.lower()]
            lines = [_par_line(p) for p in rows]
            part, size, n = [], len(head), 0
            for line in lines + [None]:
                if line is None or (part and size + len(line) > _CHUNK_MAX):
                    if part:
                        out.append({
                            "id": ident + (f"_{n}" if n else ""),
                            "family": fam, "title": title, "category": "parameters",
                            "source": "td-live-probe", "url": None, "min_version": "all",
                            "tags": [fam.lower(), "parameters", page.lower()],
                            "aliases": aliases,
                            "text": head + " " + " | ".join(part),
                        })
                        n += 1
                    part, size = [], len(head)
                if line is not None:
                    part.append(line)
                    size += len(line) + 3
    return out


def stats() -> Dict[str, Any]:
    ops = _load()["ops"]
    fams: Dict[str, int] = {}
    for rec in ops.values():
        fams[rec.get("family", "?")] = fams.get(rec.get("family", "?"), 0) + 1
    return {"build": build(), "operators": len(ops),
            "parameters": sum(len(r.get("pars") or []) for r in ops.values()),
            "families": dict(sorted(fams.items()))}


def _main(argv=None):  # pragma: no cover - thin CLI
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("op_type", nargs="?", help="describe one operator type")
    ap.add_argument("--pattern")
    ap.add_argument("--page")
    ap.add_argument("--sanitize", metavar="DIR", nargs="?", const=CATALOG_DIR,
                    help="strip machine state from a freshly probed catalog (run after every probe)")
    a = ap.parse_args(argv)
    if a.sanitize:
        print(json.dumps({"sanitized_parameters": sanitize_dir(a.sanitize)}))
        return
    out = describe(a.op_type, a.pattern, a.page) if a.op_type else stats()
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":  # pragma: no cover
    _main()
