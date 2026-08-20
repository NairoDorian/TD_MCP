# Changelog

All notable changes to this project will be documented in this file.

---

## [1.11.0] — 2026-08-20 — Structured wiki KB (full official docs, offline)

The knowledge base grows from 1,091 curated chunks to **9,602** by ingesting a
complete local mirror of `docs.derivative.ca` — the tree TouchDesigner already
ships as *OfflineHelp*, or any saved dump. No network, no API keys, ~17 s to
parse 1,775 pages.

### Added
- **`td_mcp/kb/wiki_ingest.py`** — structured MediaWiki ingestion, standard
  library only. Reads each page's `RLCONF` metadata block (`wgPageName`,
  `wgTitle`, `wgCategories`), `og:url`, and `article:modified_time`, then walks
  the body with a subtree-skipping `HTMLParser` that drops navigation chrome
  (TOC, `mw-editsection`, `catlinks`, lingo tooltips) instead of flattening it
  into the text. Produces **one chunk per section**, so a retrieval hit points
  at *Blur TOP § Parameters - Blur Page*, not a whole page.
  - **Deep-link citations.** Every chunk carries the section's MediaWiki anchor
    as `url#anchor`, so a cited answer opens at the exact section.
  - **Title-prefixed text.** Each chunk begins `"<Page> — <Section>"`; a bare
    parameter description is meaningless without the operator it belongs to.
  - **Parameter names harvested into tags.** `<code>` runs inside Parameters /
    Members / Methods sections become chunk tags, so lexical retrieval matches
    the *internal* name an agent must actually write (`size`, `horzandvert`) —
    not just the human label.
  - **Family + page type from wiki categories.** `TOPs` → TOP operator,
    `TOP Classes` → TOP Python class, for all seven families incl. POP; falls
    back to the title suffix, then `concept`.
  - **Inherited class sections dropped.** Every `*_Class` page re-embeds the
    full `OP Class` / `TOP Class` docs; those blocks are skipped in favour of
    the canonical pages, and a class whose members are entirely inherited still
    gets one resolvable chunk rather than vanishing.
  - **Boilerplate deduplicated across pages.** "Parameters - Common Page" and
    "Common Operator Info Channels" are byte-identical on ~150 operator pages;
    they are emitted once (first page in sorted order wins) so the same 9 KB of
    text cannot drown lexical ranking.
  - **Experimental twins skipped.** `Experimental-X` is dropped when the
    released `X` page exists in the same mirror, and POP/experimental pages are
    stamped `min_version = 2023.10000` so version-filtered queries exclude them.
  - **Deterministic.** Sorted walk and stable ids, so the build is reproducible
    and the KB integrity receipt stays valid.
- **`build_kb --wiki-root / --no-wiki / --wiki-limit`** — wiki ingestion is now
  part of the deterministic KB build, auto-detecting `TD_MCP_WIKI_ROOT`, the TD
  install's OfflineHelp tree, or a sibling `TD HELP` dump. A fresh clone with no
  mirror builds exactly as before.
- **Version reconciliation in `build_kb`** — curated seeds are the authority on
  build gates (PBR MAT ≥ 2022.20000, POPs ≥ 2023.10000). Their `min_version` now
  propagates onto imported corpus/wiki chunks of the same title, closing a leak
  where a version-filtered query could still surface a gated operator through an
  unstamped twin chunk.
- **Exact-alias nickname boost** — a one- or two-word query that exactly matches
  a curated chunk's alias (`"movie"` → Movie File In TOP) boosts that chunk,
  which a 9,602-chunk lexical index would otherwise bury.
- **`tests/test_wiki_ingest.py`** — 19 tests over hand-written MediaWiki-shaped
  fixtures; no TouchDesigner install or docs mirror required.

### Fixed
- **Eval over-counted recall and reported nDCG above 1.0.** A retrieved chunk
  was credited against the expectation set as a flat membership test, so one
  expectation could be satisfied twice — `render_top` and its `corpus_render_top`
  twin each counted — and a query missing a genuine expectation still scored
  1.000. Each expectation is now credited to at most one retrieved chunk. This
  **lowers the previously reported figure**: measured honestly, the curated-only
  KB scores recall@5 **0.991**, and the wiki-backed KB **1.000**.
- **`offline_import` bricked the KB it was meant to grow.** It appended
  naively tag-stripped, 4,000-char-truncated pages to `chunks.jsonl` *after* the
  build, which invalidated `kb_receipt.json` — and the retriever then refused the
  whole KB as corrupted, degrading to zero documents. It is now a thin wrapper
  that rebuilds through `build_kb`, so the receipt covers the wiki content.
- **Strategy-count test asserted a magic number.** `test_six_strategies`
  hardcoded 6, but the count is one per chunk category plus two; a KB built with
  a wiki mirror legitimately has 7. It now asserts the composition.

### Changed
- `td_docs_search` renders `Page § Section` headings, `td_docs_read_page`
  filters on the structured `section` field and cites its source URL, and
  `td_docs_browse` lists sections rather than only page titles.
- The eval set gains 7 wiki-coverage queries, marked `needs: "wiki"` and skipped
  automatically when the KB was built without a mirror, so CI stays green.
- Eval expectations accept OR-groups (a nested list) for queries where more than
  one document is a correct canonical answer.

---

## [1.10.0] — 2026-08-19 — Ecosystem sweep (last-15-days commits)

Absorbed from reference repos that moved between 2026-08-04 and 2026-08-19.
All 28 repos under `github-mcp/` were re-pulled; 6 had new commits, and each
item below traces to one. See `PICKED_FROM.md` for the full provenance table.

### Security
- **Code-bearing route guard** (Pantani/tdmcp #143, 2026-08-11). `TD_MCP_EXEC_MODE=off`
  previously disabled `execute_python` and nothing else, while five other routes
  still handed caller-supplied Python to TouchDesigner: parameter expressions
  (`set_parameters` with `{"expr": ...}`), `bind_parameter_expression`, a DAT's
  text (`set_dat_text`), executable operator types (`create_node` of a
  Script/Execute/GLSL/Expression op), a DAT's `file` parameter, arbitrary
  `exec_node_method` calls, and `import_recipe` blueprints carrying any of the
  above. Exec-off now refuses **every** code route, naming which one it caught;
  `exec_node_method` keeps an allowlist of recompute/reset methods that evaluate
  no caller text. Detection is on the trailing identifier, so `scriptCHOP`,
  `glslmultiTOP` and `textDAT` resolve without an operator-type table.

### Added
- **Worker-thread `run()` lint** (dylanroscover/Embody v6.0.251, 2026-08-17).
  Derivative confirmed on 2026-08-17 that calling TD's global `run()` off the
  main thread does **not** raise on current builds — it silently corrupts TD
  state and crashes later, far from the call site. `execute_python` and
  `set_dat_text` now statically lint submitted Python and ride a
  `THREADING WARNING` back on the response at **write** time, since nothing
  catches it at runtime. Advisory only: the write still lands. Bounded up
  front (128 KB cap, `run(` prefilter before parsing, per-target dedupe,
  64-finding cap), and unparseable source lints to nothing so a fragment is
  never failed by its own lint.
- **Bridge/server version handshake** (quentinR2/TouchDesignerMcp #8, 2026-08-18).
  The bridge is installed by pasting a file into a `.toe`, so it goes stale
  invisibly — the project keeps working until the server calls a tool added
  after the paste, which fails as an unknown tool with no hint why. The bridge
  now stamps `BRIDGE_VERSION`, returns it from `project_info`/`describe_td_tools`,
  and `TDClient` resolves it once per client and attaches a warning naming both
  versions and the fix. `scripts/check_bridge_stale.py` fails CI when the stamp
  drifts from `pyproject.toml`, so the signal cannot silently rot.
- **Snapshot provenance on doc lookups** (Pantani/tdmcp #150, 2026-08-11). The
  corpus is an imported snapshot, not the running build, so a miss was being
  read as proof an operator does not exist. `corpus.operator_lookup` now returns
  `found` / `lookup_status` / `data_version`, and `td_docs_operator` prints an
  explicit "not in the documentation snapshot — this does not prove it is absent
  from your build" notice plus the nearest real operator names. Hits cite the
  snapshot they came from.
- **Close-match operator suggestions**: `corpus.nearest_operator_names` (difflib,
  with a leading-word fallback so a wrong family suffix like "Blur COP" still
  suggests "Blur TOP"), surfaced on every lookup miss.
- **`tests/bridge_pure.py`**: execs the bridge's pure-helper block straight out
  of the shipped source instead of re-typing it into the tests, so bridge tests
  can no longer drift from the bridge.

### Fixed
- **`td_build` reported the legacy generation, not the release** (dcc-mcp #5,
  2026-08-13). `project_info` returned `app.version`, which still reports `099`;
  the public `year.build` identifier lives on `app.build`. Every version-compat
  check compares against `2023.11000`-style corpus builds, so `td_build` was
  unusable for the one job it had. Now derived from both, with the raw
  generation kept as `td_version`.
- **Warnings were invisible, or counted as errors** (Pantani/tdmcp #146,
  2026-08-11). `get_errors` returned only `node.errors` as bare strings.
  It now returns severity-tagged `diagnostics` plus `error_count` /
  `warning_count` (and `warnings`), with `errors` unchanged for compatibility,
  so a caller cannot mistake a warning for a failure.
- **`version_mismatch_warning` froze the server version at import**: binding
  `__version__` as a default argument ignored the installed package's real
  version; it is now resolved at call time.

### Changed
- Version bumped to **1.10.0**; `BRIDGE_VERSION` tracks it, enforced by
  `scripts/check_bridge_stale.py`.
- Test suite: 288 → 367 passing.

---

## [Unreleased]

### Fixed
- **Empty-index crash**: when `chunks.jsonl` is missing (fresh clone, no KB build),
  the RAG `Index` no longer raises `IndexError` — scorers return `[]` and
  `Retriever.search` degrades to "no docs" instead of crashing. Added
  `tests/test_retriever.py` regression suite (§IMPROVEMENT_PLAN 4.1).
- **CI green on fresh clone**: the GitHub Actions workflow now installs `.[test]`
  (includes `networkx` + `pytest`), builds `chunks.jsonl` via
  `python -m td_mcp.kb.build_kb` before running pytest, and `pyproject.toml`
  declares `test` extra + `[dependency-groups] dev` so `uv run pytest` works
  standalone (§IMPROVEMENT_PLAN 4.2).
- **Packaging**: switched to `find` package discovery so `td_mcp.util` (previously
  missing from the explicit packages list) is included in built distributions.

### Added
- **Citable retrieval**: every KB chunk now carries a `url` permalink
  (`https://docs.derivative.ca/<op>`) from the merged MIT corpus and curated
  records; `td_docs_*` responses print a `link:` citation (§IMPROVEMENT_PLAN 2.1).
- **Family disambiguation**: looking up a bare operator name that is ambiguous
  across families (e.g. "Noise" → Noise TOP/CHOP/SOP/POP) now emits a warning
  listing all matches and the families involved, in `td_docs_operator`,
  `td_docs_parameter`, and `td_docs_search` (§IMPROVEMENT_PLAN 2.1).
- **SQLite-backed session memory** (§2.3): `td_mcp.memory.SessionMemory` now
  stores turns in a stdlib `sqlite3` FTS5 table instead of JSONL. Recall ranks
  by `trust_tier` (`official > bundled > personal > community > experimental`)
  first, then keyword overlap, then recency. `td_memory_save` accepts an
  optional `trust_tier` argument; the `td_memory_save`/`td_memory_recall` tool
  surface is unchanged. Path overridable via `TD_MCP_MEMORY_DB`.
- **Opt-in observability sink** (§2.3): `td_mcp.observability.TraceLog` records
  LLM-style span rows (tool calls, LLM calls, timed contexts) to a SQLite
  trace log. Gated behind `TD_MCP_OBSERVE` (unset = zero-overhead no-op).
  Every `call_tool` in the offline server is now traced when enabled.
- **Wiki-index fusion** (§2.2): `SqliteWikiStrategy` consumes a
  `touchdesigner-wiki-rag` SQLite FTS5 index (`manual.db`) and fuses its hits
  into the RRF pool with a `wiki:` document URI. Opt-in via `TD_MCP_WIKI_DB`
  (path to the wiki-rag DB) or the `wiki_db` arg on `ParallelRetriever`.
  No chromadb/sentence-transformers needed for this path.
- **Live↔offline verification loop** (§3.1): `TDClient.build_and_verify` now
  feeds the created network + `capture_viewport` verdict into the offline
  `td_validate_live` orchestrator, so a black/flat/transparent render lowers
  the score and produces recovery hints. `score_build` accepts a
  `viewport_verdict` field with `black`/`flat`/`transparent` penalties.
- **MCP 2026-07-28 migration plan** (§5.1): added `MCP_2026_MIGRATION.md`
  auditing the SDK 2.0.0 upgrade, stateless Streamable HTTP removal, and
  auth middleware changes needed for protocol revision 2026-07-28.
- **Bounded operations** (§6.1): env-tunable caps in the bridge — batch size
  (`TD_MCP_MAX_BATCH`), parameter count (`TD_MCP_MAX_PARAMS`), traversal depth
  (`TD_MCP_MAX_DEPTH`), CHOP samples (`TD_MCP_MAX_SAMPLES`), DAT rows
  (`TD_MCP_MAX_ROWS`), DAT text bytes (`TD_MCP_MAX_DAT_BYTES`) — all enforced
  server-side.
- **`set_dat_text` with optimistic concurrency** (§6.1): new live tool writes a
  DAT's text and accepts an `expected_sha256` guard; concurrent clobbers are
  refused with the actual hash.
- **Traceback/path redaction** (§6.2 TDPilot H-1): bridge error responses
  redact user home paths (`~/...`) and `.td_mcp` (`<TD_MCP_HOME>`) so 500s
  don't leak the operator's layout.
- **Schema↔handler parity tests** (§6.2 TDPilot): `tests/test_parity.py` asserts
  the live tool registry, `tools/list` metadata, HTTP schema surface, and TD
  bridge dispatch stay in lockstep. Caught a real drift: `build_and_verify`
  was advertised but missing from the bridge dispatch.
- **Adaptive HyDE** (wiki-rag): HyDE now fires only when the base fused pass
  scores below a threshold (`hyde_trigger`), and the hypothetical-doc result is
  kept only if strictly better. Always-on HyDE remains available via explicit
  opt-in.
- **Operator-aware intent boost** (TrueFiasco, MIT-safe reimpl): when a query
  names a known operator, its chunks get a hard RRF boost, so
  "noise top amplitude" surfaces the Noise TOP parameter chunks over generic
  matches.
- **Freshness banner** (wiki-rag): opt-in `TD_MCP_FRESHNESS=1` checks the wiki
  `recentchanges` feed against a `fetch-manifest.json` (cached hourly,
  offline-silent) and prepends a "⚠ STALE INDEX" banner to doc results.
- **L0/L1/L2 doc tiering** (wiki-rag): new `td_docs_browse` (L0 section map),
  `td_docs_read_page` (L2 full page), and `manual://index` +
  `manual://operators/{family}` MCP resources.
- **Token-efficient reads** (vedant1317): `get_non_default_params` live tool
  returns only non-default params (the bridge's `get_node` already emits them;
  this exposes them directly).
- **Exec-mode tiers** (TDPilot): `TD_MCP_EXEC_MODE` supports
  `off|restricted|standard|full`; restricted refuses `exec`/`eval`/`open`/
  `__import__` and other dangerous builtins before execution.
- **Image-block viewport** (mintiasaikoh): `capture_viewport` attaches base64
  PNG image content so MCP clients can render the render output directly.
- **KB integrity receipt** (TrueFiasco): optional `kb_receipt.json` pins the
  SHA-256 of `chunks.jsonl`/`embeddings.jsonl`; on mismatch the retriever
  refuses to load and degrades loudly instead of silently serving a bad index.
- **`td_scaffold_show`** (ai-brain): three opinionated show scaffolds (`vj`,
  `audio_reactive`, `installation`) with ai-brain's canonical section layout
  (audio_in/analysis/bus/scene_bank/fx_rack/mixer/mapper/monitor/safety).
- **Name-integrity eval gate** (TrueFiasco): `rag/eval.py --name-integrity`
  verifies every expected chunk id in the eval set resolves to a real KB chunk
  (0 unresolved / 0 retokenized) and reports per-family corpus coverage.
- **KB receipt generator**: `build_kb` now writes `kb_receipt.json` alongside
  `chunks.jsonl` so the integrity check is populated automatically.
- **Bridge staleness guard** (quentinR2): `scripts/check_bridge_stale.py` fails
  CI if the bridge's `_DISPATCH` surface drifts from the live tool registry;
  wired into `.github/workflows/ci.yml`.
- **Param-family expansion** (TrueFiasco AGPL reimpl, MIT-safe): list-valued
  params expand to TD's flat suffix params (t → tx/ty/tz, color → colorr/g/b,
  fromrange → fromrange1/2); `param_resolver.expand_params()`.
- **Conversation compaction** (TDPilot): `memory.compact_messages()` summarizes
  older turns into one text-only synthetic message, keeps recent N verbatim,
  pure local heuristics, no LLM call.
- **Technique memory cycle** (TDPilot): `memory.TechniqueStore` with validation
  ladder (candidate → validated_local → validated_portable), replay auto-
  promote/demote, scope promotion (project → global), SQLite-backed.
- **Structured exec results** (TDPilot): bridge `execute_python` returns
  `result`/`result_type`/`result_is_structured` with JSON-safe conversion of
  TD objects.
- **`get_parameter_info`** (touch_mcp): full per-parameter schema — label,
  value, mode, default, page, style, bounds, menu names/labels.
- **`bind_parameter_expression`** (mrinalghosh): sets par.expr, snaps mode to
  EXPRESSION, evals, and returns *new* cook errors so bad expressions never
  silently no-op.
- **Gotcha-annotated templates** (mrinalghosh/quentinR2): 5 templates with
  encoded gotchas (`chop_source_with_null`, `glsl_top_vec4_uniform`,
  `audio_in_with_analyze`, `feedback_loop_top`, `render_pipeline`) exposed via
  `td_gotcha_template`.
- **Source registry + show-readiness checklist** (ai-brain):
  `td_source_registry` (license/TD-version-vetted sources) and
  `td_show_readiness` (before_automation/before_show/asset_intake).
- **Activity log + history recall** (TDPilot): `activity.ActivityLog` ring
  buffer (200 entries) with `td_activity_log`; `td_history_recall` searches
  across session memory + technique store; `memory.pre_turn_injection()` for
  ambient pre-turn context.
- **Per-operator version compat** (bottobot): `td_docs_version_compat` reports
  addedIn/removedIn/changedIn compatibility against a target build, using the
  corpus compatibility manifests.
- **Catalog refresh** (§7.1): `TouchDesigner_MCP_Servers.md` TD_MCP row rewritten
  from placeholder to v1.9+ ultimate status; README documents the `chunks.jsonl`
  build step + fresh-clone note.
- **One-command install** (§7.3): `scripts/install_all.py` installs the package,
  builds the KB, writes client configs + skill, and prints registration JSON.
- **Activity log wired** (TDPilot): every offline `call_tool` now records a
  duration + ok status in the activity ring buffer (was previously exposed but
  not integrated).
- **Score-floor abstention** (TrueFiasco): `ParallelRetriever(score_floor=N)`
  returns `[]` when the best fused score falls below N, so out-of-domain
  queries abstain instead of surfacing weak garbage.
- **Local OfflineHelp ingestion**: `kb/offline_import.py` walks TouchDesigner's
  bundled `Samples/Learn/OfflineHelp/https.docs.derivative.ca` mirror and
  appends pages to `chunks.jsonl` — full wiki coverage with zero network.
- **`td_tool_batch`** (TDPilot): run multiple offline tools in one bounded
  round-trip; dispatcher late-bound after all tool functions are defined.
- **Name-integrity as pytest** (TrueFiasco): `tests/test_wave7.py` asserts the
  gate passes, not just via CLI flag.
- **Experimental techniques KB** (bottobot): 7 category files copied into
  `kb/corpus/experimental/` (glsl, gpu-compute, machine-learning,
  generative-systems, audio-visual, networking, python-advanced) and exposed
  via `td_docs_experimental` with category/technique aliases and code toggling.
- **Skills pack expanded** (cacheflowe): `skills/td-building/SKILL.md` now
  carries the pull-based philosophy, family map, critical conventions (par
  names, geometryCOMP, Feedback wiring, GLSL Vectors page), Python essentials,
  and the common-mistakes list.
- **RAG wave 9**: perfect eval recall (1.000 at k=5, up from 0.966) via
  plural stemming ("pops"→"pop"), precise full-name operator boost (only
  whole operator names or 2+ word aliases, both curated + corpus chunks),
  family-overview boost for workflow queries, audio-reactive tutorial boost,
  sibling-operator disambiguation in search output (wiki-rag guardrail:
  "Noise TOP" flags Noise CHOP/SOP/POP), and a new `td_kb_stats` tool.
  MRR 0.795 / nDCG 0.802 (up from 0.776 / 0.775).
- **Simplification wave**: exec mode reduced from 4 tiers to 2 (`on`/`off`
  with legacy aliases mapping to `on`); dead code removed from
  `td_docs_experimental`; `PICKED_FROM.md` created to trace every technique
  merged from the reference repos.
- **Tool parity bug fixed**: 13 tools were advertised in `list_tools` but
  missing from the `_OFFLINE_DISPATCH` used by `td_tool_batch`. Fixed — all
  56 tools now dispatch correctly.

---

## [1.9.0] - 2026-07-10

Ultimate-merge pass: features mined from the 13 reference TD-MCP repos
(`github-mcp/`) and folded into the Python codebase.

### Added
- **`td_analyze_build`** — liveness/dead-weight analysis of a network description
  (tdmcp idea): flags isolated nodes, only-fed nodes, empty COMPs, and broken
  file/path dependencies (ISOLATED / NO_OUTPUT / EMPTY_COMP / BROKEN_FILE_DEP).
- **`td_diff_networks`** — structural A/B diff of two builds (twozero /
  TD_Builder idea): added/removed/changed operators with parameter deltas and
  connection deltas.
- **`td_optimize_layout`** — deterministic, collision-free node placement
  (tdmcp / Embody idea) so generated nodes never pile up at the origin.
- **`td_resolve_params`** — normalizes friendly parameter names (`freq`) and
  loose menu values (`0`) to exact TD codes via the corpus schema
  (TD_Builder `param_name_resolver`); prevents silent no-op params.
- **`td_docs_combos`** — operators that commonly work *together* with a given
  one, from corpus `workflowPatterns` co-occurrence (relationship expansion).
- **Output budgeting** (`td_mcp/util/output_budget.py`): doc-search responses
  are capped by `TD_MCP_OUTPUT_MAX_CHARS` and report an explicit omission marker
  instead of silently truncating (TD_Builder `output_budget` idea).
- **MCP `prompts` are now first-class** in both the offline and live servers:
  `prompts/list` + `prompts/get` expose the expert personas and phase prompts.
- **Knowledge-graph co-occurrence** (`rag/knowledge_graph.combo_related`) for
  workflow/relationship queries.

### Changed
- Offline tool catalog is now **45** tools (was 40); live server unchanged at 39.

---

## [1.8.0] - 2026-07-10

Review pass: fix runtime crashes found during a full code + docs review.

### Fixed
- **In-TD bridge JSON-RPC was completely broken.** `td_mcp_bridge.py` called
  `self._handle_jsonrpc(...)` which was never defined, so every request to the
  `/jsonrpc` (Streamable HTTP) endpoint raised `AttributeError`. Implemented a
  real JSON-RPC 2.0 handler (`_handle_jsonrpc`) supporting `initialize`, `ping`,
  `tools/list`, `tools/call`, `resources/*`, `prompts/list`, JSON-RPC **batches**,
  and **notifications** (no longer hangs / 500s).
- **Live stdio MCP server never started.** `TD_MCP_MODE=mcp` / `--mcp` discarded
  the `Server` returned by `_run_stdio_server()` and exited. It now runs the
  server over stdio via `mcp.server.stdio`.
- **Agent recovery crashed on every tool failure.** `_execute_with_recovery`
  referenced an undefined `ErrorRecovery.apply_fallback`, and `_execute_parallel`
  referenced an undefined `_PARALLEL_EXECUTOR` (so the `execute_parallel` tool
  also crashed). Both now work: parallel calls run through a `ThreadPoolExecutor`
  and failures attach a structured recovery hint from `td_mcp.tools.recovery`.
- **`build_kb.py` mis-assigned a knowledge-base field.** A stray `"stream"`
  positional for *Touch Out TOP* was captured as `min_version` instead of being
  dropped; removed.
- **`bootstrap.py`** had a duplicated module header (executed at import) and a
  dead tail that redefined `AUTH_TOKEN` at import time; both cleaned up.

### Changed
- Live stdio MCP server now reports `td_mcp.__version__` in `serverInfo`
  (already done for the HTTP transport), so both transports are consistent.
- Removed an unused import (`python_class_for_operator`) from `server_offline.py`.

---

## [1.7.4] - 2026-07-10

Server improvement + master-plan cleanup.

### Added
- **`scripts/bump_version.py`** — bumps `pyproject.toml` + the top `CHANGELOG.md`
  heading in one step; warns on any stale version mentions. Single source of truth
  for the version (`td_mcp.__version__`).
- Cleaned and brought **`TD_MCP_Master_Plan.md`** up to date: accurate file layout,
  shipped/pending roadmap table (incl. hygiene/self-heal items), and removed
  references to the deleted `TaskPlanner` / `InteractiveClarifier` classes.

### Changed
- Offline MCP server (`td-mcp-offline`) now reports its version via
  `td_mcp.__version__` in `serverInfo` (consistent with the live server).
- `COMMIT.md` documents the simplified, single-command version bump.

---

## [1.7.3] - 2026-07-10

Repository review pass: consistency, dead-code removal, and public-repo hygiene.

### Added
- **`.github/workflows/ci.yml`** — GitHub Actions running `uv run pytest` on push/PR.
- **`.gitattributes`** — normalize line endings to LF across OSes.
- **`CONTRIBUTING.md`** — contribution guide pointing at `COMMIT.md`.

### Changed
- `pyproject.toml` version bumped to `1.7.3` (now matches the changelog head).
- `ARCHITECTURE.md` layout tree completed (skills/, scripts/, tests/, docs, configs);
  test count described as "150+"; dead-scaffolding limitation note updated (removed).
- `README.md` layout tree + docs table updated (CI badge, COMMIT/CONTRIBUTING, .github).

### Fixed
- `td_mcp/config_gen.py` gained a `main()` / `__main__` block so
  `uv run python -m td_mcp.config_gen` actually writes configs (was a silent no-op).
- Removed dead `TaskPlanner` / `InteractiveClarifier` classes from
  `bridge/td_mcp_agent.py` (unused; `chat` uses `_plan_task` / `_ask_user`).

### Cleaned
- `td_mcp/kb/chunks.jsonl` (generated) untracked and added to `.gitignore`.

---

## [1.7.2] - 2026-07-10

### Added
- **`COMMIT.md`** — pre-commit checklist: regenerate `SUMMARY.md`, full Markdown
  documentation pass (edit all `.md` to match current code), sync `pyproject.toml`,
  update this changelog, run tests, then explicit stage / commit / push.

---

## [1.7.1] - 2026-07-10

Repository cleanup and a code-free summary generator.

### Added
- **Code-free repository summary generator** (`scripts/generate_summary.py`) — walks the
  repo and emits `SUMMARY.md` (overview, architecture, directory tree, and a short
  description of every tracked file) with **no source code inlined**. A `repomix.config.json`
  is provided for anyone who wants a full source pack via `npx repomix` (off by default).
- **`LICENSE`** (MIT) — previously only referenced in the docs.
- **`td_mcp/tools/__init__.py`** — explicit package marker; `td_mcp.tools` added to the
  `pyproject.toml` `packages` list.

### Changed
- `README.md` rewritten: accurate layout tree, full 40/39 tool catalog, relative doc
  links, fixed the stale chunk count (216 → 1,091) and recall figure.
- `HOW_TO_USE.md` hardcoded absolute repo paths replaced with `<REPO_DIR>`.
- `pyproject.toml` version bumped `0.1.0` → `1.7.0` to match this changelog.

### Cleaned
- `.gitignore` extended (`repomix-output.md`, `.td_mcp/`).

---

## [1.7.0] - 2026-07-10

Third inspiration sweep — orchestration / continuity / packaging ideas from the
survey, plus exposing them as offline MCP tools.

### Added
- **Multi-instance discovery (twozero)**: `td_mcp.discover` — `discover_instances()`
  probes known TD-MCP ports (9980/9988/9870/8053/40404/8765) with an injectable
  probe, so an agent can pick which open TD to target. Exposed as `td_discover`.
- **Session memory (tdmcp Obsidian / AI session memory, Embody)**: `td_mcp.memory`
  — local JSONL memory of interaction turns with keyword-overlap `recall()` and
  `summarize()`. Gives an agent continuity across sessions with no vector DB.
  Exposed as `td_memory_save` / `td_memory_recall`.
- **Spatial pointer resolver (twozero `*here`/`*this`)**: `td_mcp.spatial` —
  `resolve_pointer()` / `resolve_args()` turn `*here` (current network) and
  `*this op` (selected operator) into concrete paths for the live client.
- **Progress reporting (touchdesigner_agent_mcp `report_progress`)**:
  `td_mcp.progress` — structured `{step, total, percent, label}` updates for long
  builds.
- **Project bundling `.mcpb` (tdmcp multi-client packaging)**: `td_mcp.bundle` —
  `package()` zips the project with a `server.json` manifest (excluding
  `.venv`/`__pycache__`/etc.), `read_manifest()` reads it back.
- **Recipe scaffolding from a network** (tdmcp `scaffoldRecipeFromNetwork`):
  `td_scaffold_recipe` turns a TDN/operator description into a reusable recipe
  blueprint via `recipe_vault.draft_recipe_from_chain`.
- **4 new offline MCP tools** (READ_ONLY): `td_discover`, `td_memory_save`,
  `td_memory_recall`, `td_scaffold_recipe`. Offline server now exposes 35 tools.
- **Tests**: `tests/test_discover.py`, `tests/test_memory.py`, `tests/test_spatial.py`,
  `tests/test_progress.py`, `tests/test_bundle.py`, `tests/test_offline_more_tools.py`
  (19 new cases; suite now 137 passing).

## [1.6.0] - 2026-07-10

Second full inspiration sweep — more high-value, implementable ideas, plus
exposing them as **callable offline MCP tools** so an agent can actually use
them.

### Added
- **Macro recorder / replay (tdmcp)**: `td_mcp.macro` — `MacroRecorder`
  records tool calls (tool/args/result), dedupes, serializes, and replays via
  any dispatcher (e.g. the live `batch`).
- **Version compatibility + error cache (8beeeaaat)**: `td_mcp.compat` —
  `check_compat()` (MAJOR=error / MINOR=warning / PATCH=tolerated) and an
  `ErrorCache` (TTL) so a flapping bridge doesn't spam identical transport
  errors.
- **Expert prompts (TD_Builder `get_expert_prompt`)**: `td_mcp.prompts` — phase
  personas (td_designer, network_builder, td_glsl_expert, td_python_expert,
  ui_expert, critic) routed per build phase (plan/build/self_improve).
- **Performance analyzer (TD-Codex)**: `td_mcp.perf` — classifies fps, ranks
  slowest cooks, and emits concrete optimization suggestions from a snapshot.
- **Media-server connectors (TD-Codex)**: `td_mcp.showcontrol.media_server()`
  plans Millumin / Resolume / Notch / Disguise / QLab / MadMapper connectors
  (transport + endpoint + target operator).
- **7 new offline MCP tools** (all READ_ONLY, risk-tiered): `td_glsl_pattern`,
  `td_network_template`, `td_expert_prompt`, `td_compat_check`, `td_score_build`,
  `td_mediaserver`, `td_analyze_performance`. The offline server now exposes 31
  tools.
- **Tests**: `tests/test_macro.py`, `tests/test_compat.py`, `tests/test_prompts.py`,
  `tests/test_perf.py`, `tests/test_offline_new_tools.py` (25 new cases, now 118
  total passing).

## [1.5.0] - 2026-07-10

Full inspiration sweep across the entire `github-mcp` ecosystem — implementing
the remaining high-value, implementable ideas (not the placeholder/mock stubs).

### Added
- **Vision / histogram caption (tdmcp `captionTop`)**: `td_mcp.vision` —
  `analyze_pixels()` computes deterministic pixel stats (`mean_luma`,
  `near_black_fraction`, `saturation_mean`, `classification`) and a viewport
  verdict (`is_black`/`is_flat`/`fully_transparent`/`pass`) with NO vision LLM
  required; `caption_from_stats()` emits a text caption + `viewport_verdict()`
  bridges to the agent loop. A blind agent can now "see" the render.
- **Build scoring + self-repair loop (tdmcp `scoreBuild`/`repairNetwork`)**:
  `td_mcp.scoring` — `score_build()` grades a network 0..100 (A–F) from
  validity, typed/wired completeness and corpus backing; `repair_network()`
  iteratively applies `auto_repair` until clean (the `autoRepairLoop`).
- **Token-efficient logs (Embody discipline)**: `td_mcp.tools.logs` — a bounded
  `LogRing` plus `attach_piggyback()` that only attaches WARNING/ERROR `_logs`
  to a result, keeping successful runs quiet within context budgets.
- **GLSL pattern library + network templates (bottobot `get_glsl_pattern` /
  `get_network_template`)**: `td_mcp.glsl_patterns` — 6 named, paste-ready
  fragment shaders and 4 ready-to-build network templates (audio-reactive,
  feedback, render-scene, LED wall) emitting TDN operator lists.
- **Per-client config + skill generation (Embody / twozero)**: `td_mcp.config_gen`
  — generates the exact `.mcp.json` / `claude_desktop_config.json` and a tailored
  `CLAUDE.md` skill doc for td-mcp's two servers, for a one-step client install.
- **TDN idle auto-checkpoint (Embody)**: `tdn.checkpoint()` / `restore_checkpoint()`
  persist a git-diffable `.tdn` snapshot to disk (volatile headers ignored by
  `diff_tdn`) for crash recovery / auto-restore on project open.
- **Security hardening — Origin pinning (Embody)**: `server_live` now enforces a
  configured `TD_MCP_ALLOWED_ORIGINS` allowlist (Host-pinning / CSRF defense) on
  top of the existing DNS-rebind Host check, in the Streamable-HTTP server.
- **Tests**: `tests/test_vision.py`, `tests/test_scoring.py`, `tests/test_logs.py`,
  `tests/test_glsl.py`, `tests/test_config_gen.py` (23 new cases, all passing).

## [1.4.0] - 2026-07-10

Agent self-healing & build-quality improvements, inspired by surveying the
full `github-mcp` ecosystem (Embody, tdmcp, TD_Builder_alpha, touch_mcp,
twozero_td_mcp, bottobot, …).

### Added
- **Recovery hints (Embody)**: `td_mcp.tools.recovery` — a pure error-signature
  catalog that returns `{cause, action, next_tools}` for every common TD/bridge
  failure (unreachable bridge, node-not-found, unknown param, family mismatch,
  cook error, read-only/auth blocks, …). Wired into `TDClient._call` so **every
  failed live tool response now carries a `recovery` block**, letting an agent
  self-correct instead of retrying blindly.
- **Staged build validator + auto-repair (TD_Builder 5-stage + tdmcp
  `autoRepairLoop`)**: `td_mcp.validation` — `validate_build()` runs five staged
  passes (schema → semantic → reference → logical → td_rules/family-compat),
  `suggest_repairs()` turns findings into actions, and `auto_repair()` returns a
  corrected description (drops dangling inputs/connections, auto-names unnamed
  nodes, removes typeless nodes). Corpus-aware: uses real operator families and
  parameter schemas when available.
- **Layout lint (Embody)**: `td_mcp.tools.layout` — `lint_layout()` flags nodes
  at (0,0), overlaps, zero-size, stray/orphan docks, and unnamed operators;
  `placement_hint()` gives a deterministic next-free position to stop the
  classic node pile-up.
- **Recipe vault upgrade (tdmcp)**: `recipe_vault` now stores tdmcp-style
  design-file metadata (`difficulty`, `td_version_min`, `technique`) and ships
  `draft_recipe_from_chain()` to auto-skeleton a reusable blueprint from an
  operator chain. Listings surface the new fields.
- **Tests**: `tests/test_recovery.py`, `tests/test_validation.py`,
  `tests/test_layout.py`, `tests/test_recipe.py` (23 new cases, all passing).

## [1.3.0] - 2026-07-10

Improvements informed by reviewing sibling TouchDesigner MCP projects
(`github-mcp/Embody` Envoy, `DOCUMENTATION/`) in the parent folder.

### Fixed
- **Bridge `do_GET`/`do_POST` were defined twice** (`td_mcp_bridge.py`). The
  second (stub) definitions shadowed the real ones, silently killing SSE
  streaming and root JSON-RPC — two headline features advertised by the
  Streamable-HTTP transport. Removed the dead stubs so the real handlers
  (WebSocket upgrade, SSE, `/` JSON-RPC, chat UI, status, resources) are active.
  Added `tests/test_scan_ws.py::test_single_do_get_post` to lock this in.
- **CORS `*` wildcard was still live despite the docstring claiming it was
  removed for CSRF** (`td_mcp_bridge.py` `_send` + `do_OPTIONS`). Replaced with
  `_cors_origin()`, which only echoes back a *loopback* Origin and downgrades
  everything else to `http://127.0.0.1`. Added
  `tests/test_scan_ws.py::test_cors_reflects_loopback_blocks_external`.
- **Undefined `debug` helper** (`td_mcp_bridge.py`): `start()`/`stop()`
  referenced `debug(...)` which was never defined, raising `NameError` on a
  second `start()` or on `stop()`. Added a `debug` log helper.
- **Redundant duplicate WebSocket-upgrade check** in the bridge `do_GET`.

### Changed
- **`skills/td-building/SKILL.md`** gained a *Connectivity (do this first)*
  section and a *Self-correction* section distilled from Embody's
  `td-connectivity` / `mcp-safety` rules: check reachability first, resolve
  `*here`/`*this`, treat `capture_viewport` quality verdicts as the source of
  truth for visual work, and follow `recovery_hints` instead of retrying.

---

## [1.2.0] - 2026-07-10

### Fixed
- **Rerank score misassignment**: `ParallelRetriever` now keeps each doc's own
  RRF score attached through the cross-encoder reordering (previously scores
  were reassigned by position, so the wrong score landed on the wrong doc after
  reranking). Added `tests/test_rag.py::test_rerank_keeps_scores`.
- **Streamable-HTTP `tools/list` exposed empty schemas**: the HTTP MCP server
  now emits the full input schema, description, and risk annotations for every
  tool (previously it sent `{"properties": {}}` with `fn.__doc__` which was
  `None` for the lambda-registered tools, making HTTP clients blind to tool
  parameters).
- **Legacy CLI was dead**: `td-mcp-live` subcommands (`status`, `create`,
  `set`, `exec`, `list`, `batch`, `read`, `scan`, `find`, ...) were
  commented out, so the README quick-start commands did nothing. Reimplemented
  a typed argparse CLI that dispatches to the bridge client.

### Changed
- **Unified tool metadata**: both the stdio and Streamable-HTTP servers now
  derive their tool descriptions, input schemas, and risk annotations from a
  single `_TOOL_META` source, eliminating duplicated schema definitions.
- **Risk classification** (`td_mcp/tools/risk.py`) now covers all 39 live
  bridge tools, so `READ_ONLY` / `WRITE_ADDITIVE` / `DESTRUCTIVE` hints are
  consistent across the live server, the offline server, and any bridge policy
  enforcement (`TD_MCP_MAX_RISK`, `TD_MCP_ALLOW_EXEC`, `TD_BUILDER_LIVE_READONLY`).

### Cleaned
- Removed a duplicated `_json_loads` helper in `server_offline.py`.

---

## [1.1.0] - 2026-07-09

### Added
- **Live Project RAG (Network Context Scanner)**: Implemented `_do_scan_network()` in `bridge/td_mcp_bridge.py`. Recursively walks the active TouchDesigner COMP graph up to configurable `depth`, collecting node paths, types, wiring inputs, non-default parameters, and cook errors into a structured JSON snapshot.
- **Agent Context Injection**: Updated `bridge/td_mcp_agent.py` to automatically call `scan_network` at the start of every chat session and inject the live topology into the LLM system prompt so the model is always aware of what already exists in the project.
- **`scan_network` MCP Tool**: Registered the new tool in `td_mcp/server_live.py` (`list_tools` schema + `call_tool` handler + `TDClient.scan_network` method).
- **Natively Embedded Chat UI Panel** (`bridge/chat_ui.html`): A self-contained, zero-dependency HTML/CSS/JS chat interface with a premium glassmorphic dark-mode design. Features include: provider selector (Ollama / Gemini / OpenAI), persistent credential storage via `localStorage`, a live network node sidebar panel, health status indicator, and a full multi-step autonomous agent loop communicating directly with the bridge.
- **Chat UI served on `GET /`**: Dropped a Web Render TOP at `http://localhost:9980/` loads the chat panel directly inside TouchDesigner.
- **Pure-Python WebSocket Bi-directional Streaming**: Implemented RFC 6455 WebSocket support in `bridge/td_mcp_bridge.py` with no external dependencies — includes handshake key derivation (`_ws_handshake_key`), frame encoder (`_ws_make_frame`), frame decoder (`_ws_read_frame`), token-authenticated connection upgrade, and a tool-dispatch loop (`_handle_websocket`) that streams results back in real time.
- **New Test Module** (`tests/test_scan_ws.py`): 6 tests covering WebSocket RFC 6455 handshake key derivation, small/medium payload frame roundtrips, close frame encoding, offline `scan_network` graceful failure, and agent tool schema validation.

### Changed
- Bridge `do_OPTIONS` now returns proper CORS headers for cross-origin access from the Chat UI.
- `Access-Control-Allow-Origin` updated to `*` to allow the embedded HTML panel to communicate freely on localhost.

---

## [1.0.0] - 2026-07-09

This is the initial release of the TouchDesigner MCP (TD_MCP) "Ultimate" Super-Server, uniting the best features of the TouchDesigner MCP ecosystem (including parallel-RAG search, live network control, and autonomous in-app building) under a clean, unified structure.

### Added
- **Live stdio MCP Server**: Graduated `td_mcp.server_live` from a CLI client to a fully compliant standard stdio Model Context Protocol (MCP) server (via `--mcp` flag) exposing 16 live control tools (e.g. `create_node`, `set_parameters`, `get_errors`, `capture_viewport`).
- **Autonomous in-TouchDesigner Agent**: Created `bridge/td_mcp_agent.py`, a zero-dependency OpenAI-compatible agent script designed to run natively inside TouchDesigner Text DATs. It supports 13 function-calling tool schemas and interacts with local Ollama, Gemini, or OpenAI to edit networks autonomously.
- **Spatial Context Pointers**: Implemented `*here` and `*this` path resolution inside the TouchDesigner bridge `bridge/td_mcp_bridge.py` utilizing the TouchDesigner Python `ui` API at runtime.
- **New Unit Tests**:
  - `tests/test_live_server.py` to verify the live server's wiring and tool registration.
  - `tests/test_agent.py` to assert autonomous agent schemas compile and fail gracefully when offline.
- **Documentation**: Created `HOW_TO_USE.md` mapping out installation, starting the bridge, configuring Claude/Cursor clients, and using the autonomous agent loop.

### Fixed
- **Setuptools Packaging**: Fixed a build error by removing the non-existent `"td_mcp.bridge"` from `packages` in `pyproject.toml` (since `bridge` is a top-level workspace directory and not a python subpackage).

### Changed
- **Dependencies**: Upgraded all project dependencies in `uv.lock` to their latest compatible versions via `uv lock --upgrade`.
- **Test Suite**: Expanded test coverage to 30 tests (all passing).
- **README Updates**: Updated `README.md` to document the unified layout, live MCP server setups, new tools, and updated recall metrics (0.966).
