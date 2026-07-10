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
    "td_mediaserver":        "READ_ONLY",
    "td_analyze_performance":"READ_ONLY",

    # Session memory + discovery + recipe scaffolding — READ_ONLY (local only)
    "td_discover":           "READ_ONLY",
    "td_memory_save":        "WRITE_ADDITIVE",
    "td_memory_recall":      "READ_ONLY",
    "td_scaffold_recipe":    "READ_ONLY",

    # Live bridge tools — read-only inspectors
    "get_parameters":        "READ_ONLY",
    "get_errors":            "READ_ONLY",
    "list_nodes":            "READ_ONLY",
    "project_info":          "READ_ONLY",
    "capture_viewport":      "READ_ONLY",
    "get_resource":          "READ_ONLY",
    "describe_td_tools":     "READ_ONLY",
    "read_chop":             "READ_ONLY",
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

    # Live bridge tools — destructive
    "delete_node":           "DESTRUCTIVE",
    "execute_python":        "DESTRUCTIVE",  # arbitrary code
}


# MCP annotation mapping per risk class
ANNOTATIONS = {
    "READ_ONLY":        {"readOnlyHint": True,  "destructiveHint": False, "idempotentHint": True},
    "WRITE_ADDITIVE":   {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    "WRITE_CHECKPOINT": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    "DESTRUCTIVE":      {"readOnlyHint": False, "destructiveHint": True,  "idempotentHint": False},
}


def risk_class(tool_name):
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