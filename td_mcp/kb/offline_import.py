"""Local OfflineHelp ingestion (TD's bundled docs mirror).

TouchDesigner ships a complete offline copy of docs.derivative.ca under
``<TD_INSTALL>/Samples/Learn/OfflineHelp/https.docs.derivative.ca``. This
importer walks that tree and appends plain-text wiki pages to the KB chunks —
zero network access, always available to TD owners.

Run:
    uv run python -m td_mcp.kb.offline_import --root "C:/Program Files/Derivative/TouchDesigner/Samples/Learn/OfflineHelp/https.docs.derivative.ca"

Auto-detects the root from ``TD_MCP_OFFLINE_HELP`` or the default install
path when possible.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "chunks.jsonl")

DEFAULT_ROOTS = [
    os.environ.get("TD_MCP_OFFLINE_HELP", ""),
    r"C:\Program Files\Derivative\TouchDesigner\Samples\Learn\OfflineHelp\https.docs.derivative.ca",
    r"C:\Program Files\Derivative\TouchDesigner.099\Samples\Learn\OfflineHelp\https.docs.derivative.ca",
    r"C:\Program Files\Derivative\TouchDesigner\OfflineHelp\https.docs.derivative.ca",
]

_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.I | re.S)
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _find_root(explicit=None):
    for root in ([explicit] if explicit else DEFAULT_ROOTS):
        if root and os.path.isdir(root):
            return root
    return None


def _html_to_text(html):
    text = _SCRIPT_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    return re.sub(r"\s+", " ", text).strip()


def _page_title(html, path):
    m = _TITLE_RE.search(html)
    if m:
        return m.group(1).strip().split(" - ")[0].strip()
    return path.stem.replace("_", " ")


def _family_from_path(path, root):
    rel = os.path.relpath(str(path), root).lower()
    for fam in ("top", "chop", "sop", "dat", "mat", "comp", "pop"):
        if f"/{fam}/" in rel or f"_{fam}/" in rel or f"/{fam}s/" in rel:
            return fam.upper()
    return None


def _already_imported(chunk_id):
    if not os.path.exists(OUT):
        return False
    prefix = f'{{"id":"{chunk_id}"'
    with open(OUT, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith(prefix):
                return True
    return False


def import_offline(root=None, limit=None, verbose=True):
    root = _find_root(root)
    if not root:
        print("OfflineHelp root not found. Set TD_MCP_OFFLINE_HELP or pass --root.")
        return 0
    count = 0
    for path in sorted(Path(root).rglob("*.html")):
        try:
            html = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        title = _page_title(html, path)
        if not title or len(title) < 3:
            continue
        chunk_id = "offline_" + re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
        if _already_imported(chunk_id):
            continue
        family = _family_from_path(path, root)
        text = _html_to_text(html)[:4000]
        if len(text) < 80:
            continue
        with open(OUT, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "id": chunk_id,
                "title": title,
                "family": family,
                "category": "operator" if family else "concept",
                "source": "offline-help",
                "url": "https://docs.derivative.ca/" + title.replace(" ", "_"),
                "min_version": "all",
                "tags": [family.lower()] if family else ["wiki"],
                "aliases": [],
                "text": text,
            }, ensure_ascii=False) + "\n")
        count += 1
        if verbose and count % 50 == 0:
            print(f"  {count} pages imported...", flush=True)
        if limit and count >= limit:
            break
    if verbose:
        print(f"Imported {count} pages from {root} -> {OUT}")
    return count


def main():
    ap = argparse.ArgumentParser(description="Import TouchDesigner OfflineHelp pages.")
    ap.add_argument("--root", help="OfflineHelp root (https.docs.derivative.ca)")
    ap.add_argument("--limit", type=int, help="cap the number of pages")
    args = ap.parse_args()
    n = import_offline(args.root, args.limit)
    return 0 if n >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
