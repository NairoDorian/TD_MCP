"""Tool risk classification (TrueFiasco 4-class, MIT-safe reimpl).

Each tool gets a risk class; the live bridge can enforce policies:
  * READ_ONLY         - readOnlyHint=true,  no mutations
  * WRITE_ADDITIVE    - creates new nodes/params, no deletion
  * WRITE_CHECKPOINT  - mutates but idempotent (save/checkpoint)
  * DESTRUCTIVE       - deletes/overwrites, needs confirm/undo

These map to MCP tool annotations: readOnlyHint, destructiveHint, idempotentHint.
"""

RISK_CLASS = {
    # Offline (doc/RAG) tools — READ_ONLY
    "td_docs_search":        "READ_ONLY",
    "td_docs_operator":      "READ_ONLY",
    "td_docs_python":        "READ_ONLY",
    "td_docs_glsl":          "READ_ONLY",
    "td_docs_template":      "READ_ONLY",
    "td_docs_version":       "READ_ONLY",
    "td_docs_family":        "READ_ONLY",
    "td_docs_parameter":     "READ_ONLY",
    "td_docs_glossary":      "READ_ONLY",
    "td_docs_compare":       "READ_ONLY",
    "td_docs_connections":   "READ_ONLY",
    "td_docs_workflow":      "READ_ONLY",
    "td_docs_version_info":  "READ_ONLY",
    "td_docs_related":       "READ_ONLY",
    "td_build_network":      "READ_ONLY",   # generates YAML, no TD mutation

    # Show-control / LED planning — READ_ONLY
    "td_showcontrol_plan":   "READ_ONLY",
    "td_led_map":            "READ_ONLY",

    # Inspiration planners (glsl templates, experts, compat, scoring, media, perf)
    "td_glsl_pattern":       "READ_ONLY",
    "td_network_template":   "READ_ONLY",
    "td_expert_prompt":      "READ_ONLY",
    "td_compat_check":       "READ_ONLY",
    "td_score_build":        "READ_ONLY",
    "td_validate_live":      "READ_ONLY",
    "td_mediaserver":        "READ_ONLY",
    "td_analyze_performance":"READ_ONLY",

    # Offline authoring, analysis and scaffolding — READ_ONLY.
    # Every one of these is a pure function over a *description*: they emit
    # YAML/JSON for a caller to act on and never touch a running TouchDesigner.
    # They were relying on the WRITE_ADDITIVE fallback, so a client running a
    # read-only policy refused plain documentation lookups.
    "td_docs_browse":        "READ_ONLY",
    "td_docs_read_page":     "READ_ONLY",
    "td_docs_combos":        "READ_ONLY",
    "td_docs_experimental":  "READ_ONLY",
    "td_docs_version_compat": "READ_ONLY",
    "td_kb_stats":           "READ_ONLY",
    "td_describe_op_type":   "READ_ONLY",
    "td_build_feedback":     "READ_ONLY",
    "td_build_audio_reactive": "READ_ONLY",
    "td_build_particle":     "READ_ONLY",
    "td_build_3d_scene":     "READ_ONLY",
    "td_build_glsl_shader":  "READ_ONLY",
    "td_build_led_wall":     "READ_ONLY",
    "td_build_dmx_fixture":  "READ_ONLY",
    "td_build_video_pipeline": "READ_ONLY",
    "td_build_midi_rig":     "READ_ONLY",
    "td_build_kinect_skeleton": "READ_ONLY",
    "td_validate_build":     "READ_ONLY",
    "td_analyze_build":      "READ_ONLY",
    "td_diff_networks":      "READ_ONLY",
    "td_optimize_layout":    "READ_ONLY",
    "td_resolve_params":     "READ_ONLY",
    "td_self_heal":          "READ_ONLY",
    "td_scaffold_show":      "READ_ONLY",
    "td_gotcha_template":    "READ_ONLY",
    "td_source_registry":    "READ_ONLY",
    "td_show_readiness":     "READ_ONLY",
    "td_activity_log":       "READ_ONLY",
    "td_history_recall":     "READ_ONLY",
    # A batch is only as safe as what it dispatches, so it keeps the
    # conservative class rather than inheriting its children's.
    "td_tool_batch":         "WRITE_ADDITIVE",

    # Session memory + discovery + recipe scaffolding — READ_ONLY (local only)
    "td_discover":           "READ_ONLY",
    "td_memory_save":        "WRITE_ADDITIVE",
    "td_memory_recall":      "READ_ONLY",
    "td_scaffold_recipe":    "READ_ONLY",

    # Live bridge tools — read-only inspectors
    "get_parameters":        "READ_ONLY",
    "describe_op_type":      "READ_ONLY",   # probes in /sys/quiet, destroyed
    "get_parameter_info":    "READ_ONLY",
    "get_non_default_params":"READ_ONLY",
    "get_errors":            "READ_ONLY",
    "list_nodes":            "READ_ONLY",
    "project_info":          "READ_ONLY",
    "capture_viewport":      "READ_ONLY",
    # `observe` force-cooks a few frames to judge motion. It mutates nothing,
    # but it is not free -- classified read-only, deliberately, with the cost
    # bounded by _MOTION_MAX_FRAMES in the bridge.
    "observe":               "READ_ONLY",
    "get_resource":          "READ_ONLY",
    "describe_td_tools":     "READ_ONLY",
    "read_chop":             "READ_ONLY",
    "diff_chop":             "READ_ONLY",
    "read_pop":              "READ_ONLY",
    "read_top":              "READ_ONLY",
    "read_dat":              "READ_ONLY",
    "scan_network":          "READ_ONLY",
    "get_node":              "READ_ONLY",
    "map_network":           "READ_ONLY",
    "get_connections":       "READ_ONLY",
    "get_performance":       "READ_ONLY",
    "validate_network":      "READ_ONLY",
    "find_nodes":            "READ_ONLY",

    # Live bridge tools — additive writers
    "create_node":           "WRITE_ADDITIVE",
    "set_parameters":        "WRITE_ADDITIVE",
    "pulse_parameter":       "WRITE_ADDITIVE",
    "build_network":         "WRITE_ADDITIVE",  # replace=true overwrites same-named ops
    "batch":                 "WRITE_ADDITIVE",  # mixed, but creates more than deletes
    "build_and_verify":      "WRITE_ADDITIVE",
    "connect_nodes":         "WRITE_ADDITIVE",
    "rename_node":           "WRITE_ADDITIVE",
    "copy_node":             "WRITE_ADDITIVE",
    "auto_layout":           "WRITE_ADDITIVE",
    "set_node_color":        "WRITE_ADDITIVE",
    "set_node_comment":      "WRITE_ADDITIVE",
    "disconnect_nodes":      "WRITE_ADDITIVE",
    "exec_node_method":      "WRITE_ADDITIVE",
    "snapshot_network":      "WRITE_ADDITIVE",
    "restore_network":       "WRITE_ADDITIVE",
    "set_flags":             "WRITE_ADDITIVE",
    "set_node_position":     "WRITE_ADDITIVE",
    "export_recipe":         "WRITE_ADDITIVE",
    "import_recipe":         "WRITE_ADDITIVE",
    "save_tox":              "WRITE_ADDITIVE",
    "timeline":              "WRITE_ADDITIVE",

    # Live bridge tools — mutating but idempotent
    # Clearing a stale script-error badge changes no content and re-running it
    # is a no-op, so it is a checkpoint-class write rather than an additive one.
    "clear_script_errors":   "WRITE_CHECKPOINT",

    # Live bridge tools — destructive
    "delete_node":           "DESTRUCTIVE",
    "execute_python":        "DESTRUCTIVE",  # arbitrary code
    # Both of these hand TouchDesigner caller-supplied Python by another name:
    # a parameter expression is evaluated, and a DAT's text IS the program for
    # an Execute/Script DAT. set_dat_text also overwrites the whole DAT. They
    # are gated by TD_MCP_ALLOW_EXEC for the same reason.
    "bind_parameter_expression": "DESTRUCTIVE",
    "set_dat_text":          "DESTRUCTIVE",
}


# MCP annotation mapping per risk class
ANNOTATIONS = {
    "READ_ONLY":        {"readOnlyHint": True,  "destructiveHint": False, "idempotentHint": True},
    "WRITE_ADDITIVE":   {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    "WRITE_CHECKPOINT": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    "DESTRUCTIVE":      {"readOnlyHint": False, "destructiveHint": True,  "idempotentHint": False},
}


def risk_class(tool_name):
    """Risk class for a tool.

    The fallback is deliberately the *middle* of the range, not the bottom:
    an unclassified tool must never read as read-only. It is still a fallback,
    though -- ``tests/test_parity.py`` requires every advertised tool to carry
    an explicit entry, because a new tool arriving with a permissive default is
    exactly how an unreviewed capability ships (Embody v6.0.264, where two new
    tools landed without a registry entry and the drift guard refused them).
    """
    return RISK_CLASS.get(tool_name, "WRITE_ADDITIVE")


def tool_annotations(tool_name):
    return ANNOTATIONS[risk_class(tool_name)]


# Optional: live bridge can enforce a global policy
#   TD_BUILDER_LIVE_READONLY=1  -> all live tools become READ_ONLY
#   TD_MCP_MAX_RISK=WRITE_ADDITIVE  -> block DESTRUCTIVE
def enforce_policy(tool_name, env=None):
    import os
    env = env or os.environ
    if env.get("TD_BUILDER_LIVE_READONLY") == "1":
        return "READ_ONLY"
    max_risk = env.get("TD_MCP_MAX_RISK", "DESTRUCTIVE")
    order = ["READ_ONLY", "WRITE_ADDITIVE", "WRITE_CHECKPOINT", "DESTRUCTIVE"]
    tc = risk_class(tool_name)
    if order.index(tc) > order.index(max_risk):
        return max_risk
    return tc