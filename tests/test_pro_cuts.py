"""Tests for the retention features: hook, dead-end trims, crop, color."""

import numpy as np
import pytest

from tokcut import analysis as A
from tokcut.render import color_args

SRC_HLG = {"w": 1038, "h": 1616, "duration": 95.5, "fps": 60,
           "audio": True, "transfer": "arib-std-b67", "primaries": "bt2020"}
SRC_SDR = {"w": 1920, "h": 1080, "duration": 60.0, "fps": 60,
           "audio": True, "transfer": "bt709", "primaries": "bt709"}


# ---------------------------------------------------------------- color

def test_color_args_hlg_kept():
    args = color_args(SRC_HLG)
    assert "arib-std-b67" in args
    assert "bt2020nc" in args


def test_color_args_sdr_gets_bt709():
    args = color_args(SRC_SDR)
    assert "bt709" in args
    assert "arib-std-b67" not in args


def test_color_args_unknown_defaults_sdr():
    args = color_args({"w": 1, "h": 1, "duration": 1, "fps": 30,
                       "audio": False})
    assert "bt709" in args


# ---------------------------------------------------------------- trims

def test_trim_dead_ends_cuts_long_lead_and_tail():
    segs = [[0.0, 10.0, 0], [10.0, 20.0, 2], [20.0, 30.0, 0]]
    out = A.trim_dead_ends(segs)
    assert out[0][0] == pytest.approx(8.5)   # 1.5s of lead-in kept
    assert out[-1][1] == pytest.approx(21.0)  # 1.0s of tail kept


def test_trim_dead_ends_keeps_short_edges():
    segs = [[0.0, 1.0, 0], [1.0, 20.0, 2]]
    out = A.trim_dead_ends(segs)
    assert out[0][0] == 0.0  # short lead untouched


def test_trim_dead_ends_ignores_action_edges():
    segs = [[0.0, 10.0, 2], [10.0, 20.0, 0], [20.0, 30.0, 2]]
    out = A.trim_dead_ends(segs)
    assert out[0][0] == 0.0
    assert out[-1][1] == 30.0


def test_trim_dead_ends_drops_short_trailing_non_action():
    # ends on a 2.4s "lag" outro (stopping the recording) — drop it so
    # the video ends on the action segment
    segs = [[0.0, 10.0, 2], [10.0, 12.4, 1]]
    out = A.trim_dead_ends(segs)
    assert out[-1][2] == 2
    assert out[-1][1] == 10.0


def test_trim_dead_ends_no_action_keeps_tail():
    # uniform-motion video (all lag): nothing to "end on", keep the tail
    segs = [[0.0, 10.0, 1], [10.0, 12.0, 1]]
    out = A.trim_dead_ends(segs)
    assert len(out) == 2


# ---------------------------------------------------------------- hook

def test_pick_hook_finds_peak():
    fps = A.SAMPLE_FPS
    scores = np.zeros(60 * fps)
    scores[30 * fps] = 100.0  # peak at t=30
    win = A.pick_hook(scores, 60.0)
    assert win is not None
    start, end = win
    assert start <= 30.0 <= end
    assert end - start == pytest.approx(1.3)


def test_pick_hook_skips_opening_seconds():
    fps = A.SAMPLE_FPS
    scores = np.zeros(60 * fps)
    scores[0] = 100.0          # peak in the opening — pointless as a hook
    scores[40 * fps] = 50.0    # later, lower peak should win
    win = A.pick_hook(scores, 60.0)
    assert win is not None
    assert win[0] >= 4.0 - 1.3


def test_pick_hook_short_video_none():
    assert A.pick_hook(np.ones(12), 2.0) is None


# ---------------------------------------------------------------- crop

def _frames_with_active_box(n=30, ah=60, aw=100):
    """Static frames except a flickering box (rows 10-40, cols 20-70)."""
    rng = np.random.default_rng(0)
    frames = np.full((n, ah, aw), 50, dtype=np.int16)
    noise = rng.integers(0, 120, size=(n, 30, 50))
    frames[:, 10:40, 20:70] += noise.astype(np.int16)
    return frames


def test_content_crop_finds_active_region():
    src = dict(SRC_SDR)
    crop = A.content_crop(_frames_with_active_box(), src)
    assert crop is not None
    x, y, w, h = crop
    # active box in source coords: x 384-1344, y 180-720 (1920x1080 src)
    assert x <= 384 and x + w >= 1344
    assert y <= 180 and y + h >= 720
    assert w * h < 0.92 * src["w"] * src["h"]


def test_content_crop_full_motion_returns_none():
    rng = np.random.default_rng(1)
    frames = rng.integers(0, 255, size=(30, 60, 100)).astype(np.int16)
    assert A.content_crop(frames, dict(SRC_SDR)) is None


def test_content_crop_static_returns_none():
    frames = np.full((30, 60, 100), 80, dtype=np.int16)
    assert A.content_crop(frames, dict(SRC_SDR)) is None


def test_content_crop_respects_min_keep():
    # tiny active region must not produce an extreme zoom
    frames = np.full((30, 60, 100), 50, dtype=np.int16)
    rng = np.random.default_rng(2)
    frames[:, 28:32, 48:52] += rng.integers(
        0, 120, size=(30, 4, 4)).astype(np.int16)
    src = dict(SRC_SDR)
    crop = A.content_crop(frames, src)
    assert crop is not None
    assert crop[2] >= src["w"] * 0.55 - 2
    assert crop[3] >= src["h"] * 0.55 - 2


# ------------------------------------------------------------- classify

def test_classify_uniform_motion_single_tier():
    scores = np.full(120, 5.0) + np.random.default_rng(3).normal(
        0, 0.01, 120)
    tiers = A.classify(scores)
    assert set(np.unique(tiers)) == {1}


def test_to_segments_clamps_to_duration():
    tiers = np.array([2] * 61, dtype=int)  # 61 samples @6fps ≈ 10.17s
    segs = A.to_segments(tiers, sample_fps=6, duration=10.0)
    assert segs[-1][1] == pytest.approx(10.0)
