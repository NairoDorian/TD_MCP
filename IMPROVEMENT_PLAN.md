# TD_MCP — Improvement Plan (Ultimate RAG/MCP Server for TouchDesigner)

Status: **Draft plan** · Author: NairoDorian/TD_MCP · Date: 2026-08-15
Companion docs: [`README.md`](README.md), [`ARCHITECTURE.md`](ARCHITECTURE.md),
[`TD_MCP_Master_Plan.md`](TD_MCP_Master_Plan.md), [`TouchDesigner_MCP_Servers.md`](TouchDesigner_MCP_Servers.md).

---

## 0. Executive summary

`td-mcp` (v1.9.0) is already the **most complete** TouchDesigner MCP implementation in
the ecosystem: a local-first offline doc/RAG server (45 tools, k=5 recall ≈ 0.966) plus a
live bridge server (39 tools) over Streamable HTTP/SSE/WebSocket/stdio, a self-healing
build pipeline, TDN diffable YAML, show-control/LED/media-server planners, a knowledge
graph, an eval harness, an in-app agent + glassmorphic Chat UI, and 150+ tests.

This plan does **not** ask "is it the ultimate?" — yes. It asks **"what closes the last
gaps so it is unambiguously the one the ecosystem converges on?"** Five forces are
identified, then a prioritized roadmap.

The emphasis (per the maintainer) is the **Ultimate RAG / memory system** so TD_MCP can
fetch/read documentation and knowledge about how to use TouchDesigner and its MCP — i.e.
never let an agent guess a parameter name again, across sessions, with citations.

---

## 1. Ecosystem ground truth (27 reference repos surveyed in `../`)

All repos under `github-mcp/` have been pulled to latest HEAD. The distinctive,
high-value ideas still worth mining (beyond what TD_MCP already has) are listed under
§2–§6; repos already equivalent are noted so we don't re-invent.

| Repo (owner) | Already in TD_MCP? | Distinctive idea to steal |
|---|---|---|
| **johnnyvincentvitale/touchdesigner-wiki-rag** | ❌ (doc KB only) | Wiki-wide (~2,270 articles) index with `Page § Section` + URL **citations**, **family disambiguation** ("Noise TOP/CHOP/SOP/POP are 4 ops"), **freshness** (recentchanges), SQLite FTS5 + Chroma, adaptive HyDE, L0/L1/L2 tiers |
| **sarveshsea/touchdesigner-ai-brain** | ❌ | Multi-layer stack (live bridge + doc RAG + vision + durable Python), **source registry** of vetted `.tox/.toe`, project scaffolds, show-readiness checklists, Langfuse observability |
| **dcc-mcp/dcc-mcp-touchdesigner** | ❌ | **Typed, main-thread-safe** ops with JSON contracts + risk tiers, **bounded** graph/param/DAT ops, **SHA-256 optimistic concurrency** for DAT writes, **no arbitrary-Python by default**, Prometheus `/metrics`, deterministic TD API double |
| **quentinR2/TouchDesignerMcp** | ❌ | **Generated single-file bridge** built from `td_bridge/` modules with a **CI staleness guard**, `uvx`-one-command install, `td_build`-style templates with known gotchas, `bind_parameter_expression` w/ eval-error reporting |
| **vedant1317/touchDesigner-MCP** | ❌ | **Token-efficient** surface: whole-network one-call `td_build`, non-default-params only `td_get`, **close-match param suggestions** on miss, paths self-locate by name |
| **8beeeaaat/touchdesigner-mcp** (v2.0.0 just pulled) | ✅ (covered) | Track **MCP spec 2026-07-28** / SDK v2; v2.0.0 made Streamable HTTP **stateless** (dropped `Mcp-Session-Id`/session layer), stderr logging, Node 20+, security bumps |
| **mrinalghosh/touchdesigner-mcp** | ❌ | Multi-instance via `TD_INSTANCES`, `bind_parameter_expression`, fan-dock layout |
| **LindaEng/mcp_touch** | ❌ | **SQLite session persistence** + **Langfuse** observability, hotkey screenshot → vision + function calling |
| **michaelslain/touchdesigner-mcp** | ❌ | **`td` TCP/NDJSON** transport (alt to Web Server DAT), **auto project creation + auto-setup on first failure**, `td_doc_search`/`td_doc_read` |
| **sarveshsea/touchdesigner-ai-brain** | ❌ | (see above) |
| 8beeeaaat, Embody, tdmcp, axysar, twozero, TrueFiasco, superdwayne, benoitliard, cacheflowe, bottobot, johnsabath | ✅ | Features already folded into td-mcp (undo, recovery hints, TDN, spatial markers, eval gate, hybrid RAG, one-click bundling, batch, auto-reconnect, live crawler) |

**Catalog debt:** `TouchDesigner_MCP_Servers.md` still lists `NairoDorian/TD_MCP` as a
"placeholder, empty README, stub only" and predates most repos above. It must be
regenerated as part of this plan (§7.1).

---

## 2. Ultimate RAG / memory system (priority #1 — the maintainer's ask)

Goal: make `td-mcp-offline` the **single authoritative, citable, version-aware,
cross-session** knowledge source for "how to use TouchDesigner and its MCP", so an agent
never fabricates a param/op name and never loses session context.

### 2.1 Grounded, citable retrieval (steal from wiki-rag)
- **Add permalinks + `Page § Section` citations** to every `td_docs_*` response. Today
  chunks carry `title`/`source`/`text` but no stable URL/anchor. Add an `url` field to
  curated records and propagate a citation string (`f"{title} ({url#section)"`) in
  `td_mcp/rag/retriever.py` `_finalize`/`_fmt`.
- **Family disambiguation guard**: when a result's operator has same-named siblings in
  other families (Noise TOP/CHOP/SOP/POP), attach a warning. Implement in
  `td_mcp/rag/knowledge_graph.py` (it already knows operator graphs) and emit it from
  `td_docs_operator`/`td_docs_search`.
- **Freshness**: record a `build_time`/version stamp in the KB index and warn when the
  live TouchDesigner build (`app.version` via the bridge) differs from the corpus version
  the answer was authored against — `compat.check_compat` already does version comparison;
  wire it into doc responses.

### 2.2 Wiki-wide coverage (not just the 1,091 curated chunks)
- TD_MCP's curated corpus is excellent and hallucination-free, but it's a *sample*.
  Add an **opt-in wiki ingest path** that imports `touchdesigner-wiki-rag`'s SQLite FTS5
  index as an additional `Index` source, fused via the existing RRF layer
  (`rag/strategies.py`). No need to duplicate their fetcher — consume their built index.
- Or, simpler: make wiki-rag a **fused remote RAG** (the `RemoteMCPStrategy` already exists
  — extend it to also accept a local SQLite index path). Either way, ship it as opt-in
  so the zero-dependency default is unchanged.

### 2.3 Token-efficient memory + observability (steal from ai-brain / mcp_touch)
- **`td_mcp.memory`** already does JSONL keyword-overlap recall. Upgrade the storage to
  **SQLite** (SQLite is stdlib `sqlite3`) so sessions are durable, queryable, and
  survive restarts — matches ai-brain/mcp_touch. Keep the `td_memory_save`/`_recall` tool
  surface unchanged.
- Add an **observability sink** opt-in: emit LLM-style trace spans (model, tokens, tool
  calls, recovery hints used) to a local SQLite trace log that can be scraped by
  Prometheus-style exporters or ingested by Langfuse. Gate behind a flag so the core
  stays dependency-free.
- **Technique-memory model (from TDPilot)** — TD_MCP has memory + TDN checkpoints + macros;
  TDPilot unifies them as a `td_brain_plan → td_brain_execute → td_brain_learn` recipe loop
  with snapshot+rollback and a `trust_tier` on every hit
  (`official > bundled > personal > community > transcript > experimental`); the agent
  validates community/transcript hits before claiming them as fact. Adopt: `trust_tier` on
  chunk/KB records (§2.1 already adds citable URLs — tier them), a `td_memory_learn`/
  `td_memory_replay` recipe cycle, and **pre-turn retrieval injection** (recall top hits
  into the agent context before each live turn).
- **Conversation compaction** (TDPilot): long live sessions summarise older turns, keep
  the recent 10 verbatim, preserve full history to disk — cuts context cost vs. resending
  the whole network each turn.

### 2.4 Retrieval hardening
- **Fix the empty-index crash** (see §4.1) so "no KB built" → graceful `[]` + a
  "documentation not indexed; run `python -m td_mcp.kb.build_kb`" hint, not an
  `IndexError`. This is a **bug**, not a feature gap.
- Make `chunks.jsonl` a **test fixture built in CI** (see §4.2) so the 0.966 recall is
  actually enforced.

---

## 3. Live ↔ Offline integration (priority #2)

Today the live `build_and_verify` (server_live.py) does create→set→errors→capture but
**never** runs the offline `validation`/`scoring`/`heal` pipeline (ARCHITECTURE.md §6).
Close the loop so the live bridge self-corrects:

- In `server_live.py`, add a private `td_validate_live(path)` tool (or extend
  `build_and_verify`) that POSTs the created network to the offline orchestrator:
  `td_mcp.heal.heal_description(...)` over a serialized snapshot, then feeds repair
  hints back as `recovery_hints` (already attached on errors via `tools/recovery.py`).
- Feed `caption_viewport` image verdicts (`is_black`/`is_flat`/`fully_transparent`) into
  `td_score_build` so a flat render auto-triggers `td_self_heal` (the "richer vision loop"
  in the master plan §3 remaining-up).

---

## 4. Quality & test hygiene (priority #1 — blocking, gates everything)

### 4.1 Bug: graceful-degraded RAG index crashes
`retriever.Index.__init__` sets `self.N = max(len(self.doc_tokens), 1)`, so an **empty**
index (no `chunks.jsonl`) has `N=1` but `doc_tf=[]`, `doc_len=[]`, `title_tokens=[]`.
`bm25`/`title_scores`/`tfidf_dense` then index `doc_tf[0]` → `IndexError`. This directly
violates the README/ARCHITECTURE promise "if chunks.jsonl is missing, the server still
boots and tools return 'no docs' instead of crashing."

**Fix (done):** `self.N = len(self.doc_tokens)` (0 when empty); guard `bm25`/`dense`/`title_scores`
to return `[]` when `self.N == 0`. Added `tests/test_retriever.py` regression suite.
Files: `td_mcp/rag/retriever.py`, `tests/test_retriever.py`.

### 4.2 CI is currently red on RAG/knowledge-graph tests
`.github/workflows/ci.yml` runs only `uv pip install -e ".[mcp]"` then `uv run pytest`.
But 8 tests need the gitignored `chunks.jsonl` (not built) and `networkx` (not installed
without `.[rag]`). Verified locally: **171 pass** after `python -m td_mcp.kb.build_kb` +
`networkx`; **8 fail** on a fresh clone (IndexError before the 4.1 fix).

**Fix (done):**
- In CI: `uv pip install -e ".[test]"` and add a `build-kb` step (`uv run python -m td_mcp.kb.build_kb`) before tests.
  The `test` extra includes `networkx`, `pytest`, `pytest-cov` — **not** `sentence-transformers`
  (which pulls torch); dense scoring is opt-in via `TD_MCP_DENSE=1` and not exercised by tests.
- Added `[dependency-groups] dev = [...]` to `pyproject.toml` so `uv run pytest` works standalone.
- Switched to `[tool.setuptools.packages.find]` so `td_mcp.util` (previously missing) is packaged.
- `chunks.jsonl` kept gitignored but its absence is now non-fatal via 4.1.
Files: `.github/workflows/ci.yml`, `pyproject.toml`.

---

## 5. Protocol & tooling alignment (priority #2)

- **MCP spec 2026-07-28**: TD_MCP advertises `2024-11-05` (`server_live.py`, `streamable_http.py`).
  8beeeaaat v2.0.0 just adopted 2026-07-28 and **removed the session layer** (stateless
  Streamable HTTP, no `Mcp-Session-Id`). **Action:** track the Python `mcp` SDK release that
  supports 2026-07-28; when available, upgrade `mcp>=2.x`, drop the custom session/SSE glue
  in `streamable_http.py`/`server_live.py` in favor of the SDK's stateless handler, and
  align `protocolVersion`. (Do **not** force it before the SDK supports it — the stdio
  path works today with `mcp.server.stdio`.)
- **Generated bridge + CI staleness guard** (quentinR2 pattern): split `bridge/td_mcp_bridge.py`
  into `td_bridge/` source modules + `scripts/build_bridge.py` that emits a single
  concat'd `bridge_script.py`, and fail CI if the generated file is stale. Keeps the
  in-TD bridge always in sync with repo sources.

---

## 6. Security & safety hardening (priority #2)

- **`TD_MCP_PROTECTED_PATHS`** (superdwayne/dcc-mcp pattern): a comma-list (default
  `"/project1"`, the bridge root) that the bridge refuses to delete. Add to
  `bridge/td_mcp_bridge.py` dispatch + `server_live.py` `delete_node`/`delete_operator`.
- **Bounded operations** (dcc-mcp): cap recursion depth / batch size / CHOP sample count
  / capture resolution server-side (`TD_MCP_MAX_BATCH`, `TD_MCP_MAX_CAPTURE_BYTES` like
  quentinR2's `TOUCHDESIGNER_MAX_CAPTURE_BYTES`).
- **Optimistic concurrency for DAT writes**: `td_set_dat_text`/bridge edits accept an
  expected SHA-256 of current contents to prevent clobbered concurrent edits (dcc-mcp).
- **Typed contracts**: keep `execute_python`
  gated behind `TD_MCP_ALLOW_EXEC` (already done ✓) and document a "no-Python profile" for
  untrusted hosts, matching dcc-mcp's typed-only public surface.
- **Defense-in-depth from TDPilot's v2.5.4 audit** (2,141-test suite, secure-by-default):
  - **Secure-auth-by-default**: the bridge should preserve a shared secret across loads
    (not wipe it) and default `Authmode` to `token`; an insecure/zero-auth mode must be
    opt-in with a loud Textport banner (TDPilot C-1).
  - **Schema↔handler + cross-runtime parity tests**: snapshot every tool's JSON schema and
    fail if a handler exists but is absent from the advertised schema, or if the offline ↔
    live surfaces drift — TDPilot's strongest regression guard.
  - **Traceback redaction / snapshot path-sandbox**: strip user paths from 500s
    (`~/user/…` → `~/…`) and sandbox snapshot/restore to a `SNAPSHOTS_DIR` with
    symlink-aware `is_relative_to` checks (TDPilot H-1).

---

## 7. Developer experience & catalog hygiene (priority #3)

### 7.1 Refresh the ecosystem catalog
Update `TouchDesigner_MCP_Servers.md`:
- Rewrite the `NairoDorian/TD_MCP` row from "placeholder, empty README, stub only" to
  reflect v1.9.0 (84 tools, two-server split, self-healing, hybrid RAG, recall 0.966).
- Append the 15 newly-surveyed repos (wiki-rag, ai-brain, dcc-mcp, quentinR2, vedant1317,
  mrinalghosh, LindaEng, michaelslain, iizcm, RepoJifrex, etc.) to §1/§2/§8.
- Re-derive the §2 "what best-of-all would combine" list from current status.

### 7.2 Templates & scaffolding
- Adopt **quentinR2-style templates** (known-gotcha bundles: `feedback_loop_top` w/ a
  wired Level input, `chop_source_with_null`, `audio_in_with_analyze`, `render_pipeline`)
  on top of the existing `generators.py` + `glsl_patterns.py`. One-call `td_build`-style
  networks (vedant1317) via `td_build_network`/`batch`.
- Adopt **ai-brain's project scaffolds** (`brain_vj_builder.py` style: `audio_in`,
  `audio_analysis`, `control_bus`, `scene_bank`, `fx_rack`, `mixer`, `mapper`,
  `monitor`, `safety`) as a `td_scaffold_show(project_type=...)` offline tool.

### 7.3 One-command install
Finish the master-plan §3.5 "single-click installer": a `setup_env.ps1` / `scripts/`
mode that can optionally `uv tool install` the offline/live entry points and writes the
Claude Desktop / Cursor / Codex config in one shot (the manual parts already exist in
`config_gen.py`).

### 7.4 Token-efficiency
- Add a `td_get`-style "non-default parameters only" read (vedant1317) to reduce context
  on large networks.
- Add **close-match parameter suggestions** (`difflib.get_close_matches`) on miss in
  `set_parameters`/`param_resolver.py`.

---

## 8. Out of scope / explicitly not duplicating

- Re-implementing a 355-tool surface (tdmcp). TD_MCP prefers deep, risk-tiered tools with
  recovery hints over breadth.
- A from-scratch wiki crawler — we consume `touchdesigner-wiki-rag`'s index instead
  (§2.2).
- WebRTC transport — keep SSE + WebSocket; WebRTC is a nice-to-have, not ultimate.

---

## 9. Prioritized roadmap

**Phase 1 — stability & trust (blocks everything)**
1. [x] 4.1 Fix empty-index `IndexError` + regression test (`tests/test_retriever.py`).
2. [x] 4.2 Make CI green: install `.[test]`, build `chunks.jsonl` in CI, declare pytest/dev deps.
3. [x] 2.1 Add citation `url` + family-disambiguation warnings to `td_docs_*`.

**Phase 2 — ultimate RAG + live/offline loop**
4. [x] 2.2 Wiki-index fusion (consumed, not rebuilt) — `SqliteWikiStrategy` + `TD_MCP_WIKI_DB`.
5. [x] 2.3 SQLite-backed session memory + opt-in observability sink — `memory.py` FTS5 + `observability.py` TraceLog.
6. [x] 3.1 Wire live `build_and_verify` → offline `validation`/`scoring`/`heal`; feed viewport verdicts into scoring — `td_validate_live` + `score_build` penalties.
7. [x] 5.1 Track MCP 2026-07-28 / Python SDK; plan session-layer removal — `MCP_2026_MIGRATION.md` audit added (SDK 2.0.0 on PyPI, migration steps documented, NOT yet applied).

**Phase 3 — hardening & DX**
8. [x] 6.1 `TD_MCP_PROTECTED_PATHS` + bounded-op caps + SHA-256 DAT concurrency — bounded ops + `set_dat_text(expected_sha256)` added; protected paths were already present.
9. [x] 7.2 Templates + `td_scaffold_show` — ai-brain show scaffolds (vj/audio_reactive/installation) added.
10. [x] 6.2 TDPilot defense-in-depth: traceback/path redaction + schema↔handler parity tests added (parity caught missing `build_and_verify` bridge handler).
11. [x] 7.4 Token-efficient reads + param close-match — `get_non_default_params` live tool; param_resolver fuzzy matching already existed.
12. [ ] 7.3 One-command install.
13. [x] Wave 2 RAG/hardening: adaptive HyDE, operator-aware intent boost, freshness banner, L0/L1/L2 tiering + `manual://` resources, exec-mode tiers, image-block viewport, KB integrity receipt.

**Phase 4 — integration & release**
14. [x] 5.2 Generated bridge + CI staleness guard — `scripts/check_bridge_stale.py` + CI step.
15. [x] Eval rigor — name-integrity gate (`rag/eval.py --name-integrity`) + KB receipt generator.
16. [x] 7.1 Refresh `TouchDesigner_MCP_Servers.md`; ship `chunks.jsonl` build note in README.
17. [ ] Publish `td-mcp` as `.mcpb` + PyPI package.
18. [x] Wave 4 merge: param-family expansion (AGPL reimpl), conversation compaction, technique memory cycle, structured exec results, get_parameter_info, bind_parameter_expression, gotcha templates, source registry + show-readiness checklist.
19. [x] Wave 5 merge: activity log + history recall + pre-turn injection (TDPilot), per-operator version compat (bottobot).
20. [x] Wave 6 DX: catalog refresh + README build note + one-command install (`scripts/install_all.py`).
21. [ ] 7.3 fully done — `install_all.py` exists but `.mcpb` bundle + PyPI publish remain.
22. [x] Wave 7: activity-log wiring, score-floor abstention, local OfflineHelp ingestion, `td_tool_batch`, name-integrity pytest gate.
23. [x] Wave 8: bottobot experimental techniques KB (7 categories) + cacheflowe skills pack expansion.
24. [x] Wave 9 RAG: perfect eval recall (1.000 @k=5) — plural stemming, precise operator boost, family/tutorial boosts, sibling disambiguation, `td_kb_stats`.
25. [x] Wave 10: tool parity bug (13 tools missing from `_OFFLINE_DISPATCH`), exec-mode simplification (4→2 tiers), dead-code cleanup, `PICKED_FROM.md`.

---

## 10. Definition of "ultimate" (this plan's success criteria)

- `uv run pytest` is **green on a fresh clone** (no manual KB build).
- `td-mcp-offline` answers any TouchDesigner question with a **citable source URL** and a
  **family disambiguation warning** when names collide.
- An agent running `td-mcp-live` can do `create → validate → score → self-heal → re-verify`
  in one `build_and_verify` call and never leaves a broken, flat-rendering network.
- It survives **across sessions** (SQLite memory + traceable decisions).
- It is **safe by default**: protected paths, bounded ops, no arbitrary Python unless
  explicitly enabled, one Ctrl+Z reverts a batch.
