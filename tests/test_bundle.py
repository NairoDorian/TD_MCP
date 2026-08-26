"""Tests for td_mcp.bundle (.mcpb packaging).

The contract under test is "an MCP host could install this", not "a zip was
produced". Before Wave 12 the module wrote a member named ``server.json`` with
a schema of its own invention: the file was a valid zip with a plausible
manifest inside and no host on earth could read it.
"""

import json
import os
import tempfile
import zipfile

import pytest

from td_mcp.bundle import (
    DIRNAME,
    MANIFEST_NAME,
    ManifestError,
    build_manifest,
    package,
    read_manifest,
    td_mcp_manifest,
    validate_manifest,
)


def _project(d, name="myproj"):
    proj = os.path.join(d, name)
    os.makedirs(os.path.join(proj, "sub"))
    with open(os.path.join(proj, "server.py"), "w") as f:
        f.write("print('hi')")
    with open(os.path.join(proj, "sub", "x.txt"), "w") as f:
        f.write("data")
    return proj


class TestManifestShape:
    def test_manifest_is_named_manifest_json(self):
        """The host reads manifest.json at the archive root. Nothing else."""
        with tempfile.TemporaryDirectory() as d:
            proj = _project(d)
            out = package(proj, os.path.join(d, "myproj.mcpb"),
                          manifest=build_manifest("myproj", "python",
                                                  ["-m", "myproj"]))
            with zipfile.ZipFile(out) as z:
                names = z.namelist()
            assert MANIFEST_NAME in names
            assert "server.json" not in names

    def test_manifest_carries_the_required_spec_fields(self):
        m = build_manifest("myproj", "python", ["-m", "myproj"])
        assert m["manifest_version"]
        assert m["server"]["type"] == "python"
        assert m["server"]["entry_point"]
        assert m["server"]["mcp_config"]["command"] == "python"
        assert m["server"]["mcp_config"]["args"] == ["-m", "myproj"]

    def test_legacy_dxt_version_alias_is_emitted(self):
        """Hosts in the field still read the pre-rename key."""
        m = build_manifest("myproj", "python", [])
        assert m["dxt_version"] == m["manifest_version"]

    def test_unknown_server_type_is_refused(self):
        with pytest.raises(ManifestError):
            build_manifest("x", "python", [], server_type="perl")


class TestValidation:
    def test_a_good_manifest_has_no_problems(self):
        assert validate_manifest(build_manifest("x", "python", ["-m", "x"])) == []

    def test_missing_server_block_is_caught(self):
        assert "missing server block" in validate_manifest(
            {"manifest_version": "0.2", "name": "x", "version": "1"})

    def test_missing_command_is_caught(self):
        m = build_manifest("x", "python", [])
        m["server"]["mcp_config"]["command"] = ""
        assert any("command" in p for p in validate_manifest(m))

    def test_absolute_path_baked_at_build_time_is_caught(self):
        """An absolute path points at the machine that built the bundle."""
        m = build_manifest("x", "python", ["-m", "x"],
                           env={"PYTHONPATH": "/home/builder/TD_MCP"})
        problems = validate_manifest(m)
        assert any("absolute path" in p for p in problems), problems

    def test_dirname_anchored_path_is_accepted(self):
        m = build_manifest("x", "python", ["-m", "x"],
                           env={"PYTHONPATH": DIRNAME + "/lib"})
        assert validate_manifest(m) == []

    def test_the_old_bespoke_schema_would_be_refused(self):
        """The exact shape this module used to emit."""
        legacy = {"name": "td-mcp", "version": "1.0.0", "description": "",
                  "command": "uv", "args": ["run"], "format": "mcpb"}
        problems = validate_manifest(legacy)
        assert "missing manifest_version" in problems
        assert "missing server block" in problems

    def test_package_refuses_an_uninstallable_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            proj = _project(d)
            with pytest.raises(ManifestError):
                package(proj, os.path.join(d, "bad.mcpb"),
                        manifest={"name": "x", "version": "1"})


class TestPackaging:
    def test_package_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            proj = _project(d)
            out = os.path.join(d, "myproj.mcpb")
            manifest = build_manifest("myproj", "python", ["-m", "myproj"])
            path = package(proj, out, manifest=manifest)
            assert os.path.exists(path)
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                assert MANIFEST_NAME in names
                assert "server.py" in names
            m = read_manifest(path)
            assert m["name"] == "myproj"
            assert m["server"]["mcp_config"]["command"] == "python"

    def test_package_excludes_venv(self):
        with tempfile.TemporaryDirectory() as d:
            proj = os.path.join(d, "p")
            os.makedirs(os.path.join(proj, ".venv"))
            with open(os.path.join(proj, "main.py"), "w") as f:
                f.write("x")
            with open(os.path.join(proj, ".venv", "big"), "w") as f:
                f.write("y")
            out = package(proj, os.path.join(d, "p.mcpb"))
            with zipfile.ZipFile(out) as z:
                assert any(n == "main.py" for n in z.namelist())
                assert all(not n.startswith(".venv") for n in z.namelist())

    def test_a_stray_manifest_in_the_tree_cannot_shadow_ours(self):
        with tempfile.TemporaryDirectory() as d:
            proj = _project(d)
            with open(os.path.join(proj, MANIFEST_NAME), "w") as f:
                json.dump({"name": "impostor"}, f)
            out = package(proj, os.path.join(d, "p.mcpb"),
                          manifest=build_manifest("real", "python", ["-m", "x"]))
            assert read_manifest(out)["name"] == "real"

    def test_read_manifest_reports_a_bundle_with_no_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "bad.mcpb")
            with zipfile.ZipFile(bad, "w") as z:
                z.writestr("server.json", "{}")
            with pytest.raises(ManifestError):
                read_manifest(bad)


class TestShippedManifest:
    def test_offline_manifest_installs(self):
        from td_mcp import __version__
        m = td_mcp_manifest(__version__)
        assert validate_manifest(m) == []
        assert m["server"]["mcp_config"]["args"] == [
            "-m", "td_mcp.server_offline", "--mcp"]
        assert m["server"]["mcp_config"]["env"]["PYTHONPATH"] == DIRNAME

    def test_live_manifest_installs_and_exposes_bridge_settings(self):
        m = td_mcp_manifest("1.11.0", live=True)
        assert validate_manifest(m) == []
        assert m["name"] == "td-mcp-live"
        assert set(m["user_config"]) == {"bridge_host", "bridge_port"}
