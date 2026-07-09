"""Runtime access to the merged MIT corpus (operators, Python API, versions).

Built by `import_corpus.py` into `td_mcp/kb/corpus/`. Pure stdlib.
The retriever/tool layer uses this for version-accurate, breadth-complete
operator + Python lookups that the hand-curated 216-chunk KB alone can't match.
"""

import json
import os
import re

HERE = os.path.dirname(__file__)
CORPUS_DIR = os.path.join(HERE, "corpus")
VERSIONS_DIR = os.path.join(CORPUS_DIR, "versions")

_BUILD_RE = re.compile(r"(\d{4}(?:\.\d+)?)")


def _load(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def parse_build(version):
    if not version:
        return None
    m = _BUILD_RE.search(version)
    return m.group(1) if m else None


def _to_tuple(v):
    if v is None:
        return (0,)
    return tuple(int(p) for p in str(v).split("."))


def load_operators():
    return _load(os.path.join(CORPUS_DIR, "operators.json")) or {}


def load_python_api():
    return _load(os.path.join(CORPUS_DIR, "python_api.json")) or {}


def load_version_manifest():
    return _load(os.path.join(VERSIONS_DIR, "version-manifest.json")) or {}


def load_operator_compat():
    return _load(os.path.join(VERSIONS_DIR, "operator-compatibility.json")) or {}


def load_python_compat():
    return _load(os.path.join(VERSIONS_DIR, "python-api-compatibility.json")) or {}


def operator_record(name):
    """Best operator record for a name/nickname (case-insensitive, slug-tolerant)."""
    ops = load_operators()
    if not ops:
        return None
    q = name.strip().lower().replace(" ", "_")
    if q in ops:
        return ops[q]
    for key, rec in ops.items():
        aliases = {key.lower(), rec.get("name", "").lower(), rec.get("displayName", "").lower()}
        if name.strip().lower() in aliases:
            return rec
        if q == key.lower():
            return rec
    # partial / substring fallback (e.g. "blur" -> Blur TOP)
    qn = name.strip().lower()
    for key, rec in ops.items():
        hay = (key + " " + rec.get("name", "") + " " + rec.get("displayName", "")).lower()
        if qn in hay.replace("_", " "):
            return rec
    return None


def python_class_for_operator(op_name):
    """Derive the Python class name for an operator (e.g. 'Noise TOP' -> 'NoiseTOP_Class').

    TD's convention: remove spaces, add 'TOP'/'CHOP'/'SOP' suffix + '_Class'.
    We try to match against known Python classes first, then fall back to convention.
    """
    rec = operator_record(op_name)
    if not rec:
        return None
    # Try to find exact match in Python classes
    classes = load_python_api()
    display = rec.get("displayName") or rec.get("name", "")
    # Convention: remove spaces, add family suffix, add _Class
    family = rec.get("category", "").upper()
    # e.g. "Noise TOP" -> "NoiseTOP" + "_Class"
    base = display.replace(" ", "")
    # Remove family from end if present (e.g. "Movie File In TOP" -> "MovieFileIn")
    if base.endswith(family):
        base = base[:-len(family)]
    candidate = f"{base}{family}_Class"
    if candidate in classes:
        return candidate
    # Try without family
    candidate2 = f"{display.replace(' ', '')}_Class"
    if candidate2 in classes:
        return candidate2
    return candidate


def python_record(name):
    classes = load_python_api()
    if not classes:
        return None
    q = name.strip().lower()
    if q in classes:
        return classes[q]
    for key, rec in classes.items():
        if q in {key.lower(), rec.get("className", "").lower(), rec.get("displayName", "").lower()}:
            return rec
    return None


def _str(x):
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        return x.get("name") or x.get("op") or x.get("id") or x.get("displayName") or ""
    return str(x)


def _join(seq, n=8):
    return ", ".join(_str(x) for x in (seq or [])[:n])


def _param_summary(rec, n=12):
    params = rec.get("parameters") or []
    names = [p.get("name") for p in params if p.get("name")]
    shown = names[:n]
    tail = f" (+{len(names) - n} more)" if len(names) > n else ""
    return shown, f"{len(names)} params", tail


def operator_spec_text(rec):
    """Human + RAG-friendly text block for one operator."""
    name = rec.get("displayName") or rec.get("name")
    fam = rec.get("category")
    parts = [f"{name} ({fam})."]
    if rec.get("description"):
        parts.append(rec["description"].strip())
    params = rec.get("parameters") or []
    if params:
        pstr = ", ".join(p.get("name", "?") for p in params[:24])
        parts.append(f"Parameters ({len(params)}): {pstr}.")
    if rec.get("commonInputs"):
        ins = _join(rec["commonInputs"], 8)
        parts.append(f"Common inputs: {ins}.")
    if rec.get("commonOutputs"):
        outs = _join(rec["commonOutputs"], 8)
        parts.append(f"Common outputs: {outs}.")
    if rec.get("relatedOperators"):
        rel = _join(rec["relatedOperators"], 10)
        parts.append(f"Related: {rel}.")
    if rec.get("tips"):
        parts.append("Tips: " + " ".join(rec["tips"][:3]))
    if rec.get("warnings"):
        parts.append("Warnings: " + " ".join(rec["warnings"][:2]))
    return " ".join(parts)


def param_schema(rec, names=None):
    """Return {param_name: {type, default, min, max, menu}} for an operator."""
    out = {}
    for p in rec.get("parameters") or []:
        nm = p.get("name")
        if not nm:
            continue
        if names and nm not in names:
            continue
        out[nm] = {
            "type": p.get("type"),
            "dataType": p.get("dataType"),
            "default": p.get("defaultValue"),
            "min": p.get("minValue"),
            "max": p.get("maxValue"),
            "menu": p.get("menuItems") or [],
            "animatable": p.get("isAnimatable"),
        }
    return out


def version_info(build):
    """Return version metadata for a build id like '2022' or '2023.10000'."""
    man = load_version_manifest()
    versions = man.get("versions") if isinstance(man, dict) else man
    if not versions:
        return None
    bt = _to_tuple(build)
    best = None
    for v in versions:
        vid = v.get("id")
        vt = _to_tuple(vid)
        if vt <= bt and (best is None or vt >= _to_tuple(best.get("id"))):
            best = v
    return best


def operator_connections(name):
    rec = operator_record(name)
    if not rec:
        return None
    return {
        "name": rec.get("displayName") or rec.get("name"),
        "family": rec.get("category"),
        "inputs": rec.get("commonInputs") or [],
        "outputs": rec.get("commonOutputs") or [],
        "related": rec.get("relatedOperators") or [],
        "workflow_patterns": rec.get("workflowPatterns") or [],
    }


def corpus_chunks():
    """Emit retriever chunks (op()/py() shape) for every merged corpus record.

    These give the retriever 900+ operator/Python entries with rich param
    names + version stamps, on top of the hand-curated seeds in build_kb.
    """
    chunks = []
    for key, rec in load_operators().items():
        fam = rec.get("category")
        version = parse_build(rec.get("version")) or "all"
        aliases = sorted({key.lower(), rec.get("name", "").lower(),
                          rec.get("displayName", "").lower()})
        chunks.append({
            "id": f"corpus_{key}",
            "family": fam,
            "title": rec.get("displayName") or rec.get("name"),
            "category": "operator",
            "source": rec.get("url") or "github-mcp/tdmcp",
            "min_version": version,
            "tags": rec.get("tags") or [fam.lower()] if fam else [],
            "aliases": [a for a in aliases if a],
            "text": operator_spec_text(rec),
        })
    for key, rec in load_python_api().items():
        version = "all"
        chunks.append({
            "id": f"corpus_py_{key}",
            "family": None,
            "title": rec.get("displayName") or rec.get("className"),
            "category": "python",
            "source": "github-mcp/tdmcp",
            "min_version": version,
            "tags": [rec.get("category", "python").lower()] if rec.get("category") else ["python"],
            "aliases": sorted({key.lower(), rec.get("className", "").lower(),
                               rec.get("displayName", "").lower()}),
            "text": _class_spec_text(rec),
        })
    return chunks


def _class_spec_text(rec):
    name = rec.get("displayName") or rec.get("className")
    parts = [f"{name} (Python API)."]
    if rec.get("description"):
        parts.append(rec["description"].strip())
    members = rec.get("members") or []
    if members:
        mstr = ", ".join(m.get("name", "?") for m in members[:24])
        parts.append(f"Members ({len(members)}): {mstr}.")
    methods = rec.get("methods") or []
    if methods:
        fstr = ", ".join(m.get("name", "?") for m in methods[:16])
        parts.append(f"Methods ({len(methods)}): {fstr}.")
    return " ".join(parts)


def compare_operators(a, b):
    ra, rb = operator_record(a), operator_record(b)
    if not ra or not rb:
        missing = [n for n, r in ((a, ra), (b, rb)) if r is None]
        return {"ok": False, "error": f"unknown operator(s): {missing}"}
    pa = {p.get("name") for p in ra.get("parameters") or [] if p.get("name")}
    pb = {p.get("name") for p in rb.get("parameters") or [] if p.get("name")}
    return {
        "ok": True,
        "a": ra.get("displayName"),
        "b": rb.get("displayName"),
        "shared": sorted(pa & pb),
        "only_a": sorted(pa - pb),
        "only_b": sorted(pb - pa),
    }


_STOP = set("the a an of to in on for with and or is are be as by from".split())
_TOKEN = re.compile(r"[a-z0-9_]+")


def suggest_workflow(query, k=8):
    """Keyword-overlap workflow suggestion across the operator corpus."""
    ops = load_operators()
    if not ops:
        return []
    q_tokens = {t for t in _TOKEN.findall(query.lower()) if t not in _STOP}
    scored = []
    for rec in ops.values():
        text = " ".join([
            rec.get("displayName", ""), rec.get("name", ""),
            rec.get("description", ""), " ".join(rec.get("tags", []) or []),
            " ".join(_str(x) for x in rec.get("relatedOperators") or []),
            " ".join(_str(x) for x in rec.get("workflowPatterns") or []),
        ]).lower()
        toks = set(_TOKEN.findall(text))
        overlap = len(q_tokens & toks)
        if overlap:
            scored.append((overlap, rec.get("displayName") or rec.get("name"),
                           rec.get("category")))
    scored.sort(key=lambda x: -x[0])
    return [{"name": n, "family": f, "score": s} for s, n, f in scored[:k]]
