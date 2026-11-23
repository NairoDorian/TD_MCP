# Working on TD_MCP (for AI agents and humans)

## The major rule: verify in TouchDesigner — doing beats guessing

TD_MCP exists to be driven by an AI agent, so verifying that it works *for an
agent* is part of the job. At any moment, without asking first, you may open
the test project and check a behaviour, a parameter name or a fix live:

```bash
python scripts/td_live.py launch     # open ../TD_MCP_TESTS/TEST-01/NewProject.toe with a known token
python scripts/td_live.py deploy     # after changing bridge/: copy it into TEST-01 and restart it
python scripts/live_smoke.py         # use TD_MCP exactly as an agent does (MCP stdio client)
python scripts/demo_build.py         # build + verify the examples/ template network end to end
python scripts/td_live.py call describe_op_type '{"op_type": "blurTOP"}'
python scripts/td_live.py py "result = op('/project1').children[0].OPType"
```

- **Verify through the agent path**, not only the bridge: `live_smoke.py`
  speaks MCP to `td-mcp-live --mcp` and `td-mcp-offline --mcp`. In 1.13 this
  caught three bugs that 944 unit tests could not see.
- **A claim about TouchDesigner is a guess until it is probed.** Read
  parameter names from `describe_op_type` / the catalog, and settle behaviour
  with a probe in TEST-01 before modelling it in `tests/td_double.py`. Several
  "known" facts did not survive a probe (writing `par.val` switches the mode;
  a bad menu token is silently ignored; an OP Viewer TOP needs frames).
- Build in `/project1/zz_<something>` and delete it afterwards. Never edit
  `/project1/td_mcp` (the bridge refuses anyway).
- TouchDesigner 2025.33230 is the reference build. The token only works when
  the td_mcp component's own `Token` parameter is empty.

## Other ground rules

- **Study, don't copy.** The sibling folders under `github-mcp/` are reference
  repos. Derivative's TDMCP/TDMCPSkills and eviscerations are not MIT: take
  ideas, re-derive facts live, write prose yourself. Record provenance in
  `PICKED_FROM.md` and an `UPSTREAM_SYNC_<date>.md`.
- **Never commit machine state.** After re-probing the parameter catalog run
  `python -m td_mcp.kb.param_catalog --sanitize` (local IPs, device GUIDs,
  window titles, fonts, paths); tests fail until you do. No absolute user
  paths in tracked files — use `<REPO_DIR>`.
- Before every commit follow `COMMIT.md` (summary, docs pass, version bump,
  `pytest`, explicit staging). CI must be green after the push.
