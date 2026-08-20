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
EXPERIMENTAL_DIR = os.path.join(CORPUS_DIR, "experimental")

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


_CATEGORY_ALIASES = {
    "shader": "glsl", "raymarching": "glsl", "sdf": "glsl",
    "gpu": "gpu-compute", "cuda": "gpu-compute", "compute": "gpu-compute",
    "ml": "machine-learning", "ai": "machine-learning", "onnx": "machine-learning",
    "audio": "audio-visual", "fft": "audio-visual", "reactive": "audio-visual",
    "network": "networking", "osc": "networking", "ndi": "networking",
    "python": "python-advanced", "numpy": "python-advanced", "opencv": "python-advanced",
    "generative": "generative-systems", "particle": "generative-systems",
}


def load_experimental_techniques(category=None):
    """Load bottobot's experimental technique library (7 category files).

    ``category`` accepts an exact category name or a synonym alias
    (``"shader"`` → ``glsl``). Returns the full category dict with techniques,
    or None when not found.
    """
    if category:
        cat = _CATEGORY_ALIASES.get(category.lower(), category.lower())
        path = os.path.join(EXPERIMENTAL_DIR, cat + ".json")
        return _load(path)
    out = {}
    if os.path.isdir(EXPERIMENTAL_DIR):
        for fn in sorted(os.listdir(EXPERIMENTAL_DIR)):
            if fn.endswith(".json"):
                rec = _load(os.path.join(EXPERIMENTAL_DIR, fn))
                if rec:
                    out[rec.get("category") or fn[:-5]] = rec
    return out or None


def list_experimental_categories():
    """Return [(category, displayName, technique_count)] for all categories."""
    data = load_experimental_techniques()
    if not data:
        return []
    return [(cat, rec.get("displayName", cat), len(rec.get("techniques") or []))
            for cat, rec in sorted(data.items())]


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


def operators_matching(name):
    """All operator records matching a name/nickname (for disambiguation).

    Unlike ``operator_record`` (which returns the *first* hit), this returns
    *every* match so callers can detect when a name like "Noise" is ambiguous
    across families (Noise TOP / Noise CHOP / Noise SOP / Noise POP).
    """
    ops = load_operators()
    if not ops:
        return []
    q = name.strip().lower().replace(" ", "_")
    qn = name.strip().lower()
    matches, seen = [], set()

    def _add(key, rec):
        if key not in seen:
            matches.append(rec)
            seen.add(key)

    # exact key / alias matches
    for key, rec in ops.items():
        aliases = {key.lower(), rec.get("name", "").lower(), rec.get("displayName", "").lower()}
        if qn in aliases or q == key.lower():
            _add(key, rec)
    # substring fallback (only if exact missed)
    if not matches:
        for key, rec in ops.items():
            hay = (key + " " + rec.get("name", "") + " " + rec.get("displayName", "")).lower()
            if qn and qn in hay.replace("_", " "):
                _add(key, rec)
    return matches


# --- Snapshot provenance (tdmcp #150, 2026-08-11) --------------------------
#
# The corpus is an imported *snapshot* of the TouchDesigner docs, not the
# running build. An agent that asks for an operator and gets nothing back
# will otherwise conclude the operator does not exist, and go on to tell the
# user so -- when the truth may be that the snapshot predates it, or that TD
# spells it differently. Every miss therefore says which snapshot was
# searched and that a miss is not a non-existence proof.

SNAPSHOT_NOTICE = (
    "Absence from the imported documentation snapshot does not prove the "
    "operator is absent from your TouchDesigner build. Verify against a live "
    "instance (td-mcp-live: describe_td_tools / create_node) before reporting "
    "it as unavailable."
)

_snapshot_version_cache = None


def snapshot_version():
    """Stable identifier for the imported corpus: '<n> operators @ <date>'.

    Derived from what the corpus actually contains rather than a hand-edited
    constant, so it cannot claim to be fresher than the data.
    """
    global _snapshot_version_cache
    if _snapshot_version_cache is not None:
        return _snapshot_version_cache
    ops = load_operators() or {}
    latest = ""
    for rec in ops.values():
        stamp = (rec.get("lastUpdated") or "")[:10]
        if stamp > latest:
            latest = stamp
    _snapshot_version_cache = (
        "%d operators @ %s" % (len(ops), latest) if ops else "no corpus imported"
    )
    return _snapshot_version_cache


def operator_lookup(name):
    """Operator record plus provenance, so a miss is never read as absence.

    Returns ``{found, lookup_status, data_version, record?, suggestions?,
    snapshot_notice?}``.
    """
    rec = operator_record(name)
    if rec is not None:
        return {
            "found": True,
            "lookup_status": "found_in_snapshot",
            "data_version": snapshot_version(),
            "record": rec,
        }
    return {
        "found": False,
        "lookup_status": "not_in_snapshot",
        "data_version": snapshot_version(),
        "suggestions": nearest_operator_names(name),
        "snapshot_notice": SNAPSHOT_NOTICE,
    }


def nearest_operator_names(name, k=5):
    """Closest operator display names to `name` (vedant1317 close-match idea).

    A miss is far more often a spelling or family slip ("Blur COP", "movie in
    TOP") than a genuinely unknown operator, so the miss path should hand back
    the names worth trying next.
    """
    import difflib

    ops = load_operators() or {}
    if not ops:
        return []
    display = {}
    for key, rec in ops.items():
        label = rec.get("displayName") or rec.get("name") or key
        display[label] = None
    query = (name or "").strip()
    if not query:
        return []
    close = difflib.get_close_matches(query, list(display), n=k, cutoff=0.5)
    if close:
        return close
    # difflib is unforgiving about a wrong family suffix ("Blur COP"); fall
    # back to matching the leading word so those still get suggestions.
    head = query.split()[0].lower()
    return [label for label in display if label.lower().startswith(head)][:k]


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


def operator_version_compat(name, build=None):
    """Per-operator addedIn/removedIn/changedIn compatibility (bottobot).

    Returns ``{name, category, added_in, removed_in, changes, compatible,
    reason}``. Missing compat data is conservatively allowed (compatible=True)
    so we never silently refuse a real operator.
    """
    rec = operator_record(name)
    if rec is None:
        return {"ok": False, "error": f"unknown operator {name!r}"}
    compat = load_operator_compat()
    key = rec.get("id") or (rec.get("name", "").lower().replace(" ", "_"))
    entry = compat.get(key) or {}
    added = entry.get("addedIn") or rec.get("addedIn") or parse_build(rec.get("version"))
    removed = entry.get("removedIn")
    changes = entry.get("changedIn") or []
    compatible = True
    reason = None
    if build:
        bt = _to_tuple(build)
        if removed and _to_tuple(removed) <= bt:
            compatible = False
            reason = f"removed in TD {removed}"
        elif added and _to_tuple(added) > bt:
            compatible = False
            reason = f"requires TD {added} (target {build})"
    return {
        "ok": True,
        "name": rec.get("displayName") or rec.get("name"),
        "category": rec.get("category"),
        "added_in": added,
        "removed_in": removed,
        "changes": changes,
        "compatible": compatible,
        "reason": reason,
    }


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
            "url": rec.get("url"),
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
            "url": rec.get("url"),
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
