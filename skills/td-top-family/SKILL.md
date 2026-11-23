---
name: td-top-family
description: Building and debugging TOP (texture) chains in TouchDesigner via TD_MCP - generators, compositing, feedback, resolution and pixel format, and how to verify what a TOP shows.
---

# TOP chains

## Shape of a chain
- Generator -> processing -> `nullTOP` named for its role (`null_out`, `null_mask`).
  Downstream references point at the null, so inserting a step never breaks them.
- Turn the output's display flag on early (`set_flags {display: true}`) so the network
  background shows the result while you build.
- Generators: `noiseTOP`, `rampTOP`, `constantTOP`, `textTOP`, `moviefileinTOP`,
  `videodeviceinTOP`, `renderTOP`, `glslTOP`. Processing: `levelTOP`, `blurTOP`,
  `transformTOP`, `compositeTOP`, `lookupTOP`, `displaceTOP`, `feedbackTOP`.

## Parameter names agents get wrong (TD 2025.33230)
- Noise TOP: `period`, `amp`, `mono` (monochrome), `type`; resolution is the tuplet
  `resolution` -> `resolutionw` / `resolutionh` (`set_parameters {resolution: [w, h]}`).
- Level TOP: `brightness1` (not `brightness`), `clampinput` menu
  `automatic | clamp | unclamped`.
- Blur TOP amount is `size` (label "Filter Size", pixels, hard minimum 1).
- Constant TOP ships WHITE and opaque: `colorr/g/b` = 1, `alpha` = 1.
- Composite TOP default `operand` is `multiply`, not over. Under `over`, input 0 is the
  foreground and input 1 the background (verified: 50% red in 0 over blue = purple).

## Resolution and format
- Most TOPs take resolution from input 0 (`outputresolution = useinput`); set
  `outputresolution = custom` before `resolutionw/h` mean anything.
- 8-bit formats clamp to 0..1. Data, displacement or HDR work needs `format`
  `rgba16float` or `rgba32float`; `levelTOP clampinput = unclamped` keeps the range.
- Depth TOP: `pixelformat rgba16float` with `depthspace reranged` gives usable 0..1.

## Feedback
- `feedbackTOP` `top` parameter names the TOP to read back (usually the chain's end);
  without it the feedback has no target. Decay below 1 (a `levelTOP` with `opacity`
  < 1 or a multiply) keeps it bounded; keep format and resolution identical around the
  loop.

## Render
- `renderTOP` defaults: `camera` = `cam1`, `geometry` = `*`, `lights` = `*`. Name your
  camera `cam1` or set `camera` explicitly; see `td-sop-render`.
- Camera COMP `projection` tokens: `perspective | ortho | persporthoblend | custommatrix`.

## Verify
- `capture_viewport` returns a PNG and `is_black` / `is_flat`; `observe` says whether
  it changes over frames. `read_top` gives resolution, format and GPU memory.
- `moviefileinTOP` `file` defaults to TD's sample `Banana.tif`: a banana in your output
  means the file was never set.
