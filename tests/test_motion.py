"""Motion verdicts (Wave 12).

`capture_viewport` answers "is there a picture". It cannot answer "is anything
happening", and a feedback or audio-reactive network that renders one correct
frame and then freezes is structurally valid, visually plausible and broken.
"""

from __future__ import annotations

import pytest

from td_mcp import scoring
from td_mcp.vision import (
    STATIC_THRESHOLD,
    frame_signature,
    make_test_image,
    motion_verdict,
)
from tests.td_double import build_bridge


class TestFrameSignature:
    def test_signature_is_a_grid_not_a_scalar(self):
        pix, w, h = make_test_image("colorful", 32, 32)
        assert len(frame_signature(pix, w, h)) == 64

    def test_empty_buffer_yields_nothing(self):
        assert frame_signature(b"", 0, 0) == []

    def test_short_buffer_yields_nothing(self):
        assert frame_signature(b"\x00" * 8, 32, 32) == []

    def test_a_scrolling_pattern_changes_the_signature(self):
        """The reason a scalar mean will not do: a pattern that moves across
        the canvas holds its mean constant while every pixel changes."""
        w = h = 32

        def shifted(offset):
            buf = bytearray()
            for y in range(h):
                for x in range(w):
                    v = 255 if ((x + offset) // 8) % 2 == 0 else 0
                    buf += bytes((v, v, v, 255))
            return bytes(buf)

        a = frame_signature(shifted(0), w, h)
        b = frame_signature(shifted(8), w, h)
        assert sum(a) == pytest.approx(sum(b), rel=0.05)  # same mean
        assert motion_verdict([a, b])["is_static"] is False  # different picture


class TestMotionVerdict:
    def test_identical_frames_are_static(self):
        pix, w, h = make_test_image("colorful", 32, 32)
        sig = frame_signature(pix, w, h)
        v = motion_verdict([sig, sig, sig])
        assert v["is_static"] is True
        assert v["frames"] == 3
        assert "frozen" in v["caption"]

    def test_changing_frames_are_not_static(self):
        a = frame_signature(*make_test_image("black", 32, 32))
        b = frame_signature(*make_test_image("colorful", 32, 32))
        v = motion_verdict([a, b])
        assert v["is_static"] is False
        assert "animating" in v["caption"]

    def test_one_frame_is_unknown_not_false(self):
        """Claiming a network animates on the strength of one frame is exactly
        the error being fixed here."""
        sig = frame_signature(*make_test_image("colorful", 32, 32))
        v = motion_verdict([sig])
        assert v["is_static"] is None
        assert v["max_delta"] is None

    def test_no_frames_is_unknown(self):
        assert motion_verdict([])["is_static"] is None
        assert motion_verdict(None)["is_static"] is None

    def test_empty_signatures_are_discarded(self):
        sig = frame_signature(*make_test_image("colorful", 32, 32))
        assert motion_verdict([[], sig, []])["is_static"] is None

    def test_sub_threshold_jitter_still_reads_as_static(self):
        a = [10.0] * 64
        b = [10.0 + STATIC_THRESHOLD / 4] * 64
        assert motion_verdict([a, b])["is_static"] is True

    def test_ragged_signatures_do_not_crash(self):
        assert motion_verdict([[1.0, 2.0, 3.0], [1.0, 9.0]])["is_static"] is False


class TestScoringPenalisesAFrozenRender:
    SPEC = {"operators": [
        {"name": "n1", "type": "Noise TOP", "parameters": {}, "inputs": [None]}]}

    def test_static_render_scores_lower(self):
        base = scoring.score_build(self.SPEC)
        frozen = scoring.score_build(
            {**self.SPEC, "viewport_verdict": {"is_static": True}})
        assert frozen["score"] < base["score"]
        assert "static" in frozen["viewport_flags"]

    def test_moving_render_is_not_penalised(self):
        moving = scoring.score_build(
            {**self.SPEC, "viewport_verdict": {"is_static": False}})
        assert moving["viewport_flags"] == []
        assert moving["score"] == scoring.score_build(self.SPEC)["score"]

    def test_unknown_motion_is_not_penalised(self):
        """None means "too few frames to judge" and must read as neither
        fine nor broken."""
        unknown = scoring.score_build(
            {**self.SPEC, "viewport_verdict": {"is_static": None}})
        assert unknown["viewport_flags"] == []


class TestObserveHandler:
    @pytest.fixture()
    def bridge(self):
        return build_bridge()

    def test_reports_unknown_when_no_pixels_are_available(self, bridge):
        """A CHOP has no numpyArray(); motion cannot be judged and must not be
        guessed."""
        br, td = bridge
        td.make("n1", "noiseCHOP", channels={"c": [1.0]})
        out = br["_do_observe"]("/project1/n1")
        assert out["ok"] is True
        assert out["verdict"]["is_static"] is None
        assert "motion could not be judged" in out["note"]

    def test_detects_a_frozen_render(self, bridge):
        br, td = bridge
        node = td.make("n1", "noiseTOP")
        br["_frame_signature"] = lambda n: [1.0] * 64
        out = br["_do_observe"]("/project1/n1")
        assert out["verdict"]["is_static"] is True
        assert out["motion"]["frames"] == 3

    def test_detects_an_animating_render(self, bridge):
        br, td = bridge
        td.make("n1", "noiseTOP")
        seq = iter([[0.0] * 64, [50.0] * 64, [100.0] * 64])
        br["_frame_signature"] = lambda n: next(seq)
        out = br["_do_observe"]("/project1/n1")
        assert out["verdict"]["is_static"] is False

    def test_frame_count_is_capped(self, bridge):
        br, td = bridge
        td.make("n1", "noiseTOP")
        calls = []
        br["_frame_signature"] = lambda n: (calls.append(1), [1.0] * 64)[1]
        br["_do_observe"]("/project1/n1", frames=9999)
        assert len(calls) == br["_MOTION_MAX_FRAMES"]

    def test_at_least_two_frames_are_always_taken(self, bridge):
        br, td = bridge
        td.make("n1", "noiseTOP")
        calls = []
        br["_frame_signature"] = lambda n: (calls.append(1), [1.0] * 64)[1]
        br["_do_observe"]("/project1/n1", frames=1)
        assert len(calls) == 2

    def test_missing_node(self, bridge):
        br, _ = bridge
        assert br["_do_observe"]("/nope")["ok"] is False


class TestNumpyArrayRange:
    """`TOP.numpyArray()` returns floats in 0..1. The first live session's
    healthy Noise TOP came back mean 0.46 and was judged black by thresholds
    written for 0..255 -- every good render scored -20."""

    @pytest.fixture()
    def bridge(self):
        # numpy is what TouchDesigner supplies to capture_viewport; it is in
        # the [test] extra so this regression runs in CI, and skips elsewhere.
        pytest.importorskip("numpy")
        return build_bridge()

    def _top(self, td, name, value):
        import numpy as np
        px = np.full((16, 16, 4), value, dtype=np.float32)
        px[..., 3] = 1.0
        return td.make(name, "noiseTOP", pixels=px)

    def test_a_mid_grey_float_render_is_not_black(self, bridge):
        br, td = bridge
        self._top(td, "n1", 0.46)
        verdict = br["_do_capture_viewport"]("/project1/n1")["verdict"]
        assert verdict["is_black"] is False
        assert verdict["mean"] == pytest.approx(0.46 * 255, abs=1.0)

    def test_a_black_float_render_is_black(self, bridge):
        br, td = bridge
        self._top(td, "n1", 0.0)
        assert br["_do_capture_viewport"]("/project1/n1")["verdict"]["is_black"] is True

    def test_a_uniform_float_render_is_flat(self, bridge):
        br, td = bridge
        self._top(td, "n1", 0.7)
        verdict = br["_do_capture_viewport"]("/project1/n1")["verdict"]
        assert verdict["is_flat"] is True and verdict["is_black"] is False

    def test_a_noisy_float_render_is_neither(self, bridge):
        br, td = bridge
        td.make("n1", "noiseTOP")  # the double's default: random 0.25..0.75
        verdict = br["_do_capture_viewport"]("/project1/n1")["verdict"]
        assert verdict["is_black"] is False and verdict["is_flat"] is False

    def test_byte_arrays_are_left_alone(self, bridge):
        import numpy as np
        br, td = bridge
        px = np.full((16, 16, 4), 118, dtype=np.uint8)
        td.make("n1", "noiseTOP", pixels=px)
        verdict = br["_do_capture_viewport"]("/project1/n1")["verdict"]
        assert verdict["mean"] == pytest.approx(118.0, abs=0.5)

    def test_observe_sees_motion_in_float_frames(self, bridge):
        """Full-scale change in 0..1 data is 1.0 -- below the tile threshold
        unless the frames are rescaled first."""
        import numpy as np
        br, td = bridge
        frames = [np.full((16, 16, 4), v, dtype=np.float32) for v in (0.2, 0.5, 0.8)]
        td.make("n1", "noiseTOP", frames=frames)
        assert br["_do_observe"]("/project1/n1", frames=3)["verdict"]["is_static"] is False

    def test_observe_sees_a_frozen_float_render(self, bridge):
        import numpy as np
        br, td = bridge
        frames = [np.full((16, 16, 4), 0.5, dtype=np.float32)] * 3
        td.make("n1", "noiseTOP", frames=frames)
        assert br["_do_observe"]("/project1/n1", frames=3)["verdict"]["is_static"] is True
