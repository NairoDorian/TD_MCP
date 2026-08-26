"""TDN — TouchDesigner Network serialization (diffable YAML).

A network is a plain dict::

    {"format": "tdn", "version": "2.1", "generator": "td-mcp",
     "network_path": "/project1", "operators": [Operator, ...]}

Operator::

    {"name", "type", "position":[x,y], "size":[w,h], "color":[r,g,b],
     "comment", "tags":[], "parameters":{name:value},
     "expressions":{name:expr}, "text": "...", "text_source": "embedded|file",
     "custom_pars":{}, "flags":[], "inputs":[src_name_or_null,...],
     "excluded_parameters":[...], "children":[Operator,...]}

Everything is pure-Python and dependency-light (PyYAML). Networks serialize to
text so builds are reviewable, git-diffable (git textconv strips volatile
headers), and restorable. The live bridge calls `import_network` to build a net
inside TD and `diff_tdn` to compare the live graph to an on-disk `.tdn`.

Version 2.1 (Wave 12) closes four fidelity holes, three of them found the hard
way by Embody's exporter and documented in
``github-mcp/Embody/docs/reports/tdn-exporter-fidelity-moonshine.md``:

* **Code was silently dropped.** There was no field for a DAT's text at all, so
  a checkpoint round-tripped a network while discarding every line of GLSL and
  every callback. Embody shipped the same class of bug behind an
  ``include_dat_content`` flag and fixed it with a rule worth stealing whole:
  the flag means "skip what a file already holds", never "discard" — a
  file-backed DAT is omitted, an unbacked one is *always* embedded. **Any doubt
  embeds.**
* **Expressions were frozen into constants.** A parameter in EXPRESSION mode is
  non-default, so a value-only exporter writes its current evaluation and the
  rebuilt network is dead. Expressions and bindings now travel in their own map.
* **A rewire diffed as equal.** ``diff_tdn`` compared parameters and type and
  nothing else, in a format whose entire selling point is diffability.
* **Runtime state leaked into committed files.** A negotiated port, a
  machine-specific asset path, live window geometry. Tag an operator
  ``tdn_exclude:<parname>`` and that parameter's constant value is omitted —
  visibly, in the file, because a silently omitted parameter is
  indistinguishable from one sitting at its default.
"""

import copy
import os

import yaml

FORMAT = "tdn"
VERSION = "2.1"
#: Header keys that change on every re-export of an unchanged net.
VOLATILE_KEYS = ("build", "generator", "td_build", "exported_at", "source_file")

#: Tag prefix marking one parameter as runtime state, not configuration.
EXCLUDE_TAG_PREFIX = "tdn_exclude:"

#: Operator fields compared by :func:`diff_tdn`. Anything a caller can change
#: has to be in here, or a real edit reports as no change at all.
COMPARED_FIELDS = ("type", "position", "size", "color", "comment", "tags",
                   "inputs", "flags", "text", "expressions")


def new_network(network_path="/project1", operators=None):
    return {
        "format": FORMAT,
        "version": VERSION,
        "generator": "td-mcp",
        "network_path": network_path,
        "operators": operators or [],
    }


def operator(name, op_type, position=None, parameters=None, inputs=None,
             comment=None, tags=None, children=None, color=None, size=None,
             flags=None, expressions=None, text=None, text_source=None,
             excluded_parameters=None):
    op = {"name": name, "type": op_type}
    if position is not None:
        op["position"] = list(position)
    if size is not None:
        op["size"] = list(size)
    if color is not None:
        op["color"] = list(color)
    if comment is not None:
        op["comment"] = comment
    if tags is not None:
        op["tags"] = list(tags)
    if parameters is not None:
        op["parameters"] = dict(parameters)
    if expressions:
        op["expressions"] = dict(expressions)
    if text is not None:
        op["text"] = text
        op["text_source"] = text_source or "embedded"
    elif text_source is not None:
        op["text_source"] = text_source
    if excluded_parameters:
        op["excluded_parameters"] = list(excluded_parameters)
    if inputs is not None:
        op["inputs"] = list(inputs)
    if flags is not None:
        op["flags"] = list(flags)
    if children is not None:
        op["children"] = list(children)
    return op


# ---------------------------------------------------------------------------
# Runtime-state exclusion
# ---------------------------------------------------------------------------
def excluded_parameters(tags):
    """Parameter names an operator's tags mark as runtime state.

    ``tdn_exclude:Port`` on the operator means "export this operator, its
    wiring and its other parameters, but not this parameter's constant value" —
    for a negotiated port, a session file path, a live readout. The tag lives in
    the project's own network rather than in this package's source, so it
    reaches parameters on non-COMP operators (which is exactly where ``Port``
    and ``file`` live) without anyone patching td-mcp.
    """
    out = []
    for tag in tags or []:
        text = str(tag)
        if text.startswith(EXCLUDE_TAG_PREFIX):
            name = text[len(EXCLUDE_TAG_PREFIX):].strip()
            if name:
                out.append(name)
    return out


def apply_exclusions(op):
    """Drop tag-excluded parameters from `op`, recording what was dropped.

    Expressions and bindings are deliberately *kept*: an expression is
    configuration (it says how the value is derived), while the constant it
    currently evaluates to is the session state worth losing.
    """
    names = excluded_parameters(op.get("tags"))
    if not names:
        return op
    op = dict(op)
    params = dict(op.get("parameters") or {})
    dropped, unknown = [], []
    for name in names:
        if name in params:
            del params[name]
            dropped.append(name)
        elif name not in (op.get("expressions") or {}):
            # A typo'd parameter name silently excludes nothing, which is the
            # worst outcome: the leak stays and the author believes it is
            # fixed. Record it so the exporter can warn.
            unknown.append(name)
    op["parameters"] = params
    if dropped:
        # Recorded in the file itself: a silently omitted parameter is
        # indistinguishable from one sitting at its default.
        op["excluded_parameters"] = sorted(dropped)
    if unknown:
        op["excluded_parameters_unknown"] = sorted(unknown)
    return op


# ---------------------------------------------------------------------------
# DAT text: any doubt embeds
# ---------------------------------------------------------------------------
def dat_text_policy(text, file_backed=False, externalized=False):
    """Decide whether a DAT's text is embedded in the export.

    Returns ``(should_embed, source)``. The flag means "skip what a file
    already holds", never "throw authored code away": if the content lives in a
    file that travels with the project, embedding it only duplicates it; if it
    does not, the export is the *only* copy and dropping it loses the code with
    the network.
    """
    if not text:
        return False, "empty"
    if file_backed or externalized:
        return False, "file"
    return True, "embedded"


def attach_dat_text(op, text, file_backed=False, externalized=False):
    """Attach a DAT's text to an operator dict under the embed policy."""
    embed, source = dat_text_policy(text, file_backed, externalized)
    op = dict(op)
    op["text_source"] = source
    if embed:
        op["text"] = text
    return op


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def resolve_output_path(output_file, project_folder=None):
    """Anchor an export path to the project rather than the process cwd.

    A relative ``output_file`` resolves against TouchDesigner's *current working
    directory*, not the project root — Embody's exporter wrote a 903 KB
    whole-project dump one directory deeper than intended, then failed to track
    it because ``Path.relative_to`` cannot relate a relative path to an absolute
    one, and the resulting ValueError was swallowed by a broad ``except`` and
    logged as a warning while the export reported success.
    """
    if not output_file:
        raise ValueError("output_file is required")
    path = str(output_file)
    if os.path.isabs(path):
        return os.path.normpath(path)
    base = project_folder or os.getcwd()
    return os.path.normpath(os.path.join(str(base), path))


def relative_to_project(path, project_folder):
    """Path relative to the project, or None when it lies outside it.

    ``None`` means "this file is outside the project" — a specific situation
    deserving a specific message, not an exception to swallow.
    """
    if not project_folder:
        return None
    try:
        rel = os.path.relpath(os.path.abspath(str(path)),
                              os.path.abspath(str(project_folder)))
    except ValueError:  # different drives on Windows
        return None
    if rel.startswith(".." + os.sep) or rel == "..":
        return None
    return rel.replace(os.sep, "/")


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------
def export_network(net, apply_tag_exclusions=True):
    """Serialize a network dict to TDN YAML (deterministic, diffable)."""
    net = copy.deepcopy(net)
    net.setdefault("format", FORMAT)
    net.setdefault("version", VERSION)
    net.setdefault("generator", "td-mcp")
    if apply_tag_exclusions:
        net["operators"] = _map_ops(net.get("operators"), apply_exclusions)
    return yaml.safe_dump(net, sort_keys=False, default_flow_style=False,
                          allow_unicode=True)


def import_network(text):
    """Parse TDN YAML back into a network dict."""
    net = yaml.safe_load(text)
    if not isinstance(net, dict) or net.get("format") != FORMAT:
        raise ValueError("not a TDN document")
    return net


def _map_ops(ops, fn):
    out = []
    for op in ops or []:
        mapped = fn(op)
        if mapped.get("children"):
            mapped = dict(mapped)
            mapped["children"] = _map_ops(mapped["children"], fn)
        out.append(mapped)
    return out


def _strip_volatile(net):
    net = copy.deepcopy(net)
    for k in VOLATILE_KEYS:
        net.pop(k, None)
    return net


def _index_ops(net):
    """Index operators by their path within the network, not their bare name.

    Two operators called ``noise1`` under different parents are two operators.
    Keying on the bare name collapsed them into one, so a diff reported
    phantom changes between unrelated nodes and missed real ones.
    """
    out = {}

    def walk(ops, prefix):
        for op in ops or []:
            path = f"{prefix}/{op.get('name')}"
            out[path] = op
            walk(op.get("children"), path)

    walk(net.get("operators"), "")
    return out


def diff_tdn(a_text, b_text):
    """Diff two TDN documents.

    Volatile headers are ignored so an unchanged network re-exported yields an
    empty diff. Every field in :data:`COMPARED_FIELDS` participates: comparing
    only parameters meant a rewiring, a move, a retag or an edited shader all
    diffed as ``is_equal: True``.
    """
    a = _strip_volatile(import_network(a_text) if isinstance(a_text, str) else a_text)
    b = _strip_volatile(import_network(b_text) if isinstance(b_text, str) else b_text)
    ia, ib = _index_ops(a), _index_ops(b)
    added = sorted(n for n in ib if n not in ia)
    removed = sorted(n for n in ia if n not in ib)
    changed = []
    for path in sorted(set(ia) & set(ib)):
        oa, ob = ia[path], ib[path]
        pa = oa.get("parameters") or {}
        pb = ob.get("parameters") or {}
        pdelta = {}
        for k in sorted(set(pa) | set(pb)):
            if pa.get(k) != pb.get(k):
                pdelta[k] = {"from": pa.get(k), "to": pb.get(k)}
        fdelta = {}
        for field in COMPARED_FIELDS:
            va, vb = oa.get(field), ob.get(field)
            if va != vb:
                fdelta[field] = {"from": va, "to": vb}
        if pdelta or fdelta:
            changed.append({"name": oa.get("name"), "path": path,
                            "type": oa.get("type"),
                            "param_changes": pdelta,
                            "field_changes": fdelta})
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "is_equal": not (added or removed or changed),
    }


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------
def checkpoint(network_path, operators, base_dir=None, tag="autosave"):
    """Embody-style idle auto-checkpoint: write a cheap .tdn of the current
    network to disk so a crash can be recovered. Returns the file path. The
    ``tag`` lets multiple containers keep independent checkpoints."""
    import time

    base_dir = base_dir or os.path.join(os.path.expanduser("~"), ".td_mcp", "tdn")
    os.makedirs(base_dir, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in network_path)
    path = os.path.join(base_dir, f"{safe}.{tag}.tdn")
    net = new_network(network_path=network_path, operators=operators)
    # Volatile header so diff_tdn ignores re-export churn.
    net["exported_at"] = time.time()
    text = export_network(net)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def restore_checkpoint(path):
    """Read a checkpoint .tdn back into a network dict (for auto-restore on
    project open). Raises on malformed files."""
    with open(path, "r", encoding="utf-8") as f:
        return import_network(f.read())
