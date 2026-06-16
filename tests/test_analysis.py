import numpy as np
import pytest

from remy import analysis as A


def test_classify_three_tiers():
    # weight the distribution so the top values exceed the 80th percentile
    scores = np.array([0.0] * 20 + [5.0] * 20 + [50.0] * 10, dtype=float)
    tiers = A.classify(scores)
    assert tiers[0] == 0      # dead (below 45th pct)
    assert tiers[-1] == 2     # action (above 80th pct)
    assert set(np.unique(tiers)).issubset({0, 1, 2})


def test_to_segments_merges_short_runs():
    # alternating tiers that are each shorter than MIN_SEG_SEC should merge
    tiers = np.array([0, 0, 2, 0, 0, 2, 0, 0], dtype=int)
    segs = A.to_segments(tiers, sample_fps=6)
    # all runs are < MIN_SEG_SEC so they collapse to a single segment
    assert len(segs) == 1
    assert segs[0][0] == 0.0


def test_to_segments_boundaries_cover_timeline():
    tiers = np.array([2] * 30 + [0] * 30, dtype=int)
    segs = A.to_segments(tiers, sample_fps=6)
    assert segs[0][0] == 0.0
    assert segs[-1][1] == pytest.approx(60 / 6)
    # segments are contiguous
    for a, b in zip(segs, segs[1:]):
        assert a[1] == pytest.approx(b[0])


def test_assign_speeds_action_stays_realtime():
    segs = [[0, 10, 2], [10, 20, 0]]  # first seg is action (tier 2)
    out, _ = A.assign_speeds(segs, target=None)
    action_speed = out[0][2]
    dead_speed = out[1][2]
    assert action_speed == A.SPEED_ACTION
    assert dead_speed == A.SPEED_DEAD


def test_assign_speeds_hits_target():
    segs = [[0, 30, 2], [30, 90, 0]]  # 90s raw
    out, est = A.assign_speeds(segs, target=40)
    assert est == pytest.approx(40, abs=1.5)
    # action segment is never sped up even when solving for a target
    assert out[0][2] == pytest.approx(A.SPEED_ACTION, abs=1e-6)


def test_assign_speeds_respects_max_speed():
    segs = [[0, 1, 2], [1, 600, 0]]  # extreme: tiny action, huge dead
    out, _ = A.assign_speeds(segs, target=5)
    for _, _, sp in out:
        assert sp <= A.MAX_SPEED + 1e-6


def test_smooth_preserves_length():
    scores = np.random.rand(120)
    assert len(A.smooth(scores)) == len(scores)


def test_saliency_map_shape_and_range():
    frames = np.random.randint(0, 255, size=(20, 40, 30), dtype=np.uint8)
    sal = A.saliency_map(frames)
    assert sal.shape == (40, 30)
    assert sal.min() >= 0.0
    assert sal.max() <= 1.0 + 1e-6
