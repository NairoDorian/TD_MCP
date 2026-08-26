"""Bridge hardening absorbed from the ecosystem's last-15-days commits.

- code-bearing route guard      (Pantani/tdmcp #143, 2026-08-11)
- worker-thread run() lint      (dylanroscover/Embody v6.0.251, 2026-08-17)
- TD release identifier         (dcc-mcp/dcc-mcp-touchdesigner #5, 2026-08-13)
- diagnostic severity split     (Pantani/tdmcp #146, 2026-08-11)

Every helper here is exec'd out of the shipped bridge source (see
``tests/bridge_pure.py``), so these assertions bind the real file.
"""

from __future__ import annotations

import pytest

from tests.bridge_pure import load

BR = load()
code_bearing_sources = BR["code_bearing_sources"]
code_route_denied = BR["code_route_denied"]
worker_run_findings = BR["worker_run_findings"]
threading_warning = BR["threading_warning"]
touchdesigner_release = BR["touchdesigner_release"]
split_diagnostics = BR["split_diagnostics"]


# --- code-bearing route guard --------------------------------------------

@pytest.mark.parametrize("op_type", [
    "executeDAT", "scriptCHOP", "scriptSOP", "glslTOP",
    "glslmultiTOP", "expressionCHOP", "evaluateDAT", "shaderparkTOP",
])
def test_executable_operator_types_are_code_bearing(op_type):
    assert code_bearing_sources(op_type=op_type)


def test_creating_a_bare_text_dat_is_not_itself_a_code_route():
    # A Text DAT created empty executes nothing. The code route is the write
    # that puts source into it, which set_dat_text guards separately -- so
    # blocking the creation too would only cost the caller a scratch buffer.
    assert code_bearing_sources(op_type="textDAT") == []
    assert code_bearing_sources(op_type="textDAT", text="print(1)")


@pytest.mark.parametrize("op_type", ["noiseTOP", "blurTOP", "mergeCHOP", "boxSOP"])
def test_inert_operator_types_are_not_code_bearing(op_type):
    assert code_bearing_sources(op_type=op_type) == []


def test_expression_form_is_code_regardless_of_parameter_name():
    # A perfectly ordinary parameter turns into code the moment it is set
    # as an expression rather than a value.
    assert code_bearing_sources(op_type="noiseTOP",
                                params={"period": {"expr": "absTime.seconds"}})
    assert code_bearing_sources(op_type="noiseTOP", params={"period": 4.0}) == []


@pytest.mark.parametrize("param", [
    "code", "expr", "expression", "bindexpr", "periodexpr",
    "callbacks", "python", "script", "glsldecl", "shaderbody", "ext1object",
])
def test_code_bearing_parameter_names(param):
    assert code_bearing_sources(op_type="containerCOMP", params={param: "x"})


def test_type_specific_filter_parameters():
    # groupSOP's filter is evaluated as Python; a Noise TOP has no such param.
    assert code_bearing_sources(op_type="groupSOP", params={"filter": "@P.y > 0"})
    assert code_bearing_sources(op_type="noiseTOP", params={"filter": "x"}) == []


def test_dat_file_parameter_is_a_code_route():
    # Pointing a DAT at a file loads that file as the DAT's text.
    assert code_bearing_sources(op_type="textDAT", params={"file": "/tmp/x.py"})
    # ...but a file parameter on a Movie File In TOP is just media.
    assert code_bearing_sources(op_type="moviefileinTOP",
                                params={"file": "/tmp/x.mov"}) == []


def test_dat_text_write_is_a_code_route():
    assert "DAT text" in code_bearing_sources(text="print(1)")
    assert "DAT text" in code_bearing_sources(op_type="textDAT", text="print(1)")


def test_sources_are_deduplicated_and_ordered():
    sources = code_bearing_sources(op_type="scriptCHOP",
                                   params={"callbacks": "a", "expr": "b"})
    assert sources[0].startswith("operator type")
    assert len(sources) == len(set(sources))


def test_denial_names_the_action_and_the_sources():
    denial = code_route_denied("set_parameters", ["parameter expr"])
    assert denial["ok"] is False
    assert "set_parameters" in denial["error"]
    assert "parameter expr" in denial["error"]
    assert denial["code_sources"] == ["parameter expr"]


def test_every_known_bypass_is_classified_as_code():
    """The routes that made TD_MCP_EXEC_MODE=off a false claim.

    Each entry is a real way an agent could run Python inside TouchDesigner
    while execute_python was blocked. If any of these stops being detected,
    exec-off silently becomes a lie again.
    """
    bypasses = [
        # (label, kwargs)
        ("expression on an ordinary parameter",
         dict(op_type="noiseTOP", params={"period": {"expr": "op('x').cook()"}})),
        ("a Script CHOP's callbacks",
         dict(op_type="scriptCHOP", params={"callbacks": "def cook(): ..."})),
        ("an Execute DAT",
         dict(op_type="executeDAT")),
        ("a GLSL TOP's shader",
         dict(op_type="glslTOP", params={"pixeldat": "x"})),
        ("a DAT pointed at a file on disk",
         dict(op_type="textDAT", params={"file": "/tmp/payload.py"})),
        ("writing a DAT's text",
         dict(text="import os; os.system('...')")),
        ("a parameter-mode expression field",
         dict(op_type="containerCOMP", params={"bindexpr": "x"})),
    ]
    missed = [label for label, kwargs in bypasses
              if not code_bearing_sources(**kwargs)]
    assert missed == [], "exec-off no longer blocks: " + ", ".join(missed)


def test_guards_are_wired_into_every_code_bearing_handler():
    """Structural: detection is useless if a handler forgets to call it."""
    import os

    path = os.path.join(os.path.dirname(__file__), "..", "bridge", "td_mcp_bridge.py")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()

    guarded = {
        "_do_create": "code_bearing_sources",
        "_do_set_parameters": "code_bearing_sources",
        "_do_bind_parameter_expression": "code_route_denied",
        "_do_set_dat_text": "code_route_denied",
        "_do_exec_node_method": "code_route_denied",
        "_do_import_recipe": "code_bearing_sources",
    }
    for handler, expected in guarded.items():
        start = source.index("def %s(" % handler)
        end = source.index("\ndef ", start + 1)
        body = source[start:end]
        assert "ALLOW_EXEC" in body, "%s does not check ALLOW_EXEC" % handler
        assert expected in body, "%s does not call %s" % (handler, expected)


def test_threading_lint_is_wired_into_both_write_paths():
    import os

    path = os.path.join(os.path.dirname(__file__), "..", "bridge", "td_mcp_bridge.py")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    for handler in ("_do_execute_python", "_do_set_dat_text"):
        start = source.index("def %s(" % handler)
        end = source.index("\ndef ", start + 1)
        assert "threading_warning" in source[start:end], \
            "%s does not lint submitted code" % handler


# --- worker-thread run() lint --------------------------------------------

def test_thread_target_calling_run_is_flagged():
    src = (
        "import threading\n"
        "def worker():\n"
        "    run('op(\"x\").cook()')\n"
        "threading.Thread(target=worker).start()\n"
    )
    findings = worker_run_findings(src)
    assert [f["function"] for f in findings] == ["worker"]
    assert findings[0]["call"] == "run()"
    assert findings[0]["line"] == 3


def test_helper_one_level_down_is_flagged_with_via():
    src = (
        "import threading\n"
        "def helper():\n"
        "    run('x')\n"
        "def worker():\n"
        "    helper()\n"
        "threading.Thread(target=worker).start()\n"
    )
    findings = worker_run_findings(src)
    assert findings and findings[0]["via"] == "helper"
    assert findings[0]["function"] == "worker"


def test_thread_subclass_run_override_is_flagged():
    src = (
        "import threading\n"
        "class W(threading.Thread):\n"
        "    def run(self):\n"
        "        run('x')\n"
    )
    assert [f["function"] for f in worker_run_findings(src)] == ["W.run"]


def test_lambda_target_is_flagged():
    src = "import threading\nthreading.Thread(target=lambda: run('x')).start()\n"
    assert [f["function"] for f in worker_run_findings(src)] == ["<lambda>"]


def test_executor_submit_positional_target_is_flagged():
    src = (
        "def worker():\n"
        "    run('x')\n"
        "executor.submit(worker)\n"
    )
    assert worker_run_findings(src)


def test_thread_positional_first_arg_is_group_not_target():
    # Thread's first positional is `group`, never the callable -- reading it
    # as a target would be a false positive.
    src = (
        "import threading\n"
        "def worker():\n"
        "    run('x')\n"
        "threading.Thread(None, some_other_fn).start()\n"
    )
    assert worker_run_findings(src) == []


def test_main_thread_run_is_not_flagged():
    assert worker_run_findings("def go():\n    run('x')\ngo()\n") == []


def test_td_dot_run_is_flagged_but_other_dotted_run_is_not():
    hostile = (
        "import threading\n"
        "def worker():\n"
        "    td.run('x')\n"
        "threading.Thread(target=worker).start()\n"
    )
    assert [f["call"] for f in worker_run_findings(hostile)] == ["td.run()"]

    benign = (
        "import threading, subprocess\n"
        "def worker():\n"
        "    subprocess.run(['ls'])\n"
        "threading.Thread(target=worker).start()\n"
    )
    assert worker_run_findings(benign) == []


def test_module_scope_rebinding_of_run_suppresses_bare_run():
    src = (
        "import threading\n"
        "def run(x):\n"
        "    return x\n"
        "def worker():\n"
        "    run('x')\n"
        "threading.Thread(target=worker).start()\n"
    )
    assert worker_run_findings(src) == []


def test_a_method_named_run_does_not_suppress_the_lint():
    # Every threading.Thread subclass has a run method; treating that as a
    # rebinding would silence the lint on precisely the code it exists for.
    src = (
        "import threading\n"
        "class W(threading.Thread):\n"
        "    def run(self):\n"
        "        run('x')\n"
    )
    assert worker_run_findings(src)


def test_unparseable_source_lints_to_nothing():
    # Submitted code may be a fragment; a lint must never fail a write.
    assert worker_run_findings("def broken(:\n  run('x')") == []


def test_oversized_source_skips_the_parse():
    huge = "# pad\n" * 40000 + "run('x')\n"
    assert len(huge) > 131072
    assert worker_run_findings(huge) == []


def test_source_without_run_skips_the_parse():
    assert worker_run_findings("import threading\nthreading.Thread(target=f)\n") == []


@pytest.mark.parametrize("bad", [None, "", 123, [], {}])
def test_non_string_source_is_safe(bad):
    assert worker_run_findings(bad) == []


def test_findings_are_capped():
    body = "".join("    run('x%d')\n" % i for i in range(200))
    src = "import threading\ndef worker():\n" + body + "threading.Thread(target=worker).start()\n"
    assert len(worker_run_findings(src)) == 64


def test_threading_warning_is_actionable_or_absent():
    src = (
        "import threading\n"
        "def worker():\n"
        "    run('x')\n"
        "threading.Thread(target=worker).start()\n"
    )
    warning = threading_warning(src, "execute_python")
    assert "THREADING WARNING" in warning
    assert "execute_python" in warning
    assert "worker" in warning
    assert "main thread" in warning
    assert threading_warning("x = 1", "execute_python") is None


# --- TD release identifier ------------------------------------------------

def test_release_prefers_the_public_build_identifier():
    # app.version reports the legacy generation; app.build carries year.build.
    assert touchdesigner_release("099", "2025.32820") == "2025.32820"


def test_release_combines_a_bare_numeric_build():
    assert touchdesigner_release("099", "30000") == "099.30000"


def test_release_falls_back_to_version_then_unknown():
    assert touchdesigner_release("2023.11880", "") == "2023.11880"
    assert touchdesigner_release(None, None) == "unknown"
    assert touchdesigner_release("", "") == "unknown"


def test_release_is_comparable_against_corpus_builds():
    # The point of the fix: the returned id must parse as year.build so
    # compat checks can compare it against corpus records.
    release = touchdesigner_release("099", "2025.32820")
    assert int(release.split(".")[0]) >= 2023


# --- diagnostic severity split -------------------------------------------

def test_diagnostics_split_counts_each_severity():
    split = split_diagnostics(["boom", "bang"], ["heads up"])
    assert split["total"] == 3
    assert split["error_count"] == 2
    assert split["warning_count"] == 1


def test_warnings_are_not_counted_as_errors():
    split = split_diagnostics([], ["just a warning"])
    assert split["error_count"] == 0
    assert split["warning_count"] == 1
    assert split["diagnostics"][0]["severity"] == "warning"


def test_diagnostics_split_handles_empty_and_none():
    for errors, warnings in ((None, None), ([], [])):
        split = split_diagnostics(errors, warnings)
        assert split == {"diagnostics": [], "total": 0,
                         "error_count": 0, "warning_count": 0}


def test_diagnostics_stringify_non_string_entries():
    class Err:
        def __str__(self):
            return "cook error"

    split = split_diagnostics([Err()], [])
    assert split["diagnostics"][0]["message"] == "cook error"


class TestForbiddenBuiltinsAreNamesNotSubstrings:
    """`execute_python` refused most legitimate TouchDesigner Python.

    The check was `if name in code` over the raw source, and `par.eval()` --
    the commonest call in TD Python -- contains "eval". Reading any parameter
    was refused. It cost two silent probe failures in one live session before
    the error text was read. Substring matching was never a defence either:
    `getattr(__builtins__, 'ev' + 'al')` walks straight past it.
    """

    def _f(self):
        from tests.bridge_pure import load
        return load()["forbidden_builtins"]

    def test_par_eval_is_allowed(self):
        assert self._f()('x = op("/a").par.file.eval()') == []

    def test_every_attribute_form_is_allowed(self):
        f = self._f()
        for code in ("me.par.Tx.eval()", "d.par.file.eval()",
                     "result = n.par.w.eval() + n.par.h.eval()"):
            assert f(code) == [], code

    def test_a_bare_builtin_call_is_caught(self):
        assert self._f()('eval("1+1")') == ["eval"]

    def test_an_alias_is_caught(self):
        """`f = eval` then `f(...)` -- the name is what matters, not the call."""
        assert self._f()("f = eval\nf('1')") == ["eval"]

    def test_a_mention_in_a_string_is_not_code(self):
        assert self._f()('result = "eval and open and exec"') == []

    def test_a_variable_that_merely_contains_the_word_is_allowed(self):
        f = self._f()
        assert f("retrieval = 1") == []
        assert f("opened = True") == []
        assert f("compiled_shader = 2") == []

    def test_each_forbidden_builtin_is_still_caught(self):
        f = self._f()
        from tests.bridge_pure import load
        for name in load()["FORBIDDEN_BUILTINS"]:
            assert f(f"x = {name}") == [name], name

    def test_several_are_reported_together(self):
        assert self._f()("a = open\nb = compile") == ["compile", "open"]

    def test_unparseable_code_raises_rather_than_running(self):
        import pytest as _pytest
        with _pytest.raises(SyntaxError):
            self._f()("def (:")


class TestExecutePythonGuard:
    def test_a_parameter_read_is_no_longer_refused(self):
        from tests.td_double import build_bridge
        br, td = build_bridge()
        node = td.make("n1", "noiseTOP", params={"period": 4.0})
        out = br["_do_execute_python"]("result = op('/project1/n1').par.period.eval()")
        assert out["ok"] is True and out["result"] == 4.0

    def test_a_real_builtin_is_refused_with_a_useful_message(self):
        from tests.td_double import build_bridge
        br, _ = build_bridge()
        out = br["_do_execute_python"]("eval('1')")
        assert out["ok"] is False
        assert out["forbidden"] == ["eval"]
        assert "par.eval()" in out["error"], "must say what is still allowed"

    def test_a_syntax_error_is_reported_not_executed(self):
        from tests.td_double import build_bridge
        br, _ = build_bridge()
        out = br["_do_execute_python"]("def (:")
        assert out["ok"] is False and "could not parse" in out["error"]
