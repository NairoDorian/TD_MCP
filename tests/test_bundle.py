"""Tests for td_mcp.bundle (.mcpb packaging)."""

import os
import tempfile
import zipfile

from td_mcp.bundle import build_manifest, package, read_manifest


def test_package_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        proj = os.path.join(d, "myproj")
        os.makedirs(os.path.join(proj, "sub"))
        with open(os.path.join(proj, "server.py"), "w") as f:
            f.write("print('hi')")
        with open(os.path.join(proj, "sub", "x.txt"), "w") as f:
            f.write("data")
        out = os.path.join(d, "myproj.mcpb")
        manifest = build_manifest("myproj", "uv", ["run", "myproj"])
        path = package(proj, out, manifest=manifest)
        assert os.path.exists(path)
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            assert "server.json" in names
            assert "server.py" in names
        m = read_manifest(path)
        assert m["name"] == "myproj"
        assert m["format"] == "mcpb"


def test_package_excludes_venv():
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
