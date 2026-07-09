"""LED / pixel mapping (wall / strip / voxel grid + DMX channel export).

Reimplements familienak's led-mapping service *math* as pure Python: pixel
counts, universe allocation (3 DMX channels per RGB pixel, 512 per universe),
layout coordinates and a DMX channel-map export table. Deterministic and
unit-testable; the TD side turns these into real pixel-mapping DATs.
"""

DMX_CHANNELS_PER_PIXEL = 3
UNIVERSE_SIZE = 512


def _universe_count(pixels):
    return (pixels * DMX_CHANNELS_PER_PIXEL + UNIVERSE_SIZE - 1) // UNIVERSE_SIZE


def led_wall(width, height, led_type="rgb", layout="serpentine",
             name="led_wall", spacing=10):
    """A rectangular LED wall. Returns pixel geometry + universe math."""
    total = width * height
    coords = _grid_coords(width, height, layout, spacing)
    return {
        "type": "wall",
        "name": name,
        "width": width,
        "height": height,
        "led_type": led_type,
        "layout": layout,
        "total_pixels": total,
        "universe_count": _universe_count(total),
        "coords": coords,
    }


def led_strip(length, led_type="rgb", name="led_strip", spacing=10):
    coords = [[i * spacing, 0] for i in range(length)]
    return {
        "type": "strip",
        "name": name,
        "length": length,
        "led_type": led_type,
        "total_pixels": length,
        "universe_count": _universe_count(length),
        "coords": coords,
    }


def voxel_grid(width, height, depth, led_type="rgb", name="voxel_grid",
               spacing=10):
    total = width * height * depth
    coords = [[x * spacing, y * spacing, z * spacing]
              for x in range(width) for y in range(height) for z in range(depth)]
    return {
        "type": "voxel",
        "name": name,
        "width": width, "height": height, "depth": depth,
        "led_type": led_type,
        "total_pixels": total,
        "universe_count": _universe_count(total),
        "coords": coords,
    }


def _grid_coords(width, height, layout, spacing):
    coords = []
    for y in range(height):
        row = range(width) if (layout != "serpentine" or y % 2 == 0) else range(width - 1, -1, -1)
        for x in row:
            coords.append([x * spacing, y * spacing])
    return coords


def dmx_channel_map(mapping, start_universe=0):
    """Export a pixel -> (universe, channel) table for a wall/strip/voxel.

    Returns a list of rows: {"pixel": i, "x", "y", "universe", "channel_r",
    "channel_g", "channel_b"}.
    """
    rows = []
    p = 0
    u = start_universe
    ch = 1  # 1-based DMX channel
    for i, c in enumerate(mapping.get("coords", [])):
        if ch + DMX_CHANNELS_PER_PIXEL - 1 > UNIVERSE_SIZE:
            u += 1
            ch = 1
        rows.append({
            "pixel": i,
            "x": c[0], "y": (c[1] if len(c) > 1 else 0),
            "universe": u,
            "channel_r": ch, "channel_g": ch + 1, "channel_b": ch + 2,
        })
        p += 1
        ch += DMX_CHANNELS_PER_PIXEL
    return rows
