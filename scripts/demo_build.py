#!/usr/bin/env python3
"""Build a template network from ``examples/`` the way an agent should.

The built-in demonstration of TD_MCP's loop, run entirely over MCP (stdio
client -> td-mcp-offline / td-mcp-live -> bridge -> TouchDesigner):

  1. plan     td_validate_build on the spec, offline, strict  (no TD needed)
  2. build    build_network: every operator, parameter, wire and flag in ONE
              call -- one Ctrl+Z in TouchDesigner removes it all
  3. verify   get_errors (recursive), capture_viewport on the output,
              observe (is it moving?), get_performance (frame budget)
  4. report   evidence printed; the PNG lands in .td_mcp/demo/<name>.png

    python scripts/td_live.py launch                 # TouchDesigner up
    python scripts/demo_build.py                     # examples/feedback_trails.json
    python scripts/demo_build.py examples/other.json --cleanup

The network is built in /project1/<spec name> and left there for a human to
look at, unless --cleanup. Exit status is non-zero if any stage fails.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import td_live  # noqa: E402

OUT_DIR = ROOT / ".td_mcp" / "demo"   # gitignored


def _params(module: str) -> StdioServerParameters:
    env = dict(os.environ, TD_MCP_AUTH_TOKEN=td_live.token(), PYTHONIOENCODING="utf-8")
    return StdioServerParameters(command=sys.executable, args=["-m", module, "--mcp"],
                                 env=env, cwd=str(ROOT))


def _json(result) -> dict:
    for block in result.content:
        if getattr(block, "type", None) == "text":
            try:
                return json.loads(block.text)
            except ValueError:
                return {"_text": block.text}
    return {}


class Report:
    def __init__(self):
        self.failed = False

    def stage(self, name: str, ok: bool, detail: str = "") -> None:
        self.failed |= not ok
        print(f"[{'OK  ' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


async def plan(spec: dict, rep: Report) -> None:
    async with stdio_client(_params("td_mcp.server_offline")) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            out = _json(await s.call_tool("td_validate_build", {"spec": json.dumps(spec)}))
            findings = out.get("validation", {}).get("findings", [])
            rep.stage("plan: td_validate_build (offline)", out.get("ok") and not findings,
                      "score %s; %d finding(s)%s" % (
                          out.get("score", {}).get("score"), len(findings),
                          "".join("\n         %s %s" % (f["code"], f["message"]) for f in findings)))


async def build_and_verify(spec: dict, comp: str, cleanup: bool, rep: Report) -> None:
    async with stdio_client(_params("td_mcp.server_live")) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            await s.call_tool("delete_node", {"path": comp})  # a previous run's copy
            name = comp.rsplit("/", 1)[-1]
            made = _json(await s.call_tool("create_node", {
                "path": comp.rsplit("/", 1)[0] or "/", "type": "baseCOMP", "name": name,
                "nodeX": 400, "nodeY": -400}))
            rep.stage("build: container " + comp, made.get("ok"), made.get("error", ""))
            t0 = time.perf_counter()
            built = _json(await s.call_tool("build_network", {"spec": spec, "target_parent": comp}))
            ms = (time.perf_counter() - t0) * 1000
            rep.stage("build: build_network (one call, one undo step)", built.get("ok"),
                      "%s operators, %s wires in %.0f ms%s" % (
                          built.get("created"), built.get("wired"), ms,
                          "".join("\n         " + i for i in built.get("issues", []))))
            for note in built.get("resolver", []):
                print("         resolver:", note)

            await asyncio.sleep(1.5)  # let the feedback loop accumulate trails
            errs = _json(await s.call_tool("get_errors", {"path": comp, "recurse": True}))
            count = errs.get("error_count", errs.get("errorCount", 0))
            rep.stage("verify: get_errors recurse", errs.get("ok") and not count,
                      "%s error(s)" % count)

            out = spec.get("output") or "null_out"
            cap = await s.call_tool("capture_viewport", {"path": f"{comp}/{out}"})
            meta = _json(cap)
            verdict = meta.get("verdict", {})
            images = [b for b in cap.content if getattr(b, "type", None) == "image"]
            if images:
                OUT_DIR.mkdir(parents=True, exist_ok=True)
                png = OUT_DIR / f"{spec.get('name', 'demo')}.png"
                png.write_bytes(base64.b64decode(images[0].data))
            rep.stage("verify: capture_viewport %s" % out,
                      bool(images) and verdict.get("is_black") is False and verdict.get("is_flat") is False,
                      "mean %s, std %s%s" % (verdict.get("mean"), verdict.get("std"),
                                             f"; saved {png.relative_to(ROOT)}" if images else ""))

            obs = _json(await s.call_tool("observe", {"path": f"{comp}/{out}", "frames": 6}))
            motion = obs.get("motion", {})
            rep.stage("verify: observe (is it animating?)", motion.get("is_static") is False,
                      motion.get("caption", "is_static=%s" % motion.get("is_static")))

            # Median over 10 real frames: a single read right after a build
            # carries one-off costs (measured: 167% of a frame vs ~2.4% steady).
            perf = _json(await s.call_tool("get_performance", {"path": comp, "samples": 10}))
            hot = (perf.get("hotspots") or [{}])[0]
            budget = perf.get("live_total_budget_pct")
            rep.stage("verify: get_performance (median of 10 frames)",
                      perf.get("ok") and budget is not None and budget < 50,
                      "%s%% of a %s ms frame; hottest %s (%.3f ms, peak %.3f ms)" % (
                          budget, perf.get("frame_budget_ms"),
                          hot.get("path", "-").rsplit("/", 1)[-1],
                          hot.get("cpu_ms", 0) + hot.get("gpu_ms", 0), hot.get("peak_ms", 0)))
            if cleanup:
                gone = _json(await s.call_tool("delete_node", {"path": comp}))
                rep.stage("cleanup", gone.get("ok"))
            else:
                print(f"\nLeft in TouchDesigner at {comp} -- open it to look. "
                      "Ctrl+Z there undoes build_network in one step.")


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    spec_path = Path(args[0]) if args else ROOT / "examples" / "feedback_trails.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    comp = "/project1/" + spec.get("name", spec_path.stem)
    rep = Report()
    print(f"demo: {spec_path.name} -> {comp}\n  {spec.get('description', '')}\n")
    asyncio.run(plan(spec, rep))
    if td_live.status() is None:
        rep.stage("live: bridge reachable", False,
                  "run `python scripts/td_live.py launch` first")
    else:
        asyncio.run(build_and_verify(spec, comp, "--cleanup" in argv, rep))
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
