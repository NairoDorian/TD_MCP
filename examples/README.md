# Examples — template networks, built the way an agent should

Each `*.json` here is a network spec in the format `td_validate_build` checks
offline and `build_network` builds live: `{"operators": [{"name", "type",
"parameters", "inputs", "flags"}], "connections": []}`. Parameter names are
TouchDesigner's own (checked against the live-probed catalog in CI).

## Run the demonstration

```bash
python scripts/td_live.py launch      # TouchDesigner on TD_MCP_TESTS/TEST-01
python scripts/demo_build.py          # builds feedback_trails into /project1/feedback_trails
```

`demo_build.py` drives TD_MCP over MCP exactly as an agent does:

| Stage | Tool | Evidence |
|---|---|---|
| plan | `td_validate_build` (offline, strict) | 0 findings |
| build | `build_network` | every operator, parameter, wire and flag in one call (one Ctrl+Z) |
| verify | `get_errors` recursive | 0 errors |
| verify | `capture_viewport` on `null_out` | not black, not flat; PNG in `.td_mcp/demo/` |
| verify | `observe` | animating across real frames |
| verify | `get_performance samples=10` | median cost per frame |
| show | `show_in_editor` | the user's editor moves to the output, selected and framed, with a status-bar message |
| show | `screenshot path=out1` | the output's viewer window, opened and captured; the model sees it |

Measured on TouchDesigner 2025.33230 (2026-11-23): 11 operators and 10 wires in
~350 ms, 0 errors, animating (max tile delta ~15), about 3-4% of a 60 fps frame. The network is left in TouchDesigner for you to open; pass `--cleanup`
to remove it.

## `feedback_trails.json`

An LFO (`lfo_drift` -> `null_ctrl`) drives the 4D offset of a colour noise
field; a Feedback TOP whose `top` is `null_out` reads the previous frame back,
a Transform TOP zooms it 1%, a Level TOP fades it to 93%, and a Composite TOP
(`maximum`) lays the fresh noise over the fading trails. `out1` (Out TOP,
label `trails`) and `out_ctrl` (Out CHOP, label `drift`) give the COMP real
output connectors, so it can be dropped into a project and wired up. It exercises a
CHOP-to-parameter expression, a tuplet (`resolution`), a menu token
(`operand`), a feedback reference and the display flag.

## Adding an example

1. Write the spec; name operators for their job; end the chain in `null_out`
   (or set `"output"`).
2. `python -m pytest tests/test_examples.py` -- it must validate with zero
   findings in strict mode.
3. Build it live with `python scripts/demo_build.py examples/<name>.json` and
   look at the capture before calling it done.
