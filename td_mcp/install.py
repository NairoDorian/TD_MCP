"""Install SOP v1 — an install lifecycle designed for an agent to drive.

Adapted from ``dcc-mcp/dcc-mcp-touchdesigner`` 0.1.2, whose central idea is
worth stating plainly: **an external process cannot finish installing a
TouchDesigner integration, and it should not pretend otherwise.** The bridge
lives inside a ``.toe`` as a Text DAT. No CLI can create it, and no CLI can
restart TouchDesigner. So instead of a script that prints "done!" and leaves
the user to discover the missing half, this returns a machine-readable plan
whose ``next_steps`` say exactly what a human or an agent must do, and a
``verify`` that only reports success once that half is genuinely in place.

The shape::

    install | status | verify | uninstall | upgrade      # verbs
    --json --dry-run --yes                               # every verb
    ~/.td_mcp/receipts/touchdesigner.json                # what was staged
    exit 0 / 10 / 20 / 30 / 40 / 50                      # which stage failed

Exit codes are per stage rather than a blanket 1, so a caller can tell "your
TouchDesigner is too old" (preflight) from "the copy failed" (install) from
"it is staged but you have not restarted yet" (requires_restart) without
parsing prose.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from td_mcp import __version__

#: Bumped when the report shape changes in a way a consumer must notice.
SCHEMA_VERSION = 1

EXIT_OK = 0
EXIT_PREFLIGHT = 10
EXIT_ACQUIRE = 20
EXIT_INSTALL = 30
EXIT_VERIFY = 40
EXIT_REQUIRES_RESTART = 50

LIFECYCLE_VERBS = ("install", "status", "verify", "uninstall", "upgrade")

#: Verbs that write. They require --yes, so a bare invocation is always safe.
MUTATING_VERBS = ("install", "uninstall", "upgrade")

REPO_ROOT = Path(__file__).resolve().parent.parent


class InstallFailure(Exception):
    """A failure that maps to one stage, and therefore to one exit code."""

    def __init__(self, exit_code: int, stage: str, reason: str):
        super().__init__(reason)
        self.exit_code = exit_code
        self.stage = stage
        self.reason = reason


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------
def integration_root() -> Path:
    return Path(os.environ.get("TD_MCP_HOME")
                or (Path.home() / ".td_mcp"))


def receipt_path() -> Path:
    return integration_root() / "receipts" / "touchdesigner.json"


def staged_bridge_path() -> Path:
    return integration_root() / "bridge" / "td_mcp_bridge.py"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> Optional[str]:
    try:
        return _sha256(path.read_text(encoding="utf-8"))
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
def _kb_path() -> Path:
    return REPO_ROOT / "td_mcp" / "kb" / "chunks.jsonl"


def preflight(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Everything checkable before writing anything.

    A failure here is worth a distinct exit code: "your Python is too old" and
    "the copy failed" are different problems with different fixes, and a
    caller that gets `1` for both has to parse prose to tell them apart.
    """
    root = Path(repo_root or REPO_ROOT)
    bridge = root / "bridge" / "td_mcp_bridge.py"
    problems: List[str] = []
    if sys.version_info < (3, 10):
        problems.append(
            f"python {sys.version_info.major}.{sys.version_info.minor} is "
            f"below the 3.10 minimum")
    if not bridge.is_file():
        problems.append(f"bridge source not found at {bridge}")
    return {
        "id": "preflight",
        "status": "ok" if not problems else "failed",
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}"
                          f".{sys.version_info.micro}",
        "python": sys.executable,
        "repo_root": str(root),
        "bridge_source": str(bridge),
        "knowledge_base_built": _kb_path().is_file(),
        "problems": problems,
    }


def _bridge_version(source: str) -> Optional[str]:
    for line in source.splitlines():
        if line.startswith("BRIDGE_VERSION"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


# ---------------------------------------------------------------------------
# The step a CLI cannot perform
# ---------------------------------------------------------------------------
def _next_steps(bridge_file: Path) -> List[Dict[str, Any]]:
    """The half of the install that has to happen inside TouchDesigner.

    Returned as one machine-executable entry rather than prose, so an agent
    can act on it: a `file_edit` naming the DAT to create and the content to
    put in it, plus `why` it cannot be done from here.
    """
    return [{
        "id": "create-bridge-dat",
        "description": ("Create a Text DAT named `td_mcp_bridge` in the "
                        "TouchDesigner project and paste the staged bridge "
                        "into it, then run `op('td_mcp_bridge').module.start()`"),
        "file_edit": {
            "path": "touchdesigner://current-project/text-dat/td_mcp_bridge",
            "action": "create",
            "content_file": str(bridge_file),
        },
        "verify_with": "td-mcp-install verify --json",
        "why": ("The bridge runs inside TouchDesigner as a Text DAT. An "
                "external process cannot create an operator in a running "
                "project, and cannot save or restart it."),
    }]


# ---------------------------------------------------------------------------
# Plan / apply
# ---------------------------------------------------------------------------
def plan(verb: str = "install", repo_root: Optional[Path] = None
         ) -> Dict[str, Any]:
    """Everything that *would* happen, with nothing written yet."""
    if verb not in LIFECYCLE_VERBS:
        raise InstallFailure(EXIT_PREFLIGHT, "preflight",
                             f"unknown verb {verb!r}; expected one of "
                             f"{', '.join(LIFECYCLE_VERBS)}")
    root = Path(repo_root or REPO_ROOT)
    pre = preflight(root)
    if pre["problems"]:
        raise InstallFailure(EXIT_PREFLIGHT, "preflight",
                             "; ".join(pre["problems"]))

    bridge_source = (root / "bridge" / "td_mcp_bridge.py").read_text(
        encoding="utf-8")
    target = staged_bridge_path()
    existing = inspect_install()

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "planned",
        "verb": verb,
        "dcc_type": "touchdesigner",
        "adapter_version": __version__,
        "bridge_version": _bridge_version(bridge_source),
        "python": sys.executable,
        "integration_root": str(integration_root()),
        "receipt_path": str(receipt_path()),
        "installation_state": existing["installation_state"],
        "steps": [
            pre,
            {"id": "stage-bridge", "status": "planned",
             "target": str(target),
             "bridge_sha256": _sha256(bridge_source)},
            {"id": "write-client-configs", "status": "planned",
             "target": str(integration_root() / "client_configs")},
            {"id": verb, "status": "planned"},
        ],
        "next_steps": _next_steps(target),
        "verify": empty_verify(),
        "_artifacts": {"bridge": bridge_source},
    }


def empty_verify() -> Dict[str, Any]:
    return {"directly_usable": False, "failure_stage": None,
            "failure_reason": None}


def apply_install(report: Dict[str, Any]) -> Dict[str, Any]:
    """Stage the artifacts described by `report` and write the receipt."""
    artifacts = report.pop("_artifacts", {})
    bridge_source = artifacts.get("bridge")
    if not bridge_source:
        raise InstallFailure(EXIT_ACQUIRE, "acquire",
                             "plan carried no bridge source")
    target = staged_bridge_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(bridge_source, encoding="utf-8")
        configs = _write_client_configs()
    except OSError as e:
        raise InstallFailure(EXIT_INSTALL, "install", str(e))

    receipt = {
        "schema_version": SCHEMA_VERSION,
        "adapter_version": __version__,
        "bridge_version": report.get("bridge_version"),
        "python": sys.executable,
        "artifacts": {
            str(target): _sha256(bridge_source),
            **{str(p): _sha256_file(p) for p in configs},
        },
    }
    _write_receipt(receipt)

    for step in report["steps"]:
        if step["id"] in ("stage-bridge", "write-client-configs",
                          report["verb"]):
            step["status"] = "ok"
    # Staged, not finished: the DAT still has to be created inside the running
    # project. Saying "installed" here is the lie this module exists to avoid.
    report["status"] = "requires_restart"
    report["exit_code"] = EXIT_REQUIRES_RESTART
    report["receipt"] = receipt
    return report


def _write_client_configs() -> List[Path]:
    from td_mcp import config_gen

    out_dir = integration_root() / "client_configs"
    out_dir.mkdir(parents=True, exist_ok=True)
    config_gen.write_configs(str(out_dir), project_dir=str(REPO_ROOT))
    return sorted(p for p in out_dir.iterdir() if p.is_file())


def _write_receipt(receipt: Dict[str, Any]) -> None:
    path = receipt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")


def _read_receipt() -> Optional[Dict[str, Any]]:
    try:
        return json.loads(receipt_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Status / verify / uninstall
# ---------------------------------------------------------------------------
def inspect_install() -> Dict[str, Any]:
    receipt = _read_receipt()
    if receipt is None:
        return {"installation_state": "absent", "receipt": None}
    drifted = [path for path, digest in (receipt.get("artifacts") or {}).items()
               if _sha256_file(Path(path)) != digest]
    state = "installed" if not drifted else "drifted"
    if receipt.get("adapter_version") != __version__:
        state = "outdated"
    return {"installation_state": state, "receipt": receipt,
            "drifted_artifacts": drifted}


def status() -> Dict[str, Any]:
    inspection = inspect_install()
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "verb": "status",
        "adapter_version": __version__,
        "installation_state": inspection["installation_state"],
        "integration_root": str(integration_root()),
        "receipt_path": str(receipt_path()),
        "drifted_artifacts": inspection.get("drifted_artifacts") or [],
        "knowledge_base_built": _kb_path().is_file(),
        "exit_code": EXIT_OK,
    }


def verify() -> Dict[str, Any]:
    """Is this installation *directly usable*?

    Not "did the files copy" — every check below is a thing that has to be
    true before an agent can actually get an answer out of the server. The
    last one is a real read-only probe rather than an import: importing proves
    the module parses, not that it works.
    """
    report = {
        "schema_version": SCHEMA_VERSION,
        "verb": "verify",
        "adapter_version": __version__,
        "checks": [],
    }

    def check(name: str, ok: bool, detail: str = "") -> bool:
        report["checks"].append({"id": name, "status": "ok" if ok else "failed",
                                 "detail": detail})
        return ok

    inspection = inspect_install()
    stage, reason = None, None

    if not check("receipt-present", inspection["receipt"] is not None,
                 str(receipt_path())):
        stage, reason = "install", "no receipt; run `install --yes` first"
    elif not check("artifacts-match-receipt",
                   not inspection.get("drifted_artifacts"),
                   ", ".join(inspection.get("drifted_artifacts") or [])):
        stage, reason = "install", "staged artifacts do not match the receipt"
    elif not check("knowledge-base-built", _kb_path().is_file(), str(_kb_path())):
        stage, reason = ("verify",
                         "knowledge base not built; run "
                         "`python -m td_mcp.kb.build_kb`")
    else:
        probe = _probe_offline()
        if not check("offline-probe", probe["ok"], probe["detail"]):
            stage, reason = "verify", probe["detail"]

    directly_usable = stage is None
    report["verify"] = {"directly_usable": directly_usable,
                        "failure_stage": stage, "failure_reason": reason}
    report["status"] = "ok" if directly_usable else "failed"
    report["exit_code"] = EXIT_OK if directly_usable else EXIT_VERIFY
    return report


def _probe_offline() -> Dict[str, Any]:
    """A real read-only call, not an import."""
    try:
        from td_mcp.server_offline import td_docs_search
        out = td_docs_search("noise", k=1)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}
    if not out or not str(out).strip():
        return {"ok": False, "detail": "td_docs_search returned nothing"}
    return {"ok": True, "detail": "td_docs_search answered"}


def uninstall() -> Dict[str, Any]:
    """Remove what the receipt says we staged, and nothing else."""
    receipt = _read_receipt()
    removed, kept = [], []
    for path in list((receipt or {}).get("artifacts") or {}):
        p = Path(path)
        try:
            if p.is_file():
                p.unlink()
                removed.append(str(p))
        except OSError as e:
            kept.append(f"{p}: {e}")
    if receipt_path().is_file():
        try:
            receipt_path().unlink()
            removed.append(str(receipt_path()))
        except OSError as e:
            kept.append(f"{receipt_path()}: {e}")
    return {
        "schema_version": SCHEMA_VERSION, "verb": "uninstall",
        "status": "ok" if not kept else "failed",
        "removed": removed, "kept": kept,
        # The Text DAT lives in the user's project file. Deleting it is their
        # call, and this process could not do it anyway.
        "next_steps": [{
            "id": "remove-bridge-dat",
            "description": "Delete the `td_mcp_bridge` Text DAT from the project",
            "why": "It lives inside the .toe, which this process does not own.",
        }],
        "exit_code": EXIT_OK if not kept else EXIT_INSTALL,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def run(verb: str, *, assume_yes: bool = False, dry_run: bool = False,
        repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Execute one lifecycle verb and return its report."""
    if verb == "status":
        return status()
    if verb == "verify":
        return verify()
    if verb == "uninstall":
        if not assume_yes or dry_run:
            return _refusal(verb, dry_run)
        return uninstall()
    if verb in ("install", "upgrade"):
        report = plan(verb, repo_root)
        if dry_run or not assume_yes:
            report.pop("_artifacts", None)
            report["status"] = "planned"
            report["exit_code"] = EXIT_OK
            if not assume_yes and not dry_run:
                report["note"] = "pass --yes to apply this plan"
            return report
        return apply_install(report)
    raise InstallFailure(EXIT_PREFLIGHT, "preflight", f"unknown verb {verb!r}")


def _refusal(verb: str, dry_run: bool) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION, "verb": verb, "status": "planned",
        "note": ("dry run; nothing was written" if dry_run
                 else f"pass --yes to {verb}"),
        "exit_code": EXIT_OK,
    }


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="td-mcp-install",
        description="td-mcp install lifecycle (Install SOP v1)")
    ap.add_argument("verb", nargs="?", default="status", choices=LIFECYCLE_VERBS)
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--yes", action="store_true", help="apply a mutating verb")
    ap.add_argument("--dry-run", action="store_true", help="never write")
    args = ap.parse_args(argv)

    # Same reason as the servers: this prints paths and messages that may not
    # survive the console's locale codepage. Pinned in main(), never at import.
    from td_mcp.util.stdio import pin_stdio_utf8
    pin_stdio_utf8()

    try:
        report = run(args.verb, assume_yes=args.yes, dry_run=args.dry_run)
    except InstallFailure as e:
        report = {"schema_version": SCHEMA_VERSION, "verb": args.verb,
                  "status": "failed", "failure_stage": e.stage,
                  "failure_reason": e.reason, "exit_code": e.exit_code}

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_human(report)
    return int(report.get("exit_code", EXIT_OK))


def _print_human(report: Dict[str, Any]) -> None:
    print(f"td-mcp {report.get('verb')}: {report.get('status')}")
    if report.get("failure_reason"):
        print(f"  {report['failure_stage']}: {report['failure_reason']}")
    for step in report.get("steps", []):
        print(f"  [{step['status']:>7}] {step['id']}")
    for chk in report.get("checks", []):
        print(f"  [{chk['status']:>7}] {chk['id']}"
              + (f" - {chk['detail']}" if chk.get("detail") else ""))
    v = report.get("verify") or {}
    if v.get("failure_reason"):
        print(f"  not usable yet: {v['failure_reason']}")
    for step in report.get("next_steps", []):
        print(f"\nNext, inside TouchDesigner:\n  {step['description']}")
        print(f"  why: {step['why']}")
    if report.get("note"):
        print(f"  note: {report['note']}")


if __name__ == "__main__":
    raise SystemExit(main())
