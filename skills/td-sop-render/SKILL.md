---
name: td-sop-render
description: 3D in TouchDesigner via TD_MCP - SOP geometry, the Geometry COMP, cameras, lights, materials (Phong/PBR/Constant), instancing from CHOP/DAT/TOP/POP data, and the Render TOP that ties them together.
---

# Geometry, materials, instancing, render

## Minimum scene
- `geometryCOMP` (with a SOP or POP inside, render flag on) + `cameraCOMP` +
  `lightCOMP` + `renderTOP`. Render TOP defaults: `camera` = `cam1`, `geometry` = `*`,
  `lights` = `*` -- name the camera `cam1` or set `camera` explicitly.
- Camera `projection`: `perspective | ortho | persporthoblend | custommatrix`.
- Light `lighttype`: `point | cone | distant`.
- Transform at the Geometry COMP (`tx ty tz`, `rx ry rz`, `sx sy sz`) rather than with
  SOPs when only placement changes: cheaper, and it composes with instancing.

## Materials
- Assign on the Geometry COMP (`material`). Phong MAT diffuse colour is `diffr` /
  `diffg` / `diffb` with `diffusemap` for a texture; PBR MAT starts at `basecolorr/g/b`.
- `constantMAT` for unlit colour; `glslMAT` for custom shading (see `td-glsl`).

## Instancing (one draw, many copies)
- On the Geometry COMP: `instancing` on, `instanceop` = the data source (CHOP, DAT,
  TOP or POP). Then map channels/columns per transform: `instancetx/ty/tz`,
  `instancerx/ry/rz`, `instancesx/sy/sz` -- or point whole groups at other operators
  with `instancetop` / `instancerop` / `instancesop` / `instancepop`.
- Table DAT sources: `instancefirstrow` = `names` (default) treats row 0 as headers.
- Colour per instance: `instancecolorop` + `instancecolormode`; texture per instance:
  `instancetexs` and `instancetexindexop`.
- Transform order `instxord` defaults to `srt`.
- Instancing beats a Copy SOP by orders of magnitude for large counts; use the Copy SOP
  only when the copies must become real geometry for later SOP work.

## SOPs
- SOPs are CPU geometry: fine for modelling and small meshes, the wrong place for
  per-frame work on large point counts -- use POPs (`td-pop-family`) there.
- Grid SOP: `rows`, `cols`, `sizex`, `sizey`.

## Verify
- `capture_viewport` on the Render TOP. Black usually means no camera match, no light
  on a lit material, geometry outside the camera, or a render flag off.
- `get_errors` with `recurse=true` on the Geometry COMP catches material and
  instance-source errors.
