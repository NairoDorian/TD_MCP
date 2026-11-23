---
name: td-glsl
description: Writing GLSL in TouchDesigner via TD_MCP - GLSL TOP / Multi TOP pixel and compute shaders, GLSL MAT, GLSL POP, uniforms, the docked DATs each one creates, and reading compile errors.
---

# GLSL in TouchDesigner

## What creating one gives you (verified 2025.33230)
- `glslTOP` creates `<name>_pixel`, `<name>_compute` and `<name>_info` DATs beside it.
  The shader DATs are wired through `pixeldat` / `computedat` (also `vertexdat`,
  `predat`). The info DAT holds the compile log.
- `glslPOP` creates `<name>_compute` and `<name>_info` (`computedat`).
- `glslMAT` takes `vdat` (vertex), `pdat` (pixel), `gdat` (geometry).
- Write shader text with `set_dat_text` on the DAT the parameter points to -- never
  guess the DAT name; read it from `get_parameters names=pixeldat,computedat`.

## Compile errors
- A broken shader often still "cooks" and shows a fallback image: the cook error list
  can be empty while the shader is dead. `get_errors` (include_shader on, default)
  reads the docked info DAT too -- call it after every shader write.

## Writing TD shaders
- No `#version` line: TouchDesigner injects it.
- Output through TD's helpers: declare `out vec4 fragColor;` and write
  `fragColor = TDOutputSwizzle(color);`. Sample inputs with `texture(sTD2DInputs[0],
  vUV.st)`; resolution helpers live in `uTD2DInfos[0]`.
- Use `texture()`, not the removed GLSL 1.2 `texture2D()`.
- Loops need constant bounds; guard any dynamic array index.

## Uniforms
- TOP / MAT / POP all use the `vec` sequence: `vec0name` + `vec0valuex..w` (POP adds
  `vec0type`). Drive a value with an expression on `vec0valuex` (e.g. from a CHOP).
- GLSL POP also has a `const` sequence (`const0name`, `const0value`) that is compiled
  into the code -- every change recompiles. Animated values belong in `vec`.
- A uniform declared in the shader but not set on the operator compiles and reads
  zero: if an effect does nothing, check names match exactly.

## Resolution and format
- GLSL TOP `outputresolution` defaults to `useinput`; with no input set `custom` and
  `resolutionw/h`. Data passes need `format` `rgba16float` / `rgba32float`
  (8-bit clamps to 0..1).

## Verify
- `get_errors` (shader log) -> `capture_viewport` (is_black / is_flat) -> `observe`
  if the shader should animate (uTime-style uniforms need a changing input).
- Prefer stock operators for simple maths; a shader earns its place for work no
  operator chain can do.
