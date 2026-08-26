"""Pin the stdio streams to UTF-8 for MCP's JSON-RPC transport.

MCP clients send UTF-8 JSON-RPC. Python does **not** read it that way by
default: ``sys.stdin``/``sys.stdout`` are bound to the *locale* codepage, which
is CP1252 on a Western Windows install and US-ASCII on a GUI-launched macOS
process. The failure modes are quiet and awful:

* A message containing an em dash arrives with the wrong character count, so a
  content-length framing check disagrees with the sender (Embody measured a
  5-character probe arriving as 7).
* A byte the codepage cannot decode raises inside the read loop and kills the
  transport outright, which reads to the user as "the MCP server crashed".

Two rules, both learned the hard way (Embody v6.0.264):

1. **Pin from ``main()``, never at import.** Rebinding a process's standard
   streams is a global side effect. The in-TD tests exec this package inside
   TouchDesigner, where an import-time pin would mutate TD's own streams and
   corrupt the Textport for everything else in the session.
2. **Never let the pin itself be fatal.** A stream that has already been
   replaced (a pytest capture, a pipe wrapper, an embedded interpreter) may not
   expose ``reconfigure``; failing to pin is worth reporting, not dying over.
"""

from __future__ import annotations

import sys
from typing import List

#: Streams worth pinning, in the order they are attempted.
_STREAMS = ("stdin", "stdout", "stderr")


def pin_stdio_utf8(errors: str = "replace") -> List[str]:
    """Rebind stdin/stdout/stderr to UTF-8. Returns the streams pinned.

    ``errors='replace'`` on output keeps a single unencodable character from
    taking down a whole response; stdin is pinned strictly so malformed input
    is reported rather than silently mangled into something that parses.
    """
    pinned: List[str] = []
    for name in _STREAMS:
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8",
                        errors="strict" if name == "stdin" else errors)
        except (ValueError, OSError, AttributeError):
            # Already-detached or non-text streams: leave them as they are.
            continue
        pinned.append(name)
    return pinned
