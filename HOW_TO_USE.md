# How to Use TouchDesigner MCP

This guide covers setting up the TouchDesigner bridge, launching the MCP servers (offline and live), configuring your AI client (Claude Desktop, Cursor, VS Code, Gemini, etc.), and using the toolset.

> Reference documentation lives in [`docs/`](docs/README.md): [install](docs/INSTALL.md) · [the TouchDesigner component](docs/TOUCHDESIGNER_COMPONENT.md) · [tools](docs/TOOLS.md) · [diagnostics](docs/DIAGNOSTICS.md) · [agent guide](docs/AGENT_GUIDE.md) · [troubleshooting](docs/TROUBLESHOOTING.md).

---

## 1. Prerequisites

Make sure you have:
1. **TouchDesigner** installed and running.
2. **Python 3.11** or [uv](https://github.com/astral-sh/uv) installed.
   - Run `./setup_env.ps1` from the `td-mcp` directory to configure the exact Python environment (matching TouchDesigner's Python version).

---

## 2. Setting up the TouchDesigner Bridge

The live server talks to a **component inside your project**. Nothing outside
TouchDesigner can create that component for you, so this step is yours.

### The one-liner

Open your project, open the Textport (`Alt+T`), and run:

```python
TD_MCP_REPO = r'C:/path/to/td-mcp'
exec(open(TD_MCP_REPO + '/bridge/bootstrap.py').read())
```

It builds `/project1/td_mcp` and starts the bridge:

```
[td_mcp] component ready at /project1/td_mcp (mode=link)
[td_mcp] bridge DAT is synced to C:/path/to/td-mcp/bridge/td_mcp_bridge.py
[td_mcp] listening on http://127.0.0.1:9980
[td_mcp] auth token: xK9_pR2mQvN4tL8sW1hY6bZ3
```

**Copy that token** — your MCP client needs it. Then **save your project**: the
component lives in the `.toe`, and if you do not save you will do this again
next session.

Re-running the script is safe; it updates the component in place and keeps any
parameter values you have set.

### What it built

```
/project1/td_mcp                Base COMP — parameter page "TD MCP"
├── bridge      Text DAT        the bridge, synced to the .py on disk
├── startup     Execute DAT     starts on Start and on Create, stops on Exit
└── control     Parameter Exec  Active / Port / Token / Allow Exec → restart
```

Use the component's parameters rather than the Textport: toggle **Active** to
start and stop, pulse **Restart Bridge** after changing the source, pulse
**Print Auth Token** to see the token again.

Because the Text DAT is *synced to the file*, editing
`bridge/td_mcp_bridge.py` and pulsing **Restart Bridge** is the whole update
loop — no re-pasting, and git sees every change.

### Making a drag-and-drop `.tox`

```python
TD_MCP_SAVE_TOX = 1
TD_MCP_REPO = r'C:/path/to/td-mcp'
exec(open(TD_MCP_REPO + '/bridge/bootstrap.py').read())
```

Writes `td_mcp.tox` next to your project, with the bridge **embedded** so it
works on machines that have never seen this repo. Drag it into any project and
it starts itself.

A `.tox` is opaque compressed binary that only TouchDesigner can author, which
is why this repository ships the builder rather than the artifact. The full
reasoning — and a survey of how the other 30 TouchDesigner MCP projects package
themselves — is in
**[docs/TOUCHDESIGNER_COMPONENT.md](docs/TOUCHDESIGNER_COMPONENT.md)**.

### Doing it by hand

1. Create a **Base COMP** named `td_mcp`; inside it a **Text DAT** named `bridge`.
2. Either set the DAT's **File** parameter to `bridge/td_mcp_bridge.py` and turn
   on **Sync to File**, or paste the file's contents in.
3. Textport: `op('/project1/td_mcp/bridge').module.start()`
4. Stop with `…module.stop()`.

You get a working bridge with no parameters and no auto-start — fine for a
one-off session.

---

## 3. Running the MCP Servers

We run **two servers** to keep your context footprint lightweight:
- **`td-mcp-offline`**: Standard documentation, version manifests, tutorials, GLSL patterns, and offline network builders.
- **`td-mcp-live`**: Bridge connection to query, create, edit, wire, and render in a live TouchDesigner session.

### 3a. Command Line Execution (CLI Mode)

Before setting up the MCP protocol, you can test both servers directly in the terminal:

- **Offline RAG Search**:
  ```powershell
  uv run td-mcp-offline "blur top parameters"
  ```
- **Live Bridge control** (requires bridge running in TD):
  ```powershell
  # Set authorization token
  $env:TD_MCP_AUTH_TOKEN="YOUR_AUTO_GENERATED_TOKEN"

   # Check project info
   uv run td-mcp-live status

  # Create a node
  uv run td-mcp-live create /project1 CircleTOP --name my_circle
  ```

### 3b. Running in MCP Mode (Stdio)

To configure the servers for your AI agent, run them with the `--mcp` flag:

- **Offline Server**:
  ```powershell
  uv run td-mcp-offline --mcp
  ```
- **Live Server**:
  ```powershell
  $env:TD_MCP_AUTH_TOKEN="YOUR_AUTO_GENERATED_TOKEN"
  uv run td-mcp-live --mcp
  ```

---

## 4. AI Client Configuration

Add the servers to your AI editor or client configuration files.

### Claude Desktop (`claude_desktop_config.json`)
Usually located at `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "td-mcp-offline": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "<REPO_DIR>",
        "td-mcp-offline",
        "--mcp"
      ]
    },
    "td-mcp-live": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "<REPO_DIR>",
        "td-mcp-live",
        "--mcp"
      ],
      "env": {
        "TD_MCP_AUTH_TOKEN": "YOUR_AUTO_GENERATED_TOKEN"
      }
    }
  }
}
```

### Cursor (`mcp.json`)
Usually located at `%USERPROFILE%\.cursor\mcp.json`. Add the following configurations in the UI or directly to the file:

```json
{
  "mcpServers": {
    "td-mcp-offline": {
      "command": "uv run --project <REPO_DIR> td-mcp-offline --mcp"
    },
    "td-mcp-live": {
      "command": "uv run --project <REPO_DIR> td-mcp-live --mcp",
      "env": {
        "TD_MCP_AUTH_TOKEN": "YOUR_AUTO_GENERATED_TOKEN"
      }
    }
  }
}
```

---

## 5. Advanced Features

### Spatial Context Markers
You can use spatial markers in your prompts to reference your live workspace without spelling out full paths:
- **`*here`**: Resolves to the path of the TouchDesigner network pane you currently have open (e.g., `/project1`).
- **`*this`**: Resolves to the path of the currently selected operator in your active network pane.

*Example prompt to your agent:*
> "Add a Blur TOP under `*here` and connect it to `*this`."

### Verification Loop
The `build_and_verify` tool runs a creation loop:
1. Creates the node.
2. Verifies parameter names and sets them.
3. Checks for any cook errors.
4. Renders a thumbnail viewport capture and computes an `is_black` / `is_flat` quality verdict.
This tells the AI agent if the rendering output is empty or broken so it can automatically self-heal.

### Chat UI Panel (in-app)
Once the bridge is running, open `http://localhost:9980/` in a Web Render TOP or any browser to load a zero-dependency, glassmorphic chat panel. It has a provider selector (Ollama / Gemini / OpenAI), persistent credential storage, a live network-node sidebar, a health indicator, and a multi-step autonomous agent loop that talks directly to the bridge. `capture_viewport` verdicts and `recovery_hints` are surfaced so you can watch the agent self-correct.

### Streamable HTTP mode
`td-mcp-live` can also run as an HTTP MCP server (`POST /` JSON-RPC + `GET /` SSE, multi-session, DNS-rebind guard) on `127.0.0.1:8765` for HTTP-capable MCP clients:
```powershell
uv run td-mcp-live --http
```

---

## 6. Running an Autonomous Agent Node Natively inside TouchDesigner

If you want an **AI agent to run entirely inside TouchDesigner** and add nodes autonomously by itself without needing external IDE chat panels, you can use the zero-dependency script [bridge/td_mcp_agent.py](bridge/td_mcp_agent.py).

### How to set it up:
1. Paste the contents of `bridge/td_mcp_agent.py` into a new **Text DAT** inside TouchDesigner (e.g. name it `mcp_agent`).
2. Make sure the bridge is running in your TouchDesigner session (see Section 2).

### Running in TouchDesigner (with Ollama - 100% Offline & Key-Free):
Ensure Ollama is running locally (`http://localhost:11434`), then run this in the TouchDesigner Textport or a script DAT:
```python
# Import the agent DAT module
agent = op('mcp_agent').module

# Run the autonomous builder chat session
agent.chat(
    prompt="Create a feedback loop containing a Noise TOP and a Threshold TOP under *here",
    provider="ollama",
    model="qwen2.5:3b"  # or your installed local model
)
```

### Running with Gemini:
Ensure you have the `GEMINI_API_KEY` environment variable set or pass it directly:
```python
agent = op('mcp_agent').module

# Run with Gemini (requires internet connection)
agent.chat(
    prompt="Add a Torus SOP andPhong MAT, assign the material to the geometry COMP under *here",
    provider="gemini",
    api_key="YOUR_GEMINI_API_KEY"  # optional if GEMINI_API_KEY env is set
)
```

### Running with OpenAI:
```python
agent = op('mcp_agent').module

agent.chat(
    prompt="Add an Audio Device In CHOP and map it reactive to visual params",
    provider="openai",
    api_key="YOUR_OPENAI_API_KEY"
)
```

---

## 7. Developer Testing

To verify the installation and that all configurations compile and pass the test suite:
```powershell
# Run the test suite
uv run pytest
```
All tests should pass.
