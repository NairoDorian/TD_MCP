# Upstream sync — 2026-09-14

All 31 sibling repos fetched from origin. Three had moved since the last sync
(2026-08-26); one of them moved a lot.

| Repo | Delta | Verdict |
|---|---|---|
| **dylanroscover/Embody** | 162 commits, v6.0.280 → v6.2.51 | Mined (below) |
| mahsan07/hermes-skill-touchdesigner-mcp | 2 commits — docs/skill package only, no runtime | Nothing to take |
| 8beeeaaat/touchdesigner_mcp | 1 commit — orval dependency bump | Nothing to take |

All other repos: no new commits.

## What Embody shipped (v6.1 / v6.2 highlights)

- **v6.2.51 — seven TD gotchas verified live on 2025.33230** (issue #94).
  Claims that did not reproduce were left out — exactly the evidentiary bar
  this project tries to hold.
- **v6.2.50/42 — ExportReleaseToe**: one pulse walks the whole locked-`.toe`
  export with a preview, a `pre_release_toe` hook, and confirm-gates.
- **v6.2.49** — saves report what they cannot keep; a host-destroy guard
  (Envoy refuses a call that would destroy or reload its own host);
  stale-bridge cleanup keyed by `--config`/`--port`, not heartbeat filename;
  the pytest tier fenced so a test run cannot kill TouchDesigner.
- **v6.2.46/43** — port-leak recovery and bounded server restarts.
- **v6.2.41 — MCP SDK 2.2.0** (`_MCP_MIN_VERSION` bump, verified in a
  throwaway venv first, then end to end).
- **v6.2.40/8 — TDN → TDXN rename** across their externalization format (wire
  frozen; both spellings read forever). *Naming only — no technique.*
- **v6.2.19** — `capture_op` for every operator family (not just TOPs); modal
  dialog list/dismiss to unstick a frozen TD from off the main thread; stable
  error codes + recovery hints on every failure; unattended bootstrap.
- **v6.2.13** — custom parameter definitions round-trip per component, not per
  group: per-component defaults/min/max/clamp/norm across a tuplet, six
  definition fields that were never exported (`password`, `styleCloneImmune`,
  `bindRange`, `defaultExpr`, `defaultBindExpr`, `defaultMode`), `UV` vs `UVW`
  style downgrade, `TOPMulti` drop, three authored COMP flags.
- **v6.2.10** — performance metrics are **milliseconds everywhere**; rename
  sweeps must cover all five reference surfaces (DAT text, `par.expr`,
  `par.bindExpr`, constant-mode op-ref `par.val`, `run()` strings).

## What we picked this sync

| From | What | Where it lives |
|---|---|---|
| Embody v6.2.51 | The six gotchas we did not already hold: `Par.default` never sets a value; Extension Object is constant-mode Str (never `.expr`); Timer CHOP ships `cyclelimit` on / `maxcycles` 4; CHOP Execute DAT is a per-frame demand source (and Execute DAT `framestart` defaults off); `allowCooking=False` is COMP-only; plus `findChildren(depth=)` exactness re-verified | `skills/td-building/SKILL.md` § Field-verified API traps |
| Embody v6.2.51 | `allowCooking=False` on a non-COMP raises — the bridge now returns actionable guidance instead of a raw `tdError` string | `bridge/td_mcp_bridge.py` (`_do_set_flags`), `tests/td_double.py` (flags modeled), `tests/test_bridge_handlers.py` |
| Plugin_FFT + PluginBuilder_V2 (local TD_PROJECTS, not a repo) | Complete custom C++ operator knowledge: C-ABI exports, virtual-base overrides, parameter discipline (one eval per cook), CHOP/TOP/CUDA buffer patterns, the TripleBuffer hard-real-time cook architecture, FFTW planner hazards, CMake/Ninja build and DLL rename-in-place hot reload | `docs/CPP_CUSTOM_OPERATORS.md`, `skills/td-building/SKILL.md` (pointer) |

## Not picked, deliberately

- **TDXN rename**: our TDN format is independent; renaming would churn every
  consumer for zero technique. Noted for awareness only.
- **ExportReleaseToe / modal-dialog dismissal / `capture_op` all-families**:
  good ideas, logged as backlog (IMPROVEMENT_PLAN), not implemented this sync.
- **MCP SDK 2.2.0 minimum**: our pin stays `mcp>=2.0,<3`; nothing in 2.2.0's
  surface is needed by our server today.
