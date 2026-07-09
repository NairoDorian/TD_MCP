---
name: td-building
description: Build and troubleshoot TouchDesigner networks (TOPs/CHOPs/SOPs/POPs/DATs/COMPs) with correct operator, parameter, and Python API names. Use whenever the user is creating, wiring, scripting, or debugging a TouchDesigner project, GLSL shader, audio-reactive, or feedback system.
---

# TouchDesigner building

You are helping someone build a TouchDesigner (TD) network. TD is node-graph creative-coding software by Derivative. Operator families: TOP (images), CHOP (channels/animation), SOP (geometry), POP (particles, 2023+), DAT (data/text), COMP (containers/3D).

## Hard rules (stop hallucinating)

- Parameter **identifiers** are exact and stable: `moviefilein1.par.file` (NOT `.filename`), `null1.par.tx`, etc. Always look up the real name before writing `par.X = ...`.
- Python API classes: `OP_Class` (base), `TOP_Class`, `CHOP_Class`, `SOP_Class`, `DAT_Class`, plus per-operator classes like `moviefileinTOP_Class`. Don't invent method names — common ones: `.par`, `.create(name, type)`, `.copy()`, `.destroy()`, `.change()`, `.inputs[i]`, `.outputs[i]`.
- Callback signatures matter: DAT Execute, CHOP Execute, Panel Execute, and Script DAT `run()` each have specific args. Look them up.
- POPs are newer (build 2023.10000+); don't assume SOP naming applies.

## Workflow

1. **Look it up before coding.** Query the doc/RAG tools (`td_docs_search`, `td_docs_operator`, `td_docs_python`, `td_docs_glsl`, `td_docs_template`) with the specific operator/class name + param you need.
2. **Build small, verify, repeat.** Create a node, set parameters, wire it, then check `get_errors` / render a preview before adding the next stage.
3. **Wire in Python** with `op('out1').inputs[0] = op('noise1')` or `.inputConnectors[0].connect(...)`. `.copy()` first if you want to preserve an existing graph.
4. **Export CHOPs to params** for data-driven motion: `op('chan1').exportOPar('null1', 'tx')`.

## Recipes

- **Audio-reactive:** Audio Device In CHOP → Math/Filter CHOP → `exportOPar` onto a TOP/COMP parameter.
- **Feedback:** Noise/Level TOP → Feedback TOP input0; Feedback input1 (the loop) gets a Level TOP offset/decay. Add Threshold for glow.
- **GLSL:** fragment shader reads `texture(sTD2DInputs[0], vUV.st)`; write `fragColor`; wire a TOP into input 0. Use `uTD2DInfos[0]` for resolution.
- **3D render:** Geometry COMP + Camera COMP + Light COMP → Render TOP.

## Connectivity (do this first)
Before any tool call, confirm the bridge is reachable so you fail fast instead of
guessing:
- Call `status` / `GET /api/status` on `td-mcp-live` (or the bridge `status()`). If TD
  is down, start it and wait for the bridge token before issuing mutations.
- Resolve spatial pointers first: `*here` = the active network pane, `*this` = the
  selected operator. Prefer these over hard-coded `/project1/...` paths so commands
  land where the user is actually working.
- Treat `capture_viewport` / TOP captures as the source of truth for visual work
  (Embody's `Quality: FAIL` idea): an `is_black`/`is_flat`/`fully-transparent`
  verdict means the render is empty — never declare a visual task "done" on a
  failing or blank capture. Iterate until the verdict is clean.

## Self-correction
Most failures ride back structured `recovery_hints` (`{cause, action, next_tools}`):
follow them instead of retrying the same call verbatim. Common patterns:
- `no such op` → `list_nodes` / `td_docs_family` to confirm spelling.
- `invalid parameter` → `td_docs_parameter` / `get_parameters` before `set_parameters`.
- `cook error` → `get_errors` then `td_docs_search` for the operator.
- `timeout` → split the build into smaller batches / raise the client timeout.

## Safety

- Prefer bridges that wrap mutations in `ui.undo` so one Ctrl+Z reverts a whole agent batch.
- Protect critical COMPs; disable `exec`/`execute_python` on untrusted prompts.
- Know your bridge's port and don't collide with another MCP server (td-mcp = 9980).

## Reference

- Docs: https://docs.derivative.ca
- Python: https://docs.derivative.ca/TouchDesigner_Python_Classes
- Curriculum: https://learn.derivative.ca
- Server catalog + merge plan: see the `DOCUMENTATION/` notes.
