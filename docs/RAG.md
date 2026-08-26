# Retrieval

**1.000 recall @k=5** with the full knowledge base, **0.991** curated-only. No
API keys, no network, no GPU required.

The design goal is narrow and worth stating: **an agent must never fabricate a
parameter or operator name.** Everything below serves that.

---

## The knowledge base

Two layers, both local.

| Layer | Chunks | Source | Build time |
|---|---|---|---|
| **Curated** | 1,091 | Hand-authored + merged operator corpus, ships with the repo | ~2 s |
| **Wiki** | +8,511 | The offline `docs.derivative.ca` mirror TouchDesigner installs | ~17 s |

```bash
uv run python -m td_mcp.kb.build_kb                       # curated — required
uv run python -m td_mcp.kb.wiki_ingest --offline-help \
  "C:/Program Files/Derivative/TouchDesigner/Config/OfflineHelp"   # → 9,602
```

`chunks.jsonl` is gitignored and must be built. Without it every doc tool
answers "documentation not indexed" — the retriever degrades gracefully rather
than crashing, which is correct and also easy to miss.

### Why ingest the mirror instead of crawling

The curated corpus is hallucination-free but a *sample*. The obvious fix is to
consume another project's crawled index (still supported —
`TD_MCP_WIKI_DB` fuses `touchdesigner-wiki-rag`'s SQLite), but that makes
wiki-wide coverage conditional on a second project being installed and crawled.

TouchDesigner already ships the whole thing. `kb/wiki_ingest.py` parses that
mirror directly into first-class KB chunks: no network, no extra dependency, and
the chunks live in the same index — and the same integrity receipt — as
everything else rather than in a bolted-on fusion source.

It parses each page's `RLCONF` metadata block, `og:url` and
`article:modified_time`, then walks the body with a subtree-skipping
`HTMLParser` that **drops** navigation chrome (TOC, edit sections, catlinks,
tooltips) instead of flattening it into the text. One chunk per section, so a
hit points at a section rather than a page.

---

## How a query is answered

```
query
 ├─ BM25 (global + per-source)          ← always; the tuned default path
 ├─ Dense MiniLM + HyDE                  ← opt-in: [rag] extra + TD_MCP_DENSE=1
 ├─ CrossEncoder rerank                  ← opt-in
 └─ Remote/SQLite fusion                 ← opt-in: TD_MCP_WIKI_DB, or an MCP doc server
                    ↓
        Reciprocal Rank Fusion
                    ↓
   operator-intent boost · plural stemming · family boost
                    ↓
        score floor → abstain rather than guess
```

Dense retrieval is **off by default**. The default path is pure-Python BM25 with
operator-aware boosting, and it is what the 1.000 figure is measured on — not a
degraded fallback.

The score floor matters as much as the ranking: below it the server says it does
not know. A confident wrong parameter name costs the user a debugging session; a
"not in the corpus" costs one more question.

---

## Citations

Every hit carries `Page § Section` and a deep-link URL.

```
Noise TOP § Parameters — https://docs.derivative.ca/Noise_TOP#Parameters
```

Assert with the citation or say you are guessing. See
[AGENT_GUIDE.md](AGENT_GUIDE.md#trust-and-citation).

---

## Family disambiguation

**"Noise" is four different operators** — Noise TOP, Noise CHOP, Noise SOP,
Noise POP — with different parameters and different families. An agent that
flattens them will confidently set a parameter that does not exist.

Any result whose operator has same-named siblings carries a warning naming them.
`td_docs_family` lists a family; `td_docs_compare` diffs two operators'
parameters directly.

---

## Version awareness

TouchDesigner operators appear, change and disappear across builds. POPs are
2025+. `td_docs_version_compat` gives per-operator `addedIn` / `removedIn` /
`changedIn`, and answers carry a freshness banner when the corpus was authored
against a different build than the one you are running — the live bridge reports
`app.build`, and `compat.check_compat` compares them.

`app.version` still reports the legacy generation (`"099"`); the public
`year.build` identifier is on `app.build`. Version comparisons need the latter.

---

## Tiering

| Tier | Tool | For |
|---|---|---|
| **L0** | `td_docs_browse` | Orient: a section/title map of the KB |
| **L1** | `td_docs_search`, `td_docs_operator`, `td_docs_parameter` | The answer |
| **L2** | `td_docs_read_page` | One page in full, optionally one section |

Start at L1. Drop to L0 when you do not know what you are looking for; go to L2
when a snippet is not enough.

---

## Evaluation

```bash
uv run python -m td_mcp.rag.eval                    # recall@k / MRR / nDCG
uv run python -m td_mcp.rag.eval --name-integrity   # no fabricated names
uv run python -m td_mcp.kb.build_kb --receipt       # integrity receipt
```

The **name-integrity gate** is the one that matters most: it fails if any answer
contains an operator name absent from the corpus. Recall measures whether the
right thing was found; name-integrity measures whether anything was invented.

Two past eval bugs, both fixed, both instructive:

- The eval **credited one expectation more than once** — a chunk and its
  `corpus_` twin both counted — which reported nDCG > 1.0 and masked real
  misses. Honest figures are 1.000 with the wiki KB and 0.991 curated-only.
- `offline_import` **appended post-build**, invalidating `kb_receipt.json`, so
  the retriever rejected the entire KB as corrupted. Ingestion now always
  rebuilds through `build_kb`.

A metric that cannot go down is not measuring anything.

---

## Memory

Retrieval answers "what does TouchDesigner do". Memory answers "what did *we*
do".

- `td_memory_save` / `td_memory_recall` — SQLite FTS5, survives restarts.
- `td_history_recall` — session memory *and* the technique store together.
- `td_activity_log` — recent tool calls.

Both feed pre-turn injection: relevant prior context is recalled before a live
turn rather than re-derived.

---

## Configuration

| Variable | Effect |
|---|---|
| `TD_MCP_DENSE=1` | Enable dense vectors + HyDE (needs `[rag]`) |
| `TD_MCP_WIKI_DB` | Fuse a `touchdesigner-wiki-rag` SQLite index |

---

## See also

- [TOOLS.md](TOOLS.md#documentation-and-retrieval) — the doc tools
- [AGENT_GUIDE.md](AGENT_GUIDE.md) — how to use them well
- [TESTING.md](TESTING.md#retrieval-evaluation) — the eval harness
