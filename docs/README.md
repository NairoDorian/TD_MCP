# td-mcp documentation

Start here.

---

## Getting it running

| | |
|---|---|
| **[INSTALL.md](INSTALL.md)** | Both halves — the Python servers and the TouchDesigner component. The install lifecycle, environment variables, upgrading. |
| **[TOUCHDESIGNER_COMPONENT.md](TOUCHDESIGNER_COMPONENT.md)** | **What file goes into TouchDesigner, and why.** The `/td_mcp` component, `link` vs `embed`, building a drag-and-drop `.tox`, and how every other project in the ecosystem solves it. |
| **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** | Symptom-first. Every entry has been seen. |

## Using it

| | |
|---|---|
| **[TOOLS.md](TOOLS.md)** | All 120 tools, with risk classes and the reduction contract. |
| **[PROJECT_FILES.md](PROJECT_FILES.md)** | Reading saved `.toe`/`.tox` files with TouchDesigner closed: how, and the file format as measured over 768 shipped components. |
| **[AGENT_GUIDE.md](AGENT_GUIDE.md)** | How to sequence them. Written for the model, and for whoever writes its prompt. |
| **[DIAGNOSTICS.md](DIAGNOSTICS.md)** | **TouchDesigner reports errors in four places and three are invisible to the obvious API call.** The most important page here. |
| **[RAG.md](RAG.md)** | How retrieval works, why citations and family disambiguation matter. |

## Building on it

| | |
|---|---|
| **[TDN_FORMAT.md](TDN_FORMAT.md)** | The diffable network format, and four rules each bought with a real bug. |
| **[TESTING.md](TESTING.md)** | The TouchDesigner API double, and the guards that catch drift. |
| **[SECURITY.md](SECURITY.md)** | The exec gate, auth, protected paths, bounded ops, the threading hazard. |
| **[ECOSYSTEM.md](ECOSYSTEM.md)** | 57 projects mirrored, the ones worth taking from surveyed. |
| **[CPP_CUSTOM_OPERATORS.md](CPP_CUSTOM_OPERATORS.md)** | The compiled custom-operator SDK (CPlusPlus CHOP/TOP/DAT/SOP/POP): C-ABI exports, cook callbacks, buffers, the hard-real-time threading pattern, and the PluginBuilder build/hot-reload workflow. |

## Project-level

| | |
|---|---|
| [../README.md](../README.md) | Overview and quick start |
| [../ARCHITECTURE.md](../ARCHITECTURE.md) | How the pieces fit |
| [../CHANGELOG.md](../CHANGELOG.md) | What changed and why |
| [../IMPROVEMENT_PLAN.md](../IMPROVEMENT_PLAN.md) | The roadmap, with what is done and what is not |
| [../UPSTREAM_SYNC_2026-08-26.md](../UPSTREAM_SYNC_2026-08-26.md) | The last ecosystem sync, and the eight bugs it found |
| [../PICKED_FROM.md](../PICKED_FROM.md) | Per-feature attribution |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | |

---

## If you have five minutes

Read **[DIAGNOSTICS.md](DIAGNOSTICS.md)**. It is the one page here that will
change how you automate TouchDesigner regardless of which MCP server you use:
`op.errors()` is a *method returning a string*, it is one of four places errors
appear, and reading only it will report a visibly broken network as clean.

## If you are installing

1. [INSTALL.md § The Python side](INSTALL.md#1-the-python-side)
2. [INSTALL.md § The TouchDesigner side](INSTALL.md#2-the-touchdesigner-side)
3. [TOUCHDESIGNER_COMPONENT.md](TOUCHDESIGNER_COMPONENT.md) if you want to
   understand what you just dropped into your project

## If you are writing an agent prompt

[AGENT_GUIDE.md § The five rules](AGENT_GUIDE.md#the-five-rules), then
[TOOLS.md](TOOLS.md).

## If you are contributing

[TESTING.md](TESTING.md) first — particularly the rule about the API double:
**model the TouchDesigner API as documented, including the awkward parts.** A
permissive fake would have accepted every bug v1.12.0 fixed.
