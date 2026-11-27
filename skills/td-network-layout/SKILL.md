---
name: td-network-layout
description: Placing and tidying operators in a TouchDesigner network through TD_MCP - signal-flow layout, spacing from real node sizes, naming, colour and comments, and the final cleanup pass after a build.
---

# Network layout

## Rules
- Signal flows left to right: every wire goes forward (positive X). Parallel chains
  stack downward. Network Y grows UPWARD in TouchDesigner.
- Space by real size: `get_node` reports `position` and `size` ([nodeWidth,
  nodeHeight]); place the next operator at `x + width + gap`. Typical tiles are ~130x90; COMPs are wider.
  A fixed `+200` step overlaps wide tiles.
- Nothing left at (0, 0) and nothing overlapping: `validate_network` reports both.
- Related operators sit together: a MAT beside its Geometry COMP, camera and lights
  near the Render TOP, a CHOP feeding a parameter near the operator it drives.

## While building
- Pass `nodeX` / `nodeY` to `create_node` when you know the slot; without them the
  bridge places the operator in a lane for its family.
- `set_node_position` for single moves, `batch` for many; `auto_layout` re-flows a
  whole COMP left to right when a build got messy.

## Naming
- Name for the job, not the type: `noise_terrain`, `lag_smooth`, `null_out`.
- End every chain in a null named for what reads it; other operators reference the
  null, so the chain behind it can change freely.
- After `rename_node`, references in other operators' parameters, expressions and DAT
  text do not follow -- `find_nodes` and re-point them.

## Final tidy (after verification passes)
1. `validate_network` -> fix overlaps and origin nodes.
2. Colour groups by role with `set_node_color` (inputs, processing, outputs).
3. `set_node_comment` on anything non-obvious: what it does and why.
4. Frame each section of a larger network with `annotate(action='create',
   mode='networkbox', around=[...operator names...], title=...)`. Annotations are
   utility operators: `list_nodes` and `find_nodes` do not show them, so read and
   edit them with `annotate` only.
5. Re-run `get_errors` recurse, `health`, and one `capture_viewport` -- tidying
   must not change what renders.
