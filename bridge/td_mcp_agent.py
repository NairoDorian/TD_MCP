"""td_mcp_agent — run an autonomous building agent directly inside TouchDesigner.

This script runs a zero-dependency OpenAI-compatible agent loop (supporting
Gemini, Ollama, and OpenAI). It allows a Text DAT inside TouchDesigner to
send natural-language requests, get tool-calling decisions from the model,
and execute those decisions locally via the bridge.

Requirements:
- The bridge must be running (e.g. start it in your session).
- For Gemini: Set GEMINI_API_KEY environment variable.
- For Ollama: Ensure Ollama is running on localhost:11434.
"""

import os
import json
import base64
import urllib.request
import urllib.error

# Default connection settings to the local bridge
DEFAULT_BRIDGE_PORT = 9980
DEFAULT_HOST = "127.0.0.1"


# ---------------------------------------------------------------------------
# Tool Schemas (OpenAI-compatible function definitions)
# ---------------------------------------------------------------------------
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "create_node",
            "description": "Create a TouchDesigner node of a specific type at a path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Parent path (e.g., /project1 or *here)"},
                    "type": {"type": "string", "description": "Operator type (e.g., CircleTOP, NoiseTOP, WaveCHOP)"},
                    "name": {"type": "string", "description": "Optional custom name"},
                    "nodeX": {"type": "integer", "description": "Optional explicit X position (auto-laid-out if omitted)"},
                    "nodeY": {"type": "integer", "description": "Optional explicit Y position (auto-laid-out if omitted)"}
                },
                "required": ["path", "type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_node",
            "description": "Delete a TouchDesigner node.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the node to delete"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_parameters",
            "description": "Set parameters for a TouchDesigner node. Values are normally scalars, but a parameter can also be a dict: {\"expr\": \"me.time\"} to set an expression, or {\"pulse\": true} to pulse a button parameter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the node"},
                    "params": {"type": "object", "description": "Key-value map of parameters to set (e.g., {'post': 0.95})"}
                },
                "required": ["path", "params"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_parameters",
            "description": "Get parameter names and values for a node.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the node"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_errors",
            "description": "Get compilation or cook errors for a node.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the node"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_python",
            "description": "Execute arbitrary python code in TouchDesigner. Use this when high-level tools do not suffice.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code block to execute"}
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_nodes",
            "description": "List child nodes of a path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the parent COMP (e.g., /project1)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "project_info",
            "description": "Get global TouchDesigner project version, FPS, and file name.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "capture_viewport",
            "description": "Capture node viewport and return is_black/is_flat verdict.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the node to capture"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_chop",
            "description": "Read channel values from a CHOP.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the CHOP"},
                    "channel": {"type": "string", "description": "Optional single channel name"},
                    "samples": {"type": "integer", "description": "Max samples to read (default 10)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_top",
            "description": "Read resolution from a TOP.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the TOP"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_dat",
            "description": "Read text/rows from a DAT.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the DAT"},
                    "rows": {"type": "integer", "description": "Max rows to read (default 10)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "build_and_verify",
            "description": "Create -> verify (errors) -> preview (viewport verdict) loop for a node.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Parent path"},
                    "op_type": {"type": "string", "description": "Operator type (e.g., MovieFileInTOP)"},
                    "params": {"type": "object", "description": "Optional parameters to set"}
                },
                "required": ["path", "op_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "scan_network",
            "description": "Recursively scan the TouchDesigner network topology, collecting connections and parameters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Start path to scan (e.g. *here)"},
                    "depth": {"type": "integer", "description": "Max depth to scan (default 3)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "connect_nodes",
            "description": "Wire one TouchDesigner node's output into another node's input.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_path": {"type": "string", "description": "Path to the source node"},
                    "to_path": {"type": "string", "description": "Path to the destination node"},
                    "from_output": {"type": "integer", "description": "Source output index (default 0)"},
                    "to_input": {"type": "integer", "description": "Destination input index (default 0)"}
                },
                "required": ["from_path", "to_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "rename_node",
            "description": "Rename a TouchDesigner node.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the node"},
                    "new_name": {"type": "string", "description": "New name for the node"}
                },
                "required": ["path", "new_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "copy_node",
            "description": "Copy a node into another parent COMP.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the node to copy"},
                    "new_parent": {"type": "string", "description": "Path to the destination COMP"},
                    "new_name": {"type": "string", "description": "Optional new name"}
                },
                "required": ["path", "new_parent"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "auto_layout",
            "description": "Automatically arrange all child nodes in a COMP in a clean left-to-right or top-down grid.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the parent COMP"},
                    "direction": {"type": "string", "description": "'left-right' (default) or 'top-down'"},
                    "spacing_x": {"type": "integer", "description": "Horizontal spacing in pixels (default 200)"},
                    "spacing_y": {"type": "integer", "description": "Vertical spacing in pixels (default 200)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_node",
            "description": "Get detailed info about one node: type, path, connections, non-default params, errors, and position.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the node"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_node_color",
            "description": "Set the display color of a node to visually organise the network.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the node"},
                    "r": {"type": "number", "description": "Red channel 0..1"},
                    "g": {"type": "number", "description": "Green channel 0..1"},
                    "b": {"type": "number", "description": "Blue channel 0..1"}
                },
                "required": ["path", "r", "g", "b"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_tox",
            "description": "Save a COMP operator as a reusable .tox component file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the COMP to save"},
                    "file_path": {"type": "string", "description": "Optional output file path (defaults to temp dir)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_node_comment",
            "description": "Annotate a node with a comment and/or tags for documentation and visual grouping.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the node"},
                    "comment": {"type": "string", "description": "Comment text to attach"},
                    "tags": {"type": "string", "description": "Comma-separated tags or a list of tags"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "map_network",
            "description": "Emit a Graphviz DOT graph of the active network showing node connections, positions, and topology for spatial reasoning.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the parent COMP (*here for current)"},
                    "depth": {"type": "integer", "description": "Max depth to traverse (default 2)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "disconnect_nodes",
            "description": "Break the wire from one node's output into another node's input.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_path": {"type": "string", "description": "Path to the source node"},
                    "to_path": {"type": "string", "description": "Path to the destination node"},
                    "to_input": {"type": "integer", "description": "Destination input index (default 0)"}
                },
                "required": ["from_path", "to_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_connections",
            "description": "Return a normalized wiring map (inputs + outputs) for a node.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the node"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "exec_node_method",
            "description": "Call a method on a node (e.g. cook, reset, or pulse a parameter). Accepts optional positional args.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the node"},
                    "method": {"type": "string", "description": "Method name to call"},
                    "args": {"type": "array", "description": "Optional positional arguments"}
                },
                "required": ["path", "method"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "snapshot_network",
            "description": "Durable checkpoint: save a COMP to a temp .tox that survives the undo stack.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the COMP to checkpoint"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "restore_network",
            "description": "Restore a checkpoint .tox (from snapshot_network) into a parent COMP.",
            "parameters": {
                "type": "object",
                "properties": {
                    "snapshot": {"type": "string", "description": "Snapshot .tox path returned by snapshot_network"},
                    "target_parent": {"type": "string", "description": "Parent COMP to load into (default *here)"}
                },
                "required": ["snapshot"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_performance",
            "description": "Rank child cook times (CPU/GPU) to surface performance hotspots.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the parent COMP"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "validate_network",
            "description": "Scene-contract check: flags nodes left at the origin (0,0), overlapping nodes, and cook errors.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the parent COMP (*here for current)"},
                    "depth": {"type": "integer", "description": "Max depth to traverse (default 2)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_flags",
            "description": "Toggle node flags like bypass, viewer, or excludeFromCook.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the node"},
                    "flags": {"type": "object", "description": "Map of flag name to bool, e.g. {\"bypass\": true}"}
                },
                "required": ["path", "flags"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_nodes",
            "description": "Search the network descendants by name substring and/or operator type.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Parent path to search under (*here)"},
                    "query": {"type": "string", "description": "Substring to match in node names"},
                    "type": {"type": "string", "description": "Operator type substring to match (e.g. 'CHOP')"},
                    "depth": {"type": "integer", "description": "Max search depth (default 4)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_node_position",
            "description": "Move a single node to an explicit grid position (x, y).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the node"},
                    "x": {"type": "integer", "description": "Grid X position"},
                    "y": {"type": "integer", "description": "Grid Y position"}
                },
                "required": ["path", "x", "y"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "timeline",
            "description": "Control the global timeline: play / pause / toggle / frame / seek / rate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "One of play, pause, toggle, frame, seek, rate"},
                    "value": {"type": "number", "description": "Frame number, seconds to seek, or FPS for rate"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "export_recipe",
            "description": "Serialize a subnetwork into a portable JSON blueprint for repeatable builds.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the root COMP to export"},
                    "depth": {"type": "integer", "description": "Max depth to export (default 3)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "import_recipe",
            "description": "Rebuild a blueprint (from export_recipe) under a parent COMP to clone a network.",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipe": {"type": "object", "description": "The JSON blueprint returned by export_recipe"},
                    "target_parent": {"type": "string", "description": "Parent COMP to build into (default *here)"}
                },
                "required": ["recipe"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "caption_viewport",
            "description": "Capture a node's viewport and use a vision-capable model to describe the render and detect problems (black/flat/broken/shader errors). Returns the caption text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the node/viewer to capture"},
                    "query": {"type": "string", "description": "What to look for (default: describe the render and report visual problems)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "macro_start",
            "description": "Start recording a macro (sequence of tool calls) for later replay.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "macro_stop",
            "description": "Stop recording the current macro.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "macro_save",
            "description": "Save the recorded macro to a JSON file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Optional output file path (defaults to temp dir)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "macro_load",
            "description": "Load a macro from a JSON file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the macro JSON file"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "macro_replay",
            "description": "Replay a recorded macro by executing its steps sequentially.",
            "parameters": {
                "type": "object",
                "properties": {
                    "delay": {"type": "number", "description": "Optional delay between steps in seconds"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "macro_status",
            "description": "Get the current macro recorder status (recording/step count).",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]


# ---------------------------------------------------------------------------
# Tool Execution Helper (talks to local bridge over HTTP)
# ---------------------------------------------------------------------------
def _execute_tool(name, args, port=DEFAULT_BRIDGE_PORT, auth_token=None):
    token = auth_token or os.environ.get("TD_MCP_AUTH_TOKEN")
    url = f"http://{DEFAULT_HOST}:{port}/mcp"
    payload = json.dumps({"tool": name, "args": args or {}}).encode("utf-8")
    headers = {
        "Content-Type": "application/json"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Multimodal vision captioning (tdmcp copilot_vision / nested `observe`)
# ---------------------------------------------------------------------------
def _vision_request(base_url, api_key, model, text, image_b64, provider):
    """Send a text+image prompt to an OpenAI-compatible vision endpoint."""
    url = f"{base_url}/chat/completions"
    content = [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
    ]
    payload = {"model": model, "messages": [{"role": "user", "content": content}]}
    headers = {"Content-Type": "application/json"}
    if api_key:
        if provider == "gemini":
            headers["api-key"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read().decode("utf-8"))
        return resp.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:  # noqa: BLE001
        return f"[vision error] {e}"


def _caption_viewport(args, port=DEFAULT_BRIDGE_PORT, auth_token=None,
                      provider="gemini", api_key=None, base_url=None, model=None):
    """Capture a node viewer and ask a vision model to describe/critique it."""
    path = args.get("path")
    query = args.get("query") or (
        "Describe this TouchDesigner render. Is it black, flat/single-color, "
        "or visually broken? Report any obvious shader/geometry errors.")
    cap = _execute_tool("capture_viewport", {"path": path, "detail": "brief"},
                        port=port, auth_token=auth_token)
    if not cap.get("ok"):
        return cap
    img_path = cap.get("file")
    if not img_path or not os.path.exists(img_path):
        return {"ok": False, "error": "capture produced no image file"}
    try:
        with open(img_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("ascii")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"image read failed: {e}"}
    caption = _vision_request(base_url, api_key, model, query, img_b64, provider)
    verdict = cap.get("verdict", {})
    return {"ok": True, "caption": caption, "verdict": verdict}


# ---------------------------------------------------------------------------
# Macro Recorder — record/replay agent tool sequences (tdmcp macro_recorder)
# ---------------------------------------------------------------------------
class MacroRecorder:
    """Record a sequence of tool calls and replay them deterministically."""

    def __init__(self):
        self.recording = False
        self.steps = []  # list of {"tool": name, "args": {...}}
        self.start_time = None

    def start(self):
        self.recording = True
        self.steps = []
        self.start_time = time.time()
        return {"ok": True, "status": "recording"}

    def stop(self):
        self.recording = False
        duration = time.time() - self.start_time if self.start_time else 0
        return {"ok": True, "status": "stopped", "step_count": len(self.steps), "duration_sec": round(duration, 2)}

    def record(self, tool_name: str, args: dict):
        if not self.recording:
            return
        self.steps.append({"tool": tool_name, "args": args, "ts": time.time() - (self.start_time or time.time())})

    def get_macro(self):
        return {"steps": self.steps, "created": self.start_time}

    def save(self, path=None):
        import tempfile
        out = path or os.path.join(tempfile.gettempdir(), f"td_macro_{int(time.time())}.json")
        with open(out, "w") as f:
            json.dump(self.get_macro(), f, indent=2)
        return {"ok": True, "file": out, "step_count": len(self.steps)}

    def load(self, path):
        with open(path) as f:
            data = json.load(f)
        self.steps = data.get("steps", [])
        return {"ok": True, "step_count": len(self.steps)}

    def replay(self, port=DEFAULT_BRIDGE_PORT, auth_token=None, delay=0.0):
        """Replay recorded steps sequentially."""
        results = []
        for step in self.steps:
            if delay:
                time.sleep(delay)
            res = _execute_tool(step["tool"], step["args"], port=port, auth_token=auth_token)
            results.append({"tool": step["tool"], "args": step["args"], "result": res})
        return {"ok": True, "replayed": len(results), "results": results}


# Global recorder instance
_MACRO_RECORDER = MacroRecorder()


def _macro_start():
    return _MACRO_RECORDER.start()


def _macro_stop():
    return _MACRO_RECORDER.stop()


def _macro_save(path=None):
    return _MACRO_RECORDER.save(path)


def _macro_load(path):
    return _MACRO_RECORDER.load(path)


def _macro_replay(port=DEFAULT_BRIDGE_PORT, auth_token=None, delay=0.0):
    return _MACRO_RECORDER.replay(port=port, auth_token=auth_token, delay=delay)


def _macro_status():
    return {
        "recording": _MACRO_RECORDER.recording,
        "step_count": len(_MACRO_RECORDER.steps),
        "start_time": _MACRO_RECORDER.start_time,
    }

# ---------------------------------------------------------------------------
# Autonomous Agent Loop
# ---------------------------------------------------------------------------
def chat(prompt, provider="gemini", api_key=None, model=None, base_url=None,
         port=DEFAULT_BRIDGE_PORT, auth_token=None):
    """Start an autonomous agent chat session that builds TouchDesigner networks.

    Args:
        prompt: Natural language building request.
        provider: 'gemini' (default), 'ollama' (offline), or 'openai'.
        api_key: LLM API key (if needed; fallbacks to env variables).
        model: Specific model name to override the default.
        base_url: Custom API completion endpoint base URL.
        port: TouchDesigner bridge HTTP port (default 9980).
        auth_token: Authorization token for the bridge.
    """
    # 1. Resolve LLM configuration based on provider
    provider = provider.lower()
    if provider == "gemini":
        api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("Error: GEMINI_API_KEY environment variable or api_key parameter is required for Gemini.")
            return
        base_url = base_url or "https://generativelanguage.googleapis.com/v1beta/openai"
        model = model or "gemini-2.5-flash"
    elif provider == "ollama":
        base_url = base_url or "http://127.0.0.1:11434/v1"
        model = model or "qwen2.5:3b"
    elif provider == "openai":
        api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print("Error: OPENAI_API_KEY environment variable or api_key parameter is required for OpenAI.")
            return
        base_url = base_url or "https://api.openai.com/v1"
        model = model or "gpt-4o-mini"
    else:
        print(f"Error: Unknown provider {provider}. Supported providers: gemini, ollama, openai")
        return

    # 2. Get active network RAG context (scan_network)
    active_graph = ""
    try:
        scan_res = _execute_tool("scan_network", {"path": "*here", "depth": 2}, port=port, auth_token=auth_token)
        if scan_res.get("ok"):
            active_graph = json.dumps(scan_res.get("nodes"), indent=2)
    except Exception:
        pass

    # 3. System Instructions
    system_prompt = (
        "You are an expert TouchDesigner developer agent. Your goal is to help the user build "
        "and edit their node networks. You have access to real TouchDesigner live-control tools.\n"
        "Rules:\n"
        "1. Prioritize clean layout and wire connections.\n"
        "2. Set parameter values accurately. Common param names are lowercased.\n"
        "3. When referring to the active network, use spatial marker '*here' as the path.\n"
        "4. Always call get_errors on newly created or modified nodes to verify they cook without errors.\n"
        "5. Be concise in your explanations. State clearly what you created.\n"
        "6. After creating multiple nodes, always call auto_layout to arrange them cleanly.\n"
        "7. Use connect_nodes to wire nodes after creating them — never leave wires disconnected.\n"
        "8. Use set_node_color to color-code nodes by role: blue for inputs, orange for effects, green for output.\n"
        "9. Use map_network to sanity-check topology/wiring before declaring a task complete.\n"
        "10. After finishing, run validate_network to catch unplaced/overlapping nodes or cook errors.\n"
        "11. For exact wiring, call get_connections; to change a parameter via expression use "
        "set_parameters with a value like {\"expr\": \"me.time\"}, or {\"pulse\": true} to pulse.\n\n"
    )
    if active_graph:
        system_prompt += f"CURRENT ACTIVE NETWORK TOPOLOGY (*here):\n{active_graph}\n\nUse this context to understand existing nodes, their parameter states, and wire connections."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt}
    ]

    print(f"\n=== Starting Agent Session [Provider: {provider}, Model: {model}] ===")
    print(f"Prompt: {prompt}")

    # Maximum loop iterations to prevent runaway agents
    max_steps = 20
    for step in range(max_steps):
        print(f"\n--- Agent Loop Step {step + 1} ---")

        # Build payload
        payload = {
            "model": model,
            "messages": messages,
            "tools": TOOLS_SCHEMA
        }

        headers = {
            "Content-Type": "application/json"
        }
        if api_key:
            if provider == "gemini":
                headers["api-key"] = api_key
            else:
                headers["Authorization"] = f"Bearer {api_key}"

        url = f"{base_url}/chat/completions"
        if provider == "gemini" and "api-key" not in headers:
            url += f"?key={api_key}"

        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        resp = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    resp = json.loads(r.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                err_body = e.read().decode('utf-8', 'ignore')
                if e.code in (429, 503) and attempt < 2:
                    import time
                    print(f"LLM rate-limited (HTTP {e.code}), retrying in {2 ** attempt}s...")
                    time.sleep(2 ** attempt)
                    continue
                print(f"LLM API Error (HTTP {e.code}): {err_body}")
                return
            except Exception as e:
                print(f"Connection Error: {e}")
                return
        if resp is None:
            return

        choice = resp.get("choices", [{}])[0]
        message = choice.get("message", {})

        # Append assistant response to message history (including tool_calls list if any)
        messages.append(message)

        if message.get("content"):
            print(f"Agent: {message['content']}")

        tool_calls = message.get("tool_calls")

        # Dedup: skip tool calls that are identical to the immediately preceding call
        if tool_calls:
            seen_calls = set()
            unique_calls = []
            for tc in tool_calls:
                fn = tc.get("function", {})
                key = (fn.get("name"), fn.get("arguments", "{}"))
                if key not in seen_calls:
                    seen_calls.add(key)
                    unique_calls.append(tc)
            tool_calls = unique_calls

        if not tool_calls:
            print("\n=== Agent Task Finished Successfully ===")
            break

        # Process each tool call sequentially
        for tool_call in tool_calls:
            tc_id = tool_call.get("id")
            fn = tool_call.get("function", {})
            name = fn.get("name")
            arguments_str = fn.get("arguments", "{}")

            try:
                args = json.loads(arguments_str)
            except Exception:
                args = {}

            print(f"Tool Call: {name}({args})")

            # Execute tool call (caption_viewport is a client-side vision tool; macro_* are client-side recorder tools)
            if name == "caption_viewport":
                res = _caption_viewport(args, port=port, auth_token=auth_token,
                                        provider=provider, api_key=api_key,
                                        base_url=base_url, model=model)
            elif name == "macro_start":
                res = _macro_start()
            elif name == "macro_stop":
                res = _macro_stop()
            elif name == "macro_save":
                res = _macro_save(args.get("path"))
            elif name == "macro_load":
                res = _macro_load(args.get("path"))
            elif name == "macro_replay":
                res = _macro_replay(port=port, auth_token=auth_token, delay=args.get("delay", 0.0))
            elif name == "macro_status":
                res = _macro_status()
            else:
                res = _execute_tool(name, args, port=port, auth_token=auth_token)
            print(f"Tool Result: {json.dumps(res)}")

            # Record macro step if recording
            if _MACRO_RECORDER.recording:
                _MACRO_RECORDER.record(name, args)

            # Append tool response
            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "name": name,
                "content": json.dumps(res)
            })
