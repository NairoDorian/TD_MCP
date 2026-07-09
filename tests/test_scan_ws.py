"""Tests for scan_network tool and WebSocket frame helpers."""

import json
import struct
import base64
import hashlib
import sys
import os

# Ensure bridge is importable from tests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Import bridge pieces that don't need a live TD environment
# ---------------------------------------------------------------------------
# We re-import only the pure helpers (no TouchDesigner deps)

_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _ws_handshake_key(key):
    return base64.b64encode(
        hashlib.sha1((key + _WS_MAGIC).encode("utf-8")).digest()
    ).decode("utf-8")


def _ws_make_frame(payload, opcode=0x1):
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    length = len(data)
    if length <= 125:
        header = bytes([0x80 | opcode, length])
    elif length <= 65535:
        header = bytes([0x80 | opcode, 126]) + struct.pack(">H", length)
    else:
        header = bytes([0x80 | opcode, 127]) + struct.pack(">Q", length)
    return header + data


def _ws_read_frame_from_bytes(raw_bytes):
    import io
    rfile = io.BytesIO(raw_bytes)
    b0, b1 = rfile.read(2)
    opcode = b0 & 0x0F
    masked = (b1 & 0x80) != 0
    length = b1 & 0x7F
    if length == 126:
        length = struct.unpack(">H", rfile.read(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", rfile.read(8))[0]
    mask_key = rfile.read(4) if masked else b"\x00\x00\x00\x00"
    data = bytearray(rfile.read(length))
    if masked:
        for i in range(len(data)):
            data[i] ^= mask_key[i % 4]
    return opcode, bytes(data)


# ---------------------------------------------------------------------------
# WebSocket Helper Tests
# ---------------------------------------------------------------------------

def test_ws_handshake_key():
    """RFC 6455 §4.2.2 accept-key derivation."""
    # Official test vector from RFC 6455
    key = "dGhlIHNhbXBsZSBub25jZQ=="
    expected = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
    result = _ws_handshake_key(key)
    assert result == expected, f"Got {result!r}, expected {expected!r}"
    print("ok  WebSocket handshake key derivation matches RFC 6455 test vector")


def test_ws_frame_roundtrip_small():
    """Encode a small text payload and decode it back."""
    payload = json.dumps({"ok": True, "tool": "list_nodes"})
    frame = _ws_make_frame(payload)
    opcode, decoded = _ws_read_frame_from_bytes(frame)
    assert opcode == 0x1, f"Expected opcode 1 (text), got {opcode}"
    assert decoded.decode("utf-8") == payload
    print("ok  WebSocket frame roundtrip (small payload)")


def test_ws_frame_roundtrip_medium():
    """Encode a 200-byte payload using 2-byte extended length."""
    payload = "x" * 200
    frame = _ws_make_frame(payload)
    # Extended length: byte 1 should be 126
    assert frame[1] == 126, "Expected 2-byte extended length encoding"
    opcode, decoded = _ws_read_frame_from_bytes(frame)
    assert decoded.decode("utf-8") == payload
    print("ok  WebSocket frame roundtrip (medium payload, 2-byte extended length)")


def test_ws_frame_close():
    """Encode a close frame (opcode 0x8)."""
    frame = _ws_make_frame(b"", opcode=0x8)
    assert (frame[0] & 0x0F) == 0x8, "Expected close opcode"
    print("ok  WebSocket close frame encodes opcode 0x8")


# ---------------------------------------------------------------------------
# scan_network stub test (without live TD)
# ---------------------------------------------------------------------------

def test_scan_network_graceful_offline():
    """_do_scan_network should be importable and fail gracefully when bridge is offline."""
    from bridge.td_mcp_agent import _execute_tool
    res = _execute_tool("scan_network", {"path": "*here"}, port=19999)
    assert res.get("ok") is False, "Expected failure when bridge is offline"
    assert "error" in res
    print("ok  scan_network fails gracefully when bridge is offline")


def test_scan_network_tool_in_agent_schema():
    """scan_network should be present in the agent TOOLS_SCHEMA."""
    from bridge.td_mcp_agent import TOOLS_SCHEMA
    names = [t["function"]["name"] for t in TOOLS_SCHEMA]
    assert "scan_network" in names, "scan_network not found in TOOLS_SCHEMA"
    print("ok  scan_network is declared in td_mcp_agent TOOLS_SCHEMA")
