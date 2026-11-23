"""td-mcp offline server: documentation / RAG tools over a local KB.

Run as MCP (needs `mcp`):   python -m td_mcp.server_offline
Run as CLI (no deps):        python -m td_mcp.server_offline "blur top params"
"""

import argparse
import os
import sys

import mcp.types as types

from td_mcp.rag.retriever import build_retriever
from td_mcp.rag.strategies import ParallelRetriever
from td_mcp import __version__
from td_mcp import instructions as _instructions
from td_mcp.kb import corpus
from td_mcp.kb.corpus import _str
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
from td_mcp import validation as validation_mod
from td_mcp.tools import layout as layout_mod
from td_mcp import param_resolver as param_resolver_mod
from td_mcp.tools.risk import risk_class, tool_annotations
from td_mcp.util import _json_safe
from td_mcp.util.output_budget import truncate_text, DEFAULT_MAX_CHARS
from td_mcp.observability import get_trace_log
from td_mcp import freshness as freshness_mod
from td_mcp import scaffolds as scaffolds_mod
from td_mcp import safety_registry as safety_registry_mod
from td_mcp import activity as activity_mod

DEFAULT_CHUNKS = os.path.join(os.path.dirname(__file__), "kb", "chunks.jsonl")


def _fmt(results, query, max_chars=None):
    if not results:
        return f"No documentation matched: {query!r}. Try a different operator/Python class name."
    lines = [f"Found {len(results)} doc chunk(s) for {query!r}:\n"]
    banner = freshness_mod.freshness_banner()
    if banner:
        lines.append("> " + banner + "\n")
    for c, sc in results:
        head = f"### {c.get('title')}"
        if c.get('section'):
            head += f" § {c['section']}"
        head += f"  [{c.get('family') or c.get('category')}]  (score {sc})"
        meta = f"source: {c.get('source', '')}"
        url = c.get('url')
        if url:
            meta += f"\nlink: {url}"
        text = c.get('text', '')
        # Sibling-operator guardrail (wiki-rag): when a hit is an operator,
        # flag the same-named operators in other families.
        siblings = knowledge_graph.sibling_operators(c.get('title'))
        # Explicit per-call cap wins; otherwise honor the env-tunable cap.
        cap = max_chars or DEFAULT_MAX_CHARS
        if cap:
            text, truncated, _info = truncate_text(text, cap)
            if truncated:
                text += f"\n[{_info['omitted_chars']} chars omitted by output budget]"
        if siblings:
            text += f"\n[siblings: {', '.join(siblings)}]"
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
        wiki_db = os.environ.get("TD_MCP_WIKI_DB")
        _PR = ParallelRetriever(build_retriever(), reranker=reranker, remote=remote,
                                wiki_db=wiki_db)
    return _PR


# ============================================================
# Corpus-backed offline tools
# ============================================================
def _disambig_note(name, family=None, category=None):
    """Family-disambiguation warning for a bare operator name (if any).

    Returns ``None`` when the name is unambiguous or the caller already
    scoped the lookup via ``family``/``category``.
    """
    if family or category:
        return None
    return knowledge_graph.family_disambiguation(name)


def td_docs_search(query, family=None, category=None, version=None, k=5, max_chars=None):
    out = _fmt(get_pr().search(query, family=family, category=category, version=version, k=k),
               query, max_chars=max_chars)
    note = _disambig_note(query, family, category)
    if note:
        out += "\n\n> " + note
    return out


def td_docs_operator(name, k=3):
    """Full parameter + connector spec for one operator, with auto-appended Python class docs."""
    out = ""
    note = knowledge_graph.family_disambiguation(name)
    if note:
        out += note + "\n\n"
    # Provenance first (tdmcp #150): the corpus is an imported snapshot, so a
    # miss has to say which snapshot missed and that a miss proves nothing
    # about the caller's actual TouchDesigner build. Without this an agent
    # reads a weak fuzzy match as "here is your operator", or an empty result
    # as "no such operator", and reports either to the user as fact.
    lookup = corpus.operator_lookup(name)
    if not lookup["found"]:
        out += ("> **Not in the documentation snapshot** (%s).\n> %s\n"
                % (lookup["data_version"], corpus.SNAPSHOT_NOTICE))
        if lookup["suggestions"]:
            out += ("> Closest names in the snapshot: %s\n"
                    % ", ".join(lookup["suggestions"]))
        out += "\n"
    # First get the operator doc
    out += td_docs_search(name, category="operator", k=k)
    if lookup["found"]:
        out += "\n\n_Source: documentation snapshot (%s)._" % lookup["data_version"]
    # Then try to append the Python class (cacheflowe technique)
    # Map operator name to Python class: "Movie File In TOP" -> "MoviefileinTOP"
    op_rec = lookup.get("record")
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


def td_describe_op_type(op_type, pattern=None, page=None, include_menus=True,
                        include_help=True, include_common=True):
    """Parameter names, labels, styles, creation defaults and menu tokens for an
    operator TYPE, from the catalog probed out of a running TouchDesigner.

    The look-before-you-guess read (Embody ``describe_op_type``, Derivative
    TDMCP ``get_help``): TD abbreviates names unpredictably -- Blur TOP's
    amount is ``size``, Noise TOP's monochrome toggle is ``mono``, Lag CHOP has
    ``lag1``/``lag2`` -- so one call here replaces a guess-and-error round trip.
    """
    from td_mcp.kb import param_catalog
    return _json_dumps(param_catalog.describe(
        op_type, pattern=pattern, page=page, include_menus=include_menus,
        include_help=include_help, include_common=include_common))


def _catalog_parameter_text(op_type, note=None):
    """td_docs_parameter rendered from the live-probed catalog, page by page."""
    from td_mcp.kb import param_catalog
    info = param_catalog.op_info(op_type)
    lines = [f"### {info['label']} {info['family']}  (`{op_type}`, "
             f"inputs {info.get('min_inputs')}..{info.get('max_inputs')})",
             f"_Parameter names probed from TouchDesigner {param_catalog.build()} "
             "-- these are the exact names set_parameters accepts._", ""]
    if note:
        lines.append("> " + note)
    by_page = {}
    for p in info.get("pars") or []:
        by_page.setdefault(p.get("page") or "?", []).append(p)
    for pg, rows in by_page.items():
        lines.append(f"**{pg}**")
        for p in rows:
            bits = [p.get("style") or "?"]
            if p.get("menu"):
                bits.append("menu=" + "|".join(p["menu"][:12])
                            + ("|..." if len(p["menu"]) > 12 else ""))
            if p.get("default") not in (None, ""):
                bits.append(f"default={p['default']!r}")
            if p.get("clamp"):
                bits.append(f"clamp={p['clamp'][0]}..{p['clamp'][1]}")
            line = f"- `{p['name']}` {p.get('label', '')} ({', '.join(bits)})"
            if p.get("help") and pg != "Common":
                line += f" -- {p['help'][:140]}"
            lines.append(line)
        lines.append("")
    return "\n".join(lines).rstrip()


def td_docs_parameter(op_name, k=1):
    """Return the parameter schema for one operator: exact names from the
    live-probed catalog when it knows the type, else the scraped corpus."""
    from td_mcp.kb import param_catalog
    note = knowledge_graph.family_disambiguation(op_name)
    op_type = param_catalog.resolve_op_type(op_name)
    if op_type:
        return _catalog_parameter_text(op_type, note)
    rec = corpus.operator_record(op_name)
    if rec is None:
        msg = f"No operator matched {op_name!r}. Try td_docs_family for a list."
        if note:
            msg = note + "\n\n" + msg
        return msg
    schema = corpus.param_schema(rec)
    lines = [f"### {rec.get('displayName') or rec.get('name')} "
             f"[{rec.get('category')}]  (since {rec.get('version','?')})", ""]
    if note:
        lines.append("> " + note)
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


def td_docs_browse(query=None, family=None, limit=40):
    """L0 — orient before retrieving: section/title map of the KB.

    ``query`` is optional; without it the whole family/index is listed.
    ``family`` scopes to one operator family (TOP/CHOP/SOP/DAT/COMP/POP).
    """
    ret = get_pr()
    if family and family.upper() not in ret.families():
        return (f"Unknown family {family!r}. Known families: "
                f"{', '.join(ret.families())}.")
    rows = []
    seen = set()
    for c in ret.chunks:
        if family and c.get("family") != family.upper():
            continue
        title = c.get("title")
        kind = c.get("family") or c.get("category")
        section = c.get("section")
        label = f"{title} § {section}" if section else title
        if (label, kind) in seen:
            continue
        if query and query.lower() not in label.lower():
            continue
        seen.add((label, kind))
        rows.append((label, kind))
        if len(rows) >= limit:
            break
    header = f"### KB section map ({len(rows)} entries"
    if family:
        header += f", family={family.upper()}"
    if query:
        header += f", query={query!r}"
    header += "):\n"
    lines = [header]
    for title, kind in rows:
        lines.append(f"- {title}  [{kind}]")
    return "\n".join(lines)


def td_docs_read_page(title, section=None):
    """L2 — read one KB page (title match) in full, optionally scoped to a section.

    Complements ``td_docs_browse`` (L0) and ``td_docs_search`` (L1).
    """
    ret = get_pr()
    matches = [c for c in ret.chunks if (c.get("title") or "").lower() == (title or "").lower()]
    if not matches:
        # fall back to substring
        matches = [c for c in ret.chunks if (title or "").lower() in (c.get("title") or "").lower()]
    if not matches:
        return f"No KB page matched {title!r}. Try td_docs_browse for the index."
    lines = [f"### {matches[0].get('title')}  [{matches[0].get('family') or matches[0].get('category')}]", ""]
    for c in matches:
        text = c.get("text", "")
        if section:
            # Prefer the structured section field (wiki chunks carry one);
            # fall back to a substring probe for legacy chunks.
            sec = c.get("section")
            if sec is not None:
                if section.lower() not in sec.lower():
                    continue
            elif section.lower() not in text.lower():
                continue
        lines.append(text)
        if c.get("url"):
            lines.append(f"[source: {c['url']}]")
        lines.append("")
    return "\n".join(lines).strip()


def td_scaffold_show(project_type="vj"):
    """Scaffold a TouchDesigner show/VJ project blueprint (ai-brain idea).

    Returns a TDN-ready operator list for common project types: ``vj``,
    ``audio_reactive``, or ``installation``. Feed the ``operators`` field into
    ``td_build_network`` to get diffable YAML.
    """
    sc = scaffolds_mod.scaffold_show(project_type)
    if "error" in sc:
        return _json_dumps(sc)
    lines = [f"### Scaffold: {sc['name']}", sc["description"], "",
             "Sections: " + ", ".join(sc["sections"]), ""]
    lines.append("Operators (pass to td_build_network):")
    for op in sc["operators"]:
        ins = [i for i in (op.get("inputs") or []) if i]
        lines.append(f"  - {op['name']}  {op['type']}"
                     + (f"  <- {', '.join(ins)}" if ins else ""))
    return "\n".join(lines)


def td_gotcha_template(name="feedback_loop_top"):
    """Return a gotcha-annotated network template (mrinalghosh/quentinR2)."""
    from td_mcp import glsl_patterns as glsl_mod
    t = glsl_mod.get_gotcha_template(name)
    if "error" in t:
        return _json_dumps(t)
    lines = [f"### Template: {t['name']}", t["description"], "", "Gotchas:"]
    for g in t.get("gotchas", []):
        lines.append(f"  - {g}")
    if t.get("options"):
        lines.append("Options:")
        for k, spec in t["options"].items():
            lines.append(f"  - {k} ({spec['type']}, default={spec.get('default')!r})")
    return "\n".join(lines)


def td_source_registry(source_id=None):
    """List the vetted TouchDesigner asset/reference sources with license notes."""
    if source_id:
        return _json_dumps(safety_registry_mod.source_by_id(source_id))
    return _json_dumps(safety_registry_mod.list_sources())


def td_show_readiness(section="all"):
    """Return the live-build/show-readiness checklist (ai-brain)."""
    return _json_dumps(safety_registry_mod.show_readiness(section))


def td_activity_log(limit=50):
    """Return the recent tool-call activity log (TDPilot activity_log)."""
    return _json_dumps(activity_mod.get_activity_log().list(limit))


def td_history_recall(query, k=5):
    """Recall matching entries across session memory and technique store."""
    return _json_dumps(activity_mod.history_recall(query, k))


def td_docs_version_compat(name, build=None):
    """Per-operator addedIn/removedIn/changedIn compatibility (bottobot)."""
    return _json_dumps(corpus.operator_version_compat(name, build))


def td_kb_stats():
    """KB coverage statistics: chunk counts, operators per family, and wiki-rag
    SQLite stats when ``TD_MCP_WIKI_DB`` points at a manual.db index."""
    ret = get_pr()
    index = ret.index
    fams = sorted({c.get("family") for c in index.chunks if c.get("family")})
    stats = {
        "chunks": len(index.chunks),
        "operators": len(corpus.load_operators()),
        "techniques": sum(1 for c in index.chunks if c.get("category") == "technique"),
        "families": {f: sum(1 for c in index.chunks if c.get("family") == f) for f in fams},
        "categories": {},
    }
    for c in index.chunks:
        cat = c.get("category") or "unknown"
        stats["categories"][cat] = stats["categories"].get(cat, 0) + 1
    wiki_db = os.environ.get("TD_MCP_WIKI_DB")
    if wiki_db and os.path.exists(wiki_db):
        try:
            from td_mcp.rag.strategies import SqliteWikiStrategy
            wstats = SqliteWikiStrategy(wiki_db).stats()
            stats["wiki_rag"] = wstats
        except Exception:  # noqa: BLE001
            pass
    return _json_dumps(stats)


def td_docs_experimental(category=None, technique_id=None, show_code=True):
    """Return bottobot's experimental technique library.

    ``category`` may be an exact category (glsl, gpu-compute, machine-learning,
    generative-systems, audio-visual, networking, python-advanced) or a synonym
    (shader, ml, audio, etc.). ``technique_id`` filters to one technique.
    """
    data = corpus.load_experimental_techniques(category)
    if data is None:
        cats = corpus.list_experimental_categories()
        return _json_dumps({"error": "unknown experimental category",
                            "available": [c for c, _, _ in cats]})
    if isinstance(data, dict) and "techniques" in data:
        # single category requested
        cat_data = data
        if technique_id:
            for t in cat_data.get("techniques", []):
                if t.get("id") == technique_id:
                    out = {"category": cat_data["category"], "technique": t}
                    if not show_code:
                        out["technique"].pop("code", None)
                    return _json_dumps(out)
            return _json_dumps({"error": f"unknown technique {technique_id!r}"})
        # whole category, optionally strip code snippets
        if not show_code:
            cat_data = dict(cat_data)
            cat_data["techniques"] = [
                {k: v for k, v in t.items() if k != "code"}
                for t in cat_data.get("techniques", [])
            ]
        return _json_dumps(cat_data)
    # all categories
    return _json_dumps(data)


_OFFLINE_DISPATCH = {}


def _register_offline():
    """Populate the offline tool dispatch used by ``td_tool_batch``."""
    for fn in (td_docs_search, td_docs_operator, td_docs_python, td_docs_glsl,
               td_docs_template, td_docs_version, td_docs_family, td_docs_parameter,
               td_describe_op_type, td_docs_compare, td_docs_connections, td_docs_workflow,
               td_docs_version_info, td_docs_related, td_build_network,
               td_docs_glossary, td_glsl_pattern, td_network_template,
               td_expert_prompt, td_compat_check, td_score_build,
               td_validate_build, td_analyze_build, td_diff_networks,
               td_optimize_layout, td_resolve_params, td_docs_combos,
               td_self_heal, td_mediaserver, td_analyze_performance,
               td_discover, td_memory_save, td_memory_recall,
               td_scaffold_recipe, td_docs_browse, td_docs_read_page,
               td_scaffold_show, td_gotcha_template, td_source_registry,
               td_show_readiness, td_activity_log, td_history_recall,
               td_docs_version_compat, td_kb_stats,
               td_docs_experimental, td_tool_batch, td_validate_live,
               td_showcontrol_plan, td_led_map,
               td_build_feedback, td_build_audio_reactive,
               td_build_particle, td_build_glsl_shader,
               td_build_led_wall, td_build_dmx_fixture,
               td_build_video_pipeline, td_build_midi_rig,
               td_build_kinect_skeleton):
        name = "td_" + fn.__name__[3:] if not fn.__name__.startswith("td_") else fn.__name__
        _OFFLINE_DISPATCH[name] = fn


def td_tool_batch(ops, max_ops=8):
    """Run multiple offline tools in one round-trip (TDPilot ``tool_batch``).

    ``ops`` is a JSON list of ``{"tool": "...", "args": {...}}``. Bounded to
    ``max_ops`` (env-tunable via ``TD_MCP_MAX_BATCH``).
    """
    import json as _json
    if isinstance(ops, str):
        try:
            ops = _json.loads(ops)
        except Exception:  # noqa: BLE001
            return _json_dumps({"error": "bad ops JSON"})
    if not isinstance(ops, list):
        return _json_dumps({"error": "ops must be a list"})
    max_ops = int(os.environ.get("TD_MCP_MAX_BATCH", str(max_ops)))
    results = []
    for call in ops[:max_ops]:
        tool = call.get("tool")
        fn = _OFFLINE_DISPATCH.get(tool)
        if fn is None:
            results.append({"tool": tool, "error": "unknown tool"})
            continue
        args = call.get("args", {})
        try:
            # Signature probing is brittle; invoke with named args only when the
            # function accepts them. Most offline tools take simple kwargs.
            import inspect as _inspect
            sig = _inspect.signature(fn)
            callable_args = {k: v for k, v in args.items() if k in sig.parameters}
            results.append({"tool": tool, "result": fn(**callable_args)})
        except Exception as e:  # noqa: BLE001
            results.append({"tool": tool, "error": str(e)})
    return _json_dumps(results)


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


def td_validate_live(spec, viewport_verdict=None, live_diagnostics=None):
    """Validate + score a *live* network snapshot.

    Two signals come back from the running project and neither is derivable
    from the description:

    * ``viewport_verdict`` -- ``capture_viewport``'s is_black / is_flat /
      fully_transparent judgment, so a network that renders nothing is marked
      down however clean its structure.
    * ``live_diagnostics`` -- ``get_errors``'s payload (or a list of them, one
      per operator). Cook errors, script tracebacks and GLSL compile failures
      are invisible to structural validation: a DAT whose ``onCook`` raises is
      a perfectly well-formed description. Feeding them in is what lets
      ``td_self_heal`` act on a network that is red rather than merely
      mis-shaped.

    This is the bridge-facing companion to ``td_validate_build``.
    """
    import json as _json
    data = _parse_build_spec(spec)
    if isinstance(data, str):
        return data

    def _maybe_json(value):
        if isinstance(value, str):
            try:
                return _json.loads(value)
            except Exception:  # noqa: BLE001
                return None
        return value

    vv = _maybe_json(viewport_verdict)
    diags = _maybe_json(live_diagnostics)
    if isinstance(vv, dict) or diags:
        data = dict(data)
        if isinstance(vv, dict):
            data["viewport_verdict"] = vv
        if diags:
            data["live_diagnostics"] = diags
    return _json_dumps(heal_mod.assess_build(data))


def td_analyze_build(spec):
    """Dead-weight / liveness analysis of a network description (tdmcp idea).

    Beyond *validity* (td_validate_build), this flags *usage* problems:
    isolated nodes (no connections at all), only-fed nodes (never consumed),
    empty COMPs, and broken file/path dependencies. Pure (no running TD)."""
    import json as _json
    data = _parse_build_spec(spec)
    if isinstance(data, str):
        return data
    return _json_dumps(validation_mod.analyze_build(data))


def td_diff_networks(a, b):
    """Structural A/B diff of two network descriptions (twozero / TD_Builder idea).

    Reports operators added / removed / changed (with parameter deltas) and
    connection deltas between build ``a`` and build ``b``. Each spec accepts the
    same TDN/YAML/JSON format as the other build tools."""
    import json as _json
    da = _parse_build_spec(a)
    db = _parse_build_spec(b)
    if isinstance(da, str):
        return da
    if isinstance(db, str):
        return db
    return _json_dumps(validation_mod.diff_networks(da, db))


def td_optimize_layout(spec):
    """De-overlap a network description's node positions (tdmcp / Embody idea).

    Reassigns deterministic, collision-free positions (left-to-right grid with
    clearance) so generated nodes never pile up at the origin. Returns the
    relocated spec as JSON; other fields are preserved."""
    import json as _json
    data = _parse_build_spec(spec)
    if isinstance(data, str):
        return data
    ops = layout_mod.spread_positions(data.get("operators") or [])
    out = {"operators": ops, "connections": [dict(c) for c in (data.get("connections") or [])]}
    return _json_dumps(out)


def td_resolve_params(spec):
    """Normalize an operator spec's parameter names + menu values (TD_Builder idea).

    Agents often emit friendly names (``freq``) and loose menu values (``0``)
    that silently no-op in TouchDesigner. This maps them to the exact TD
    parameter codes / valid menu items using the corpus schema and returns the
    corrected spec plus per-operator warnings."""
    import json as _json
    data = _parse_build_spec(spec)
    if isinstance(data, str):
        return data
    resolved, warnings, ok = param_resolver_mod.resolve_build(data)
    return _json_dumps({"ok": ok, "resolved_spec": resolved, "warnings": warnings})


def td_docs_combos(name, k=8):
    """Operators that work *together* with ``name`` (relationship expansion).

    Surfaces real co-occurrence from the corpus ``workflowPatterns`` — e.g.
    "show me networks that use Noise with X". Complements td_docs_related
    (graph neighbours) and td_docs_workflow (keyword chains)."""
    combos = knowledge_graph.combo_related(name, k=k)
    if not combos:
        return f"No co-occurrence data found for {name!r}."
    return f"Operators that commonly work with {name!r}:\n" + "\n".join(
        f"  {i+1}. {c}" for i, c in enumerate(combos)
    )


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
    return _json.dumps(_json_safe(obj), indent=2, default=_json_safe)


def td_discover():
    """Discover running TouchDesigner bridge instances on the local network of
    known ports (twozero multi-instance). Returns reachable candidates."""
    return _json_dumps(discover_mod.discover_instances())


def td_memory_save(role, text, tags=None, trust_tier="personal"):
    """Persist an interaction turn to local session memory (tdmcp memory, ai-brain).

    ``trust_tier`` (official|bundled|personal|community|experimental) weights
    recall ordering so authoritative hits surface first.
    """
    tag_list = tags.split(",") if isinstance(tags, str) else (tags or [])
    m = memory_mod.SessionMemory()
    entry = m.save(role, text, tag_list, trust_tier=trust_tier)
    return _json_dumps({"ok": True, "entries": len(m), "trust_tier": entry["trust_tier"]})


def td_memory_recall(query, k=5):
    """Recall the most relevant past interactions by keyword overlap,
    ranked by trust tier then relevance."""
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
# MCP server (MCP SDK 2.0 — on_* constructor kwargs, stateless transports)
# ============================================================

def _input_schema(schema):
    """A JSON Schema *object* from a bare property map.

    MCP requires ``inputSchema`` to be ``{"type": "object", "properties":
    {...}}``. Every offline tool declared a bare ``{"param": {"type": ...}}``
    map, which the SDK's result validation rejects -- so ``tools/list``
    answered *"Handler returned an invalid result"* to every MCP client since
    the v2 migration, and **no client ever saw the offline tools**. The
    in-process tests never re-validated the result the way the wire does.

    The non-standard ``"optional": True`` marker becomes ``required``.
    """
    schema = dict(schema or {})
    if schema.get("type") == "object" and "properties" in schema:
        return schema
    props, required = {}, []
    for pname, pspec in schema.items():
        pspec = dict(pspec) if isinstance(pspec, dict) else {"type": "string"}
        optional = pspec.pop("optional", False)
        props[pname] = pspec
        if not optional:
            required.append(pname)
    out = {"type": "object", "properties": props}
    if required:
        out["required"] = required
    return out


def _tool(name, description, schema, annotations=None):
    """Create a Tool with v2-compatible keyword Args.

    MCP SDK 2.0 made every Tool field except ``name`` keyword-only, so the
    positional pattern ``Tool(name, desc, schema)`` from 1.x no longer works.
    This helper bridges the two call sites, and wraps the schema into the
    object form MCP requires (see ``_input_schema``).
    """
    kwargs = {"name": name, "description": description,
              "inputSchema": _input_schema(schema)}
    if annotations is not None:
        kwargs["annotations"] = annotations
    return types.Tool(**kwargs)


async def _offline_list_tools(ctx, params):
    read_only = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
    additive = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}
    return types.ListToolsResult(
        tools=[
            _tool("td_docs_search", "Hybrid search across scraped TouchDesigner docs (operators, Python classes, GLSL, tutorials).",
                    {"query": {"type": "string"}, "family": {"type": "string", "optional": True},
                     "category": {"type": "string", "optional": True}, "version": {"type": "string", "optional": True},
                     "k": {"type": "integer", "optional": True},
                     "detail": {"type": "string", "optional": True}},
                    annotations=read_only),
            _tool("td_docs_operator", "Full parameter + connector spec for one operator (e.g. 'Noise TOP').",
                   {"name": {"type": "string"}, "k": {"type": "integer", "optional": True}},
                   annotations=read_only),
            _tool("td_docs_python", "Python API class/method lookup (e.g. 'OP_Class').",
                   {"name": {"type": "string"}, "k": {"type": "integer", "optional": True}},
                   annotations=read_only),
            _tool("td_docs_glsl", "GLSL shader snippet search.",
                   {"query": {"type": "string", "optional": True}, "k": {"type": "integer", "optional": True}},
                   annotations=read_only),
            _tool("td_docs_template", "Tutorial / workflow template search.",
                   {"query": {"type": "string", "optional": True}, "k": {"type": "integer", "optional": True}},
                   annotations=read_only),
            _tool("td_docs_version", "List docs applicable to a TD build (version-aware).",
                   {"build": {"type": "string", "optional": True}, "k": {"type": "integer", "optional": True}},
                   annotations=read_only),
            _tool("td_docs_family", "List every operator in a family (TOP/CHOP/SOP/DAT/COMP/POP) with a one-line description.",
                   {"family": {"type": "string"}, "version": {"type": "string", "optional": True}},
                   annotations=read_only),
            _tool("td_docs_parameter", "Every parameter of one operator, page by page, with the EXACT "
                   "names set_parameters accepts (probed from a running TouchDesigner), style, "
                   "menu tokens, default and help. Accepts 'Level TOP', 'levelTOP' or 'level'.",
                   {"op_name": {"type": "string"}, "k": {"type": "integer", "optional": True}},
                   annotations=read_only),
            _tool("td_describe_op_type", "Look before you guess: parameter names, labels, styles, "
                   "creation defaults and menu tokens for an operator TYPE, filtered by a name/label "
                   "glob (`pattern`, e.g. 'resol' or '*color*') or `page`. TD abbreviates names "
                   "unpredictably (Blur TOP amount is `size`, Noise TOP monochrome is `mono`); one "
                   "call here replaces a failed set_parameters. Ambiguous types list candidates.",
                   {"op_type": {"type": "string"},
                    "pattern": {"type": "string", "optional": True},
                    "page": {"type": "string", "optional": True},
                    "include_menus": {"type": "boolean", "optional": True},
                    "include_help": {"type": "boolean", "optional": True},
                    "include_common": {"type": "boolean", "optional": True}},
                   annotations=read_only),
            _tool("td_docs_compare", "Compare two operators: shared / only-A / only-B parameters. Use to choose between near-equivalents.",
                   {"a": {"type": "string"}, "b": {"type": "string"}},
                   annotations=read_only),
            _tool("td_docs_connections", "Inputs / outputs / related operators for one operator (port-level wiring hints).",
                   {"name": {"type": "string"}},
                   annotations=read_only),
            _tool("td_docs_workflow", "Suggest a workflow / operator chain for a goal via keyword overlap across the corpus.",
                   {"query": {"type": "string"}, "k": {"type": "integer", "optional": True}},
                   annotations=read_only),
            _tool("td_docs_version_info", "Version metadata (Python version, support status, notes) for a TD build.",
                   {"build": {"type": "string", "optional": True}},
                   annotations=read_only),
            _tool("td_docs_related", "Graph-RAG: operators related to the given one (networkx, depth-limited).",
                   {"name": {"type": "string"}, "depth": {"type": "integer", "optional": True}},
                   annotations=read_only),
            _tool("td_build_network", "Generate a diffable TDN YAML from a list of operator specs, validating every type against the corpus.",
                   {"spec": {"type": "string"}},
                   annotations=additive),
            _tool("td_showcontrol_plan", "Build a show-control plan: Art-Net / sACN / OSC / MIDI / timecode (familienak).",
                   {"spec": {"type": "string"}},
                   annotations=read_only),
            _tool("td_led_map", "Define an LED / pixel mapping (wall / strip / voxel) + DMX channel-map export (familienak).",
                   {"spec": {"type": "string"}},
                   annotations=read_only),
            _tool("td_docs_glossary", "Compact index of every KB entry — exploration / autocomplete over the whole documentation source.",
                    {"limit": {"type": "integer", "optional": True}},
                    annotations=read_only),
            _tool("td_docs_browse", "L0 orientation: section/title map of the KB, optionally scoped to a family or query.",
                    {"query": {"type": "string", "optional": True}, "family": {"type": "string", "optional": True},
                     "limit": {"type": "integer", "optional": True}},
                    annotations=read_only),
            _tool("td_docs_read_page", "L2 read: return the full KB page(s) for a title, optionally filtered by a section substring.",
                    {"title": {"type": "string"}, "section": {"type": "string", "optional": True}},
                    annotations=read_only),
            _tool("td_scaffold_show", "Scaffold a TouchDesigner show/VJ project blueprint (vj / audio_reactive / installation).",
                    {"project_type": {"type": "string", "optional": True}},
                    annotations=read_only),
            _tool("td_gotcha_template", "Return a gotcha-annotated network template (chop_source_with_null / glsl_top_vec4_uniform / audio_in_with_analyze / feedback_loop_top / render_pipeline).",
                    {"name": {"type": "string", "optional": True}},
                    annotations=read_only),
            _tool("td_source_registry", "List vetted TouchDesigner asset/reference sources with license + TD-version notes.",
                    {"source_id": {"type": "string", "optional": True}},
                    annotations=read_only),
            _tool("td_show_readiness", "Return the live-build/show-readiness checklist (before_automation / before_show / asset_intake / all).",
                    {"section": {"type": "string", "optional": True}},
                    annotations=read_only),
            _tool("td_activity_log", "Return the recent tool-call activity log.",
                    {"limit": {"type": "integer", "optional": True}},
                    annotations=read_only),
            _tool("td_history_recall", "Recall matching entries across session memory and technique store.",
                    {"query": {"type": "string"}, "k": {"type": "integer", "optional": True}},
                    annotations=read_only),
            _tool("td_docs_version_compat", "Per-operator addedIn/removedIn/changedIn compatibility for a TD build.",
                    {"name": {"type": "string"}, "build": {"type": "string", "optional": True}},
                    annotations=read_only),
            _tool("td_kb_stats", "Return KB coverage statistics (chunk counts, operators per family, wiki-rag integration).",
                    {}, annotations=read_only),
            _tool("td_docs_experimental", "Return bottobot's experimental technique library (glsl/gpu-compute/ml/audio/networking/python/generative).",
                    {"category": {"type": "string", "optional": True},
                     "technique_id": {"type": "string", "optional": True},
                     "show_code": {"type": "boolean", "optional": True}},
                    annotations=read_only),
            _tool("td_tool_batch", "Run multiple offline tools in one round-trip (bounded to 8).",
                    {"ops": {"type": "string"}},
                    annotations=read_only),
            _tool("td_build_feedback", "Create a feedback network (Noise -> Level decay -> Feedback -> Threshold/Level -> Out).",
                   {"spec": {"type": "string"}},
                   annotations=additive),
            _tool("td_build_audio_reactive", "Create an audio-reactive network (Audio Device In -> Math/Filter -> Export to visual params).",
                   {"spec": {"type": "string"}},
                   annotations=additive),
            _tool("td_build_particle", "Create a particle system (POP or GPU TOP based).",
                   {"spec": {"type": "string"}},
                   annotations=additive),
            _tool("td_build_3d_scene", "Create a 3D scene (Geometry + Camera + Light + Render).",
                   {"spec": {"type": "string"}},
                   annotations=additive),
            _tool("td_build_glsl_shader", "Create a GLSL TOP with a shader template.",
                   {"spec": {"type": "string"}},
                   annotations=additive),
            _tool("td_build_led_wall", "Create a pixel-mapping pipeline for an LED wall (Noise -> Resolution -> TOP to CHOP -> DMX Out).",
                   {"spec": {"type": "string"}},
                   annotations=additive),
            _tool("td_build_dmx_fixture", "Create a DMX input pipeline to receive sACN/Art-Net control channels (DMX In -> Select -> Math -> Out).",
                    {"spec": {"type": "string"}},
                    annotations=additive),
            _tool("td_build_video_pipeline", "Create a video playback/processing pipeline (Movie File In -> optional Chroma Key -> LUT -> Level -> Out).",
                    {"spec": {"type": "string"}},
                    annotations=additive),
            _tool("td_build_midi_rig", "Create a MIDI input rig (MIDI In -> Select -> Math normalize -> Filter -> Out CHOP).",
                    {"spec": {"type": "string"}},
                    annotations=additive),
            _tool("td_build_kinect_skeleton", "Create a Kinect Azure skeleton-tracking rig (joints -> select -> remap -> optional Point Sprite visualization).",
                    {"spec": {"type": "string"}},
                    annotations=additive),
            _tool("td_glsl_pattern", "Return a named, paste-ready GLSL fragment shader (simple_noise / rgb_shift / hue_cycle / feedback_blend / kaleidoscope / scanline).",
                    {"name": {"type": "string", "optional": True}},
                    annotations=read_only),
            _tool("td_network_template", "Return a ready-to-build operator-chain template (audio_reactive / feedback / render_scene / led_wall).",
                    {"name": {"type": "string", "optional": True}},
                    annotations=read_only),
            _tool("td_expert_prompt", "Return an expert system prompt for a build phase (plan/build/self_improve) or a named expert (TD_Builder).",
                    {"phase": {"type": "string", "optional": True}, "name": {"type": "string", "optional": True}},
                    annotations=read_only),
            _tool("td_compat_check", "Compare client vs bridge versions: MAJOR=error, MINOR=warning, PATCH=tolerated.",
                    {"client_ver": {"type": "string"}, "bridge_ver": {"type": "string"}},
                    annotations=read_only),
            _tool("td_score_build", "Score a network description 0..100 (A–F) from validity, typed/wired completeness and corpus backing (tdmcp scoreBuild).",
                    {"spec": {"type": "string"}},
                    annotations=read_only),
            _tool("td_validate_build", "Validate + score a network description and attach recovery hints for each finding (pure, no running TD).",
                    {"spec": {"type": "string"}},
                    annotations=read_only),
            _tool("td_validate_live", "The live<->offline loop: validate + score a network description WITH the signals only a running "
                    "TouchDesigner has -- a capture_viewport verdict (is_black/is_flat/is_static) and a get_errors payload "
                    "(cook, script and shader errors). Pass both as JSON strings; the score then reflects what the network "
                    "actually does, not just its shape.",
                    {"spec": {"type": "string"}, "viewport_verdict": {"type": "string", "optional": True},
                     "live_diagnostics": {"type": "string", "optional": True}},
                    annotations=read_only),
            _tool("td_analyze_build", "Liveness/dead-weight analysis of a network description: isolated nodes, only-fed nodes, empty COMPs, broken file paths (pure, no running TD).",
                    {"spec": {"type": "string"}},
                    annotations=read_only),
            _tool("td_diff_networks", "Structural A/B diff of two network descriptions: added/removed/changed operators + parameter deltas + connection deltas.",
                    {"a": {"type": "string"}, "b": {"type": "string"}},
                    annotations=read_only),
            _tool("td_optimize_layout", "De-overlap a network description's node positions with deterministic, collision-free placement.",
                    {"spec": {"type": "string"}},
                    annotations=read_only),
            _tool("td_resolve_params", "Normalize friendly parameter names + menu values to exact TD codes via the corpus (prevents silent no-op params).",
                    {"spec": {"type": "string"}},
                    annotations=read_only),
            _tool("td_docs_combos", "Operators that commonly work together with the given one (corpus workflowPatterns co-occurrence).",
                    {"name": {"type": "string"}, "k": {"type": "integer", "optional": True}},
                    annotations=read_only),
            _tool("td_self_heal", "Self-heal a network description: validate -> auto-repair -> re-assess with recovery hints (pure, no running TD).",
                    {"spec": {"type": "string"}},
                    annotations=read_only),
            _tool("td_mediaserver", "Plan a connector to a media server (millumin / resolume / notch / disguise / qlab / madmapper).",
                    {"name": {"type": "string", "optional": True}},
                    annotations=read_only),
            _tool("td_analyze_performance", "Analyze a performance snapshot (fps + per-node cook times) into a verdict + suggestions.",
                    {"perf": {"type": "string"}},
                    annotations=read_only),
            _tool("td_discover", "Discover running TouchDesigner bridge instances on known local ports (multi-instance, twozero).",
                    {}, annotations=read_only),
            _tool("td_memory_save", "Persist an interaction turn to local session memory for cross-session continuity. Optional trust_tier weights recall ordering.",
                   {"role": {"type": "string"}, "text": {"type": "string"}, "tags": {"type": "string", "optional": True},
                    "trust_tier": {"type": "string", "optional": True}},
                   annotations=read_only),
            _tool("td_memory_recall", "Recall the most relevant past interactions by keyword overlap.",
                    {"query": {"type": "string"}, "k": {"type": "integer", "optional": True}},
                    annotations=read_only),
            _tool("td_scaffold_recipe", "Scaffold a reusable recipe blueprint from a network description (TDN/JSON/operators).",
                    {"spec": {"type": "string"}},
                    annotations=read_only),
        ],
        ttl_ms=60000,
        cache_scope="private",
    )


async def _offline_call_tool(ctx, params):
    name = params.name
    a = params.arguments or {}
    import time as _time
    _t0 = _time.perf_counter()
    ok_flag = True
    # Opt-in observability: record a timed span per tool call when
    # TD_MCP_OBSERVE is set (no-op otherwise — zero overhead when off).
    with get_trace_log().span("tool_call", tool=name):
        try:
            if name == "td_docs_search":
                _max = 500 if a.get("detail") == "brief" else None
                out = (td_docs_search(a.get("query", ""), family=a.get("family"),
                                      category=a.get("category"), version=a.get("version"),
                                      k=a.get("k", 5), max_chars=_max), "")
            elif name == "td_docs_operator":
                out = (td_docs_operator(a.get("name", ""), a.get("k", 3)), "")
            elif name == "td_validate_live":
                out = (td_validate_live(a.get("spec", ""), a.get("viewport_verdict"),
                                        a.get("live_diagnostics")), "")
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
            elif name == "td_describe_op_type":
                out = (td_describe_op_type(a.get("op_type", ""), a.get("pattern"), a.get("page"),
                                           a.get("include_menus", True), a.get("include_help", True),
                                           a.get("include_common", True)), "")
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
            elif name == "td_analyze_build":
                out = (td_analyze_build(a.get("spec", "[]")), "")
            elif name == "td_diff_networks":
                out = (td_diff_networks(a.get("a", "[]"), a.get("b", "[]")), "")
            elif name == "td_optimize_layout":
                out = (td_optimize_layout(a.get("spec", "[]")), "")
            elif name == "td_resolve_params":
                out = (td_resolve_params(a.get("spec", "[]")), "")
            elif name == "td_docs_combos":
                out = (td_docs_combos(a.get("name", ""), a.get("k", 8)), "")
            elif name == "td_self_heal":
                out = (td_self_heal(a.get("spec", "[]")), "")
            elif name == "td_mediaserver":
                out = (td_mediaserver(a.get("name", "resolume")), "")
            elif name == "td_analyze_performance":
                out = (td_analyze_performance(a.get("perf", "{}")), "")
            elif name == "td_discover":
                out = (td_discover(), "")
            elif name == "td_memory_save":
                out = (td_memory_save(a.get("role", "user"), a.get("text", ""), a.get("tags"),
                                      a.get("trust_tier", "personal")), "")
            elif name == "td_memory_recall":
                out = (td_memory_recall(a.get("query", ""), a.get("k", 5)), "")
            elif name == "td_scaffold_recipe":
                out = (td_scaffold_recipe(a.get("spec", "[]")), "")
            elif name == "td_docs_browse":
                out = (td_docs_browse(a.get("query"), a.get("family"),
                                      a.get("limit", 40)), "")
            elif name == "td_docs_read_page":
                out = (td_docs_read_page(a.get("title", ""),
                                         a.get("section")), "")
            elif name == "td_scaffold_show":
                out = (td_scaffold_show(a.get("project_type", "vj")), "")
            elif name == "td_gotcha_template":
                out = (td_gotcha_template(a.get("name", "feedback_loop_top")), "")
            elif name == "td_source_registry":
                out = (td_source_registry(a.get("source_id")), "")
            elif name == "td_show_readiness":
                out = (td_show_readiness(a.get("section", "all")), "")
            elif name == "td_activity_log":
                out = (td_activity_log(a.get("limit", 50)), "")
            elif name == "td_history_recall":
                out = (td_history_recall(a.get("query", ""), a.get("k", 5)), "")
            elif name == "td_docs_version_compat":
                out = (td_docs_version_compat(a.get("name", ""), a.get("build")), "")
            elif name == "td_kb_stats":
                out = (td_kb_stats(), "")
            elif name == "td_docs_experimental":
                out = (td_docs_experimental(a.get("category"), a.get("technique_id"),
                                            a.get("show_code", True)), "")
            elif name == "td_tool_batch":
                out = (td_tool_batch(a.get("ops", "[]")), "")
            else:
                out = ("unknown tool", "")
        except Exception as e:  # noqa: BLE001
            ok_flag = False
            out = (f"error: {e}", "")
        finally:
            _dt_ms = (_time.perf_counter() - _t0) * 1000.0
            try:
                activity_mod.get_activity_log().add(
                    name, args_summary=str(a)[:200],
                    result_summary=(str(out[0])[:200] if out else ""),
                    duration_ms=_dt_ms, ok=ok_flag)
            except Exception:  # noqa: BLE001
                pass
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=out[0])],
        result_type="complete",
    )


async def _offline_list_resources(ctx, params):
    ret = get_pr()
    families = ret.families()
    resources = [types.Resource(
        "manual://index",
        name="TouchDesigner manual index",
        description="Compact index of every operator + Python class in the KB.",
        mimeType="text/plain")]
    for fam in families:
        resources.append(types.Resource(
            f"manual://operators/{fam.lower()}",
            name=f"{fam} operators",
            description=f"All {fam} operators in the local KB.",
            mimeType="text/plain"))
    return types.ListResourcesResult(resources=resources, ttl_ms=60000, cache_scope="private")


async def _offline_read_resource(ctx, params):
    uri = params.uri
    ret = get_pr()
    if uri == "manual://index":
        text = td_docs_glossary(limit=200)
    elif uri.startswith("manual://operators/"):
        fam = uri.rsplit("/", 1)[-1].upper()
        text = td_docs_family(fam)
    else:
        text = f"Unknown resource: {uri}"
    return types.ReadResourceResult(
        contents=[types.TextResourceContents(uri=uri, text=text, mimeType="text/plain")]
    )


async def _offline_list_prompts(ctx, params):
    # Expose the expert personas + phase prompts as discoverable MCP prompts
    # (Touchdesigner___MCP_server PROMPT_TEMPLATES idea) so clients can pull
    # the right workflow prompt without knowing tool names.
    prompts = []
    for name in prompts_mod.list_experts():
        prompts.append(types.Prompt(name, description=f"Expert persona: {name}",
                                    arguments=[]))
    for phase in ("plan", "build", "self_improve"):
        prompts.append(types.Prompt(
            f"phase_{phase}",
            description=f"Combined system prompt for the '{phase}' build phase.",
            arguments=[]))
    return types.ListPromptsResult(prompts=prompts, ttl_ms=60000, cache_scope="private")


async def _offline_get_prompt(ctx, params):
    name = params.name
    if name in prompts_mod.list_experts():
        text = prompts_mod.get_expert_prompt(name) or ""
    elif name.startswith("phase_"):
        text = prompts_mod.build_phase_prompt(name[len("phase_"):])
    else:
        text = prompts_mod.build_phase_prompt("build")
    return types.GetPromptResult(
        description=name,
        messages=[types.PromptMessage(role="user",
                   content=types.TextContent(type="text", text=text))])


def create_server():
    from mcp.server import Server
    app = Server(
        name="td-mcp-offline",
        version=__version__,
        instructions=_instructions.OFFLINE,
        on_list_tools=_offline_list_tools,
        on_call_tool=_offline_call_tool,
        on_list_resources=_offline_list_resources,
        on_read_resource=_offline_read_resource,
        on_list_prompts=_offline_list_prompts,
        on_get_prompt=_offline_get_prompt,
    )
    get_pr()
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
    # See td_mcp.util.stdio: pinned in main(), never at import.
    from td_mcp.util.stdio import pin_stdio_utf8
    pin_stdio_utf8()
    if os.environ.get("TD_MCP_MODE") == "mcp" or "--mcp" in sys.argv:
        sys.argv = [a for a in sys.argv if a != "--mcp"]
        _main_mcp()
    else:
        _main_cli()


# Late-binding: populate the offline tool dispatch after every tool function is
# defined (module import order matters for the tool_batch dispatcher).
_register_offline()


if __name__ == "__main__":
    main()