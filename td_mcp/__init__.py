from td_mcp.rag.retriever import Retriever, load_chunks, build_retriever, tokenize

try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("td-mcp")
except Exception:  # pragma: no cover - importlib may be unavailable in some TD contexts
    __version__ = "0.0.0"

__all__ = ["Retriever", "load_chunks", "build_retriever", "tokenize", "__version__"]
