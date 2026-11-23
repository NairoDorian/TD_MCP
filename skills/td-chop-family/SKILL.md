---
name: td-chop-family
description: Building CHOP (channel) networks via TD_MCP - LFOs, timers, audio analysis, math/range mapping, event detection, driving parameters from channels, and verifying channel data with read_chop.
---

# CHOP networks

## Shape of a chain
- Source -> shaping -> `nullCHOP` named for its role (`null_audio`, `null_ctrl`).
  Parameters reference the null: `op('null_ctrl')['level']` in an expression.
- Common sources: `lfoCHOP`, `timerCHOP`, `noiseCHOP`, `constantCHOP`,
  `audiodeviceinCHOP`, `audiofileinCHOP`, `oscinCHOP`, `midiinCHOP`, `keyboardinCHOP`.
- Shaping: `mathCHOP`, `lagCHOP`, `filterCHOP`, `limitCHOP`, `analyzeCHOP`,
  `selectCHOP`, `mergeCHOP`, `renameCHOP`, `resampleCHOP`, `triggerCHOP`.

## Names that matter (TD 2025.33230)
- LFO CHOP: `wavetype` = `sin | normal | tri | ramp | square | pulse`, `frequency`
  (not `freq` -- the resolver maps it), `amp`, `offset`.
- Math CHOP range mapping: `fromrange1/2` -> `torange1/2`; `gain`; `chopop`.
- Lag CHOP has two lags, `lag1` (rising) and `lag2` (falling).
- Select CHOP reads `chops` (plural) and `channames`.
- Timer CHOP ships with `cyclelimit` ON and `maxcycles` 4: a looping timer stops after
  four cycles unless you change that. `length` + `lengthunits` set the duration.
- Null CHOP `cooktype` = `auto | always | selective`.

## Time
- For animation prefer an `lfoCHOP` or a `timerCHOP` over a raw `absTime.seconds`
  expression: a CHOP gives you rate, range and reset in one place, and its values
  can be inspected with `read_chop`.

## Events
- A CHOP Execute DAT (`chopexecuteDAT`, `chop` parameter) fires `offtoon` / `ontooff`
  / `whileon` / `valuechange` callbacks on the main thread. It demands its CHOP every
  frame, so the watched chain always cooks -- point it at a small, final null.
- A `triggerCHOP` (`threshold`) turns a noisy signal into clean on/off events first.

## Audio reactive, in one line
- `audiodeviceinCHOP` -> `audiospectrumCHOP` or `analyzeCHOP` (`function` `rmspower`) ->
  `mathCHOP` to 0..1 -> `lagCHOP` to smooth -> `nullCHOP` -> parameter expressions.

## Verify
- `read_chop` (format `stats`) reports count/min/max/mean/std per channel: a std of 0
  on something that should move means it is frozen or not cooking.
- `diff_chop` compares two CHOPs, e.g. before and after a filter.
- A CHOP nothing reads may not cook at all. If values look stale, check what demands it.
