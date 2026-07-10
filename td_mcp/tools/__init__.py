"""Cross-cutting tool helpers for the td-mcp live/offline servers.

Holds the pure, dependency-free utilities shared across servers:
  * risk   - tool risk classification (READ_ONLY / WRITE_ADDITIVE / WRITE_CHECKPOINT / DESTRUCTIVE)
  * recovery - Embody-style self-healing hints for every common bridge error
  * logs   - token-efficient ring-buffer logs piggybacked on tool results
  * layout - Embody-style deterministic placement hygiene (overlap / origin / dock)
"""
