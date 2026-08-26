"""The TouchDesigner component builder (`bridge/bootstrap.py`).

Until v1.12.0 this file held a **second, forked copy of the whole bridge** —
39 tools against the bridge's 46, `COMP.create` called with reversed arguments
at three sites, and every blind `node.errors` read Wave 12 fixed. Running it
installed a broken bridge, and `check_bridge_stale.py` never noticed because it
only guards `td_mcp_bridge.py`.

It is now a builder that *references* the one bridge. These tests drive it
against the TD API double, so the component it builds is verified rather than
hoped for — and `test_no_forked_bridge_logic` makes the fork impossible to
reintroduce quietly.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from tests.td_double import TDEnv

ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = ROOT / "bridge" / "bootstrap.py"


def _load():
    spec = importlib.util.spec_from_file_location("td_bootstrap", BOOTSTRAP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def boot():
    return _load()


_ENV_KEYS = ("TD_MCP_PORT", "TD_MCP_AUTH_TOKEN", "TD_MCP_ALLOW_EXEC",
             "TD_MCP_EXEC_MODE", "TD_MCP_PROTECTED_PATHS")


@pytest.fixture()
def td():
    """A TD environment that stops any bridge it started and restores the
    variables the startup DAT wrote into os.environ."""
    before = {k: os.environ.get(k) for k in _ENV_KEYS}
    env = TDEnv()
    env.make("project1_placeholder", "nullTOP")
    yield env
    env.shutdown()
    for k, v in before.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _build(boot, td, real_start=False, **kw):
    """Run `build()` with the double's globals bound into the module.

    `real_start=False` stubs `_try_start`: most tests are about the component,
    and a real start compiles the whole bridge and binds a socket. The tests
    in `TestStart` opt in.
    """
    for name, value in td.globals().items():
        setattr(boot, name, value)
    if not real_start:
        boot._try_start = lambda comp, startup: ([], False)
    kw.setdefault("script_dir", str(ROOT / "bridge"))
    kw.setdefault("target", "/project1")
    return boot.build(**kw)


# ---------------------------------------------------------------------------
# The fork must not come back
# ---------------------------------------------------------------------------
def test_no_forked_bridge_logic():
    """One copy of the bridge, not two.

    A `_DISPATCH` table or a `_do_*` handler here means the fork is back and
    the two copies will drift again — silently, because nothing else compares
    them.
    """
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert "_DISPATCH = {" not in text
    assert "def _do_" not in text
    # A smell threshold, not the real guard -- the two checks above are. The
    # fork was 1,000+ lines, so anything approaching that is worth a look.
    assert len(text.splitlines()) < 700, "bootstrap.py is growing a bridge again"


def test_it_references_the_real_bridge_filename(boot):
    assert boot.BRIDGE_FILENAME == "td_mcp_bridge.py"
    assert (ROOT / "bridge" / boot.BRIDGE_FILENAME).is_file()


def test_importable_outside_touchdesigner(boot):
    """It must not build on import — pytest has no `op`."""
    assert boot._inside_touchdesigner() is False


# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------
class TestResolveMode:
    def test_auto_links_when_the_source_is_readable(self, boot):
        assert boot.resolve_mode("auto", True, False) == "link"

    def test_auto_links_when_the_source_cannot_be_read(self, boot):
        """Embedding is precisely the mode that needs the text, so choosing it
        when the text is unavailable guarantees a failure one step later."""
        assert boot.resolve_mode("auto", False, False) == "link"

    def test_saving_a_tox_always_embeds(self, boot):
        """A .tox travels to machines that never saw this repo; a file-linked
        DAT there loads empty, and an empty bridge DAT fails as 'no module
        named start' rather than as 'the file is missing'."""
        assert boot.resolve_mode("link", True, True) == "embed"

    def test_explicit_modes_win_over_auto(self, boot):
        assert boot.resolve_mode("embed", True, False) == "embed"
        assert boot.resolve_mode("link", False, False) == "link"


# ---------------------------------------------------------------------------
# The component it builds
# ---------------------------------------------------------------------------
class TestBuild:
    def test_creates_the_component_with_the_expected_children(self, boot, td):
        comp = _build(boot, td)
        assert comp.name == "td_mcp"
        assert {c.name for c in comp.children} == {"bridge", "startup", "control"}

    def test_component_types_are_right(self, boot, td):
        comp = _build(boot, td)
        # OPType ('textDAT') is what COMP.create() takes; `type` is the
        # short form ('text'). OP_Class distinguishes them and so does the
        # double now -- a recipe recording `type` cannot be rebuilt.
        assert {c.name: c.OPType for c in comp.children} == {
            "bridge": "textDAT", "startup": "executeDAT",
            "control": "parameterexecuteDAT"}
        assert {c.name: c.type for c in comp.children} == {
            "bridge": "text", "startup": "execute", "control": "parameterexecute"}

    def test_link_mode_points_the_dat_at_the_file_and_syncs(self, boot, td):
        comp = _build(boot, td, mode="link")
        bridge = comp.op("bridge")
        assert bridge.par.file.val.endswith("td_mcp_bridge.py")
        assert bridge.par.syncfile.val is True

    def test_embed_mode_pastes_the_source_and_clears_the_link(self, boot, td):
        comp = _build(boot, td, mode="embed")
        bridge = comp.op("bridge")
        assert "BRIDGE_VERSION" in bridge.text
        assert bridge.par.syncfile.val is False
        assert bridge.par.file.val == ""

    def test_embedded_source_is_the_real_bridge(self, boot, td):
        comp = _build(boot, td, mode="embed")
        on_disk = (ROOT / "bridge" / "td_mcp_bridge.py").read_text(encoding="utf-8")
        assert comp.op("bridge").text == on_disk

    def test_startup_fires_on_start_and_on_create(self, boot, td):
        """A dragged-in .tox never sees Start. Without Create the component
        looks inert until the next restart."""
        startup = _build(boot, td).op("startup")
        assert startup.par.start.val is True
        assert startup.par.create.val is True
        assert startup.par.exit.val is True

    def test_parameter_callbacks_watch_the_owning_comp(self, boot, td):
        """`onpulse`, not `pulse` — the guess that raised
        "'td.ParCollection' object has no attribute 'pulse'" on the first
        real run. And `pars` must name something: an empty Parameters field
        watches nothing, so the callbacks would never fire."""
        control = _build(boot, td).op("control")
        assert control.par.op.val == ".."
        assert control.par.pars.val == "*"
        assert control.par.active.val is True
        assert control.par.valuechange.val is True
        assert control.par.onpulse.val is True
        assert control.par["pulse"] is None  # the name that shipped, and does not exist

    def test_the_build_sets_every_parameter_it_intends_to(self, boot, td):
        """`set_pars` collects failures instead of aborting, so a wrong name
        would otherwise degrade quietly. This is the gate that keeps it loud."""
        problems = []
        real = boot.set_pars

        def recording(node, **values):
            failed = real(node, **values)
            problems.extend(failed)
            return failed

        boot.set_pars = recording
        _build(boot, td)
        assert problems == []

    def test_rebuild_is_idempotent(self, boot, td):
        first = _build(boot, td)
        second = _build(boot, td)
        assert first is second
        assert len(second.children) == 3
        assert len(second.customPages) == 1

    def test_rebuild_preserves_a_configured_port(self, boot, td):
        comp = _build(boot, td)
        comp.par.Port.val = 9999
        _build(boot, td)
        assert comp.par.Port.val == 9999

    def test_embed_refuses_a_source_it_cannot_read(self, boot, td, monkeypatch):
        """Found on disk but unreadable — a permissions problem or a dropped
        network drive. Linking would still work; embedding cannot."""
        real = str(ROOT / "bridge" / "td_mcp_bridge.py")
        monkeypatch.setattr(boot, "locate_bridge",
                            lambda **kw: (real, None, [real]))
        with pytest.raises(RuntimeError, match="cannot embed"):
            _build(boot, td, mode="embed")

    def test_link_still_works_when_the_source_is_unreadable(self, boot, td,
                                                            monkeypatch):
        """TouchDesigner does the reading in link mode, so this process not
        being able to is not fatal."""
        real = str(ROOT / "bridge" / "td_mcp_bridge.py")
        monkeypatch.setattr(boot, "locate_bridge",
                            lambda **kw: (real, None, [real]))
        comp = _build(boot, td, mode="link")
        assert comp.op("bridge").par.syncfile.val is True


class TestCustomParameters:
    def test_every_documented_parameter_exists(self, boot, td):
        comp = _build(boot, td)
        for name, _kind, _default, _label in boot.CUSTOM_PARS:
            # ParCollection has no name-based `in`; `par['name']` is None if absent.
            assert comp.par[name] is not None, name

    def test_the_page_is_named_for_the_project(self, boot, td):
        assert _build(boot, td).customPages[0].name == "TD MCP"

    def test_active_defaults_on_so_a_drop_in_just_works(self, boot, td):
        assert _build(boot, td).par.Active.val is True

    def test_port_default_follows_the_builder(self, boot, td):
        assert _build(boot, td, port=9765).par.Port.val == 9765

    def test_token_defaults_empty_so_the_bridge_mints_one(self, boot, td):
        assert _build(boot, td).par.Token.val == ""


class TestGeneratedCallbacks:
    def test_startup_sets_env_before_touching_the_module(self, boot):
        """The bridge reads os.environ at import time and a Text DAT imports
        lazily on first `.module` access, so the order is load-bearing."""
        code = boot.STARTUP_CODE
        assert code.index("_apply_env()") < code.index("bridge.module.start()")

    def test_startup_code_is_valid_python(self, boot):
        compile(boot.STARTUP_CODE, "startup", "exec")

    def test_parexec_code_is_valid_python(self, boot):
        compile(boot.PAREXEC_CODE, "control", "exec")

    def test_startup_exposes_the_callbacks_the_parexec_calls(self, boot):
        for fn in ("start_bridge", "stop_bridge"):
            assert f"def {fn}(" in boot.STARTUP_CODE
            assert f"ctl.module.{fn}()" in boot.PAREXEC_CODE

    def test_changing_the_port_bounces_a_live_bridge(self, boot):
        """Otherwise the panel shows the new port while the old one is held."""
        assert "'Port', 'Token', 'Allowexec', 'Protectedpaths'" in boot.PAREXEC_CODE

    def test_exit_stops_the_bridge(self, boot):
        assert "def onExit()" in boot.STARTUP_CODE


class TestClientConfig:
    def test_emits_a_registerable_block(self, boot):
        cfg = boot.client_config(9980, "abc123", r"C:\repo\TD_MCP")
        server = cfg["mcpServers"]["td-mcp-live"]
        assert server["env"]["TD_MCP_AUTH_TOKEN"] == "abc123"
        assert server["env"]["TD_MCP_PORT"] == "9980"

    def test_windows_paths_are_normalised(self, boot):
        cfg = boot.client_config(9980, "t", r"C:\repo\TD_MCP")
        assert "\\" not in " ".join(cfg["mcpServers"]["td-mcp-live"]["args"])


# ---------------------------------------------------------------------------
# Locating the bridge source (field-reported, 2026-08-26)
#
# The first person to run this in TouchDesigner pasted it into a Text DAT named
# /project1/bootstrap and hit:
#
#   RuntimeError: cannot embed the bridge:
#   C:\project1\td_mcp_bridge.py is not readable.
#
# Two bugs. `__file__` inside a DAT is the *operator path*, so
# os.path.abspath('/project1/bootstrap') became 'C:\project1\bootstrap' and the
# lookup beside it named a folder that never existed. And `resolve_mode` fell
# back to "embed" when the source was unreadable — the one mode that cannot
# work without it. The documented `exec(open(...).read())` path was equally
# broken: it leaves `__file__` undefined, so build() raised NameError.
# ---------------------------------------------------------------------------
class TestLocateBridge:
    def test_finds_the_bridge_next_to_an_explicit_script_dir(self, boot):
        path, text, _ = boot.locate_bridge(script_dir=str(ROOT / "bridge"))
        assert path.endswith("td_mcp_bridge.py")
        assert "BRIDGE_VERSION" in text

    def test_repo_root_is_accepted_as_well_as_the_bridge_folder(self, boot):
        path, _, _ = boot.locate_bridge(repo=str(ROOT))
        assert os.path.isfile(path)

    def test_an_explicit_bridge_path_wins(self, boot):
        target = str(ROOT / "bridge" / "td_mcp_bridge.py")
        path, _, _ = boot.locate_bridge(bridge_path=target)
        assert os.path.normpath(path) == os.path.normpath(target)

    def test_a_bad_explicit_path_is_refused_with_guidance(self, boot, tmp_path):
        with pytest.raises(RuntimeError) as e:
            boot.locate_bridge(bridge_path=str(tmp_path / "nope.py"))
        assert "TD_MCP_REPO" in str(e.value)

    def test_not_found_lists_where_it_looked(self, boot, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(boot, "_dat_file_dir", lambda: None)
        monkeypatch.setattr(boot, "_real_file_dir", lambda: None)
        with pytest.raises(RuntimeError) as e:
            boot.locate_bridge()
        message = str(e.value)
        assert "Looked in:" in message
        assert "Run Script" in message          # names the easiest fix
        assert "Text DAT" in message            # and the DAT-paste caveat


class TestFileDiscoveryInsideTouchDesigner:
    def test_a_dat_operator_path_is_not_mistaken_for_a_file(self, boot, monkeypatch):
        """The reported bug: `__file__` == '/project1/bootstrap' is an operator
        path, and abspath() turns it into a folder that never existed."""
        monkeypatch.setitem(boot.__dict__, "__file__", "/project1/bootstrap")
        assert boot._real_file_dir() is None

    def test_a_real_file_is_used(self, boot):
        monkeypatch_file = str(ROOT / "bridge" / "bootstrap.py")
        boot.__dict__["__file__"] = monkeypatch_file
        assert boot._real_file_dir() == str(ROOT / "bridge")

    def test_missing_dunder_file_is_not_fatal(self, boot):
        """`exec(open(...).read())` in the Textport leaves it undefined —
        the documented invocation, which used to raise NameError."""
        boot.__dict__.pop("__file__", None)
        assert boot._real_file_dir() is None

    def test_a_file_backed_dat_locates_itself(self, boot, td, monkeypatch):
        """Drag bootstrap.py into the network and TD sets the DAT's File
        parameter — which is the folder td_mcp_bridge.py sits in."""
        from tests.td_double import FakeDAT, FakePar

        dat = FakeDAT("bootstrap", "textDAT")
        dat.par._add(FakePar("file", str(ROOT / "bridge" / "bootstrap.py")))
        monkeypatch.setattr(boot, "me", dat, raising=False)
        assert boot._dat_file_dir() == str(ROOT / "bridge")

    def test_a_pasted_dat_has_no_file_and_says_so(self, boot, monkeypatch):
        from tests.td_double import FakeDAT, FakePar

        dat = FakeDAT("bootstrap", "textDAT")
        dat.par._add(FakePar("file", ""))
        monkeypatch.setattr(boot, "me", dat, raising=False)
        assert boot._dat_file_dir() is None

    def test_no_me_at_all_is_not_fatal(self, boot, monkeypatch):
        monkeypatch.delattr(boot, "me", raising=False)
        assert boot._dat_file_dir() is None


class TestBuildLocatesSource:
    def test_build_works_from_a_repo_path_alone(self, boot, td):
        """No script_dir, no __file__ — the Textport TD_MCP_REPO path."""
        boot.__dict__.pop("__file__", None)
        comp = _build(boot, td, script_dir=None, repo=str(ROOT))
        assert comp.op("bridge") is not None

    def test_build_reports_a_missing_source_actionably(self, boot, td, tmp_path,
                                                       monkeypatch):
        for name, value in td.globals().items():
            setattr(boot, name, value)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(boot, "_dat_file_dir", lambda: None)
        monkeypatch.setattr(boot, "_real_file_dir", lambda: None)
        with pytest.raises(RuntimeError) as e:
            boot.build(target="/project1", script_dir=str(tmp_path))
        assert "TD_MCP_REPO" in str(e.value)


# ---------------------------------------------------------------------------
# Starting the bridge at build time (field-reported, 2026-08-26)
#
# The third real run built the component, and then the operator toggled
# Active and got six stacked tracebacks ending in "Module compilation error.
# See /project1/td_mcp/bridge for details." The bridge had never compiled in
# TouchDesigner. The builder now starts it immediately, so that failure is one
# line at build time that says where to look -- and these tests run the whole
# chain: startup DAT module -> environment applied -> bridge DAT module
# compiled with td_mcp blocked -> a real server bound on an ephemeral port.
# ---------------------------------------------------------------------------
class TestStart:
    def test_the_full_chain_starts_a_real_bridge(self, boot, td, capsys):
        comp = _build(boot, td, real_start=True, port=0)
        assert comp.op("bridge").module.status()["running"] is True
        out = capsys.readouterr().out
        assert "[td_mcp] auth token:" in out
        assert "listening on" in out

    def test_startup_applies_the_component_parameters_first(self, boot, td):
        """The bridge reads os.environ at import, and a DAT module imports on
        first `.module` access -- so the order is load-bearing."""
        comp = _build(boot, td, real_start=True, port=0)
        assert os.environ["TD_MCP_PORT"] == "0"
        assert os.environ["TD_MCP_ALLOW_EXEC"] == "1"
        assert comp.op("bridge").module.TD_MCP_PORT == 0

    def test_active_off_does_not_start(self, boot, td, capsys):
        boot.CUSTOM_PARS = tuple(
            (n, k, (False if n == "Active" else d), l)
            for n, k, d, l in boot.CUSTOM_PARS)
        comp = _build(boot, td, real_start=True, port=0)
        assert comp.op("bridge")._module is None
        assert "Active is off" in capsys.readouterr().out

    def test_a_bridge_that_will_not_compile_is_one_clear_line(self, boot, td,
                                                              capsys):
        real_locate = boot.locate_bridge

        def broken(**kw):
            path, _, searched = real_locate(**kw)
            return (path,
                    "from td_mcp.streamable_http import StreamableHTTPMixin\n",
                    searched)

        boot.locate_bridge = broken
        comp = _build(boot, td, real_start=True, mode="embed", port=0)
        out = capsys.readouterr().out
        assert "bridge did not start" in out
        assert "Module compilation error" in out
        assert comp.op("startup") is not None  # the component survived

    def test_a_started_bridge_answers_over_http(self, boot, td):
        """End to end: the builder's component, a real socket, a real tool."""
        import json
        import urllib.request

        comp = _build(boot, td, real_start=True, port=0)
        mod = comp.op("bridge").module
        port = mod._server.server_address[1]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/mcp",
            data=json.dumps({"tool": "describe_td_tools", "args": {}}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {mod.AUTH_TOKEN}"},
            method="POST")
        # td.frame_loop() drives the component's Execute DAT on Frame Start,
        # exactly as TouchDesigner does -- that is what drains the bridge's
        # main-thread queue and lets the request complete.
        with td.frame_loop(), urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read().decode("utf-8"))
        assert body["ok"] is True
        assert body["bridge_version"] == mod.BRIDGE_VERSION
        assert "get_errors" in body["tools"]

    def test_the_startup_dat_pumps_on_frame_start(self, boot, td):
        """The component wires Frame Start; the callback drains the queue."""
        comp = _build(boot, td, real_start=True, port=0)
        startup = comp.op("startup")
        assert startup.par.framestart.val is True
        mod = comp.op("bridge").module
        assert mod.status()["pump_alive"] is False
        startup.module.onFrameStart(1)
        assert mod.status()["pump_alive"] is True

    def test_frame_start_does_not_import_the_bridge_before_start(self, boot, td):
        """An early pump would import the bridge with default env -- the
        panel's port and token displayed but not in effect."""
        _build(boot, td)                      # _try_start stubbed: not started
        comp = td.project_comp.op("td_mcp")
        comp.op("startup").module.onFrameStart(1)
        assert comp.op("bridge")._module is None

    def test_without_the_frame_loop_a_request_fails_fast(self, boot, td):
        import json
        import urllib.error
        import urllib.request

        comp = _build(boot, td, real_start=True, port=0)
        mod = comp.op("bridge").module
        port = mod._server.server_address[1]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/mcp",
            data=json.dumps({"tool": "project_info", "args": {}}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {mod.AUTH_TOKEN}"},
            method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        assert "pump has never run" in e.value.read().decode("utf-8")
