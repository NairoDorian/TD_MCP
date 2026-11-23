---
name: td-pop-family
description: GPU point networks (POPs) in TouchDesigner 2025 via TD_MCP - generators, particle simulation lifecycle, GLSL POP attributes, rendering POPs in a Geometry COMP, and reading POP data without stalling the GPU.
---

# POP networks

## Model
- A POP is points (plus primitives) with attributes -- `P` position, `N`, `Color`,
  `Tex`, and your own -- living on the GPU. Unchanged attributes pass downstream
  without copying.
- Generators (`gridPOP` `rows`/`cols`/`sizex`/`sizey`, `spherePOP`, `rectanglePOP`
  `sizeu`/`sizev`, `pointgeneratorPOP`) feed processors; end in a `nullPOP`.
- Rendering: put the POP inside a `geometryCOMP` (its render/display flags on) and
  render with a `renderTOP`; a MAT on the Geometry COMP reads POP attributes.
- Reading back (`poptoCHOP`, `poptoDAT`, and the `read_pop` tool) copies GPU -> CPU.
  Fine for a few thousand points while debugging; not something to run every frame
  on a large system.

## Particle simulation (`particlePOP`)
- Lifecycle parameters: pulse `initializepulse` (reset), pulse `startpulse` (begin),
  toggle `play`. After changing birth/forces, pulse `initializepulse` then
  `startpulse` with `pulse_parameter` -- setting `true` on a pulse does not pulse it.
- Rates live on `birthrate` and `life`. Start forces small and raise them; a runaway
  system shows up first as rising GPU cook time in `get_performance`.

## GLSL POP (`glslPOP`)
- Creating one also creates `<name>_compute` and `<name>_info` DATs beside it; the
  shader lives in the compute DAT (`computedat`), the compile log in the info DAT --
  `get_errors` reads it for you.
- `outputattrs` lists the attributes the shader writes; `outputaccess` is
  `writeonly` (default) or `readwrite` -- use `readwrite` to read-modify-write.
- Uniforms: the `vec` sequence (`vec0name`, `vec0valuex..w`) is read every frame; the
  `const` sequence (`const0name`, `const0value`) is compiled into the shader, so
  changing it recompiles. Anything animated goes in `vec`.
- Built-in POPs (math, noise, transform) are usually faster to build and read than a
  hand-written shader for simple per-point maths.

## Verify
- `read_pop` returns attribute metadata always, values only under a point ceiling.
- `capture_viewport` on the POP shows its viewer through an OP Viewer; on the
  `renderTOP` it shows the final render. Check both when points "disappear".
