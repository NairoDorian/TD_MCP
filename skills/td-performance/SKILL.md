---
name: td-performance
description: Keeping TouchDesigner real-time while building through TD_MCP - the pull-based cook model, frame budgets, reading get_performance correctly, and the usual causes of a slow network.
---

# Performance

## Cook model
- TouchDesigner cooks on demand: an operator cooks when something downstream (a
  viewer, a Render TOP, an output, a CHOP Execute DAT, an expression) asks for it.
  A time-dependent chain nothing demands sits frozen on its last frame -- the most
  common reason "it is not animating". `observe` catches this.
- Every always-demanded operator costs every frame. Bypass or disconnect what you are
  not using while building.

## Budget
- One frame at 60 fps is 16.7 ms, at 30 fps 33.3 ms. All TD cook times are milliseconds.
- `get_performance` on a COMP ranks CPU+GPU cook time per operator and gives
  `budget_pct` of one frame. Operators that have not cooked within `max_age_frames`
  are marked `stale` and ranked last -- their cook time is history, not load.
- Check before a heavy build and after each step; compare `live_total_budget_pct`.

## Usual suspects
- Resolution: a TOP costs roughly its pixel count. Stay at 1920x1080 or below unless
  asked; 32-bit float only where the data needs it.
- Unbounded feedback (no decay, growing resolution).
- Python per frame: expressions with logic, Script CHOPs/SOPs, callbacks doing work.
  Prefer operators and simple references (`op('null1')['chan1']`).
- CPU geometry: large SOP networks recooking every frame -- move to POPs or instancing.
- GPU -> CPU copies every frame: `topto`/`popto` CHOPs on big data, `numpyArray()`.
- Copy SOP for thousands of copies -- instance on the Geometry COMP instead.

## Diagnose
- CPU or GPU bound? If dropping the render resolution sharply does not raise fps, the
  bottleneck is CPU (cooking, Python), not fill rate.
- `get_performance path=<one operator>` profiles a single suspect.
- Stop and report when a step pushes the frame over budget instead of building on.
