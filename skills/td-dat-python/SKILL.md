---
name: td-dat-python
description: DATs and Python inside TouchDesigner via TD_MCP - text/table DATs, callback DATs (Execute, CHOP/DAT/Parameter Execute), extensions on a COMP, custom parameters, file-synced code, and the main-thread rule.
---

# DATs and Python

## Text and table DATs
- A new `textDAT` holds `language` = `text` (its declared default is `input`, which
  inherits from a wired input -- verified 2025.33230). Set `language` to `python`,
  `glsl`, `json`, `yaml` or `xml` when the DAT holds code or data.
- Rows and columns belong in a `tableDAT`, not a text DAT. `read_dat` (format `stats`)
  summarises a table; `set_dat_text` writes text (tab-separated cells, one row per
  line for a table) and returns a sha256 for the next write's `expected_sha256`.
- Code on disk: `file` + `syncfile` on a text DAT makes TouchDesigner reload it when
  the file changes. Edit the file, then `get_errors` to see the reload's result.

## Callback DATs (main thread, every one of them)
- `executeDAT`: `start`, `create`, `framestart` ... all default OFF -- a callback you
  wrote does nothing until its toggle is on.
- `chopexecuteDAT` (`chop`): `offtoon`, `ontooff`, `whileon`, `valuechange`. It
  demands its CHOP every frame, keeping that chain cooking.
- `parameterexecuteDAT`: `op` + `pars` say what to watch (empty `pars` watches
  nothing); `valuechange`, `onpulse` toggles enable the callbacks.
- `datexecuteDAT` (`dat`): `tablechange` and friends.
- Callbacks run on TD's main thread: no network calls, no sleeps, nothing slow. A
  frame spent there is a dropped frame.

## Extensions
- On a COMP: `ext0object` holds the class instantiation as a CONSTANT-mode string,
  e.g. `op('./myExt').module.MyExt(me)` -- not an expression. `ext0name` names it,
  `ext0promote` exposes capitalised members on the COMP. Pulse `reinitextensions`
  after editing the class.
- Capitalised members are promoted (`op('comp').DoThing()`); lowercase stay internal.

## Custom parameters
- `comp.appendCustomPage('Controls')` then `page.appendFloat('Speed')` (also
  `appendInt`, `appendToggle`, `appendMenu`, `appendPulse`, `appendStr`, `appendRGB`,
  `appendXYZ`, `appendOP`, `appendFile` ...). Appenders return a ParGroup: index `[0]`
  to reach the parameter.
- Names are ONE capital letter then lowercase letters and digits only: `Speed`,
  `Speed2`, `Gainlow`. TD rejects `speed` AND `MySpeed` (verified 2025.33230).

## Values from Python
- `par.eval()` is the current value; `par.val` is only the constant slot. Assigning
  `par.val` (or `par.x = 5`) switches the parameter to CONSTANT and drops its expression.
- `op()` returns None for a missing path; check before chaining.

## Threads
- Never touch TD objects (ops, pars, DATs, `run()`) from a worker thread: it does not
  raise, it corrupts state later. Do blocking work in a thread with plain Python data
  only and hand results back through a queue that a main-thread callback drains --
  `td-building` has the pattern; TD_MCP flags thread misuse in `set_dat_text`.
