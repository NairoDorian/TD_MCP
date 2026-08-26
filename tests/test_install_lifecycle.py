"""Install SOP v1 (Wave 12, adapted from dcc-mcp-touchdesigner 0.1.2).

The contract worth testing is the honest one: an external process cannot
create a Text DAT inside a running TouchDesigner project, so `install` must
report `requires_restart` with a machine-executable next step rather than
claiming success, and `verify` must only say `directly_usable` once the whole
thing actually works.
"""

from __future__ import annotations

import json

import pytest

from td_mcp import install as inst


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Redirect the integration root so nothing touches the real ~/.td_mcp."""
    monkeypatch.setenv("TD_MCP_HOME", str(tmp_path / "td_mcp_home"))
    return tmp_path


class TestPreflight:
    def test_reports_the_bridge_source_and_python(self, home):
        pre = inst.preflight()
        assert pre["status"] == "ok", pre["problems"]
        assert pre["bridge_source"].endswith("td_mcp_bridge.py")
        assert pre["python"] == __import__("sys").executable

    def test_a_missing_bridge_is_a_preflight_failure(self, home, tmp_path):
        empty = tmp_path / "empty_repo"
        (empty / "bridge").mkdir(parents=True)
        pre = inst.preflight(empty)
        assert pre["status"] == "failed"
        assert any("bridge source not found" in p for p in pre["problems"])

    def test_plan_raises_with_the_preflight_exit_code(self, home, tmp_path):
        empty = tmp_path / "empty_repo"
        empty.mkdir()
        with pytest.raises(inst.InstallFailure) as e:
            inst.plan("install", empty)
        assert e.value.exit_code == inst.EXIT_PREFLIGHT
        assert e.value.stage == "preflight"


class TestPlan:
    def test_plan_writes_nothing(self, home):
        report = inst.plan("install")
        assert report["status"] == "planned"
        assert not inst.receipt_path().exists()
        assert not inst.staged_bridge_path().exists()

    def test_plan_carries_a_schema_version_and_stage_list(self, home):
        report = inst.plan("install")
        assert report["schema_version"] == inst.SCHEMA_VERSION
        assert [s["id"] for s in report["steps"]] == [
            "preflight", "stage-bridge", "write-client-configs", "install"]

    def test_plan_records_the_bridge_digest_before_writing(self, home):
        step = [s for s in inst.plan("install")["steps"]
                if s["id"] == "stage-bridge"][0]
        assert len(step["bridge_sha256"]) == 64

    def test_plan_reports_the_bridge_version(self, home):
        assert inst.plan("install")["bridge_version"]

    def test_unknown_verb_is_refused(self, home):
        with pytest.raises(inst.InstallFailure) as e:
            inst.plan("frobnicate")
        assert e.value.exit_code == inst.EXIT_PREFLIGHT


class TestNextSteps:
    def test_install_names_the_step_a_cli_cannot_do(self, home):
        step = inst.plan("install")["next_steps"][0]
        assert step["id"] == "create-bridge-dat"
        assert step["file_edit"]["action"] == "create"
        assert "text-dat" in step["file_edit"]["path"]
        assert "cannot create an operator" in step["why"]

    def test_the_next_step_points_at_the_staged_file(self, home):
        report = inst.plan("install")
        assert (report["next_steps"][0]["file_edit"]["content_file"]
                == str(inst.staged_bridge_path()))


class TestApply:
    def test_install_stages_the_bridge_and_writes_a_receipt(self, home):
        report = inst.apply_install(inst.plan("install"))
        assert inst.staged_bridge_path().is_file()
        assert inst.receipt_path().is_file()
        assert report["receipt"]["artifacts"][str(inst.staged_bridge_path())]

    def test_install_reports_requires_restart_not_success(self, home):
        """The DAT still has to be created inside the running project.
        Reporting 'installed' here is the lie this module exists to avoid."""
        report = inst.apply_install(inst.plan("install"))
        assert report["status"] == "requires_restart"
        assert report["exit_code"] == inst.EXIT_REQUIRES_RESTART

    def test_install_is_idempotent(self, home):
        inst.apply_install(inst.plan("install"))
        first = inst.receipt_path().read_text(encoding="utf-8")
        inst.apply_install(inst.plan("install"))
        assert inst.receipt_path().read_text(encoding="utf-8") == first

    def test_a_plan_without_artifacts_fails_at_acquire(self, home):
        report = inst.plan("install")
        report.pop("_artifacts")
        with pytest.raises(inst.InstallFailure) as e:
            inst.apply_install(report)
        assert e.value.exit_code == inst.EXIT_ACQUIRE


class TestStatus:
    def test_absent_before_install(self, home):
        assert inst.status()["installation_state"] == "absent"

    def test_installed_after_install(self, home):
        inst.apply_install(inst.plan("install"))
        assert inst.status()["installation_state"] == "installed"

    def test_drift_is_detected_by_digest(self, home):
        """A hand-edited staged artifact is not the one we vouched for."""
        inst.apply_install(inst.plan("install"))
        inst.staged_bridge_path().write_text("tampered", encoding="utf-8")
        st = inst.status()
        assert st["installation_state"] == "drifted"
        assert str(inst.staged_bridge_path()) in st["drifted_artifacts"]

    def test_a_deleted_artifact_counts_as_drift(self, home):
        inst.apply_install(inst.plan("install"))
        inst.staged_bridge_path().unlink()
        assert inst.status()["installation_state"] == "drifted"


class TestVerify:
    def test_refuses_before_install(self, home):
        report = inst.verify()
        assert report["verify"]["directly_usable"] is False
        assert report["verify"]["failure_stage"] == "install"
        assert report["exit_code"] == inst.EXIT_VERIFY

    def test_refuses_on_drift(self, home):
        inst.apply_install(inst.plan("install"))
        inst.staged_bridge_path().write_text("tampered", encoding="utf-8")
        report = inst.verify()
        assert report["verify"]["directly_usable"] is False
        assert "do not match the receipt" in report["verify"]["failure_reason"]

    def test_directly_usable_after_a_full_install(self, home):
        """Includes a real read-only probe, not an import: importing proves
        the module parses, not that it answers."""
        inst.apply_install(inst.plan("install"))
        report = inst.verify()
        assert report["verify"]["directly_usable"] is True, report["checks"]
        assert report["exit_code"] == inst.EXIT_OK
        assert [c["id"] for c in report["checks"]][-1] == "offline-probe"

    def test_a_failing_probe_is_reported_not_swallowed(self, home, monkeypatch):
        inst.apply_install(inst.plan("install"))
        monkeypatch.setattr(inst, "_probe_offline",
                            lambda: {"ok": False, "detail": "boom"})
        report = inst.verify()
        assert report["verify"]["failure_reason"] == "boom"


class TestUninstall:
    def test_removes_only_what_the_receipt_lists(self, home):
        inst.apply_install(inst.plan("install"))
        stray = inst.integration_root() / "not_ours.txt"
        stray.write_text("keep me", encoding="utf-8")
        out = inst.uninstall()
        assert out["status"] == "ok"
        assert not inst.staged_bridge_path().exists()
        assert not inst.receipt_path().exists()
        assert stray.read_text(encoding="utf-8") == "keep me"

    def test_says_what_it_cannot_remove(self, home):
        inst.apply_install(inst.plan("install"))
        out = inst.uninstall()
        assert out["next_steps"][0]["id"] == "remove-bridge-dat"

    def test_uninstall_with_no_install_is_not_an_error(self, home):
        assert inst.uninstall()["status"] == "ok"


class TestVerbGuards:
    def test_mutating_verbs_need_yes(self, home):
        report = inst.run("install")
        assert report["status"] == "planned"
        assert "--yes" in report["note"]
        assert not inst.receipt_path().exists()

    def test_dry_run_never_writes_even_with_yes(self, home):
        report = inst.run("install", assume_yes=True, dry_run=True)
        assert report["status"] == "planned"
        assert not inst.receipt_path().exists()

    def test_uninstall_needs_yes(self, home):
        inst.apply_install(inst.plan("install"))
        inst.run("uninstall")
        assert inst.receipt_path().exists()

    def test_read_only_verbs_need_nothing(self, home):
        assert inst.run("status")["exit_code"] == inst.EXIT_OK
        assert inst.run("verify")["verb"] == "verify"


class TestCli:
    def test_json_output_is_parseable(self, home, capsys):
        code = inst.main(["status", "--json"])
        assert code == inst.EXIT_OK
        assert json.loads(capsys.readouterr().out)["verb"] == "status"

    def test_install_exits_requires_restart(self, home, capsys):
        code = inst.main(["install", "--yes", "--json"])
        assert code == inst.EXIT_REQUIRES_RESTART
        assert json.loads(capsys.readouterr().out)["status"] == "requires_restart"

    def test_verify_exits_with_the_verify_code(self, home, capsys):
        code = inst.main(["verify", "--json"])
        capsys.readouterr()
        assert code == inst.EXIT_VERIFY

    def test_human_output_names_the_touchdesigner_step(self, home, capsys):
        inst.main(["install", "--yes"])
        out = capsys.readouterr().out
        assert "Next, inside TouchDesigner" in out
        assert "Text DAT" in out

    def test_each_stage_has_its_own_exit_code(self):
        codes = {inst.EXIT_OK, inst.EXIT_PREFLIGHT, inst.EXIT_ACQUIRE,
                 inst.EXIT_INSTALL, inst.EXIT_VERIFY,
                 inst.EXIT_REQUIRES_RESTART}
        assert len(codes) == 6
