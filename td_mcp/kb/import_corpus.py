"""Import the merged MIT operator / Python-API / version corpora.

Pulls the rich JSON from `tdmcp` and `touchdesigner_mcp_server` (both MIT)
under `github-mcp/` and reconciles them into a single, version-aware corpus
under `td_mcp/kb/corpus/`:

    corpus/operators.json          id -> merged operator record
    corpus/python_api.json         className -> merged python class record
    corpus/versions/manifest.json  version manifest (re-exported)
    corpus/versions/operator-compat.json
    corpus/versions/python-compat.json

Two sources, one truth: for every operator we prefer the more complete field,
union the parameter lists (by name) and the tips/warnings, and keep the latest
`version` stamp. The retriever/tool layer reads the result with
`td_mcp.kb.corpus`.

Run:  uv run python -m td_mcp.kb.import_corpus
"""

import json
import os
import re

HERE = os.path.dirname(__file__)
CORPUS_DIR = os.path.join(HERE, "corpus")
VERSIONS_DIR = os.path.join(CORPUS_DIR, "versions")

DEFAULT_RESEARCH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "github-mcp"
)

TDMCP_OPS = "tdmcp/src/knowledge/data/operators"
TDMCP_PY = "tdmcp/src/knowledge/data/python-api"
TDMCP_VERS = "tdmcp/src/knowledge/data/versions"
BOT_OPS = "touchdesigner_mcp_server/wiki/data/processed"
BOT_PY = "touchdesigner_mcp_server/wiki/data/python-api"
BOT_VERS = "touchdesigner_mcp_server/wiki/data/versions"

_BUILD_RE = re.compile(r"(\d{4}(?:\.\d+)?)")


def parse_build(version):
    """'Available since TouchDesigner 2018+' -> 2018 ; '...2023.10000+' -> 2023.10000."""
    if not version:
        return None
    m = _BUILD_RE.search(version)
    return m.group(1) if m else None


def _load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _walk(dirpath, pattern="*.json"):
    out = {}
    if not os.path.isdir(dirpath):
        return out
    for fn in sorted(os.listdir(dirpath)):
        if not fn.endswith(".json") or (pattern and pattern != "*.json"
                                        and not re.search(pattern, fn)):
            continue
        rec = _load_json(os.path.join(dirpath, fn))
        if not isinstance(rec, dict):
            continue
        key = rec.get("id") or rec.get("className")
        if key:
            out[key] = rec
    return out


def _merge_list(a, b, key=None):
    """Union two lists of dicts, de-duping on `key` (or identity)."""
    seen, out = set(), []
    for item in (a or []) + (b or []):
        k = (item.get(key) if key else json.dumps(item, sort_keys=True)) if isinstance(item, dict) else item
        if k in seen:
            continue
        seen.add(k)
        out.append(item)
    return out


def merge_operator(a, b):
    if not a:
        return b
    if not b:
        return a
    out = dict(a)
    for k, v in b.items():
        av = a.get(k)
        if isinstance(v, list) and isinstance(av, list):
            out[k] = _merge_list(av, v, key="name")
        elif not av and v:
            out[k] = v
        elif isinstance(v, str) and v and (not av or len(v) > len(av)):
            out[k] = v
    # prefer the newer build stamp
    ba, bb = parse_build(a.get("version")), parse_build(b.get("version"))
    if bb and (not ba or _to_tuple(bb) > _to_tuple(ba)):
        out["version"] = b["version"]
    return out


def _to_tuple(v):
    return tuple(int(p) for p in str(v).split("."))


def merge_class(a, b):
    if not a:
        return b
    if not b:
        return a
    out = dict(a)
    for k, v in b.items():
        av = a.get(k)
        if isinstance(v, list) and isinstance(av, list):
            out[k] = _merge_list(av, v, key="name")
        elif not av and v:
            out[k] = v
        elif isinstance(v, str) and v and (not av or len(v) > len(av)):
            out[k] = v
    return out


def import_corpus(research_root=None):
    research = research_root or os.environ.get("TD_MCP_RESEARCH") or DEFAULT_RESEARCH
    print(f"research root: {research}")

    tdmcp_ops = _walk(os.path.join(research, TDMCP_OPS))
    bot_ops = _walk(os.path.join(research, BOT_OPS))
    tdmcp_py = _walk(os.path.join(research, TDMCP_PY))
    bot_py = _walk(os.path.join(research, BOT_PY))

    operators = {}
    for key, rec in list(tdmcp_ops.items()) + list(bot_ops.items()):
        operators[key] = merge_operator(operators.get(key), rec)
    classes = {}
    for key, rec in list(tdmcp_py.items()) + list(bot_py.items()):
        classes[key] = merge_class(classes.get(key), rec)

    os.makedirs(VERSIONS_DIR, exist_ok=True)
    for name in ("version-manifest", "operator-compatibility", "python-api-compatibility", "experimental-builds"):
        for src in (os.path.join(research, TDMCP_VERS, name + ".json"),
                    os.path.join(research, BOT_VERS, name + ".json")):
            rec = _load_json(src)
            if rec:
                with open(os.path.join(VERSIONS_DIR, name + ".json"), "w", encoding="utf-8") as fh:
                    json.dump(rec, fh, ensure_ascii=False, indent=2)
                break

    os.makedirs(CORPUS_DIR, exist_ok=True)
    with open(os.path.join(CORPUS_DIR, "operators.json"), "w", encoding="utf-8") as fh:
        json.dump(operators, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(CORPUS_DIR, "python_api.json"), "w", encoding="utf-8") as fh:
        json.dump(classes, fh, ensure_ascii=False, indent=1)

    print(f"operators : {len(operators)}")
    print(f"classes   : {len(classes)}")
    return operators, classes


if __name__ == "__main__":
    import_corpus()
