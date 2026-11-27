# The TouchDesigner MCP ecosystem

A survey of the public TouchDesigner + MCP projects, mirrored side by side in
`github-mcp/` (57 as of the 2026-11-27 sync, which added 24; see
[../UPSTREAM_SYNC_2026-11-27.md](../UPSTREAM_SYNC_2026-11-27.md)). This page is about what they *do* and
what is worth taking from each; the packaging question specifically is covered
in [TOUCHDESIGNER_COMPONENT.md](TOUCHDESIGNER_COMPONENT.md#how-every-other-project-does-it).

`td-mcp` reimplements techniques under MIT. Nothing is copied — see
[../PICKED_FROM.md](../PICKED_FROM.md) for per-feature attribution, and note
that `TrueFiasco/TD_Builder_alpha` is AGPL-3.0 and is studied, never borrowed
from.

---

## The shape of the field

Almost every project is one of four things:

**Thin bridges** (the majority). A Web Server DAT inside TouchDesigner, an MCP
server outside, JSON over HTTP between them. `rickengineroom`, `wangpolanshan`,
`aliphi`, `mrinalghosh`, `mintiasaikoh`, `vedant1317`, `quentinR2`,
`benoitliard`, `axysar`, `froussio`. They differ mostly in tool count and
install polish.

**Docs-only RAG servers.** No TouchDesigner required; they answer questions
about it. `bottobot` (static snapshot, 661 ops / 214 Python classes),
`cacheflowe` (live Crawl4AI scrape), `johnnyvincentvitale` (the whole wiki,
~2,270 articles, with citations).

**Full environments.** Much larger than a bridge: fleet management, project
lifecycle, an in-app UI. `dylanroscover/Embody` (1.6 MB `.tox`, 53 tools, 2,005
tests), `Pantani/tdmcp` (355 tools), `404dotzero/twozero`, `dreamrec/TDPilot`.

**Production/show tooling.** `familienak-tech` (Art-Net/sACN/OSC/MIDI/LTC, LED
pixel-mapping, media-server bridges), `sarveshsea/touchdesigner-ai-brain`
(vetted source registry, show-readiness checklists, Langfuse).

`td-mcp` is the only one that is simultaneously a docs/RAG server **and** a live
bridge in one MIT Python package.

---

## The ideas worth stealing

Ranked by how much they changed `td-mcp`.

### 1. Embody — TouchDesigner's error surfaces (v6.0.264 / v6.0.265)

> *"An operator could sit red in the network while every Envoy error tool called
> it clean."*

The finding that drove the whole of `td-mcp` v1.12.0. TD reports on three
accessors plus GLSL text in a docked Info DAT, and reading two of them is
indistinguishable from a healthy project. See [DIAGNOSTICS.md](DIAGNOSTICS.md).

Adjacent, from the same releases:

- **Reads must reduce, never dump.** `min/max/mean/std` per channel; DAT stats
  measured 17× smaller than a full dump. → [TOOLS.md](TOOLS.md#reading-data--reductions-not-dumps)
- **`POP.points(attr, start, count)` ignores its slicing arguments.** A
  4,096-point "slice" off a 160k POP measured *slower* than reading all of it.
- **Reading an Info DAT cooks it** — a project-wide pass cost 3.36 s against a
  0.25 s budget and tripped a one-way disable latch.
- **`findChildren(depth=N)` matches an *exact* depth**, not "up to". An entire
  cross-build repair path was a silent no-op with no test coverage.
- **stdio must be pinned to UTF-8 from `main()`**, never at import — Python
  binds the standard streams to the locale codepage.
- **TDN exporter fidelity** — four findings in
  `Embody/docs/reports/tdn-exporter-fidelity-moonshine.md`. → [TDN_FORMAT.md](TDN_FORMAT.md)

### 2. dcc-mcp-touchdesigner — Install SOP v1, and typed safety

The best install story in the ecosystem, because it is honest about its own
limits: a CLI cannot create a Text DAT inside a running project, so it returns a
machine-executable `next_steps` entry instead of pretending. Verbs, `--json`,
`--yes`, `--dry-run`, a SHA-256 receipt, and **per-stage exit codes**. Adopted
wholesale — see [INSTALL.md](INSTALL.md#install-sop-v1).

Also: typed, main-thread-safe ops with JSON contracts and risk tiers; bounded
graph/param/DAT operations; optimistic concurrency for DAT writes; **no
arbitrary Python by default**; a deterministic TD API double. That last one is
what `tests/td_double.py` is.

### 3. johnnyvincentvitale/touchdesigner-wiki-rag — citable retrieval

`Page § Section` + URL on every hit, **family disambiguation** ("Noise" is four
different operators), freshness tracking, SQLite FTS5 + Chroma, adaptive HyDE,
L0/L1/L2 tiering. `td-mcp` adopted the citation and disambiguation model, and
can fuse their SQLite index directly via `TD_MCP_WIKI_DB`.

### 4. johnsabath/touchdesigner-mcp — verbs, and motion

Thirteen one-word tools (`run`, `inspect`, `set`, `create`, `wire`, `read`,
`write`, `edit`, `list`, `docs`, `observe`, `render`, `map`) — a materially
cheaper surface for the model to choose from. And `observe` returns a PNG **or
an animated GIF**, which is where `td-mcp`'s `is_static` verdict came from.

### 5. TDPilot — trust tiers and the technique loop

`official > bundled > personal > community > transcript > experimental` on every
retrieval hit, so an agent knows what it may assert as fact. Plus
`plan → execute → learn` with snapshot/rollback, conversation compaction, and
pre-turn retrieval injection. Its v2.5.4 security audit (2,141 tests) is the
source of `td-mcp`'s schema↔handler parity tests and traceback redaction.

### 6. The rest, in one line each

| Project | Idea |
|---|---|
| `8beeeaaat` | MCP 2026-07-28, **stateless** Streamable HTTP, semver compat gate between server and `.tox` |
| `quentinR2` | Generated single-file bridge + **CI staleness guard**; `uvx` one-command install |
| `vedant1317` | Whole-network one-call build; non-default-params-only reads; close-match suggestions |
| `mrinalghosh` | Multi-instance via `TD_INSTANCES`; `.mcpb` one-click; fan-dock layout |
| `mintiasaikoh` | `.mcpb` bundle; the **generator-as-source-of-truth** packaging model |
| `superdwayne` | A spec-shaped `manifest.json` — the reference for a `.mcpb` that installs |
| `michaelslain` | `td` **TCP/NDJSON** transport; auto-setup on first failure |
| `LindaEng` | SQLite session persistence + Langfuse observability; hotkey screenshot → vision |
| `benoitliard` | WebSocket + `td_batch`; auto-reconnect surviving TD restarts |
| `axysar` | `ui.undo` rollback; `td://` live resources; CORS protection |
| `twozero` | `*here` / `*this` spatial pointers; multi-instance routing; "shots" |
| `Pantani/tdmcp` | 355 tools tiered artist → building-block → atomic; Obsidian session memory |
| `familienak-tech` | Show control, LED/pixel mapping, media-server bridges, vision feedback |
| `sarveshsea` | Vetted `.tox`/`.toe` source registry; project scaffolds; show-readiness |
| `bottobot` | Version-aware docs, zero-config, experimental technique library |
| `cacheflowe` | Live `docs.derivative.ca` scrape with Crawl4AI |
| `TrueFiasco` | Key-free offline builder; hybrid RAG; **eval-gated 636/636** *(AGPL)* |
| `mahsan07` | Docs-first skill packaging: operating contract, safety boundaries, **expected evidence per step** |
| `grigabyte/td-atlas` | **Reads saved `.toe`/`.tox` offline** through TD's own `toeexpand`; `td_health` (what TD does not report); a call journal with old values; network text on save |
| `KingRaver/touchme` | Published live-probe research on 2025.33230 (glob paths, undo, wiring across networks, capture via `numpyArray` + cv2) |
| `cacheflowe/td-http-api` | HTTP bridge with agent skills (`td-common-mistakes`, process testing) and an in-TD dashboard |
| `jlopesmo/TDflowMCP` | Permission model, audit store, project memory, 17 architecture decision records |
| `GaNotchVFX/touchbridge` | Async file bridge, never-overwrite saves, GPU cost measurement |
| `skylarbarrera/nyan` | A typed `.toe` parser and a guide to editing an expansion and collapsing it back |

---

## Where `td-mcp` differs

**One package, both halves.** Offline doc/RAG *and* a live bridge, sharing a
corpus, a validator and a scorer. The offline half authors a network and
self-heals it before a live session is ever touched; the live half materialises
it and feeds real diagnostics back into the same scorer.

**No Web Server DAT.** The bridge runs its own HTTP server on a daemon thread,
which buys JSON-RPC + Streamable HTTP + SSE + WebSocket + a chat UI from one
implementation, and costs explicit threading discipline. The trade is discussed
in [TOUCHDESIGNER_COMPONENT.md](TOUCHDESIGNER_COMPONENT.md#why-td-mcp-does-not-use-a-web-server-dat).

**Depth over breadth.** 103 tools against tdmcp's 355. `td-mcp` prefers
risk-tiered tools that return recovery hints over a tool per operator.

**Local-first.** No API keys, no network. Dense vectors and reranking are opt-in
extras; the default path is pure-Python BM25 over a corpus that ships with the
repo, and the full 9,602-chunk KB is built from the offline mirror TouchDesigner
already installs on your disk.

---

## Keeping the mirror current

```bash
cd github-mcp
for d in */ ; do d="${d%/}"; [ -d "$d/.git" ] || continue
  git -C "$d" fetch --all --tags --prune
  git -C "$d" pull --ff-only
done

# Which ones moved — run before pulling to get a review list:
for d in */ ; do d="${d%/}"; [ -d "$d/.git" ] || continue
  echo "$d $(git -C "$d" rev-list --left-right --count HEAD...@{u} 2>/dev/null)"
done
```

The last sync report is [../UPSTREAM_SYNC_2026-08-26.md](../UPSTREAM_SYNC_2026-08-26.md).
Reading two upstream changelogs produced eight fixes and 200 new tests, which is
a better return than most feature work — it is worth doing on a schedule.

---

## See also

- [../TouchDesigner_MCP_Servers.md](../TouchDesigner_MCP_Servers.md) — the long-form catalog
- [../PICKED_FROM.md](../PICKED_FROM.md) — per-feature attribution
- [../IMPROVEMENT_PLAN.md](../IMPROVEMENT_PLAN.md) — what is still worth taking
