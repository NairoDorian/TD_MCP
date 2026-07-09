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
                    "name": {"type": "string", "description": "Optional custom name"}
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
            "description": "Set parameters for a TouchDesigner node.",
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
        "5. Be concise in your explanations. State clearly what you created.\n\n"
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
    max_steps = 12
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
        try:
            with urllib.request.urlopen(req) as r:
                resp = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode('utf-8', 'ignore')
            print(f"LLM API Error (HTTP {e.code}): {err_body}")
            return
        except Exception as e:
            print(f"Connection Error: {e}")
            return

        choice = resp.get("choices", [{}])[0]
        message = choice.get("message", {})

        # Append assistant response to message history (including tool_calls list if any)
        messages.append(message)

        if message.get("content"):
            print(f"Agent: {message['content']}")

        tool_calls = message.get("tool_calls")
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

            # Execute tool call
            res = _execute_tool(name, args, port=port, auth_token=auth_token)
            print(f"Tool Result: {json.dumps(res)}")

            # Append tool response
            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "name": name,
                "content": json.dumps(res)
            })
