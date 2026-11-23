#!/usr/bin/env python3
"""Use TD_MCP the way an agent does, and check that it works.

TD_MCP exists to be driven by an AI agent, so "it works" means: an MCP client
can start the servers, read the instructions and tools, and get correct
results back -- not that the handlers pass unit tests. This script is that
client. It speaks MCP over stdio to

  * ``td-mcp-offline --mcp``   (no TouchDesigner needed), and
  * ``td-mcp-live``            -> bridge -> the running TEST-01 project,

and builds, inspects, captures and cleans up a small network through the same
tools an agent calls. Start TouchDesigner first:

    python scripts/td_live.py launch      # or: deploy, after changing bridge/
    python scripts/live_smoke.py          # add --offline-only without TD

Exit status is non-zero when any check fails. Everything it creates lives in
/project1/zz_live_smoke and is deleted at the end.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import td_live  # noqa: E402  (token + bridge version helpers)

RESULTS: list[tuple[str, bool]] = []
SCRATCH = "/project1/zz_live_smoke"


def check(label: str, cond, detail=None) -> None:
    RESULTS.append((label, bool(cond)))
    extra = "" if cond or detail is None else "\n      -> " + json.dumps(detail, default=str)[:500]
    print(f"[{'PASS' if cond else 'FAIL'}] {label}{extra}")


def _text(result) -> dict:
    """The JSON payload of a tool result (first text block)."""
    for block in result.content:
        if getattr(block, "type", None) == "text":
            try:
                return json.loads(block.text)
            except ValueError:
                return {"_text": block.text}
    return {}


def _server(module: str, *args: str) -> StdioServerParameters:
    env = dict(os.environ, TD_MCP_AUTH_TOKEN=td_live.token(), PYTHONIOENCODING="utf-8")
    return StdioServerParameters(command=sys.executable, args=["-m", module, *args],
                                 env=env, cwd=str(ROOT))


async def offline() -> None:
    async with stdio_client(_server("td_mcp.server_offline", "--mcp")) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            check("offline: instructions sent at initialize",
                  "describe_op_type" in (getattr(init, "instructions", "") or ""))
            names = {t.name for t in (await s.list_tools()).tools}
            check("offline: td_describe_op_type advertised", "td_describe_op_type" in names)
            out = _text(await s.call_tool("td_describe_op_type",
                                          {"op_type": "blurTOP", "pattern": "filter size"}))
            check("offline: Blur TOP 'Filter Size' is `size`",
                  [p["name"] for p in out.get("parameters", [])] == ["size"], out)
            spec = {"operators": [{"name": "l", "type": "levelTOP",
                                   "parameters": {"clampinput": "bogus", "brightness": 1.2}}]}
            res = await s.call_tool("td_validate_build", {"spec": json.dumps(spec)})
            text = json.dumps(_text(res))
            check("offline: validator catches a bad menu value and a friendly name",
                  "MENU_INVALID" in text and "PARAM_RENAMED" in text, text[:400])


async def live() -> None:
    async with stdio_client(_server("td_mcp.server_live", "--mcp")) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            check("live: instructions sent at initialize",
                  "describe_op_type" in (getattr(init, "instructions", "") or ""))
            tools = {t.name: t for t in (await s.list_tools()).tools}
            schema = (getattr(tools.get("set_parameters"), "input_schema", None)  # SDK 2.x
                      or getattr(tools.get("set_parameters"), "inputSchema", None) or {})
            from td_mcp.server_live import _TOOL_META
            check(f"live: all {len(_TOOL_META)} tools listed, schemas declare required args",
                  len(tools) == len(_TOOL_META) and schema.get("required") == ["path", "params"],
                  {"count": len(tools), "required": schema.get("required")})
            info = _text(await s.call_tool("project_info", {}))
            want = td_live._repo_bridge_version()
            check(f"live: bridge in TouchDesigner is this repo's ({want})",
                  info.get("bridge_version") == want and "warning" not in info, info)
            d = _text(await s.call_tool("describe_op_type", {"op_type": "lfoCHOP", "pattern": "freq"}))
            check("live: describe_op_type lfoCHOP freq -> frequency",
                  [p["name"] for p in d.get("parameters", [])] == ["frequency"], d)

            await s.call_tool("delete_node", {"path": SCRATCH})  # leftovers from a crashed run
            r0 = _text(await s.call_tool("create_node", {"path": "/project1", "type": "baseCOMP",
                                                         "name": "zz_live_smoke", "nodeX": -1200,
                                                         "nodeY": -1200}))
            check("live: create scratch COMP", r0.get("ok"), r0)
            try:
                for t, n, x in (("noiseTOP", "noise1", 0), ("levelTOP", "level1", 200),
                                ("lfoCHOP", "lfo1", 0)):
                    await s.call_tool("create_node", {"path": SCRATCH, "type": t, "name": n,
                                                      "nodeX": x, "nodeY": -200 if t == "lfoCHOP" else 0})
                c = _text(await s.call_tool("connect_nodes", {"from_path": SCRATCH + "/noise1",
                                                              "to_path": SCRATCH + "/level1"}))
                check("live: connect noise -> level", c.get("ok"), c)
                sp = _text(await s.call_tool("set_parameters", {
                    "path": SCRATCH + "/noise1",
                    "params": {"resolution": [640, 360], "period": {"expr": "2 + 2"}}}))
                gp = _text(await s.call_tool("get_parameters", {
                    "path": SCRATCH + "/noise1", "names": "resolution*,period"}))
                check("live: tuplet write + expression read back as eval()",
                      sp.get("ok") and gp.get("params", {}).get("resolutionw") == 640
                      and gp.get("params", {}).get("period") == 4.0
                      and gp.get("modes", {}).get("period", {}).get("mode") == "EXPRESSION",
                      {"set": sp, "get": gp})
                bad = _text(await s.call_tool("set_parameters", {
                    "path": SCRATCH + "/level1", "params": {"clampinput": "bogus"}}))
                check("live: invalid menu token is reported, not silently kept",
                      bad.get("ok") is False and "valid:" in bad.get("error", ""), bad)
                cap = await s.call_tool("capture_viewport", {"path": SCRATCH + "/lfo1"})
                images = [b for b in cap.content if getattr(b, "type", None) == "image"]
                meta = _text(cap)
                check("live: capturing a CHOP returns an image block the agent can see",
                      images and meta.get("captured_via") == "opviewerTOP"
                      and meta.get("verdict", {}).get("is_black") is False, meta)
                perf = _text(await s.call_tool("get_performance", {"path": SCRATCH}))
                check("live: get_performance in ms with stale marking",
                      perf.get("units") == "ms" and "stale_count" in perf, perf)
                guard = _text(await s.call_tool("delete_node", {"path": "/project1/td_mcp"}))
                check("live: deleting the bridge is refused",
                      guard.get("ok") is False and guard.get("error_code") == "self_path", guard)
                spec = json.loads((ROOT / "examples" / "feedback_trails.json").read_text(encoding="utf-8"))
                await s.call_tool("create_node", {"path": SCRATCH, "type": "baseCOMP", "name": "built",
                                                  "nodeX": 400, "nodeY": 0})
                built = _text(await s.call_tool("build_network", {"spec": spec,
                                                                  "target_parent": SCRATCH + "/built"}))
                check("live: build_network builds the example in one call",
                      built.get("ok") and built.get("created") == len(spec["operators"])
                      and not built.get("cook_errors"), built.get("issues") or built)
                errs = _text(await s.call_tool("get_errors", {"path": SCRATCH, "recurse": True}))
                check("live: get_errors recurse answers", errs.get("ok"), errs)
            finally:
                done = _text(await s.call_tool("delete_node", {"path": SCRATCH}))
                check("live: cleanup", done.get("ok"), done)


def main() -> int:
    asyncio.run(offline())
    if "--offline-only" not in sys.argv:
        if td_live.status() is None:
            print("[SKIP] live: no bridge on port %d -- run `python scripts/td_live.py launch`"
                  % td_live.PORT)
            RESULTS.append(("live: bridge reachable", False))
        else:
            asyncio.run(live())
    passed = sum(ok for _, ok in RESULTS)
    print(f"\n{passed}/{len(RESULTS)} agent-path checks passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
