"""Tests for show-control + LED mapping modules."""

from td_mcp import showcontrol as sc
from td_mcp import led_mapping as led


def test_artnet_sacn():
    a = sc.artnet_output(1, name="a")
    assert a["protocol"] == "art-net" and a["universe"] == 1
    assert a["channels"] == 512
    s = sc.sacn_output(2)
    assert s["protocol"] == "sacn" and s["universe"] == 2


def test_build_show_plan():
    plan = sc.build_show_plan([sc.artnet_output(1), sc.sacn_output(3), sc.osc_receiver(9000)])
    assert plan["universe_count"] == 4
    assert len(plan["universes"]) == 2


def test_timecode():
    ltc = sc.timecode_setup("ltc")
    assert ltc["kind"] == "ltc"
    mtc = sc.timecode_setup("mtc")
    assert mtc["kind"] == "mtc"


def test_led_wall_math():
    w = led.led_wall(16, 9, name="w")
    assert w["total_pixels"] == 144
    # 144 px * 3 ch = 432 -> 1 universe (512)
    assert w["universe_count"] == 1
    assert len(w["coords"]) == 144


def test_led_strip_universes():
    s = led.led_strip(600)
    # 600*3 = 1800 ch -> ceil(1800/512) = 4 universes
    assert s["universe_count"] == 4


def test_voxel_grid():
    v = led.voxel_grid(4, 4, 4)
    assert v["total_pixels"] == 64


def test_dmx_channel_map():
    w = led.led_wall(4, 4)
    table = led.dmx_channel_map(w)
    assert len(table) == 16
    # pixel 0 -> universe 0, channels 1/2/3
    assert table[0]["universe"] == 0
    assert table[0]["channel_r"] == 1
    assert table[0]["channel_g"] == 2
    assert table[0]["channel_b"] == 3


def test_led_map_tool():
    from td_mcp import server_offline
    out = server_offline.td_led_map('{"kind":"wall","width":8,"height":8}')
    assert '"total_pixels": 64' in out
    assert "dmx_channel_map" in out
