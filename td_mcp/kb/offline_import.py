"""Local OfflineHelp ingestion — thin wrapper over the structured pipeline.

Historically this module appended naively-stripped pages to ``chunks.jsonl``
*after* the build, which invalidated ``kb_receipt.json`` (the retriever then
refused the whole KB as corrupted) and truncated every page to one 4,000-char
blob. Both problems are gone: ingestion now runs **inside** ``build_kb`` via
:mod:`td_mcp.kb.wiki_ingest` (section-level chunks, citations, param tags),
so the receipt is regenerated with the wiki content included.

Run either of:
    uv run python -m td_mcp.kb.build_kb --wiki-root "<path to wiki mirror>"
    uv run python -m td_mcp.kb.offline_import --root "<path>"   (same thing)

Roots are auto-detected from ``TD_MCP_WIKI_ROOT``, the TouchDesigner install
OfflineHelp tree, or a sibling "TD HELP" dump — see ``wiki_ingest.find_root``.
"""

from __future__ import annotations

import argparse

from td_mcp.kb import build_kb, wiki_ingest


def import_offline(root=None, limit=None, verbose=True):
    """Rebuild the KB with the wiki mirror included. Returns page count."""
    found = wiki_ingest.find_root(root)
    if not found:
        if verbose:
            print("OfflineHelp root not found. Set TD_MCP_WIKI_ROOT or pass --root.")
        return 0
    argv = ["--wiki-root", found]
    if limit:
        argv += ["--wiki-limit", str(limit)]
    build_kb.main(argv)
    return 1


def main():
    ap = argparse.ArgumentParser(
        description="Rebuild the KB including a local TouchDesigner wiki mirror.")
    ap.add_argument("--root", help="wiki mirror root (folder of .htm pages)")
    ap.add_argument("--limit", type=int, help="cap the number of pages")
    args = ap.parse_args()
    return 0 if import_offline(args.root, args.limit) else 1


if __name__ == "__main__":
    raise SystemExit(main())
