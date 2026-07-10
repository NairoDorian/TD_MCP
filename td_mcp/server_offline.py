"""td-mcp offline server: documentation / RAG tools over a local KB.

Run as MCP (needs `mcp`):   python -m td_mcp.server_offline
Run as CLI (no deps):        python -m td_mcp.server_offline "blur top params"
"""

import argparse
import os
import sys

from td_mcp.rag.retriever import build_retriever
from td_mcp.rag.strategies import ParallelRetriever
from td_mcp import __version__
from td_mcp.kb import corpus
from td_mcp.kb.corpus import _str, python_class_for_operator
from td_mcp.tdn import export_network, new_network, operator as tdn_operator
from td_mcp.rag import knowledge_graph
from td_mcp import showcontrol as sc
from td_mcp import led_mapping as led
from td_mcp import generators as gen
from td_mcp import glsl_patterns as glsl
from td_mcp import prompts as prompts_mod
from td_mcp import compat as compat_mod
from td_mcp import scoring as scoring_mod
from td_mcp import perf as perf_mod
from td_mcp import discover as discover_mod
from td_mcp import memory as memory_mod
from td_mcp import recipe_vault as recipe_vault_mod
from td_mcp import heal as heal_mod
from td_mcp.tools.risk import risk_class, tool_annotations

DEFAULT_CHUNKS = os.path.join(os.path.dirname(__file__), "kb", "chunks.jsonl")


def _fmt(results, query, max_chars=None):
    if not results:
        return f"No documentation matched: {query!r}. Try a different operator/Python class name."
    lines = [f"Found {len(results)} doc chunk(s) for {query!r}:\n"]
    for c, sc in results:
        head = f"### {c.get('title')}  [{c.get('family') or c.get('category')}]  (score {sc})"
        meta = f"source: {c.get('source', '')}"
        text = c.get('text', '')
        if max_chars and len(text) > max_chars:
            text = text[:max_chars].rstrip() + "…"
        lines.append(f"{head}\n{meta}\n{text}\n")
    return "\n".join(lines)


def _first_sentence(text, n=160):
    text = (text or "").replace("\n", " ")
    cut = text.find(". ")
    if cut > 0 and cut < n:
        return text[: cut + 1]
    return text[:n].rstrip() + ("…" if len(text) > n else "")


_PR = None


def get_pr():
    global _PR
    if _PR is None:
        reranker = None
        if os.environ.get("TD_MCP_RERANK") == "1":
            try:
                from td_mcp.rag.rerank import CrossEncoderReranker
                reranker = CrossEncoderReranker()
            except Exception:  # noqa: BLE001
                reranker = None
        remote = None
        if os.environ.get("TD_MCP_REMOTE_MCP"):
            remote = os.environ["TD_MCP_REMOTE_MCP"].split()
        _PR = ParallelRetriever(build_retriever(), reranker=reranker, remote=remote)
    return _PR


# ============================================================
# Corpus-backed offline tools
# ============================================================
def td_docs_search(query, family=None, category=None, version=None, k=5):
    return _fmt(get_pr().search(query, family=family, category=category, version=version, k=k), query)


def td_docs_operator(name, k=3):
    """Full parameter + connector spec for one operator, with auto-appended Python class docs."""
    # First get the operator doc
    out = td_docs_search(name, category="operator", k=k)
    # Then try to append the Python class (cacheflowe technique)
    # Map operator name to Python class: "Movie File In TOP" -> "MoviefileinTOP"
    op_rec = corpus.operator_record(name)
    if op_rec:
        py_name = op_rec.get("className")
        if py_name:
            py = corpus.python_record(py_name)
            if py:
                out += "\n\n---\n### Python API: " + (py.get("displayName") or py.get("className")) + "\n"
                if py.get("description"):
                    out += py["description"].strip() + "\n"
                members = py.get("members") or []
                if members:
                    out += f"\nMembers ({len(members)}): " + ", ".join(m.get("name", "?") for m in members[:16])
                methods = py.get("methods") or []
                if methods:
                    out += f"\nMethods ({len(methods)}): " + ", ".join(m.get("name", "?") for m in methods[:12])
    return out


def td_docs_python(name, k=3):
    return td_docs_search(name, category="python", k=k)


def td_docs_glsl(query="fragment", k=2):
    return td_docs_search(query, category="glsl", k=k)


def td_docs_template(query="", k=2):
    return td_docs_search(query, category="tutorial", k=k)


def td_docs_version(build="all", k=5):
    ret = build_retriever()
    return _fmt(ret.search("operator parameters api", version=build, k=k), f"build>={build}")


def td_docs_family(family, version=None):
    """List every operator in a family with a one-line description."""
    ret = get_pr()
    if family and family.upper() not in ret.families():
        return (f"Unknown family {family!r}. Known families: "
                f"{', '.join(ret.families())}.")
    ops = ret.operators_in(family.upper(), version=version)
    if not ops:
        return f"No operators found for family {family!r}."
    lines = [f"### {family.upper()} operators ({len(ops)}):\n"]
    for c in ops:
        lines.append(f"- **{c.get('title')}** — {_first_sentence(c.get('text'))}")
    return "\n".join(lines)


def td_docs_parameter(op_name, k=1):
    """Return the parameter schema for one operator, merged from the 900+ operator corpus."""
    rec = corpus.operator_record(op_name)
    if rec is None:
        return (f"No operator matched {op_name!r}. "
                f"Try td_docs_family for a list.")
    schema = corpus.param_schema(rec)
    lines = [f"### {rec.get('displayName') or rec.get('name')} "
             f"[{rec.get('category')}]  (since {rec.get('version','?')})", ""]
    if rec.get("description"):
        lines.append(rec["description"].strip() + "\n")
    if schema:
        lines.append(f"Parameters ({len(schema)}):")
        for nm, info in schema.items():
            bits = [info["type"] or "?"]
            if info["default"] is not None:
                bits.append(f"default={info['default']}")
            if info["min"] is not None or info["max"] is not None:
                bits.append(f"range={info['min']}..{info['max']}")
            if info["menu"]:
                bits.append("menu=" + "/".join(str(m) for m in info["menu"][:8]))
            lines.append(f"- {nm} ({', '.join(str(b) for b in bits)})")
    if rec.get("tips"):
        lines.append("\nTips: " + " ".join(rec["tips"][:3]))
    return "\n".join(lines)


def td_docs_compare(a, b):
    """Compare two operators: shared, only-A, only-B parameters."""
    res = corpus.compare_operators(a, b)
    if not res.get("ok"):
        return res["error"]
    lines = [f"### Compare {res['a']} vs {res['b']}", ""]
    lines.append(f"Shared params ({len(res['shared'])}): "
                 + (", ".join(res["shared"]) or "—"))
    lines.append(f"Only in {res['a']} ({len(res['only_a'])}): "
                 + (", ".join(res["only_a"]) or "—"))
    lines.append(f"Only in {res['b']} ({len(res['only_b'])}): "
                 + (", ".join(res["only_b"]) or "—"))
    return "\n".join(lines)


def td_docs_connections(name):
    """Inputs / outputs / related operators for one operator."""
    c = corpus.operator_connections(name)
    if c is None:
        return f"No operator matched {name!r}."
    lines = [f"### {c['name']} [{c['family']}] connections", ""]
    lines.append("Inputs:  " + (", ".join(_str(x) for x in c["inputs"]) or "—"))
    lines.append("Outputs: " + (", ".join(_str(x) for x in c["outputs"]) or "—"))
    lines.append("Related: " + (", ".join(_str(x) for x in c["related"]) or "—"))
    if c["workflow_patterns"]:
        lines.append("Workflow patterns: " + ", ".join(c["workflow_patterns"][:10]))
    return "\n".join(lines)


def td_docs_workflow(query, k=8):
    """Suggest a workflow/operator chain for a goal via keyword overlap."""
    sug = corpus.suggest_workflow(query, k=k)
    if not sug:
        return f"No workflow operators matched {query!r}."
    lines = [f"### Suggested operators for {query!r}:", ""]
    for s in sug:
        lines.append(f"- **{s['name']}** [{s['family']}]  (score {s['score']})")
    return "\n".join(lines)


def td_docs_version_info(build="2023.10000"):
    """Version metadata (Python version, support status, notes) for a TD build."""
    v = corpus.version_info(build)
    if v is None:
        return f"No version metadata for build {build!r}."
    lines = [f"### {v.get('label')} (id {v.get('id')})", ""]
    lines.append(f"Python: {v.get('pythonVersion')} ({v.get('pythonMajorMinor')})")
    lines.append(f"Support: {v.get('supportStatus')}")
    if v.get("notes"):
        lines.append("Notes: " + v["notes"])
    return "\n".join(lines)


def td_docs_related(name, depth=1):
    """Graph-RAG: operators related to the given one (networkx, depth-limited)."""
    rel = knowledge_graph.related_operators(name, depth=depth)
    if not rel:
        rec = corpus.operator_record(name)
        if rec is None:
            return f"No operator matched {name!r}."
        return f"{rec.get('displayName')} has no related operators in the graph."
    lines = [f"### Related to {name} (depth {depth}):", ""]
    for r in rel:
        lines.append(f"- {r}")
    return "\n".join(lines)


def td_build_network(spec):
    """Build a diffable TDN YAML from a list of operator specs, validating every type against the corpus."""
    if isinstance(spec, str):
        import json as _json
        spec = _json.loads(spec)
    operators = []
    warnings = []
    for i, s in enumerate(spec):
        op_type = s.get("type")
        rec = corpus.operator_record(op_type) if op_type else None
        if rec is None:
            warnings.append(f"unknown operator type: {op_type!r} (index {i})")
            fam = None
        else:
            fam = rec.get("category")
        name = s.get("name") or (op_type or f"op{i}").split()[0].lower() + str(i)
        x, y = s.get("position", [i * 200, 0])
        operators.append(tdn_operator(
            name, op_type or "NULL", position=[x, y],
            parameters=s.get("params"), inputs=s.get("inputs")))
    net = new_network(operators=operators)
    text = export_network(net)
    if warnings:
        text = "# WARNINGS: " + "; ".join(warnings) + "\n" + text
    return text


def td_showcontrol_plan(spec):
    """Build a show-control plan: Art-Net / sACN / OSC / MIDI / timecode (familienak)."""
    import json as _json
    spec = _json.loads(spec) if isinstance(spec, str) else spec
    outputs = []
    for o in spec.get("artnet", []):
        outputs.append(sc.artnet_output(**(o if isinstance(o, dict) else {"universe": o})))
    for o in spec.get("sacn", []):
        outputs.append(sc.sacn_output(**(o if isinstance(o, dict) else {"universe": o})))
    for p in spec.get("osc_in", []):
        outputs.append(sc.osc_receiver(port=p if isinstance(p, int) else p.get("port", 8000)))
    for ch in spec.get("midi_in", []):
        outputs.append(sc.midi_in(channel=ch if isinstance(ch, int) else ch.get("channel", 1)))
    if spec.get("timecode"):
        outputs.append(sc.timecode_setup(kind=spec["timecode"]))
    return _json.dumps(sc.build_show_plan(outputs), indent=2)


def td_led_map(spec):
    """Define an LED / pixel mapping (wall / strip / voxel) + DMX channel-map export (familienak)."""
    import json as _json
    spec = _json.loads(spec) if isinstance(spec, str) else spec
    kind = spec.get("kind", "wall")
    if kind == "strip":
        m = led.led_strip(spec["length"], **{k: v for k, v in spec.items()
                                             if k in ("led_type", "name", "spacing")})
    elif kind == "voxel":
        m = led.voxel_grid(spec["width"], spec["height"], spec["depth"],
                           **{k: v for k, v in spec.items()
                              if k in ("led_type", "name", "spacing")})
    else:
        m = led.led_wall(spec["width"], spec["height"],
                         **{k: v for k, v in spec.items()
                            if k in ("led_type", "layout", "name", "spacing")})
    table = led.dmx_channel_map(m, start_universe=spec.get("start_universe", 0))
    out = dict(m)
    out["dmx_channel_map"] = table[:20]
    out["dmx_channel_map_total"] = len(table)
    return _json.dumps(out, indent=2)


# ---------------------------------------------------------------------------
# Artist generators (tdmcp Layer 1 style) — emit TDN YAML
# ---------------------------------------------------------------------------
def _json_loads(s):
    import json as _json
    return _json.loads(s) if isinstance(s, str) else s


def td_build_feedback(spec):
    """Create a feedback network (Noise -> Level decay -> Feedback -> Threshold/Level -> Out)."""
    return td_build_network(gen.create_feedback_network(**_json_loads(spec)))


def td_build_audio_reactive(spec):
    """Create an audio-reactive network (Audio Device In -> Math/Filter -> Export to visual params)."""
    res = gen.create_audio_reactive(**_json_loads(spec))
    text = td_build_network(res["specs"])
    exports = res.get("exports")
    if exports:
        lines = ["# CHOP EXPORTS:"]
        for e in exports:
            lines.append(f"#   {e.get('from')} -> {e.get('to')}  ({e.get('note', '')})")
        text = text + "\n" + "\n".join(lines)
    return text


def td_build_particle(spec):
    """Create a particle system (POP or GPU TOP based)."""
    return td_build_network(gen.create_particle_system(**_json_loads(spec)))


def td_build_3d_scene(spec):
    """Create a 3D scene (Geometry + Material + Camera + Light + Render)."""
    return td_build_network(gen.create_3d_scene(**_json_loads(spec)))


def td_build_glsl_shader(spec):
    """Create a GLSL shader TOP with a template."""
    return td_build_network(gen.create_glsl_shader(**_json_loads(spec)))


def td_build_led_wall(spec):
    """Create a pixel-mapping pipeline for an LED wall."""
    return td_build_network(gen.create_led_wall(**_json_loads(spec)))


def td_build_dmx_fixture(spec):
    """Create a DMX input pipeline to receive sACN/Art-Net control channels."""
    return td_build_network(gen.create_dmx_fixture_pipeline(**_json_loads(spec)))


def td_build_video_pipeline(spec):
    """Create a video playback/processing pipeline (Movie File In -> LUT/Key -> Level -> Out)."""
    return td_build_network(gen.create_video_pipeline(**_json_loads(spec)))


def td_build_midi_rig(spec):
    """Create a MIDI input rig (MIDI In -> Select -> Math normalize -> Filter -> Out)."""
    return td_build_network(gen.create_midi_rig(**_json_loads(spec)))


def td_build_kinect_skeleton(spec):
    """Create a Kinect Azure skeleton-tracking rig (joints -> select -> remap -> visualize)."""
    return td_build_network(gen.create_kinect_skeleton(**_json_loads(spec)))


def td_docs_glossary(limit=200):
    """Compact index of every KB entry — exploration / autocomplete over the whole documentation source."""
    ret = get_pr()
    rows = ret.glossary(limit=limit)
    lines = [f"### Knowledge base glossary ({len(rows)} entries):\n"]
    for title, kind, mv in rows:
        tag = f"  (since {mv})" if mv and mv != "all" else ""
        lines.append(f"- {title}  [{kind}]{tag}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Inspiration tool functions (glsl patterns, templates, experts, compat,
# build scoring, media servers, performance) — all READ_ONLY planners.
# ---------------------------------------------------------------------------
def td_glsl_pattern(name="simple_noise"):
    """Return a named, paste-ready GLSL fragment shader (bottobot pattern lib)."""
    return _json_dumps(glsl.get_glsl_pattern(name))


def td_network_template(name="feedback"):
    """Return a ready-to-build operator-chain template (bottobot)."""
    return _json_dumps(glsl.get_network_template(name))


def td_expert_prompt(phase=None, name=None):
    """Return an expert system prompt for a build phase or a named expert
    (TD_Builder get_expert_prompt)."""
    if name:
        p = prompts_mod.get_expert_prompt(name)
        return p if p else f"unknown expert {name!r}; try: {prompts_mod.list_experts()}"
    phase = phase or "build"
    return prompts_mod.build_phase_prompt(phase)


def td_compat_check(client_ver, bridge_ver):
    """Compare client vs bridge versions (MAJOR=error, MINOR=warn, PATCH=ok)."""
    return _json_dumps(compat_mod.check_compat(client_ver, bridge_ver))


def _parse_build_spec(spec):
    """Parse a TDN/YAML/JSON network spec into a {operators:[...]} dict.

    Returns the dict, or an error string on malformed input.
    """
    import json as _json
    import yaml as _yaml
    if isinstance(spec, str):
        try:
            data = _yaml.safe_load(spec)
        except Exception:
            try:
                data = _json.loads(spec)
            except Exception:
                return "error: spec is not valid TDN/YAML/JSON"
    else:
        data = spec
    if isinstance(data, list):
        data = {"operators": data}
    if not isinstance(data, dict) or "operators" not in data:
        return "error: spec must contain 'operators'"
    return data


def td_score_build(spec):
    """Score a network description 0..100 (tdmcp scoreBuild). Accepts a TDN
    YAML string or a JSON operator list / {operators:[...]} dict."""
    import json as _json
    data = _parse_build_spec(spec)
    if isinstance(data, str):
        return data
    return _json_dumps(scoring_mod.score_build(data))


def td_self_heal(spec):
    """Self-heal a network description: validate -> auto-repair -> re-assess,
    attaching recovery hints for anything still unresolved. Pure (no running
    TD). Accepts a TDN/YAML/JSON spec."""
    import json as _json
    data = _parse_build_spec(spec)
    if isinstance(data, str):
        return data
    return _json_dumps(heal_mod.self_heal(data))


def td_validate_build(spec):
    """Validate + score a network description and attach recovery hints for
    each finding. Pure (no running TD). Accepts a TDN/YAML/JSON spec."""
    import json as _json
    data = _parse_build_spec(spec)
    if isinstance(data, str):
        return data
    return _json_dumps(heal_mod.assess_build(data))


def td_mediaserver(name="resolume"):
    """Plan a connector to a media server (Millumin/Resolume/Notch/Disguise…)."""
    return _json_dumps(sc.media_server(name))


def td_analyze_performance(perf):
    """Analyze a performance snapshot (fps + per-node cook times)."""
    import json as _json
    p = _json.loads(perf) if isinstance(perf, str) else perf
    return _json_dumps(perf_mod.analyze_performance(p))


def _json_dumps(obj):
    import json as _json
    return _json.dumps(obj, indent=2, default=str)


def td_discover():
    """Discover running TouchDesigner bridge instances on the local network of
    known ports (twozero multi-instance). Returns reachable candidates."""
    return _json_dumps(discover_mod.discover_instances())


def td_memory_save(role, text, tags=None):
    """Persist an interaction turn to local session memory (tdmcp memory)."""
    tag_list = tags.split(",") if isinstance(tags, str) else (tags or [])
    m = memory_mod.SessionMemory()
    m.save(role, text, tag_list)
    return _json_dumps({"ok": True, "entries": len(m)})


def td_memory_recall(query, k=5):
    """Recall the most relevant past interactions by keyword overlap."""
    m = memory_mod.SessionMemory()
    return _json_dumps(m.recall(query, int(k)))


def td_scaffold_recipe(spec):
    """Scaffold a reusable recipe blueprint from a network description (TDN
    YAML, JSON operator list, or {operators:[...]})."""
    import json as _json
    import yaml as _yaml
    if isinstance(spec, str):
        try:
            data = _yaml.safe_load(spec)
        except Exception:
            try:
                data = _json.loads(spec)
            except Exception:
                return "error: spec is not valid TDN/YAML/JSON"
    else:
        data = spec
    if isinstance(data, list):
        data = {"operators": data}
    if not isinstance(data, dict) or "operators" not in data:
        return "error: spec must contain 'operators'"
    draft = recipe_vault_mod.draft_recipe_from_chain(data)
    return _json_dumps(draft)


# ============================================================
# MCP server
# ============================================================
def create_server():
    from mcp.server import Server
    import mcp.types as types

    app = Server("td-mcp-offline", version=__version__)
    ret = get_pr()

    @app.list_tools()
    async def list_tools():
        # Risk tiering (TrueFiasco): READ_ONLY / WRITE_ADDITIVE / WRITE_CHECKPOINT / DESTRUCTIVE
        # Offline server = read-only + additive (td_build_network generates YAML)
        read_only = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
        additive = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}
        return [
            types.Tool("td_docs_search", "Hybrid search across scraped TouchDesigner docs (operators, Python classes, GLSL, tutorials).",
                        {"query": {"type": "string"}, "family": {"type": "string", "optional": True},
                         "category": {"type": "string", "optional": True}, "version": {"type": "string", "optional": True},
                         "k": {"type": "integer", "optional": True},
                         "detail": {"type": "string", "optional": True}},
                        annotations=read_only),
            types.Tool("td_docs_operator", "Full parameter + connector spec for one operator (e.g. 'Noise TOP').",
                       {"name": {"type": "string"}, "k": {"type": "integer", "optional": True}},
                       annotations=read_only),
            types.Tool("td_docs_python", "Python API class/method lookup (e.g. 'OP_Class').",
                       {"name": {"type": "string"}, "k": {"type": "integer", "optional": True}},
                       annotations=read_only),
            types.Tool("td_docs_glsl", "GLSL shader snippet search.",
                       {"query": {"type": "string", "optional": True}, "k": {"type": "integer", "optional": True}},
                       annotations=read_only),
            types.Tool("td_docs_template", "Tutorial / workflow template search.",
                       {"query": {"type": "string", "optional": True}, "k": {"type": "integer", "optional": True}},
                       annotations=read_only),
            types.Tool("td_docs_version", "List docs applicable to a TD build (version-aware).",
                       {"build": {"type": "string", "optional": True}, "k": {"type": "integer", "optional": True}},
                       annotations=read_only),
            types.Tool("td_docs_family", "List every operator in a family (TOP/CHOP/SOP/DAT/COMP/POP) with a one-line description.",
                       {"family": {"type": "string"}, "version": {"type": "string", "optional": True}},
                       annotations=read_only),
            types.Tool("td_docs_parameter", "Return the full parameter schema for one operator, merged from the 900+ operator corpus.",
                       {"op_name": {"type": "string"}, "k": {"type": "integer", "optional": True}},
                       annotations=read_only),
            types.Tool("td_docs_compare", "Compare two operators: shared / only-A / only-B parameters. Use to choose between near-equivalents.",
                       {"a": {"type": "string"}, "b": {"type": "string"}},
                       annotations=read_only),
            types.Tool("td_docs_connections", "Inputs / outputs / related operators for one operator (port-level wiring hints).",
                       {"name": {"type": "string"}},
                       annotations=read_only),
            types.Tool("td_docs_workflow", "Suggest a workflow / operator chain for a goal via keyword overlap across the corpus.",
                       {"query": {"type": "string"}, "k": {"type": "integer", "optional": True}},
                       annotations=read_only),
            types.Tool("td_docs_version_info", "Version metadata (Python version, support status, notes) for a TD build.",
                       {"build": {"type": "string", "optional": True}},
                       annotations=read_only),
            types.Tool("td_docs_related", "Graph-RAG: operators related to the given one (networkx, depth-limited).",
                       {"name": {"type": "string"}, "depth": {"type": "integer", "optional": True}},
                       annotations=read_only),
            types.Tool("td_build_network", "Generate a diffable TDN YAML from a list of operator specs, validating every type against the corpus.",
                       {"spec": {"type": "string"}},
                       annotations=additive),
            types.Tool("td_showcontrol_plan", "Build a show-control plan: Art-Net / sACN / OSC / MIDI / timecode (familienak).",
                       {"spec": {"type": "string"}},
                       annotations=read_only),
            types.Tool("td_led_map", "Define an LED / pixel mapping (wall / strip / voxel) + DMX channel-map export (familienak).",
                       {"spec": {"type": "string"}},
                       annotations=read_only),
            types.Tool("td_docs_glossary", "Compact index of every KB entry — exploration / autocomplete over the whole documentation source.",
                       {"limit": {"type": "integer", "optional": True}},
                       annotations=read_only),
            types.Tool("td_build_feedback", "Create a feedback network (Noise -> Level decay -> Feedback -> Threshold/Level -> Out).",
                       {"spec": {"type": "string"}},
                       annotations=additive),
            types.Tool("td_build_audio_reactive", "Create an audio-reactive network (Audio Device In -> Math/Filter -> Export to visual params).",
                       {"spec": {"type": "string"}},
                       annotations=additive),
            types.Tool("td_build_particle", "Create a particle system (POP or GPU TOP based).",
                       {"spec": {"type": "string"}},
                       annotations=additive),
            types.Tool("td_build_3d_scene", "Create a 3D scene (Geometry + Camera + Light + Render).",
                       {"spec": {"type": "string"}},
                       annotations=additive),
            types.Tool("td_build_glsl_shader", "Create a GLSL TOP with a shader template.",
                       {"spec": {"type": "string"}},
                       annotations=additive),
            types.Tool("td_build_led_wall", "Create a pixel-mapping pipeline for an LED wall (Noise -> Resolution -> TOP to CHOP -> DMX Out).",
                       {"spec": {"type": "string"}},
                       annotations=additive),
            types.Tool("td_build_dmx_fixture", "Create a DMX input pipeline to receive sACN/Art-Net control channels (DMX In -> Select -> Math -> Out).",
                        {"spec": {"type": "string"}},
                        annotations=additive),
            types.Tool("td_build_video_pipeline", "Create a video playback/processing pipeline (Movie File In -> optional Chroma Key -> LUT -> Level -> Out).",
                        {"spec": {"type": "string"}},
                        annotations=additive),
            types.Tool("td_build_midi_rig", "Create a MIDI input rig (MIDI In -> Select -> Math normalize -> Filter -> Out CHOP).",
                        {"spec": {"type": "string"}},
                        annotations=additive),
            types.Tool("td_build_kinect_skeleton", "Create a Kinect Azure skeleton-tracking rig (joints -> select -> remap -> optional Point Sprite visualization).",
                        {"spec": {"type": "string"}},
                        annotations=additive),
            types.Tool("td_glsl_pattern", "Return a named, paste-ready GLSL fragment shader (simple_noise / rgb_shift / hue_cycle / feedback_blend / kaleidoscope / scanline).",
                        {"name": {"type": "string", "optional": True}},
                        annotations=read_only),
            types.Tool("td_network_template", "Return a ready-to-build operator-chain template (audio_reactive / feedback / render_scene / led_wall).",
                        {"name": {"type": "string", "optional": True}},
                        annotations=read_only),
            types.Tool("td_expert_prompt", "Return an expert system prompt for a build phase (plan/build/self_improve) or a named expert (TD_Builder).",
                        {"phase": {"type": "string", "optional": True}, "name": {"type": "string", "optional": True}},
                        annotations=read_only),
            types.Tool("td_compat_check", "Compare client vs bridge versions: MAJOR=error, MINOR=warning, PATCH=tolerated.",
                        {"client_ver": {"type": "string"}, "bridge_ver": {"type": "string"}},
                        annotations=read_only),
            types.Tool("td_score_build", "Score a network description 0..100 (A–F) from validity, typed/wired completeness and corpus backing (tdmcp scoreBuild).",
                        {"spec": {"type": "string"}},
                        annotations=read_only),
            types.Tool("td_validate_build", "Validate + score a network description and attach recovery hints for each finding (pure, no running TD).",
                        {"spec": {"type": "string"}},
                        annotations=read_only),
            types.Tool("td_self_heal", "Self-heal a network description: validate -> auto-repair -> re-assess with recovery hints (pure, no running TD).",
                        {"spec": {"type": "string"}},
                        annotations=read_only),
            types.Tool("td_mediaserver", "Plan a connector to a media server (millumin / resolume / notch / disguise / qlab / madmapper).",
                        {"name": {"type": "string", "optional": True}},
                        annotations=read_only),
            types.Tool("td_analyze_performance", "Analyze a performance snapshot (fps + per-node cook times) into a verdict + suggestions.",
                        {"perf": {"type": "string"}},
                        annotations=read_only),
            types.Tool("td_discover", "Discover running TouchDesigner bridge instances on known local ports (multi-instance, twozero).",
                        {}, annotations=read_only),
            types.Tool("td_memory_save", "Persist an interaction turn to local session memory for cross-session continuity.",
                        {"role": {"type": "string"}, "text": {"type": "string"}, "tags": {"type": "string", "optional": True}},
                        annotations=read_only),
            types.Tool("td_memory_recall", "Recall the most relevant past interactions by keyword overlap.",
                        {"query": {"type": "string"}, "k": {"type": "integer", "optional": True}},
                        annotations=read_only),
            types.Tool("td_scaffold_recipe", "Scaffold a reusable recipe blueprint from a network description (TDN/JSON/operators).",
                        {"spec": {"type": "string"}},
                        annotations=read_only),
        ]

    @app.call_tool()
    async def call_tool(name, arguments):
        a = arguments or {}
        try:
            if name == "td_docs_search":
                _max = 500 if a.get("detail") == "brief" else None
                out = (_fmt(ret.search(a.get("query", ""), family=a.get("family"),
                                       category=a.get("category"), version=a.get("version"), k=a.get("k", 5)),
                         a.get("query", ""), max_chars=_max), "")
            elif name == "td_docs_operator":
                out = (td_docs_operator(a.get("name", ""), a.get("k", 3)), "")
            elif name == "td_docs_python":
                out = (td_docs_python(a.get("name", ""), a.get("k", 3)), "")
            elif name == "td_docs_glsl":
                out = (td_docs_glsl(a.get("query", "fragment"), a.get("k", 2)), "")
            elif name == "td_docs_template":
                out = (td_docs_template(a.get("query", ""), a.get("k", 2)), "")
            elif name == "td_docs_version":
                out = (td_docs_version(a.get("build", "all"), a.get("k", 5)), "")
            elif name == "td_docs_family":
                out = (td_docs_family(a.get("family", ""), a.get("version")), "")
            elif name == "td_docs_parameter":
                out = (td_docs_parameter(a.get("op_name", ""), a.get("k", 1)), "")
            elif name == "td_docs_compare":
                out = (td_docs_compare(a.get("a", ""), a.get("b", "")), "")
            elif name == "td_docs_connections":
                out = (td_docs_connections(a.get("name", "")), "")
            elif name == "td_docs_workflow":
                out = (td_docs_workflow(a.get("query", ""), a.get("k", 8)), "")
            elif name == "td_docs_version_info":
                out = (td_docs_version_info(a.get("build", "2023.10000")), "")
            elif name == "td_docs_related":
                out = (td_docs_related(a.get("name", ""), a.get("depth", 1)), "")
            elif name == "td_build_network":
                out = (td_build_network(a.get("spec", "[]")), "")
            elif name == "td_showcontrol_plan":
                out = (td_showcontrol_plan(a.get("spec", "{}")), "")
            elif name == "td_led_map":
                out = (td_led_map(a.get("spec", "{}")), "")
            elif name == "td_docs_glossary":
                out = (td_docs_glossary(a.get("limit", 200)), "")
            elif name == "td_build_feedback":
                out = (td_build_feedback(a.get("spec", "{}")), "")
            elif name == "td_build_audio_reactive":
                out = (td_build_audio_reactive(a.get("spec", "{}")), "")
            elif name == "td_build_particle":
                out = (td_build_particle(a.get("spec", "{}")), "")
            elif name == "td_build_3d_scene":
                out = (td_build_3d_scene(a.get("spec", "{}")), "")
            elif name == "td_build_glsl_shader":
                out = (td_build_glsl_shader(a.get("spec", "{}")), "")
            elif name == "td_build_led_wall":
                out = (td_build_led_wall(a.get("spec", "{}")), "")
            elif name == "td_build_dmx_fixture":
                out = (td_build_dmx_fixture(a.get("spec", "{}")), "")
            elif name == "td_build_video_pipeline":
                out = (td_build_video_pipeline(a.get("spec", "{}")), "")
            elif name == "td_build_midi_rig":
                out = (td_build_midi_rig(a.get("spec", "{}")), "")
            elif name == "td_build_kinect_skeleton":
                out = (td_build_kinect_skeleton(a.get("spec", "{}")), "")
            elif name == "td_glsl_pattern":
                out = (td_glsl_pattern(a.get("name", "simple_noise")), "")
            elif name == "td_network_template":
                out = (td_network_template(a.get("name", "feedback")), "")
            elif name == "td_expert_prompt":
                out = (td_expert_prompt(a.get("phase"), a.get("name")), "")
            elif name == "td_compat_check":
                out = (td_compat_check(a.get("client_ver", "1.0.0"), a.get("bridge_ver", "1.0.0")), "")
            elif name == "td_score_build":
                out = (td_score_build(a.get("spec", "[]")), "")
            elif name == "td_validate_build":
                out = (td_validate_build(a.get("spec", "[]")), "")
            elif name == "td_self_heal":
                out = (td_self_heal(a.get("spec", "[]")), "")
            elif name == "td_mediaserver":
                out = (td_mediaserver(a.get("name", "resolume")), "")
            elif name == "td_analyze_performance":
                out = (td_analyze_performance(a.get("perf", "{}")), "")
            elif name == "td_discover":
                out = (td_discover(), "")
            elif name == "td_memory_save":
                out = (td_memory_save(a.get("role", "user"), a.get("text", ""), a.get("tags")), "")
            elif name == "td_memory_recall":
                out = (td_memory_recall(a.get("query", ""), a.get("k", 5)), "")
            elif name == "td_scaffold_recipe":
                out = (td_scaffold_recipe(a.get("spec", "[]")), "")
            else:
                out = ("unknown tool", "")
        except Exception as e:  # noqa: BLE001
            out = (f"error: {e}", "")
        return [types.TextContent(type="text", text=out[0])]

    return app


def _main_mcp():
    from mcp.server.stdio import stdio_server
    import anyio

    app = create_server()

    async def run():
        async with stdio_server() as (r, w):
            await app.run(r, w, app.create_initialization_options())

    anyio.run(run)


def _main_cli():
    ap = argparse.ArgumentParser(description="td-mcp offline doc search (no deps)")
    ap.add_argument("query", nargs="*", help="search text")
    ap.add_argument("--family")
    ap.add_argument("--category")
    ap.add_argument("--version")
    ap.add_argument("--parameter", help="single-operator parameter spec")
    ap.add_argument("--glossary", action="store_true", help="print KB glossary")
    ap.add_argument("-k", type=int, default=5)
    args = ap.parse_args()
    if args.glossary:
        print(td_docs_glossary(limit=args.k * 40 or 200))
    elif args.parameter:
        print(td_docs_parameter(args.parameter, k=args.k))
    elif args.family:
        print(td_docs_family(args.family, version=args.version))
    else:
        q = " ".join(args.query) or "blur top parameters"
        print(td_docs_search(q, family=args.family, category=args.category,
                              version=args.version, k=args.k))


def main():
    if os.environ.get("TD_MCP_MODE") == "mcp" or "--mcp" in sys.argv:
        sys.argv = [a for a in sys.argv if a != "--mcp"]
        _main_mcp()
    else:
        _main_cli()


if __name__ == "__main__":
    main()