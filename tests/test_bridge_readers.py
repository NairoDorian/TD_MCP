"""Reader reduction contract (Wave 12, Embody's "never a blind dump").

A read tool exists to answer "what did this chain do to my data". Returning
4,096 raw numbers answers that badly and expensively. Every reader reduces by
default and offers the raw form only on request.
"""

from __future__ import annotations

import pytest

from tests.td_double import FakeDAT, build_bridge


@pytest.fixture()
def bridge():
    return build_bridge()


class TestReadChop:
    def test_defaults_to_stats_not_a_dump(self, bridge):
        br, td = bridge
        td.make("c1", "constantCHOP", channels={"chan1": [0.0, 1.0, 2.0, 3.0]})
        out = br["_do_read_chop"]("/project1/c1")
        assert out["format"] == "stats"
        st = out["channels"]["chan1"]
        assert (st["count"], st["min"], st["max"], st["mean"]) == (4, 0.0, 3.0, 1.5)
        assert st["std"] == pytest.approx(1.1180, abs=1e-4)

    def test_raw_values_still_available(self, bridge):
        br, td = bridge
        td.make("c1", "constantCHOP", channels={"chan1": list(range(10))})
        out = br["_do_read_chop"]("/project1/c1", None, 3, "values")
        assert out["format"] == "values"
        assert out["channels"]["chan1"] == [0.0, 1.0, 2.0]

    def test_single_sample_channel_has_zero_std_not_a_crash(self, bridge):
        br, td = bridge
        td.make("c1", "constantCHOP", channels={"chan1": [7.0]})
        assert br["_do_read_chop"]("/project1/c1")["channels"]["chan1"]["std"] == 0.0

    def test_empty_channel(self, bridge):
        br, td = bridge
        td.make("c1", "constantCHOP", channels={"chan1": []})
        st = br["_do_read_chop"]("/project1/c1")["channels"]["chan1"]
        assert st == {"count": 0, "min": None, "max": None, "mean": None,
                      "std": None}

    def test_unknown_channel_is_an_error_not_a_traceback(self, bridge):
        br, td = bridge
        td.make("c1", "constantCHOP", channels={"chan1": [1.0]})
        out = br["_do_read_chop"]("/project1/c1", "nope")
        assert out["ok"] is False and "nope" in out["error"]

    def test_missing_node(self, bridge):
        br, _ = bridge
        assert br["_do_read_chop"]("/nope")["ok"] is False


class TestDiffChop:
    def test_reports_what_changed(self, bridge):
        br, td = bridge
        td.make("a", "constantCHOP", channels={"c": [0.0, 1.0]})
        td.make("b", "constantCHOP", channels={"c": [0.0, 3.0]})
        out = br["_do_diff_chop"]("/project1/a", "/project1/b")
        assert out["deltas"]["c"]["max"] == 2.0
        assert out["deltas"]["c"]["mean"] == 1.0

    def test_channels_on_only_one_side_are_reported(self, bridge):
        br, td = bridge
        td.make("a", "constantCHOP", channels={"c": [1.0], "gone": [1.0]})
        td.make("b", "constantCHOP", channels={"c": [1.0], "new": [1.0]})
        out = br["_do_diff_chop"]("/project1/a", "/project1/b")
        assert out["only_in_from"] == ["gone"]
        assert out["only_in_to"] == ["new"]

    def test_missing_operand(self, bridge):
        br, td = bridge
        td.make("a", "constantCHOP", channels={"c": [1.0]})
        assert br["_do_diff_chop"]("/project1/a", "/nope")["ok"] is False


class TestReadDat:
    def _table(self, td):
        rows = [["name", "value"]] + [[f"r{i}", str(i)] for i in range(10)]
        node = FakeDAT("t1", "tableDAT", table=rows)
        td.project_comp.add(node)
        return node

    def test_defaults_to_stats_with_a_header(self, bridge):
        br, td = bridge
        self._table(td)
        out = br["_do_read_dat"]("/project1/t1")
        assert out["format"] == "stats"
        assert out["header"] == ["name", "value"]
        assert out["columns"]["value"]["numeric"]["max"] == 9.0
        assert out["columns"]["name"]["distinct"] == 10

    def test_header_row_does_not_poison_numeric_column_stats(self, bridge):
        br, td = bridge
        self._table(td)
        stats = br["_do_read_dat"]("/project1/t1")["columns"]["value"]["numeric"]
        assert stats["count"] == 10  # the header cell is excluded

    def test_middle_rows_are_elided_with_a_note(self, bridge):
        br, td = bridge
        self._table(td)
        out = br["_do_read_dat"]("/project1/t1")
        assert out["elided"] == 5
        assert len(out["rows"]) == 6

    def test_raw_rows_still_available(self, bridge):
        br, td = bridge
        self._table(td)
        out = br["_do_read_dat"]("/project1/t1", 4, "rows")
        assert out["format"] == "rows"
        assert len(out["rows"]) == 4

    def test_small_table_is_not_elided(self, bridge):
        br, td = bridge
        td.project_comp.add(FakeDAT("t2", "tableDAT", table=[["a"], ["1"]]))
        out = br["_do_read_dat"]("/project1/t2")
        assert out["elided"] == 0

    def test_empty_table(self, bridge):
        br, td = bridge
        td.project_comp.add(FakeDAT("t3", "tableDAT", table=[]))
        out = br["_do_read_dat"]("/project1/t3")
        assert out["numRows"] == 0 and out["columns"] == {}


class TestReadPop:
    def test_metadata_is_free_at_any_point_count(self, bridge):
        br, td = bridge
        pop = td.make("p1", "noisePOP")
        pop._attributes = {"P": [[0.0, 0.0, 0.0]] * 5}
        pop.numPoints = 200_000
        out = br["_do_read_pop"]("/project1/p1", format="meta")
        assert out["numPoints"] == 200_000
        # Attributes are grouped by class -- point / prim / vert -- because
        # that is how TouchDesigner exposes them (pointAttributes,
        # primAttributes, vertAttributes) and the same name can appear in more
        # than one class.
        assert out["attributes"]["point"]["P"]["size"] == 3
        assert "values" not in out

    def test_every_attribute_class_is_reported(self, bridge):
        br, td = bridge
        pop = td.make("p1", "spherePOP")
        pop._attributes = {"P": [[0.0, 0.0, 0.0]], "N": [[0.0, 0.0, 1.0]]}
        pop._vert_attributes = {"Tex": [[0.0, 0.0, 0.0]]}
        pop._num_prims = 4
        out = br["_do_read_pop"]("/project1/p1", format="meta")
        assert set(out["attributes"]) == {"point", "vert"}   # no prim attrs
        assert set(out["attributes"]["point"]) == {"P", "N"}
        assert out["numPrims"] == 4                          # numPrims is a METHOD

    def test_an_operator_with_no_attributes_says_so(self, bridge):
        """A TOP or CHOP has none; an empty map should not read as an error."""
        br, td = bridge
        td.make("n1", "noiseTOP")
        out = br["_do_read_pop"]("/project1/n1")
        assert out["ok"] is True and out["attributes"] == {}
        assert "is this a POP" in out["note"]

    def test_values_are_skipped_above_the_ceiling(self, bridge):
        """POP.points() cannot slice, so a big POP has no cheap partial read --
        it must decline rather than stall for four frames."""
        br, td = bridge
        pop = td.make("p1", "noisePOP")
        pop._attributes = {"P": [[0.0, 0.0, 0.0]]}
        pop.numPoints = 160_000
        out = br["_do_read_pop"]("/project1/p1", max_points=4096)
        assert "values_skipped" in out
        assert "cannot slice" in out["values_skipped"]
        assert "values" not in out

    def test_vector_attribute_summarises_per_component(self, bridge):
        br, td = bridge
        pop = td.make("p1", "noisePOP")
        pop._attributes = {"P": [[0.0, 1.0, 2.0], [2.0, 3.0, 4.0]]}
        pop.numPoints = 2
        comps = br["_do_read_pop"]("/project1/p1")["values"]["P"]
        assert len(comps) == 3
        assert comps[0]["min"] == 0.0 and comps[0]["max"] == 2.0
        assert comps[2]["mean"] == 3.0

    def test_scalar_attribute_summarises_directly(self, bridge):
        br, td = bridge
        pop = td.make("p1", "noisePOP")
        pop._attributes = {"scale": [1.0, 2.0, 3.0]}
        pop.numPoints = 3
        assert br["_do_read_pop"]("/project1/p1")["values"]["scale"]["mean"] == 2.0

    def test_missing_node(self, bridge):
        br, _ = bridge
        assert br["_do_read_pop"]("/nope")["ok"] is False


class TestPureSummarizers:
    def test_non_finite_values_are_skipped(self, bridge):
        br, _ = bridge
        st = br["summarize_series"]([1.0, float("nan"), float("inf"), 3.0])
        assert st["count"] == 2 and st["mean"] == 2.0

    def test_non_numeric_entries_are_skipped(self, bridge):
        br, _ = bridge
        assert br["summarize_series"](["a", "2", None])["count"] == 1

    def test_mixed_column_reports_distinct_and_sample(self, bridge):
        br, _ = bridge
        col = br["summarize_column"](["a", "b", "a", ""])
        assert col["distinct"] == 3 and col["empty"] == 1
        assert col["sample"] == ["a", "b", ""]

    def test_edge_rows_keeps_both_ends(self, bridge):
        br, _ = bridge
        out = br["edge_rows"]([[i] for i in range(20)], edge=2)
        assert out["rows"] == [[0], [1], [18], [19]]
        assert out["elided"] == 16
