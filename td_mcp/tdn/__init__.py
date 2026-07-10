"""TDN — TouchDesigner Network serialization (diffable YAML, from Embody).

A network is a plain dict:
    {"format": "tdn", "version": "2.0", "generator": "td-mcp",
     "network_path": "/project1", "operators": [Operator, ...]}

Operator:
    {"name", "type", "position":[x,y], "size":[w,h], "color":[r,g,b],
     "comment", "tags":[], "parameters":{name:value},
     "custom_pars":{}, "flags":[], "inputs":[src_name_or_null,...],
     "children":[Operator,...]}

Everything is pure-Python and dependency-light (PyYAML). Networks serialize
to text so builds are reviewable, git-diffable (git textconv strips volatile
headers), and restorable. The live bridge calls `import_network` to build a
net inside TD and `diff_tdn` to compare the live graph to an on-disk `.tdn`.
"""

import copy

import yaml

FORMAT = "tdn"
VERSION = "2.0"
# Header keys that change on every re-export of an unchanged net.
VOLATILE_KEYS = ("build", "generator", "td_build", "exported_at", "source_file")


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
             flags=None):
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
    if inputs is not None:
        op["inputs"] = list(inputs)
    if flags is not None:
        op["flags"] = list(flags)
    if children is not None:
        op["children"] = list(children)
    return op


def export_network(net):
    """Serialize a network dict to TDN YAML (deterministic, diffable)."""
    net = copy.deepcopy(net)
    net.setdefault("format", FORMAT)
    net.setdefault("version", VERSION)
    net.setdefault("generator", "td-mcp")
    return yaml.safe_dump(net, sort_keys=False, default_flow_style=False,
                          allow_unicode=True)


def import_network(text):
    """Parse TDN YAML back into a network dict."""
    net = yaml.safe_load(text)
    if not isinstance(net, dict) or net.get("format") != FORMAT:
        raise ValueError("not a TDN document")
    return net


def _strip_volatile(net):
    net = copy.deepcopy(net)
    for k in VOLATILE_KEYS:
        net.pop(k, None)
    return net


def _index_ops(net):
    out = {}
    def walk(ops):
        for o in ops or []:
            out[o.get("name")] = o
            walk(o.get("children"))
    walk(net.get("operators"))
    return out


def diff_tdn(a_text, b_text):
    """Diff two TDN documents. Volatile headers are ignored so an unchanged
    network re-exported yields an empty diff."""
    a = _strip_volatile(import_network(a_text) if isinstance(a_text, str) else a_text)
    b = _strip_volatile(import_network(b_text) if isinstance(b_text, str) else b_text)
    ia, ib = _index_ops(a), _index_ops(b)
    added = [n for n in ib if n not in ia]
    removed = [n for n in ia if n not in ib]
    changed = []
    for name in set(ia) & set(ib):
        pa, pb = ia[name].get("parameters", {}), ib[name].get("parameters", {})
        pdelta = {}
        for k in set(pa) | set(pb):
            va, vb = pa.get(k), pb.get(k)
            if va != vb:
                pdelta[k] = {"from": va, "to": vb}
        ta, tb = ia[name].get("type"), ib[name].get("type")
        if pdelta or ta != tb:
            changed.append({"name": name, "type": ta,
                            "param_changes": pdelta})
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "is_equal": not (added or removed or changed),
    }


def checkpoint(network_path, operators, base_dir=None, tag="autosave"):
    """Embody-style idle auto-checkpoint: write a cheap .tdn of the current
    network to disk so a crash can be recovered. Returns the file path. The
    ``tag`` lets multiple containers keep independent checkpoints."""
    import os
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
