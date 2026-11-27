"""A deterministic TouchDesigner API double, and a loader for the whole bridge.

Model only what TouchDesigner was SEEN to do: probe a behaviour live in the
TEST-01 project first (`python scripts/td_live.py py "..."`, see AGENTS.md),
then write it here. A double written from the code under test agrees with
the code's bugs.

Why this exists
---------------
``tests/bridge_pure.py`` execs only the block between ``# BEGIN PURE HELPERS``
and ``# END PURE HELPERS`` — about 90 lines of a 2,191-line file. Every
``_do_*`` handler, i.e. everything that actually touches TouchDesigner, was
never executed by any test. That is the blind spot in which
``_do_get_errors`` shipped reading ``node.errors`` as an iterable *attribute*
for the whole life of the project, while TouchDesigner has always documented::

    errors(recurse=False)       -> str
    warnings(recurse=False)     -> str
    scriptErrors(recurse=False) -> str

This double models the API **as TouchDesigner documents it** — the error
accessors are methods returning newline-joined strings, ``OP.docked`` is a list
and ``OP.dock`` points back at the host — so a handler that mistakes one for the
other fails here instead of in a live session.

It is deliberately not a simulator. It models the shapes handlers read and the
mutations they perform, nothing else. When a handler needs a new corner of the
TD API, model that corner here rather than loosening the test.

Usage::

    from tests.td_double import build_bridge

    br, td = build_bridge()
    td.root.add(td.make("noise1", "noiseTOP", errors="Cannot cook"))
    assert br["_do_get_errors"]("/project1/noise1")["error_count"] == 1
"""

from __future__ import annotations

import contextlib
import enum
import os
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional

BRIDGE_PATH = os.path.join(os.path.dirname(__file__), "..", "bridge", "td_mcp_bridge.py")


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------
class FakeParMode(enum.Enum):
    """Stand-in for TD's ``ParMode`` enum.

    It has to be a real enum, not a string constant: the bridge switches a
    parameter into expression mode via ``getattr(type(par.mode), "EXPRESSION")``,
    which only resolves when ``par.mode`` is an *instance* of the enum class.
    With string modes that lookup silently falls back to the current value and
    the parameter never leaves CONSTANT — a failure that looks like success.
    """

    CONSTANT = "CONSTANT"
    EXPRESSION = "EXPRESSION"
    BIND = "BIND"


class FakePar:
    def __init__(self, name: str, val: Any = 0, default: Any = 0,
                 mode: FakeParMode = FakeParMode.CONSTANT, expr: str = "",
                 readOnly: bool = False, style: str = "Float",
                 menuNames: Optional[List[str]] = None,
                 owner: "FakeOP" = None):
        self.name = name
        self.label = name.capitalize()
        self._val = val
        self.default = default
        self.mode = mode
        self.expr = expr
        self.readOnly = readOnly
        self.style = style
        self.menuNames = menuNames or []
        self.menuLabels = list(self.menuNames)
        self.owner = owner
        self.enable = True
        #: The Par this one is bound to; None while the bind does not resolve.
        self.bindMaster: Optional["FakePar"] = None
        self._bindExpr = ""
        #: The FakeSequence this parameter is a block member of, if any.
        self.sequence = None
        # Set by the double when an expression should raise on eval, so tests
        # can exercise the "bad expression" path the way TD does.
        self.eval_error: Optional[Exception] = None
        self._init_done = True

    # `par.val` in TD is a live read of the parameter's constant value.
    @property
    def val(self):
        return self._val

    @property
    def expr(self):
        return self._expr

    @expr.setter
    def expr(self, text):
        # Verified on 2025.33230: assigning an expression switches the
        # parameter to EXPRESSION mode (probe: `period.expr = '1.5 + 1.5'`).
        self._expr = text
        if text and getattr(self, "_init_done", False):
            self.mode = FakeParMode.EXPRESSION

    @property
    def bindExpr(self):
        return self._bindExpr

    @bindExpr.setter
    def bindExpr(self, text):
        # Verified on 2025.33230: assigning a bind expression switches the
        # parameter to BIND mode, and does not raise when nothing is there to
        # bind to (bindMaster is then None and eval() returns the constant).
        self._bindExpr = text
        if text:
            self.mode = FakeParMode.BIND

    @property
    def isMenu(self) -> bool:
        return bool(self.menuNames)

    @property
    def isPulse(self) -> bool:
        return self.style == "Pulse"

    @property
    def isMomentary(self) -> bool:
        return self.style == "Momentary"

    @property
    def tupletName(self) -> str:
        return getattr(self, "_tuplet_name", None) or self.name

    def pulse(self):
        self.pulses = getattr(self, "pulses", 0) + 1

    @val.setter
    def val(self, v):
        # Verified on 2025.33230: a menu ignores a token it does not have --
        # no exception, the old value stays -- and an int selects by index.
        if self.menuNames:
            if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < len(self.menuNames):
                v = self.menuNames[v]
            elif isinstance(v, str) and v not in self.menuNames:
                return
        # Verified on 2025.33230 (probes of 2026-11-26):
        #  * a BOUND parameter stays bound -- the write lands on its bind
        #    master (tx bound to holder.Amount: after `tx = 0.42` the master
        #    read 0.42, tx.eval() 0.42, tx.val the stale 0.0, mode BIND);
        #  * bound to something that does not exist, the write raises and
        #    changes nothing;
        #  * an EXPRESSION parameter switches to CONSTANT and KEEPS its
        #    expression text (it is saved that way: `label 16 trails me.name`).
        if self.mode == FakeParMode.BIND:
            if self.bindMaster is None:
                raise AttributeError("'NoneType' object has no attribute 'par'")
            self.bindMaster.val = v
            return
        if self.mode != FakeParMode.CONSTANT:
            self.mode = FakeParMode.CONSTANT
        self._val = v
        # Let the owning operator react, the way TouchDesigner does when a
        # parameter changes (a Text DAT reloads when Sync to File turns on).
        hook = getattr(self.owner, "_on_par_set", None) if self.owner else None
        if hook is not None:
            hook(self.name, v)

    @property
    def isDefault(self) -> bool:
        """Par_Class: True when the value, the expression AND the mode are all
        at their defaults. A parameter TouchDesigner ships with an expression
        (a container COMP's ``resizecomp = me``) is therefore *default* while
        in EXPRESSION mode -- and must not be exported as configuration."""
        default_mode = getattr(self, "default_mode", None) or FakeParMode.CONSTANT
        default_expr = getattr(self, "default_expr", "")
        return (self._val == self.default and self.mode == default_mode
                and (self.expr or "") == (default_expr or ""))

    def eval(self):
        if self.eval_error is not None:
            raise self.eval_error
        if self.mode == FakeParMode.BIND and self.bindMaster is not None:
            return self.bindMaster.eval()
        return self._val

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"<FakePar {self.name}={self._val!r} mode={self.mode}>"


class FakePage:
    """``COMP.appendCustomPage`` result. Appenders return a ParGroup.

    TouchDesigner's appenders return a *ParGroup* — a sequence — not a single
    parameter, which is why callers index `[0]` to reach the parameter itself.
    Returning a bare parameter here would let a caller that forgets the index
    pass in the double and fail in TouchDesigner.
    """

    def __init__(self, name: str, owner: "FakeOP"):
        self.name = name
        self.owner = owner
        self.pars: List[FakePar] = []

    def _append(self, name, label=None, default=0, style="Float"):
        par = self.owner.par._add(
            FakePar(name, default, default, style=style))
        par.label = label or name
        par.page = self
        self.pars.append(par)
        return [par]

    def appendToggle(self, name, label=None, **kw):
        return self._append(name, label, False, "Toggle")

    def appendInt(self, name, label=None, **kw):
        return self._append(name, label, 0, "Int")

    def appendFloat(self, name, label=None, **kw):
        return self._append(name, label, 0.0, "Float")

    def appendStr(self, name, label=None, **kw):
        return self._append(name, label, "", "Str")

    def appendPulse(self, name, label=None, **kw):
        return self._append(name, label, None, "Pulse")


#: Parameters TouchDesigner gives an operator on creation, transcribed from the
#: operator help pages -- NOT from what the code under test happens to set.
#:
#: That distinction cost a release. This table originally listed
#: ``parameterexecuteDAT: {"pulse": ...}`` because the builder set
#: ``control.par.pulse``, so the double agreed with the bug and the test passed.
#: TouchDesigner calls it ``onpulse``, and the real thing raised
#: ``'td.ParCollection' object has no attribute 'pulse'``. A double written from
#: the code under test cannot catch the code under test being wrong.
#:
#: Sources: Text_DAT.htm, Execute_DAT.htm, Parameter_Execute_DAT.htm.
_DEFAULT_PARS_BY_TYPE = {
    # Text DAT — File page.
    "textDAT": {"file": "", "syncfile": False, "edit": None},
    "tableDAT": {"file": "", "syncfile": False, "edit": None},
    # Execute DAT — Execute page. Event toggles are all off by default.
    "executeDAT": {"active": True, "executeloc": "here", "fromop": "",
                   "start": False, "create": False, "exit": False,
                   "framestart": False, "frameend": False,
                   "playstatechange": False, "devicechange": False},
    # Parameter Execute DAT — Parameter Execute page. `op` names the operator
    # whose parameters are watched and `pars` which of them; an empty `pars`
    # watches nothing, so a callback that never fires is the default state.
    "parameterexecuteDAT": {"active": True, "executeloc": "here", "fromop": "",
                            "op": "", "pars": "", "valuechange": False,
                            "valueschanged": False, "onpulse": False,
                            "expressionchange": False, "exportchange": False,
                            "enablechange": False},
    "webserverDAT": {"port": 9980, "active": False, "callbacks": ""},
    "moviefileinTOP": {"file": ""},
}


class FakeParCollection:
    """``node.par`` — attribute access, iteration, and ``in``."""

    def __init__(self, owner: "FakeOP"):
        object.__setattr__(self, "_owner", owner)
        object.__setattr__(self, "_pars", {})

    def _add(self, par: FakePar):
        par.owner = self._owner
        self._pars[par.name] = par
        return par

    def _sequence_lookup(self, name):
        owner = object.__getattribute__(self, "_owner")
        for seq in getattr(owner, "_sequences", ()):
            par = seq._lookup(name)
            if par is not None:
                return par
        return None

    def __getattr__(self, name):
        pars = object.__getattribute__(self, "_pars")
        if name in pars:
            return pars[name]
        if not name.startswith("_"):
            par = self._sequence_lookup(name)
            if par is not None:
                return par
        raise AttributeError(name)

    def __setattr__(self, name, value):
        """Assigning to a parameter that does not exist raises, as TD does.

        Real TouchDesigner answers ``'td.ParCollection' object has no attribute
        'pulse'``. A double that quietly creates the attribute instead turns a
        typo into a silent no-op in tests and an exception in the field --
        which is exactly how ``control.par.pulse`` (TD calls it ``onpulse``)
        shipped and failed on the first real run.
        """
        pars = object.__getattribute__(self, "_pars")
        if name in pars:
            pars[name].val = value
            return
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        owner = object.__getattribute__(self, "_owner")
        raise AttributeError(
            "'td.ParCollection' object has no attribute '%s' Context:%s"
            % (name, getattr(owner, "path", "?")))

    def __getitem__(self, name):
        """``op.par['name']`` -> the Par, or **None** if not found.

        ParCollection_Class: "If not found None is returned." Not an
        exception -- a handler that expects a KeyError here would never see
        one in TouchDesigner.
        """
        pars = object.__getattribute__(self, "_pars")
        if name in pars:
            return pars[name]
        return self._sequence_lookup(name) if isinstance(name, str) else None

    def __iter__(self):
        return iter(list(self._pars.values()))

    def __contains__(self, item):
        """TD semantics, not dict semantics.

        ParCollection documents no name-based membership; ``in`` falls back
        to iterating the Par objects and comparing, so a *string* is never in
        the collection. The bridge wrote ``if k in node.par`` and the double
        answered like a dict, which is why every real `set_parameters` call
        reported "unknown parameter: opacity (did you mean opacity?)".
        """
        return any(p is item for p in self._pars.values())

    def __len__(self):
        return len(self._pars)


# ---------------------------------------------------------------------------
# Sequences (verified on 2025.33230, probes of 2026-11-26)
# ---------------------------------------------------------------------------
class FakeSequenceBlock:
    def __init__(self, sequence: "FakeSequence", index: int):
        self.sequence = sequence
        self.index = index

    @property
    def par(self):
        """Iterating a block's parameters makes them exist -- and adds no block."""
        return self.sequence._materialize(self.index)


class FakeSequence:
    """``op.seq[...]``: a repeating group of parameters (Constant CHOP's
    ``const0name``/``const0value``, Primitive POP's ``pt0posx``...).

    Two behaviours no documentation mentions, both modelled because the
    bridge has to survive them:

    * **Block parameters exist lazily.** A fresh Primitive POP says
      ``numBlocks == 1`` while ``pars()`` lists no ``pt*`` parameter; they
      appear once something touches them.
    * **Looking a parameter up past the last block creates it.**
      ``op.par['const4value']`` on a one-channel Constant CHOP returns a
      parameter and leaves five channels behind. A name whose suffix is not a
      block member (``const3bogus``) is None and grows nothing.
    """

    def __init__(self, owner: "FakeOP", name: str, fields: Dict[str, Any],
                 numBlocks: int = 1, materialized: bool = False):
        self.owner = owner
        self.name = name
        self.fields = dict(fields)
        self._numBlocks = int(numBlocks)
        self.blockSize = 1
        self.maxBlocks = None
        if materialized:
            for i in range(self._numBlocks):
                self._materialize(i)

    @property
    def numBlocks(self) -> int:
        return self._numBlocks

    @numBlocks.setter
    def numBlocks(self, n: int):
        n = int(n)
        pars = object.__getattribute__(self.owner.par, "_pars")
        for i in range(n, self._numBlocks):
            for suffix in self.fields:
                pars.pop("%s%d%s" % (self.name, i, suffix), None)
        self._numBlocks = n

    @property
    def blocks(self):
        return [FakeSequenceBlock(self, i) for i in range(self._numBlocks)]

    def _materialize(self, index: int):
        out = []
        pars = object.__getattribute__(self.owner.par, "_pars")
        for suffix, default in self.fields.items():
            pname = "%s%d%s" % (self.name, index, suffix)
            if pname not in pars:
                par = FakePar(pname, default, default)
                par.sequence = self
                self.owner.par._add(par)
            out.append(pars[pname])
        return out

    def _lookup(self, name: str):
        match = re.fullmatch(r"%s(\d+)(.*)" % re.escape(self.name), name)
        if not match or match.group(2) not in self.fields:
            return None
        index = int(match.group(1))
        if index >= self._numBlocks:
            self._numBlocks = index + 1  # the read grew the operator
        self._materialize(index)
        return object.__getattribute__(self.owner.par, "_pars")[name]


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------
def _as_op(target) -> "FakeOP":
    """``Connector.connect(target)`` accepts an OP *or* another connector."""
    owner = getattr(target, "owner", None)
    return owner if owner is not None else target


class FakeInputConnector:
    """An input connector, per Connector_Class.

    ``connections`` is the list of **connectors** on the far end -- the
    upstream operators' *output* connectors -- and the operator there is
    ``connector.owner``. There is no ``outputOP``/``inputOP``. The double used
    to hand back a made-up ``FakeConnection`` with exactly those attributes,
    because the bridge read them, so every wiring read passed here and came
    back empty in TouchDesigner.
    """

    isInput = True
    isOutput = False

    def __init__(self, owner: "FakeOP", index: int):
        self.owner = owner
        self.index = index
        self.connections: List["FakeOutputConnector"] = []

    def connect(self, target):
        """Wire this input to `target`: an OP (its output 0) or a connector.
        Connecting an *input* replaces its existing connection."""
        source = _as_op(target)
        out_index = getattr(target, "index", 0) if hasattr(target, "owner") else 0
        self.disconnect()
        if source._parent is not self.owner._parent:
            # Verified on 2025.33230: wiring across networks raises nothing,
            # connects nothing, and the wire that was here is gone.
            return
        oc = source._output_connector(out_index)
        self.connections = [oc]
        oc.connections.append(self)

    def disconnect(self):
        for oc in list(self.connections):
            oc.connections = [c for c in oc.connections if c is not self]
        self.connections = []


class FakeOutputConnector:
    isInput = False
    isOutput = True

    def __init__(self, owner: "FakeOP", index: int):
        self.owner = owner
        self.index = index
        self.connections: List[FakeInputConnector] = []

    def connect(self, target):
        """Connecting an *output* appends a new connection to the target."""
        dst = _as_op(target)
        index = getattr(target, "index", 0) if hasattr(target, "owner") else 0
        dst.inputConnectors[index].connect(self)

    def disconnect(self):
        for ic in list(self.connections):
            ic.disconnect()


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------
_FAMILY_BY_SUFFIX = {
    "TOP": "TOP", "CHOP": "CHOP", "SOP": "SOP", "DAT": "DAT",
    "MAT": "MAT", "COMP": "COMP", "POP": "POP",
}


def _family_for(op_type: str) -> str:
    for suffix, family in _FAMILY_BY_SUFFIX.items():
        if op_type.endswith(suffix):
            return family
    return "COMP"


def _short_type(op_type: str) -> str:
    """``OP.type``: the family-less short form. 'noiseTOP' -> 'noise'."""
    for suffix in _FAMILY_BY_SUFFIX:
        if op_type.endswith(suffix) and len(op_type) > len(suffix):
            return op_type[: -len(suffix)].lower()
    return op_type.lower()


class FakeOP:
    """A TouchDesigner operator.

    The error accessors are **methods returning strings**, matching
    ``OP_Class``. TD returns an empty string when there is nothing to report,
    and newline-joins multiple messages.
    """

    def __init__(self, name: str, op_type: str = "nullTOP", parent: "FakeOP" = None,
                 errors: str = "", warnings: str = "", scriptErrors: str = ""):
        self.name = name
        # OP_Class: `type` is the short, family-less form ('noise', 'text',
        # 'execute'); `OPType` is the Python class type ('noiseTOP') and the
        # only one COMP.create() accepts. The double used to report the full
        # form for both, so recipes recording `type` looked round-trippable
        # here and failed in TouchDesigner with "Unknown operator type:
        # 'constant'".
        self.type = _short_type(op_type)
        self.family = _family_for(op_type)
        self.OPType = op_type
        self._parent = parent
        self.children: List[FakeOP] = []
        self.par = FakeParCollection(self)
        self.nodeX = 0
        self.nodeY = 0
        self.nodeWidth = 120
        self.nodeHeight = 60
        self.color = (0.55, 0.55, 0.55)
        self.comment = ""
        self.tags = set()
        self.cookTime = 0.0
        self.valid = True
        self.dock: Optional[FakeOP] = None
        self.docked: List[FakeOP] = []
        self.customPages: List["FakePage"] = []
        self.inputConnectors: List[FakeInputConnector] = [
            FakeInputConnector(self, i) for i in range(3)]
        self.outputConnectors: List[FakeOutputConnector] = [
            FakeOutputConnector(self, 0)]
        self._errors = errors
        self._warnings = warnings
        self._scriptErrors = scriptErrors
        self.saved_to: Optional[str] = None
        self._allowCooking = True
        self._sequences: List[FakeSequence] = []
        self._utility = False
        self._utility_children: List[FakeOP] = []

    # Cook bookkeeping and state the health check reads.
    bypass = False
    totalCooks = 0
    cpuCookTime = 0.0
    gpuCookTime = 0.0
    extensions = ()
    #: GLSL operators only: the compiler's own output.
    compileResult = None

    @property
    def isCOMP(self) -> bool:
        return self.family == "COMP"

    @property
    def seq(self):
        """Iterable only: attribute access by name (``op.seq.pt``) fails on a
        fresh Primitive POP in TouchDesigner, so nothing should rely on it.

        Iterating it COOKS an operator that has never cooked (verified,
        2025.33230); modelled so a test can see a read tool do it."""
        if self.totalCooks == 0:
            self.totalCooks = 1
            self.cooked_by_seq = True
        return list(self._sequences)

    def add_sequence(self, name, fields, numBlocks=1, materialized=False):
        seq = FakeSequence(self, name, fields, numBlocks, materialized)
        self._sequences.append(seq)
        return seq

    def pars(self, *patterns):
        found = list(self.par)
        if patterns:
            import fnmatch as _fn
            found = [q for q in found if any(_fn.fnmatchcase(q.name, pat) for pat in patterns)]
        return found

    @property
    def utility(self) -> bool:
        return self._utility

    @utility.setter
    def utility(self, flag: bool):
        """A utility operator (an annotation made in the editor) drops out of
        ``children``, ``op()`` and ``findChildren()``; only
        ``findChildren(includeUtility=True)`` still returns it (verified)."""
        flag = bool(flag)
        parent = self._parent
        if parent is not None and flag != self._utility:
            src, dst = ((parent.children, parent._utility_children) if flag
                        else (parent._utility_children, parent.children))
            if self in src:
                src.remove(self)
                dst.append(self)
        self._utility = flag

    @property
    def numChildren(self) -> int:
        return len(self.children) + len(self._utility_children)

    @property
    def enclosedOPs(self):
        """Annotations: the siblings whose tile lies inside this one's rectangle."""
        if self._parent is None:
            return []
        x0, y0 = self.nodeX, self.nodeY
        x1, y1 = x0 + self.nodeWidth, y0 + self.nodeHeight
        return [c for c in self._parent.children
                if c is not self and x0 <= c.nodeX and c.nodeX + c.nodeWidth <= x1
                and y0 <= c.nodeY and c.nodeY + c.nodeHeight <= y1]

    # -- what the user sees (verified on 2025.33230) ------------------------
    current = False
    selected = False
    viewer_open = False

    def openViewer(self, unique=False, borders=True):
        self.viewer_open = True

    def closeViewer(self, topMost=False):
        self.viewer_open = False

    @property
    def currentChild(self):
        return next((c for c in self.children if c.current), None)

    @property
    def selectedChildren(self):
        return [c for c in self.children if c.selected]

    @property
    def allowCooking(self) -> bool:
        return self._allowCooking

    @allowCooking.setter
    def allowCooking(self, v: bool):
        # Verified 2025.33230: `allowCooking = False` raises on a non-COMP
        # while `= True` is accepted anywhere — so a mixed-type gate passes
        # its enable pass and aborts on disable. Model the awkward part.
        if not v and self.family != "COMP":
            raise ValueError("tdError: allowCooking can only be set False on "
                             "COMPs")
        self._allowCooking = bool(v)

    @property
    def inputs(self):
        """``OP.inputs``: the connected input operators, in input order."""
        out = []
        for ic in self.inputConnectors:
            for oc in ic.connections:
                out.append(oc.owner)
        return out

    @property
    def outputs(self):
        """``OP.outputs``: every operator fed by this one."""
        out = []
        for oc in self.outputConnectors:
            for ic in oc.connections:
                out.append(ic.owner)
        return out

    @property
    def time(self):
        """``OP.time`` -- the Time COMP governing this operator. The global
        timeline is ``root.time``; there is no ``project.cook``."""
        return _ENV.time if _ENV is not None else None

    # -- identity ----------------------------------------------------------
    @property
    def path(self) -> str:
        if self._parent is None:
            return "/" + self.name if self.name else "/"
        prefix = self._parent.path.rstrip("/")
        return f"{prefix}/{self.name}"

    def parent(self, level: int = 1):
        node = self
        for _ in range(level):
            if node is None:
                return None
            node = node._parent
        return node

    # -- diagnostics (methods, returning str -- this is the point) ----------
    def errors(self, recurse: bool = False) -> str:
        out = [self._errors] if self._errors else []
        if recurse:
            out += [c.errors(True) for c in self.children]
        return "\n".join(x for x in out if x)

    def warnings(self, recurse: bool = False) -> str:
        out = [self._warnings] if self._warnings else []
        if recurse:
            out += [c.warnings(True) for c in self.children]
        return "\n".join(x for x in out if x)

    def scriptErrors(self, recurse: bool = False) -> str:
        out = [self._scriptErrors] if self._scriptErrors else []
        if recurse:
            out += [c.scriptErrors(True) for c in self.children]
        return "\n".join(x for x in out if x)

    def clearScriptErrors(self, *, recurse: bool = False, error: str = "*") -> None:
        # Keyword-only, as TouchDesigner enforces: a positional call answers
        # "Call contains unexpected positional arguments: Value:(False,)".
        self._scriptErrors = ""
        if recurse:
            for c in self.children:
                c.clearScriptErrors(True, error)

    # -- graph -------------------------------------------------------------
    def _output_connector(self, index: int) -> FakeOutputConnector:
        while len(self.outputConnectors) <= index:
            self.outputConnectors.append(
                FakeOutputConnector(self, len(self.outputConnectors)))
        return self.outputConnectors[index]

    def add(self, child: "FakeOP") -> "FakeOP":
        child._parent = self
        self.children.append(child)
        return child

    def op(self, path: str):
        return _resolve(self, path)

    def appendCustomPage(self, name: str) -> "FakePage":
        page = FakePage(name, self)
        self.customPages.append(page)
        return page

    def _install_default_pars(self):
        for name, default in _DEFAULT_PARS_BY_TYPE.get(self.OPType, {}).items():
            if name not in self.par:
                self.par._add(FakePar(name, default, default))
        return self

    def create(self, opType, name=None, initialize=True):
        """``COMP.create(opType, name)`` -- type FIRST, and TD refuses a type
        it does not know.

        Modelled strictly on purpose. A permissive ``create`` that accepts any
        two strings cannot tell a correct call from one with its arguments
        reversed, which is how ``parent.create(name, op_type)`` shipped in the
        bridge: the double said yes, TouchDesigner would have said
        "invalid operator type".
        """
        type_name = str(opType)
        if not any(type_name.endswith(f) for f in _FAMILY_BY_SUFFIX):
            raise TypeError(f"invalid operator type: {type_name!r}")
        if type_name == "annotateCOMP":
            # Verified on 2025.33230: create('annotateCOMP', 'my_note') comes
            # back named annotate1; the name has to be assigned afterwards.
            name = None
        if name is None:
            # TD auto-names: base name from the type, next free number.
            stem = type_name
            for suffix in _FAMILY_BY_SUFFIX:
                if stem.endswith(suffix):
                    stem = stem[: -len(suffix)]
                    break
            existing = {c.name for c in self.children}
            i = 1
            while f"{stem}{i}" in existing:
                i += 1
            name = f"{stem}{i}"
        cls = FakeOP
        if type_name.endswith("DAT"):
            cls = FakeDAT
        elif type_name.endswith("CHOP"):
            cls = FakeCHOP
        elif type_name.endswith("POP"):
            cls = FakePOP
        elif type_name.endswith("TOP"):
            cls = FakeTOP
        node = self.add(cls(name, type_name)._install_default_pars())
        if type_name == "annotateCOMP":
            for pname, default in (("Mode", "annotate"), ("Titletext", ""), ("Bodytext", ""),
                                   ("Backcolorr", 0.1), ("Backcolorg", 0.1), ("Backcolorb", 0.1)):
                node.par._add(FakePar(pname, default, default,
                                      menuNames=["comment", "networkbox", "annotate"]
                                      if pname == "Mode" else None))
        return node

    def copy(self, source: "FakeOP", name: str = None) -> "FakeOP":
        clone = FakeOP(name or source.name, source.type)
        for par in source.par:
            clone.par._add(FakePar(par.name, par.val, par.default,
                                   par.mode, par.expr))
        return self.add(clone)

    def destroy(self):
        # Verified on 2025.33230: destroying an operator in the middle of a
        # chain wires its FIRST input straight into everything it fed; one
        # with no input leaves those inputs empty.
        upstream = self.inputConnectors[0].connections[:1] if self.inputConnectors else []
        for oc in self.outputConnectors:
            for ic in list(oc.connections):
                ic.disconnect()
                if upstream:
                    ic.connect(upstream[0])
        for ic in self.inputConnectors:
            ic.disconnect()
        if self._parent is not None:
            self._parent.children = [c for c in self._parent.children if c is not self]
            self._parent._utility_children = [c for c in self._parent._utility_children
                                              if c is not self]
        self.valid = False

    def save(self, file_path: str, **kwargs) -> str:
        self.saved_to = file_path
        return file_path

    def loadTox(self, file_path: str) -> "FakeOP":
        return self.add(FakeOP(os.path.splitext(os.path.basename(file_path))[0],
                               "containerCOMP"))

    def findChildren(self, **kwargs) -> List["FakeOP"]:
        # NB: TD matches `depth` as an EXACT relative depth, not "up to".
        # Modelled faithfully so a handler that assumes otherwise fails here.
        depth = kwargs.get("depth")
        utility = bool(kwargs.get("includeUtility"))
        out: List[FakeOP] = []

        def walk(node, d):
            for c in list(node.children) + (list(node._utility_children) if utility else []):
                if depth is None or d == depth:
                    out.append(c)
                walk(c, d + 1)

        walk(self, 1)
        return out

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"<FakeOP {self.path} {self.type}>"


class _BlockTdMcp:
    """A meta-path finder that refuses ``td_mcp`` and everything under it."""

    def find_spec(self, name, path=None, target=None):
        if name == "td_mcp" or name.startswith("td_mcp."):
            raise ImportError(
                "td_mcp is not importable inside TouchDesigner (blocked by the "
                "test double); the bridge must be self-contained")
        return None


@contextlib.contextmanager
def block_td_mcp():
    """Make ``import td_mcp`` fail, as it does inside TouchDesigner.

    The bridge runs in a Text DAT, where this package is not on sys.path. The
    tests run with it importable, which is how a `from td_mcp.streamable_http
    import StreamableHTTPMixin` at the top of the bridge went unnoticed for
    the life of the project: the import succeeded under pytest and raised
    NameError in every real TouchDesigner. Stash the package out of
    ``sys.modules`` and refuse to re-import it for the duration.
    """
    stashed = {k: v for k, v in sys.modules.items()
               if k == "td_mcp" or k.startswith("td_mcp.")}
    for k in stashed:
        del sys.modules[k]
    finder = _BlockTdMcp()
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        sys.modules.update(stashed)


class _DATModule:
    """``DAT.module`` -- a live view over the DAT's executed namespace.

    Live, not a snapshot: the bridge keeps ``_server`` as a module global that
    ``start()`` reassigns, so a copy taken at compile time would report a
    stopped server forever.
    """

    def __init__(self, namespace: Dict[str, Any]):
        object.__setattr__(self, "_ns", namespace)

    def __getattr__(self, name):
        ns = object.__getattribute__(self, "_ns")
        try:
            return ns[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        object.__getattribute__(self, "_ns")[name] = value


class FakeDAT(FakeOP):
    def __init__(self, name: str, op_type: str = "textDAT", text: str = "",
                 table: Optional[List[List[str]]] = None, **kw):
        super().__init__(name, op_type, **kw)
        self._text = ""
        self._table = table or []
        if text:
            self.text = text
        self._module: Optional[_DATModule] = None
        self._module_text: Optional[str] = None

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, value):
        # DAT_Class: "Tables are treated as tab delimited columns, newline
        # delimited rows" -- TouchDesigner re-normalises a table's text on
        # assignment (a trailing newline, at least), so the text stored is not
        # byte-identical to the text written. A CAS scheme that hashed the
        # input never matched on the next write in the first live session.
        value = "" if value is None else str(value)
        if self.OPType == "tableDAT" and value and not value.endswith("\n"):
            value += "\n"
        self._text = value
        if self.OPType == "tableDAT":
            self._table = [row.split("\t") for row in value.splitlines()] if value.strip() else []

    def _on_par_set(self, name, value):
        # Text DAT, File page: turning Sync to File on "loads the file from
        # disk immediately" (Text_DAT.htm). Link mode relies on exactly that,
        # and an unsynced DAT is an empty module with no `start`.
        if name not in ("file", "syncfile"):
            return
        try:
            path = str(self.par.file.val)
            sync = bool(self.par.syncfile.val)
        except AttributeError:
            return
        if sync and path and os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                self.text = fh.read()

    @property
    def module(self):
        """``DAT.module`` -- the DAT's text, imported as a module.

        Compiled lazily on first access and cached until the text changes,
        which is how TouchDesigner does it and why the bridge's environment
        must be applied BEFORE the first ``.module`` touch. The namespace gets
        TD's injected globals with ``me`` bound to this DAT, ``parent`` bound
        to its parent lookup, and ``__file__`` set to the *operator path* --
        faithfully, because that is the value that sent bootstrap.py looking
        for files under ``C:\\project1``. ``td_mcp`` is blocked for the
        duration: a DAT module has no access to this package.

        A failure to compile raises the way TouchDesigner reports it:
        ``Module compilation error. See <path> for details.``
        """
        if self._module is not None and self._module_text == self.text:
            return self._module
        env = _ENV
        namespace: Dict[str, Any] = dict(env.globals()) if env is not None else {}
        namespace.update({"me": self, "parent": self.parent,
                          "__name__": self.name, "__file__": self.path})
        try:
            with block_td_mcp():
                exec(compile(self.text or "", self.path, "exec"),  # noqa: S102
                     namespace)
        except Exception as exc:
            raise RuntimeError(
                "Module compilation error. See %s for details. (%s: %s)"
                % (self.path, type(exc).__name__, exc)) from exc
        self._module = _DATModule(namespace)
        self._module_text = self.text
        return self._module

    @property
    def numRows(self) -> int:
        return len(self._table)

    @property
    def numCols(self) -> int:
        return len(self._table[0]) if self._table else 0

    def __getitem__(self, key):
        r, c = key
        return _FakeCell(self._table[r][c])


class _FakeCell:
    def __init__(self, val):
        self.val = val

    def __str__(self):
        return str(self.val)


class FakeTOP(FakeOP):
    """A TOP whose ``numpyArray()`` answers like TouchDesigner's.

    ``TOP.numpyArray()`` returns a ``float32`` HxWx4 array in **0..1** -- not
    0..255. The first live session's Noise TOP came back with mean 0.46 and
    was judged black by thresholds written for bytes. Frames are supplied by
    the test (``pixels`` or ``frames``) so motion can be modelled too.
    """

    def __init__(self, name: str, op_type: str = "nullTOP",
                 pixels=None, frames=None, width: int = 16, height: int = 16, **kw):
        super().__init__(name, op_type, **kw)
        self.width, self.height = width, height
        self._pixels = pixels
        self._frames = list(frames) if frames else None
        self._frame_i = 0

    def numpyArray(self, delayed=False, writable=False):
        import numpy as np

        if self._frames:
            arr = self._frames[min(self._frame_i, len(self._frames) - 1)]
            self._frame_i += 1
            return np.asarray(arr, dtype=np.float32)
        if self._pixels is not None:
            return np.asarray(self._pixels, dtype=np.float32)
        # A plain mid-grey noise-ish field in TD's own 0..1 float range.
        rng = np.random.default_rng(0)
        rgb = rng.random((self.height, self.width, 3), dtype=np.float32) * 0.5 + 0.25
        return np.concatenate([rgb, np.ones((self.height, self.width, 1), np.float32)], axis=2)

    def cook(self, force=False, recurse=False):
        return None


class FakeChannel:
    def __init__(self, name: str, vals: List[float]):
        self.name = name
        self.vals = list(vals)

    def __len__(self):
        return len(self.vals)

    def __getitem__(self, i):
        return self.vals[i]


class FakeCHOP(FakeOP):
    def __init__(self, name: str, op_type: str = "constantCHOP",
                 channels: Optional[Dict[str, List[float]]] = None, **kw):
        super().__init__(name, op_type, **kw)
        self.chans_by_name = {n: FakeChannel(n, v)
                              for n, v in (channels or {}).items()}
        self.numSamples = max(
            [len(c.vals) for c in self.chans_by_name.values()] or [0])

    def chans(self, *patterns, caseSensitive=True):
        """CHOP_Class: ``chans(*nameOrIndex)`` is a **method** returning a
        list; with no arguments, every channel. The double used to expose a
        `chans` property because the bridge read one, and the first live
        session answered "'builtin_function_or_method' object is not
        iterable"."""
        chans = list(self.chans_by_name.values())
        if not patterns:
            return chans
        out = []
        for pat in patterns:
            if isinstance(pat, int):
                if 0 <= pat < len(chans):
                    out.append(chans[pat])
            else:
                out += [c for c in chans if c.name == pat]
        return out

    def chan(self, *patterns, caseSensitive=True):
        """First matching Channel, or **None** (CHOP_Class)."""
        found = self.chans(*patterns, caseSensitive=caseSensitive)
        return found[0] if found else None

    @property
    def numChans(self):
        return len(self.chans_by_name)

    def __getitem__(self, key):
        """``chop['name']`` / ``chop[index]`` (CHOP_Class subscript)."""
        if isinstance(key, int):
            return list(self.chans_by_name.values())[key]
        return self.chans_by_name[key]


class FakeAttribute:
    """One POP attribute: ``name`` / ``size`` / ``type`` / ``vals``."""

    def __init__(self, name: str, vals: List, type_name: str = "AttribDataType.FLOAT32"):
        self.name = name
        self.vals = list(vals)
        self.type = type_name
        self.isArray = False
        self.arraySize = 0

    @property
    def size(self):
        first = self.vals[0] if self.vals else None
        return len(first) if isinstance(first, (list, tuple)) else 1

    def __str__(self):  # matches TD's repr: "P: 3 AttribDataType.FLOAT32"
        return f"{self.name}: {self.size} {self.type}"


class FakePOP(FakeOP):
    """POP double.

    Two things modelled from the live API rather than from the bridge:

    * Attributes live on ``pointAttributes`` / ``primAttributes`` /
      ``vertAttributes`` -- **properties**, each an iterable of Attribute
      objects. There is no ``attributes()`` method; the double used to provide
      one because the bridge called one, so `read_pop` returned an empty
      attribute map for a real 252-point sphere and the double said fine.
    * ``numPoints`` is a property while ``numPrims`` is a **method**, on the
      same object. TouchDesigner is not consistent, so neither is this.

    ``POP.points(attr, start, count)`` accepts slicing arguments and **ignores
    them** -- TouchDesigner returns the whole attribute regardless. Embody
    measured a 4,096-point "slice" off a 160k POP as slower than reading all of
    it. Modelled faithfully so a handler cannot pretend it sliced.
    """

    def __init__(self, name: str, op_type: str = "noisePOP",
                 attributes: Optional[Dict[str, List]] = None,
                 numPoints: int = 0, prim_attributes: Optional[Dict[str, List]] = None,
                 vert_attributes: Optional[Dict[str, List]] = None,
                 numPrims: int = 0, **kw):
        super().__init__(name, op_type, **kw)
        self._attributes = attributes or {}
        self._prim_attributes = prim_attributes or {}
        self._vert_attributes = vert_attributes or {}
        self.numPoints = numPoints or max(
            [len(v) for v in self._attributes.values()] or [0])
        self._num_prims = numPrims

    @staticmethod
    def _collection(mapping):
        return [FakeAttribute(k, v) for k, v in mapping.items()]

    @property
    def pointAttributes(self):
        return self._collection(self._attributes)

    @property
    def primAttributes(self):
        return self._collection(self._prim_attributes)

    @property
    def vertAttributes(self):
        return self._collection(self._vert_attributes)

    def numPrims(self):
        """A method, unlike ``numPoints``. Faithful to the live API."""
        return self._num_prims

    def points(self, attr, start=None, count=None):
        return list(self._attributes.get(attr, []))


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
def _resolve(base: FakeOP, path) -> Optional[FakeOP]:
    if path is None:
        return None
    if isinstance(path, FakeOP):
        return path
    if isinstance(path, int) and not isinstance(path, bool):
        # Verified on 2025.33230: op(5) is the operator with id 5 (/sys/drag
        # there) -- some operator, never the one a caller meant.
        return _ROOT.children[0] if _ROOT is not None and _ROOT.children else None
    path = str(path)
    if not path:
        return None
    node = _ROOT if path.startswith("/") else base
    for part in [p for p in path.strip("/").split("/") if p]:
        if node is None:
            return None
        nxt = None
        if any(ch in part for ch in "*?["):
            # Verified on 2025.33230: a pattern resolves to ONE arbitrary match
            # (glob_* gave glob_beta though glob_alpha was created first).
            import fnmatch as _fn
            hits = [c for c in node.children if _fn.fnmatchcase(c.name, part)]
            nxt = hits[-1] if hits else None
        else:
            for c in node.children:
                if c.name == part:
                    nxt = c
                    break
        node = nxt
    return node


# ---------------------------------------------------------------------------
# Application-level globals
# ---------------------------------------------------------------------------
class _Undo:
    """``ui.undo`` -- TouchDesigner's ``Undo`` object, as documented.

    ``startBlock(name, enable=True)`` / ``endBlock()`` and **no context
    manager protocol**. This class used to define ``__enter__``/``__exit__``
    because the bridge wrote ``with ui.undo:``, and so 665 tests passed while
    the first real session answered every tool call with "'td.Undo' object
    does not support the context manager protocol". A double written from the
    code under test cannot catch the code under test being wrong.

    Records every block so tests can assert a mutation ran inside one.
    """

    def __init__(self):
        self.depth = 0
        self.blocks = 0
        self.names: List[str] = []
        self.globalState = True

    def startBlock(self, name, enable=True):
        self.depth += 1
        self.blocks += 1
        self.names.append(str(name))

    def endBlock(self):
        if self.depth <= 0:
            raise RuntimeError("endBlock() without a matching startBlock()")
        self.depth -= 1

    @property
    def state(self):
        return self.depth > 0


class _Pane:
    """A network editor pane. Verified on 2025.33230: `owner` is settable (it
    navigates the editor), `homeSelected(zoom=True)` frames the selection, and
    `type` prints as PaneType.NETWORKEDITOR."""

    def __init__(self, owner: FakeOP):
        self.owner = owner
        self.name = "pane1"
        self.type = "PaneType.NETWORKEDITOR"
        self.framed = 0

    def homeSelected(self, zoom=False):
        self.framed += 1


class _Panes(list):
    def __init__(self, current: _Pane):
        super().__init__([current])
        self.current = current


class FakeUI:
    def __init__(self, root: FakeOP):
        self.undo = _Undo()
        self.panes = _Panes(_Pane(root))
        self.messages: List[str] = []
        # ui.status is a settable string (verified); window geometry and the
        # operator under the mouse are read-only state.
        self.status = ""
        self.windowX, self.windowY, self.windowWidth, self.windowHeight = 0, 0, 1280, 720
        self.performMode = False
        self.rolloverOp = None

    def status(self, msg):  # pragma: no cover - convenience
        self.messages.append(msg)


class FakeTime:
    """The Time COMP (``root.time`` / ``op.time``), per TimeCOMP_Class:
    frame, seconds, rate, play, start, end, loop, tempo."""

    def __init__(self):
        self.frame = 1.0
        self.rate = 60.0
        self.play = True
        self.start = 1.0
        self.end = 600.0
        self.loop = True
        self.tempo = 120.0

    @property
    def seconds(self):
        return (self.frame - 1.0) / self.rate


class FakeProject:
    """``project``, per Project_Class: name, folder, cookRate, realTime,
    saveVersion... There is NO ``project.cook`` -- the bridge addressed one
    for fps and the timeline, and the double had modelled it because the
    bridge did. First live session: "'td.Project' object has no attribute
    'cook'" on `project_info`."""

    def __init__(self, name="untitled.toe", folder="/tmp/project"):
        self.name = name
        self.folder = folder
        self.cookRate = 60.0
        self.realTime = True
        self.saveVersion = "2025.33070"
        self.saveBuild = "33070"
        self.paths = {}


class Pumper:
    """Stands in for TouchDesigner's frame loop for a bare bridge module.

    Calls `pump_fn` every few milliseconds on a dedicated thread, which thereby
    becomes the bridge's "main thread". `TDEnv.frame_loop()` is the faithful
    version -- it drives Execute DATs the way TD does; this one is for tests
    that load the bridge without a component around it.
    """

    def __init__(self, pump_fn, interval=0.003):
        self._pump = pump_fn
        self._interval = interval
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name="td-double-pumper")

    def _run(self):
        while not self._stop.is_set():
            try:
                self._pump()
            except Exception:  # noqa: BLE001 - keep pumping, like TD keeps cooking
                pass
            time.sleep(self._interval)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self.thread.join(timeout=2)
        return False


class FakeApp:
    def __init__(self):
        # TD still reports the legacy generation in `version`; the public
        # year.build identifier lives on `build`.
        self.version = "099"
        self.build = "2025.33070"
        self.product = "TouchDesigner"
        self.osName = "Windows"


# ---------------------------------------------------------------------------
# Environment assembly
# ---------------------------------------------------------------------------
_ROOT: Optional[FakeOP] = None
_ENV: Optional["TDEnv"] = None


class TDEnv:
    """The double's handle: build operators, then inspect what handlers did."""

    def __init__(self):
        global _ROOT, _ENV
        self.root = FakeOP("", "rootCOMP")
        _ROOT = self.root
        _ENV = self
        self.project_comp = self.root.add(FakeOP("project1", "containerCOMP"))
        self.me = self.project_comp
        self.ui = FakeUI(self.root)
        self.project = FakeProject()
        self.app = FakeApp()
        self.time = FakeTime()

    # -- construction helpers ---------------------------------------------
    def make(self, name, op_type="nullTOP", parent=None, params=None, **kw):
        cls = FakeOP
        if op_type.endswith("DAT"):
            cls = FakeDAT
        elif op_type.endswith("CHOP"):
            cls = FakeCHOP
        elif op_type.endswith("POP"):
            cls = FakePOP
        elif op_type.endswith("TOP"):
            cls = FakeTOP
        node = cls(name, op_type, **kw)
        node._install_default_pars()
        for pname, spec in (params or {}).items():
            if isinstance(spec, dict):
                node.par._add(FakePar(pname, **spec))
            else:
                node.par._add(FakePar(pname, spec, spec))
        (parent or self.project_comp).add(node)
        return node

    def dock_info(self, host: FakeOP, text: str, name: str = None) -> FakeDAT:
        """Attach an Info DAT to `host`, the way TouchDesigner docks one to a
        GLSL TOP to carry its compile errors. Discovery must be by
        ``dat.type == 'info'`` — TD keeps the old dock NAME after a rename, so
        name-based discovery silently stops working."""
        info = FakeDAT(name or f"{host.name}_info", "infoDAT", text=text)
        info.dock = host
        host.docked.append(info)
        (host.parent() or self.root).add(info)
        return info

    def shutdown(self):
        """Stop any bridge a DAT module started, so a test leaves no socket
        or serve_forever thread behind."""
        def walk(node):
            for child in list(node.children):
                mod = getattr(child, "_module", None)
                if mod is not None:
                    ns = object.__getattribute__(mod, "_ns")
                    if callable(ns.get("stop")) and ns.get("_server") is not None:
                        try:
                            ns["stop"]()
                        except Exception:  # noqa: BLE001 - teardown only
                            pass
                walk(child)
        walk(self.root)

    @contextlib.contextmanager
    def frame_loop(self, interval: float = 0.003):
        """Stand in for TouchDesigner's frame loop.

        On a dedicated thread, call ``onFrameStart(frame)`` on every Execute
        DAT whose Frame Start toggle is on -- exactly what TD does on its main
        thread each frame. The component's `startup` DAT uses that callback to
        drain the bridge's main-thread queue, so with this running a test can
        drive the real handler over HTTP the way a session would.
        """
        stop = threading.Event()
        frame = [0]

        def tick_all():
            frame[0] += 1
            for dat in self._execute_dats():
                try:
                    if not dat.par.framestart.val or not dat.par.active.val:
                        continue
                    fn = getattr(dat.module, "onFrameStart", None)
                except Exception:  # noqa: BLE001 - a DAT that will not compile
                    continue
                if fn is not None:
                    try:
                        fn(frame[0])
                    except Exception:  # noqa: BLE001 - TD logs and keeps cooking
                        pass

        def run():
            while not stop.is_set():
                tick_all()
                time.sleep(interval)

        thread = threading.Thread(target=run, daemon=True, name="td-double-frames")
        thread.start()
        try:
            yield thread
        finally:
            stop.set()
            thread.join(timeout=2)

    def _execute_dats(self):
        out = []

        def walk(node):
            for child in node.children:
                if child.OPType == "executeDAT":
                    out.append(child)
                walk(child)
        walk(self.root)
        return out

    # -- TD globals --------------------------------------------------------
    def globals(self) -> Dict[str, Any]:
        env = self

        def _op(path=None):
            if path is None:
                return env.me
            return _resolve(env.me, path)

        def _root():
            return env.root

        def _parent(level=1):
            return env.me.parent(level) or env.root

        return {
            "op": _op,
            "ops": lambda *paths: [_op(p) for p in paths],
            # `root` is the root COMP itself -- an OP, not a function. The
            # bridge wrote `root()` at nine sites and the double supplied a
            # callable, so every no-path fallback passed here and answered
            # "'td.baseCOMP' object is not callable" in TouchDesigner.
            "root": env.root,
            "parent": _parent,
            "me": env.me,
            "project": env.project,
            "ui": env.ui,
            "app": env.app,
            "run": lambda *a, **k: None,
        }


def build_bridge(env: Optional[TDEnv] = None):
    """Exec the *entire* shipped bridge with the double's globals installed.

    Returns ``(namespace, env)``. The namespace is the bridge module's globals,
    so every ``_do_*`` handler and ``_DISPATCH`` are directly callable.
    """
    env = env or TDEnv()
    with open(BRIDGE_PATH, encoding="utf-8") as fh:
        source = fh.read()
    namespace: Dict[str, Any] = {
        "__name__": "td_mcp_bridge_double",
        # The operator path, as TouchDesigner sets it for a DAT module -- not
        # the file on disk. Code that trusts `__file__` must fail here too.
        "__file__": "/project1/td_mcp/bridge",
    }
    namespace.update(env.globals())
    # `td_mcp` is not importable inside TouchDesigner. Block it, so a bridge
    # that depends on it fails here instead of on the first real run.
    with block_td_mcp():
        exec(compile(source, BRIDGE_PATH, "exec"), namespace)  # noqa: S102
    # The bridge re-binds TD globals it declares itself (none today); re-assert
    # ours so a future top-level assignment cannot silently shadow the double.
    namespace.update(env.globals())
    return namespace, env
