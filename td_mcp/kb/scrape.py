"""Scrape docs.derivative.ca into KB chunks (optional deps: requests, beautifulsoup4).

Run:  uv run python -m td_mcp.kb.scrape
Then: uv run python -m td_mcp.kb.build_index

Three stages, resumable (existing chunks are skipped):
  1. operator category index pages (TOPs/CHOPs/DATs/SOPs/POPs/MATs/COMPs)
  2. Python base classes index
  3. operator-specific Python classes (BlurTOP_Class ...)
"""

import json
import os
import re
import sys

HERE = os.path.dirname(__file__)
OUT = os.path.join(HERE, "chunks.jsonl")
BASE = "https://docs.derivative.ca"
CATEGORIES = ["TOP", "CHOP", "DAT", "SOP", "POP", "MAT", "COMP"]


def _http(url):
    try:
        import requests
    except Exception:  # noqa: BLE001
        sys.exit("install deps: uv add requests beautifulsoup4")
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.text


def _soup(html):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


def _clean(text):
    text = re.sub(r"\[edit\]|Jump to navigation|From Derivative", " ", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def _exists(chunk_id):
    if not os.path.exists(OUT):
        return False
    with open(OUT, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith(f'{{"id":"{chunk_id}"'):
                return True
    return False


def _append(chunk):
    with open(OUT, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(chunk, ensure_ascii=False) + "\n")


def scrape_operators():
    for fam in CATEGORIES:
        idx = _soup(_http(f"{BASE}/{fam}"))
        for a in idx.select("a[href*='_{fam}_']"):
            name = a.get("href", "").rsplit("/", 1)[-1].replace(".html", "")
            if not name.endswith(f"_{fam}"):
                continue
            cid = name.lower()
            if _exists(cid):
                continue
            page = _soup(_http(f"{BASE}/{name}"))
            body = page.get_text(" ", strip=True)
            _append({"id": cid, "title": name.replace(f"_{fam}", f" {fam}"),
                      "family": fam, "category": "operator", "version": "all",
                      "source": f"{BASE}/{name}", "tags": [fam.lower()],
                      "text": _clean(body)[:4000]})


def scrape_python():
    idx = _soup(_http(f"{BASE}/TouchDesigner_Python_Classes"))
    for a in idx.select("a[href*='_Class']"):
        name = a.get("href", "").rsplit("/", 1)[-1].replace(".html", "")
        if not name.endswith("_Class"):
            continue
        cid = name.lower()
        if _exists(cid):
            continue
        page = _soup(_http(f"{BASE}/{name}"))
        body = page.get_text(" ", strip=True)
        _append({"id": cid, "title": name, "family": None, "category": "python",
                  "version": "all", "source": f"{BASE}/{name}", "tags": ["python"],
                  "text": _clean(body)[:4000]})


def main():
    print("scraping operators...")
    scrape_operators()
    print("scraping python classes...")
    scrape_python()
    print(f"done -> {OUT}")


if __name__ == "__main__":
    main()
