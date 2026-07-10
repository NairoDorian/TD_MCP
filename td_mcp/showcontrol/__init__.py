"""Show-control planning (Art-Net / sACN / OSC / MIDI / timecode).

Reimplements familienak's show-control service *logic* as pure-Python builders
that emit the config an agent feeds to TouchDesigner (or exports for a lighting
console). No network sockets are opened here — this is the deterministic,
testable planning layer; the TD-side bridge turns these into real CHOP/DAT
outputs. Hazardous physical dispatch is intentionally out of scope.
"""

# DMX: one universe = 512 channels.
DMX_UNIVERSE_CHANNELS = 512


def artnet_output(universe, net=0, subnet=0, name="artnet_out", description=""):
    return {
        "protocol": "art-net",
        "name": name,
        "description": description,
        "universe": universe,
        "net": net,
        "subnet": subnet,
        "port": 6454,
        "channels": DMX_UNIVERSE_CHANNELS,
    }


def sacn_output(universe, priority=100, name="sacn_out", description=""):
    return {
        "protocol": "sacn",
        "name": name,
        "description": description,
        "universe": universe,
        "priority": priority,
        "port": 5568,
        "channels": DMX_UNIVERSE_CHANNELS,
    }


def osc_receiver(port=8000, host="0.0.0.0", name="osc_in"):
    return {"protocol": "osc", "role": "receiver", "name": name,
            "host": host, "port": port}


def osc_sender(host="127.0.0.1", port=8000, name="osc_out"):
    return {"protocol": "osc", "role": "sender", "name": name,
            "host": host, "port": port}


def midi_in(device="default", channel=1, name="midi_in"):
    return {"protocol": "midi", "role": "in", "name": name,
            "device": device, "channel": channel}


def timecode_setup(kind="ltc", fps=30):
    """kind: 'ltc' (Linear Timecode) or 'mtc' (MIDI Timecode)."""
    kind = kind.lower()
    if kind not in ("ltc", "mtc"):
        raise ValueError("kind must be 'ltc' or 'mtc'")
    op = "LtcIn" if kind == "ltc" else "MtcIn"
    return {"protocol": "timecode", "kind": kind, "operator": op,
            "fps": fps, "channel": 0 if kind == "ltc" else 1}


def build_show_plan(outputs):
    """Given a list of output configs, return a consolidated plan + the total
    universe span used."""
    universes = sorted({o.get("universe", 0) for o in outputs
                        if "universe" in o})
    return {
        "outputs": outputs,
        "universe_count": (max(universes) + 1) if universes else 0,
        "universes": universes,
    }


# --- Media-server connectors (TD-Codex media-server integration) -----------
# Recommended transport + default port for each popular media server. The TD
# side turns these into OSC/NDI/Spout/Syphon CHOP/TOP outputs; we only plan.
MEDIA_SERVERS = {
    "millumin":   {"transport": "osc", "host": "127.0.0.1", "port": 8000},
    "resolume":   {"transport": "osc", "host": "127.0.0.1", "port": 7000},
    "notch":      {"transport": "spout", "host": "127.0.0.1", "port": 0},
    "disguise":   {"transport": "ndi", "host": "127.0.0.1", "port": 0},
    "qlab":       {"transport": "osc", "host": "127.0.0.1", "port": 53000},
    "madmapper":  {"transport": "osc", "host": "127.0.0.1", "port": 8000},
}


def media_server(name, host=None, port=None):
    """Plan a connector to a media server (Millumin / Resolume / Notch /
    Disguise / QLab / MadMapper). Returns the transport + endpoint to wire in
    TD, or an error listing valid names if unknown."""
    key = (name or "").lower()
    if key not in MEDIA_SERVERS:
        return {"error": f"unknown media server {name!r}",
                "available": sorted(MEDIA_SERVERS)}
    base = dict(MEDIA_SERVERS[key])
    if host:
        base["host"] = host
    if port:
        base["port"] = port
    base["name"] = key
    base["operator"] = {
        "osc": "OSC Out DAT", "ndi": "NDI Out TOP",
        "spout": "Spout Out TOP", "syphon": "Syphon Out TOP",
    }.get(base["transport"], "OSC Out DAT")
    return base
