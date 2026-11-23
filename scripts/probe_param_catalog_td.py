"""Probe every operator type in a running TouchDesigner into a parameter catalog.

Runs INSIDE TouchDesigner (Textport, a Text DAT, or the bridge's
``execute_python``). It writes ground truth that no documentation scrape can
give: the parameter *names* ``COMP.create`` / ``par.<name>`` accept, their
style, page, tuplet, menu tokens, ranges, and the value a freshly created
operator actually holds.

Why live and not the wiki
-------------------------
The scraped corpus keys parameters by their *label* ("Brightness 1",
"Clamp Input"), types every one of them ``float`` and leaves defaults null, so
anything that validated or resolved names against it was working from fiction
(TD_MCP 1.13 found the resolver rewriting a correct ``brightness1`` into the
label ``'Brightness 1'``). Derivative's own TDMCP reached the same conclusion
about ``TDParameterHelp.json``: on 2025.33230 it mistyped 711 of 15,661
parameters, listed 728 that do not exist and omitted 766 that do. The running
build is the only source that cannot drift from itself.

Facts this relies on (verified on 2025.33230):
  * ``families`` maps each family to its operator classes (659 in all).
  * ``/sys/quiet`` is a baseCOMP with cooking disabled -- probes there do not
    cook, open devices, or touch the user's network.
  * ``Par.default`` is not always the creation value (Text DAT ``language``
    declares ``input`` while a new DAT holds ``text``), so ``default`` here is
    ``Par.eval()`` on a fresh instance, with ``declared_default`` recorded only
    when the two differ.

Usage (the bridge forbids the ``open`` builtin, so this uses pathlib)::

    FAMILIES = ['TOP']                     # optional; default = all families
    OUT_DIR = r'C:/path/to/TD_MCP/td_mcp/kb/corpus/param_catalog'
    <exec this file>

One JSON file per family lands in OUT_DIR (``TOP.json`` ...); the loader in
``td_mcp.kb.param_catalog`` reads the directory as is. ``result`` is left as a
summary for ``execute_python`` callers.

THEN, ALWAYS, outside TouchDesigner::

    python -m td_mcp.kb.param_catalog --sanitize

The raw probe records what THIS machine offers: local IP addresses
(`localaddress` menus), device GUIDs, the titles of every open window (Screen
Grab TOP `source`), installed fonts and the project's absolute path. The
sanitize step strips them; tests/test_param_catalog.py fails until it has run.
"""

import datetime as _dt
import json as _json
import pathlib as _pathlib

_NUMERIC_STYLES = {"Float", "Int", "XY", "XYZ", "XYZW", "WH", "UV", "UVW",
                   "RGB", "RGBA"}
_REF_STYLES = {"OP", "COMP", "OBJ", "PanelCOMP", "TOP", "CHOP", "SOP", "DAT",
               "MAT", "POP", "Object", "Python"}
_HELP_MAX = 240


def _plain(value):
    """A JSON-safe rendering of a parameter value."""
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, float) and value != value:  # NaN
            return None
        return value
    path = getattr(value, "path", None)
    if path is not None:
        return str(path)
    return str(value)


def _par_record(p):
    style = str(p.style)
    try:
        if style in _REF_STYLES:
            created = p.val  # the authored text, not the resolved operator
        else:
            created = p.eval()
    except Exception:  # noqa: BLE001 - an unevaluable default is still a fact
        created = p.val
    rec = {"name": p.name, "label": p.label, "style": style,
           "page": p.page.name if p.page is not None else None,
           "default": _plain(created)}
    try:
        declared = p.default
        if _plain(declared) != rec["default"]:
            rec["declared_default"] = _plain(declared)
    except Exception:  # noqa: BLE001
        pass
    if p.tupletName and p.tupletName != p.name:
        rec["tuplet"] = p.tupletName
    if p.isMenu:
        names = [str(x) for x in (p.menuNames or [])]
        labels = [str(x) for x in (p.menuLabels or [])]
        rec["menu"] = names
        if labels and labels != names:
            rec["menu_labels"] = labels
    if style in _NUMERIC_STYLES:
        rec["range"] = [_plain(p.normMin), _plain(p.normMax)]
        clamp = [_plain(p.min) if p.clampMin else None,
                 _plain(p.max) if p.clampMax else None]
        if clamp != [None, None]:
            rec["clamp"] = clamp
    if p.readOnly:
        rec["read_only"] = True
    if p.sequence is not None:
        rec["sequence"] = p.sequence.name
    helptext = (p.help or "").strip()
    # Help is per tuplet in TD (colorr/g/b share one string): keep it on the
    # first component only. "Help Not Available." is TD's placeholder.
    if (helptext and helptext != "Help Not Available."
            and (not p.tupletName or p.vecIndex == 0)):
        rec["help"] = helptext[:_HELP_MAX]
    return rec


def _keep(p):
    """Skip TD's internal page-index parameter (no page, never user-set)."""
    return not (p.name == "pageindex" and p.page is None)


def probe_family(family, quiet):
    ops, failed = {}, {}
    for cls in families[family]:  # noqa: F821 - TD global
        name = cls.__name__
        node = None
        try:
            node = quiet.create(cls, "td_mcp_probe")
            ops[name] = {
                "family": node.family,
                "label": node.label,
                "type": node.type,
                "min_inputs": node.minInputs,
                "max_inputs": node.maxInputs,
                "filter": bool(node.isFilter),
                "pages": [pg.name for pg in node.pages],
                "pars": [_par_record(p) for p in node.pars() if _keep(p)],
            }
        except Exception as exc:  # noqa: BLE001 - record and keep going
            failed[name] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
        finally:
            if node is not None:
                try:
                    node.destroy()
                except Exception:  # noqa: BLE001
                    pass
    return ops, failed


def _dump(doc):
    """One operator per line: diffable per operator without the whitespace
    of a fully indented file (which tripled the size)."""
    head = {k: v for k, v in doc.items() if k != "ops"}
    lines = ['{"ops": {']
    names = sorted(doc["ops"])
    for i, name in enumerate(names):
        tail = "," if i < len(names) - 1 else ""
        lines.append("%s: %s%s" % (_json.dumps(name), _json.dumps(
            doc["ops"][name], sort_keys=True, ensure_ascii=False,
            separators=(",", ":")), tail))
    lines.append("},")
    body = _json.dumps(head, sort_keys=True, ensure_ascii=False)[1:-1]
    lines.append(body + "}")
    return "\n".join(lines) + "\n"


def _build_id():
    """'2025.33230' -- app.build is the full id on 2025.x; older builds split it."""
    build = str(app.build)  # noqa: F821 - TD global
    if "." not in build:
        build = "%s.%s" % (app.version, build)  # noqa: F821
    return build


def run(fams=None, out_dir=None):
    quiet = op("/sys/quiet")  # noqa: F821 - TD global
    if quiet is None:
        raise RuntimeError("/sys/quiet is missing in this build")
    out = _pathlib.Path(out_dir) if out_dir else None
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
    build = _build_id()
    summary = {"build": build, "families": {}}
    for fam in (fams or list(families.keys())):  # noqa: F821
        ops, failed = probe_family(fam, quiet)
        doc = {"build": build,
               "generated": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "family": fam, "ops": ops, "failed": failed}
        if out is not None:
            (out / ("%s.json" % fam)).write_text(_dump(doc), encoding="utf-8")
        summary["families"][fam] = {"ops": len(ops), "failed": failed,
                                    "pars": sum(len(o["pars"]) for o in ops.values())}
    return summary


try:
    _fams = FAMILIES  # noqa: F821 - optional, set by the caller before exec
except NameError:
    _fams = None
try:
    _out = OUT_DIR  # noqa: F821
except NameError:
    _out = None
result = run(_fams, _out)
