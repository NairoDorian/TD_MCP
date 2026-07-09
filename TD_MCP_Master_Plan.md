# TouchDesigner MCP & RAG "Ultimate" Master Plan

This document outlines the current state and future vision of the TouchDesigner Model Context Protocol (MCP) and Retrieval-Augmented Generation (RAG) ecosystem. It serves as the master specification for creating a seamless, production-ready AI co-pilot running inside and alongside TouchDesigner.

---

## 1. Executive Summary

The TouchDesigner MCP environment bridges the gap between large language models and TouchDesigner's visual programming environment. By combining a **local-first documentation RAG server**, a **live bidirectional control bridge**, and a **native TouchDesigner-resident agent**, it enables developers and artists to query reference materials and generate complex, working interactive networks autonomously using natural language.

---

## 2. Current State of the Project

We have successfully implemented and verified the primary components of this ecosystem:

### 2.1 Split MCP Architecture
- **`td-mcp-offline` (Docs/RAG Server)**:
  - Operates as a zero-cloud-dependency stdio MCP server.
  - Implements a parallel multi-RAG retriever: queries global BM25, per-source BM25 (operators, Python API, GLSL, tutorials), and optional dense vector embeddings (`all-MiniLM-L6-v2`) in parallel, fusing results via Reciprocal Rank Fusion (RRF).
  - Includes a pre-built database of **1091 curated chunks** (covering operator definitions, compatibility tables, and tutorial recipes), achieving a benchmark **recall@5 of 0.966**.
  - Provides mathematical templates for LED pixel layouts, universe configurations, DMX patch lists, and show-control protocols.
- **`td-mcp-live` (Bridge Client Server)**:
  - Connects to a running TouchDesigner instance over a local HTTP connection (port `9980`).
  - Registers 16 live control tools (e.g. `create_node`, `set_parameters`, `get_errors`, `execute_python`, and `batch`) with the client.
  - Implements the self-correcting `build_and_verify` loop: creates nodes, checks parameter correctness, catches cook errors, and renders viewport thumbnails to compute an `is_flat`/`is_black` quality verdict.
  - Features **Spatial Context Pointers**: Resolves `*here` (current pane network path) and `*this` (currently selected operator) on the fly via TouchDesigner's Python `ui` API.

### 2.2 Live TouchDesigner Bridge (`bridge/td_mcp_bridge.py`)
- Pasted inside TouchDesigner as a zero-dependency script (using only built-in libraries like `http.server` and `json`).
- Wraps every network change or tool batch in TouchDesigner's `ui.undo` system, making any agent-driven action revertible with a single `Ctrl+Z`.
- Runs securely on localhost with auto-generated auth tokens.

### 2.3 Autonomous in-App Agent (`bridge/td_mcp_agent.py`)
- A zero-dependency OpenAI-compatible agent script running inside TouchDesigner Text DATs.
- Supports 13 function-calling tool schemas to query the bridge directly.
- Fuses with **Ollama** (100% offline, local, key-free models like `qwen2.5:3b`), **Gemini API** (using the OpenAI compatibility endpoint), or **OpenAI API** to allow in-app chat commands to build networks autonomously.

---

## 3. Future Roadmap

To transition this project into the ultimate standard for AI-driven visual computing, the following features are planned for future releases:

### 3.1 Natively Embedded Chat UI Panel
- **Goal**: Move away from running agents via the Python Textport by creating a dedicated TouchDesigner UI.
- **Implementation**:
  - Build a custom **Widget COMP** or **HTML/CSS Web Render TOP** panel that displays a modern chat window directly inside TouchDesigner.
  - Let users chat with the agent, view execution logs, select models (Ollama vs. Gemini vs. OpenAI), and trigger creations from a visual side-panel.

### 3.2 Live Project RAG (Short-Term Workspace Context)
- **Goal**: Make the AI aware of the user's *actual* active project structure and custom parameters.
- **Implementation**:
  - Build a background scanner that serializes the current network graph (node names, types, connections, parameter values) into a lightweight JSON structure.
  - Inject this active project layout into the agent's system prompt or build a local indexing path, allowing prompts like: *"Find the Speed CHOP in my network, connect its output to a new Limit CHOP, and set the limit type to loop."*

### 3.3 Multimodal Vision Debugging & Canvas Alignment
- **Goal**: Enable the agent to "see" render issues, visual bugs, or panel layouts.
- **Implementation**:
  - Utilize multimodal LLMs (e.g. Gemini 1.5 Pro, LLaVA, or Claude 3.5 Sonnet) to analyze the viewport screenshots captured by the `capture_viewport` tool.
  - The agent will visually verify if a shader is compiling correctly, if a rendering is outputting solid black/flat color, or if UI containers are properly aligned.

### 3.4 WebRTC / WebSocket Bi-directional Streaming
- **Goal**: Upgrade the communication layer from HTTP polling to a high-speed, real-time connection.
- **Implementation**:
  - Replace the current HTTP-based bridge with a persistent WebSockets connection (`Websocket DAT`).
  - This allows the agent to stream parameter tweaks in real-time (creating smooth visual transitions during building) and receive instant, push-based callbacks when nodes are clicked, modified, or error out.

### 3.5 Single-Click Offline Installer
- **Goal**: Make setup trivial for non-technical artists.
- **Implementation**:
  - Create a PowerShell/Bash bootstrapping script that automatically downloads and installs Ollama, pulls the recommended model (e.g. `qwen2.5:3b`), and configures the AI editor (Claude Desktop / Cursor) in a single click.

---

## 4. Current File Layout

With the documentation files moved to the root directory for maximum accessibility, the repository structure is:
```
td-mcp/
├── pyproject.toml              # Build and package config
├── README.md                   # Quick start, tool listings, and RAG configuration
├── HOW_TO_USE.md               # User guide for running the bridge & configuration
├── CHANGELOG.md                # Project version history
├── TD_MCP_Master_Plan.md       # Consolidated design specification & future roadmap
├── TouchDesigner_Links.md      # TouchDesigner official docs and Python references
├── TouchDesigner_MCP_Servers.md# Catalog of existing TouchDesigner MCP repos
├── setup_env.ps1               # Automated local virtualenv configuration
├── td_mcp/                     # Core python source package
│   ├── server_offline.py       # Offline docs RAG server
│   ├── server_live.py          # Live bridge control server
│   ├── generators.py           # Artist network templates
│   ├── tdn/                    # Diffable YAML (TDN) importer/exporter
│   ├── showcontrol/            # Show control network builders
│   ├── led_mapping/            # LED pixel layout matrices and DMX mapping
│   ├── tools/risk.py           # TrueFiasco risk-tier definitions
│   └── kb/                     # Curated Knowledge Base and indexers
└── bridge/                     # Zero-dependency TouchDesigner-side files
    ├── td_mcp_bridge.py        # Pasted bridge server Text DAT
    └── td_mcp_agent.py         # Pasted autonomous chat agent Text DAT
```
