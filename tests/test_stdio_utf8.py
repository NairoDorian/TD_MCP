"""UTF-8 stdio pinning (Wave 12, Embody v6.0.264).

Python binds the standard streams to the *locale* codepage, but MCP clients
send UTF-8 JSON-RPC. On a Western Windows install that is CP1252, so an em
dash arrives with the wrong character count and an undecodable byte kills the
read loop. The pin must happen in ``main()`` and must never be fatal itself.
"""

import io
import sys

from td_mcp.util.stdio import pin_stdio_utf8


class _Reconfigurable:
    def __init__(self):
        self.encoding = "cp1252"
        self.errors = "strict"

    def reconfigure(self, encoding=None, errors=None):
        if encoding:
            self.encoding = encoding
        if errors:
            self.errors = errors


class _Detached:
    """A stream that has already been replaced (pytest capture, a raw pipe)."""

    def reconfigure(self, **kwargs):
        raise ValueError("underlying buffer has been detached")


def test_pins_all_three_streams(monkeypatch):
    streams = {n: _Reconfigurable() for n in ("stdin", "stdout", "stderr")}
    for name, stream in streams.items():
        monkeypatch.setattr(sys, name, stream)
    assert set(pin_stdio_utf8()) == {"stdin", "stdout", "stderr"}
    assert all(s.encoding == "utf-8" for s in streams.values())


def test_stdin_is_strict_but_output_is_forgiving(monkeypatch):
    """A single unencodable character must not take down a whole response,
    but malformed *input* should be reported rather than silently mangled."""
    streams = {n: _Reconfigurable() for n in ("stdin", "stdout", "stderr")}
    for name, stream in streams.items():
        monkeypatch.setattr(sys, name, stream)
    pin_stdio_utf8()
    assert streams["stdin"].errors == "strict"
    assert streams["stdout"].errors == "replace"


def test_a_detached_stream_is_skipped_not_fatal(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _Detached())
    monkeypatch.setattr(sys, "stdout", _Reconfigurable())
    monkeypatch.setattr(sys, "stderr", _Reconfigurable())
    assert pin_stdio_utf8() == ["stdout", "stderr"]


def test_a_stream_without_reconfigure_is_skipped(monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.BytesIO())
    monkeypatch.setattr(sys, "stdin", _Reconfigurable())
    monkeypatch.setattr(sys, "stderr", _Reconfigurable())
    assert "stdout" not in pin_stdio_utf8()


def test_missing_stream_is_skipped(monkeypatch):
    monkeypatch.setattr(sys, "stdin", None)
    monkeypatch.setattr(sys, "stdout", _Reconfigurable())
    monkeypatch.setattr(sys, "stderr", _Reconfigurable())
    assert pin_stdio_utf8() == ["stdout", "stderr"]


def test_pin_is_not_applied_at_import():
    """Importing the package must not mutate the process's streams: the in-TD
    tests exec it inside TouchDesigner, whose Textport streams are shared."""
    src = open("td_mcp/util/stdio.py", encoding="utf-8").read()
    module_level = [ln for ln in src.splitlines()
                    if ln.startswith("pin_stdio_utf8(")]
    assert module_level == []
    for path in ("td_mcp/server_live.py", "td_mcp/server_offline.py"):
        text = open(path, encoding="utf-8").read()
        assert "pin_stdio_utf8()" in text, path
        # The call must sit inside main(), i.e. indented.
        assert "\npin_stdio_utf8()" not in text, path
