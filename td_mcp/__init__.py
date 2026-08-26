import os as _os
import re as _re

from td_mcp.rag.retriever import Retriever, load_chunks, build_retriever, tokenize


def _source_version():
    """The version in ``pyproject.toml``, when running from a source checkout.

    Installed distribution metadata is written once, at install time, and an
    editable install does not refresh it when the source version is bumped. The
    two then disagree, and the disagreement is not harmless: the live server
    compares ``__version__`` against the bridge's stamped ``BRIDGE_VERSION``
    and warns that the bridge is stale, telling the user to re-paste a bridge
    that is in fact correct.

    In a checkout the source is the truth; in an installed package there is no
    ``pyproject.toml`` beside the package and metadata is the truth.
    """
    path = _os.path.join(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__))), "pyproject.toml")
    try:
        with open(path, encoding="utf-8") as fh:
            match = _re.search(r'^version\s*=\s*"([^"]+)"', fh.read(), _re.M)
    except OSError:
        return None
    return match.group(1) if match else None


def _resolve_version():
    source = _source_version()
    if source:
        return source
    try:
        from importlib.metadata import version as _pkg_version

        return _pkg_version("td-mcp")
    except Exception:  # pragma: no cover - importlib may be absent inside TD
        return "0.0.0"


__version__ = _resolve_version()

__all__ = ["Retriever", "load_chunks", "build_retriever", "tokenize", "__version__"]
