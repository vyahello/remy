"""Tests for the retention features: hook, dead-end trims, crop, color."""

import numpy as np
import pytest

from remy import analysis as A
from remy.render import color_args

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


def test_trim_dead_ends_keeps_bright_payoff_tail():
    # a low-motion but BRIGHT tail (a results screen / highlighted answer)
    # is the payoff — keep it, and lift it off the fastest tier so it reads
    bright = {(10.0, 14.0): 40.0}  # tail is bright; everything else dark
    segs = [[0.0, 10.0, 2], [10.0, 14.0, 0]]
    out = A.trim_dead_ends(
        segs, lambda s: bright.get((s[0], s[1]), 5.0))
    assert out[-1][1] == pytest.approx(14.0)  # tail kept, not dropped/cut
    assert out[-1][2] == 1  # bumped dead->lag so it isn't a 3.2x flash


def test_trim_dead_ends_drops_dark_tail_even_with_brightness():
    # a dark trailing beat (empty prompt / stop-recording shuffle) still goes
    segs = [[0.0, 10.0, 2], [10.0, 12.4, 1]]
    out = A.trim_dead_ends(segs, lambda s: 5.0)  # uniformly dark
    assert out[-1][2] == 2
    assert out[-1][1] == 10.0


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


# ----------------------------------------------------------- beat_align

def _out_times(segs):
    """Cumulative output-time cut points for an edit list."""
    t, times = 0.0, []
    for s, e, v in segs:
        t += (e - s) / v
        times.append(t)
    return times


def test_beat_align_snaps_cuts_to_grid():
    # bpm=60 -> beat every 1.0s of output time
    segs = [(0.0, 1.4, 1.0), (1.4, 4.6, 2.0), (4.6, 9.7, 1.0)]
    aligned = A.beat_align(segs, bpm=60, duration=12.0)
    for t in _out_times(aligned):
        assert t % 1.0 == pytest.approx(0.0, abs=1e-6)
    # contiguity preserved
    for a, b in zip(aligned, aligned[1:]):
        assert a[1] == pytest.approx(b[0])


def test_beat_align_keeps_speeds_and_order():
    segs = [(0.0, 2.2, 1.0), (2.2, 8.1, 3.2)]
    aligned = A.beat_align(segs, bpm=84, duration=10.0)
    assert [v for _, _, v in aligned] == [1.0, 3.2]
    assert all(e > s for s, e, _ in aligned)


def test_beat_align_never_empties_a_segment():
    # a segment much shorter than a beat must survive
    segs = [(0.0, 0.4, 1.0), (0.4, 6.0, 1.0)]
    aligned = A.beat_align(segs, bpm=30, duration=6.0)  # beat = 2.0s
    assert all(e - s >= A.MIN_SEG_SRC for s, e, _ in aligned)


def test_beat_align_end_stays_within_source():
    segs = [(0.0, 5.5, 1.0)]
    aligned = A.beat_align(segs, bpm=60, duration=5.6)
    s, e, _ = aligned[0]
    assert e <= 5.6
    assert _out_times(aligned)[-1] % 1.0 == pytest.approx(0.0, abs=1e-6)


def test_beat_align_hook_gap_respected():
    # hook (8..9.3) is NOT contiguous with the chronological cut (1.5..)
    segs = [(8.0, 9.3, 1.0), (1.5, 6.0, 1.7)]
    aligned = A.beat_align(segs, bpm=60, duration=10.0)
    times = _out_times(aligned)
    assert times[0] % 1.0 == pytest.approx(0.0, abs=1e-6)
    # the chronological segment's start must be untouched
    assert aligned[1][0] == pytest.approx(1.5)


# ----------------------------------------------------------- auto_target

def test_auto_target_short_video_keeps_base_speeds():
    # natural pacing well under AUTO_MAX -> no solving needed
    runs = [[0.0, 10.0, 2], [10.0, 20.0, 1]]  # ~10 + 10/1.7 ≈ 15.9s
    assert A.auto_target(runs) is None


def test_auto_target_long_video_compresses_to_sweet_spot():
    # mostly lag/dead: natural ≈ 20 + 100/1.7 + 60/3.2 ≈ 97s -> compress
    runs = [[0.0, 20.0, 2], [20.0, 120.0, 1], [120.0, 180.0, 0]]
    t = A.auto_target(runs)
    assert t == A.AUTO_SWEET


def test_auto_target_never_squeezes_action():
    # 50s of real action sets the floor above the sweet spot
    runs = [[0.0, 50.0, 2], [50.0, 150.0, 0]]
    t = A.auto_target(runs)
    assert t is not None
    assert t >= 50.0 * 1.05 - 1e-9


# ----------------------------------------------------------- edit_window

def test_edit_window_short_clip_untouched():
    assert A.edit_window(15.0, landscape=True) == (0.0, 15.0)
    assert A.edit_window(15.0, landscape=False) == (0.0, 15.0)


def test_edit_window_landscape_trims_obs_edges():
    head, tail = A.edit_window(60.0, landscape=True)
    assert head == A.OBS_HEAD
    assert tail == 60.0 - A.OBS_TAIL


def test_edit_window_portrait_keeps_head():
    head, tail = A.edit_window(60.0, landscape=False)
    assert head == 0.0
    assert tail == 58.0


def test_edit_window_manual_trim_stacks_with_auto():
    # explicit trim removes more than the auto window -> explicit wins
    head, tail = A.edit_window(60.0, landscape=True,
                               trim_start=5.0, trim_end=8.0)
    assert head == 5.0                 # > OBS_HEAD (1.5)
    assert tail == 60.0 - 8.0          # cut more than OBS_TAIL (3.0)


def test_edit_window_manual_trim_below_auto_keeps_auto():
    # a smaller explicit trim never keeps MORE than the auto window
    head, tail = A.edit_window(60.0, landscape=True,
                               trim_start=0.5, trim_end=1.0)
    assert head == A.OBS_HEAD          # auto still wins (1.5 > 0.5)
    assert tail == 60.0 - A.OBS_TAIL   # auto still wins (3.0 > 1.0)


def test_edit_window_over_trim_keeps_min_window():
    head, tail = A.edit_window(30.0, landscape=False,
                               trim_start=20.0, trim_end=20.0)
    assert tail - head >= 1.0          # never collapses below 1s


def test_to_segments_drops_runs_past_duration():
    import numpy as np
    # 30 lag samples then 6 action samples; window ends mid-lag at 4.5s
    tiers = np.array([1] * 30 + [2] * 6, dtype=int)
    segs = A.to_segments(tiers, sample_fps=6, duration=4.5)
    assert all(e > s for s, e, _t in segs)
    assert segs[-1][1] == pytest.approx(4.5)


def test_content_crop_never_slices_static_text():
    # static "text" detail across the full width (checkerboard columns),
    # motion only in the center: the crop must not cut into the text
    rng = np.random.default_rng(7)
    base = (np.indices((68, 120)).sum(axis=0) % 2) * 40.0 + 20.0
    frames = np.repeat(base[None, :, :], 50, axis=0)
    frames[:, 25:45, 50:70] += rng.normal(0, 30, (50, 20, 20))  # action
    src = {"w": 1920, "h": 1080, "fps": 60, "duration": 60.0, "audio": False}
    crop = A.content_crop(frames.astype(np.float32), src,
                          protect_text=True)
    # text spans the whole frame -> no seam -> no crop at all
    assert crop is None


# ----------------------------------------------------------- window_crop

def _screen_frames():
    """Synthetic desktop: textured bright margins, dark window inside."""
    rng = np.random.default_rng(11)
    base = rng.uniform(120, 200, (68, 120))   # busy wallpaper
    base[:, 12:108] = 40.0                    # the app window
    frames = np.repeat(base[None, :, :], 40, axis=0)
    frames[:, 30:50, 40:80] += rng.normal(0, 25, (40, 20, 40))  # action
    return frames.astype(np.float32)


def test_window_crop_cuts_desktop_margins():
    src = {"w": 1920, "h": 1080, "fps": 60, "duration": 60.0,
           "audio": False}
    crop = A.window_crop(_screen_frames(), src)
    assert crop is not None
    x, y, w, h = crop
    assert x >= 100          # left margin (12/120 of 1920 = 192) shaved
    assert x + w <= 1850     # right margin shaved
    assert w >= 1500         # but the whole window survives


def test_window_crop_fullscreen_window_no_crop():
    # window fills the frame -> nothing to shave
    rng = np.random.default_rng(3)
    base = np.full((68, 120), 40.0)
    frames = np.repeat(base[None, :, :], 40, axis=0)
    frames[:, 30:50, 40:80] += rng.normal(0, 25, (40, 20, 40))
    src = {"w": 1920, "h": 1080, "fps": 60, "duration": 60.0,
           "audio": False}
    assert A.window_crop(frames.astype(np.float32), src) is None


# ------------------------------------------------- action speed-up cap

def test_assign_speeds_action_stays_realtime_by_default():
    runs = [[0.0, 40.0, 2], [40.0, 60.0, 0]]
    segs, est = A.assign_speeds(runs, target=20.0)
    action_speeds = [v for (s, e, v), r in zip(segs, runs) if r[2] == 2]
    assert action_speeds == [1.0]


def test_assign_speeds_screen_action_speedup_capped():
    runs = [[0.0, 40.0, 2], [40.0, 60.0, 0]]
    segs, est = A.assign_speeds(runs, target=20.0,
                                max_action_speed=A.SCREEN_ACTION_MAX)
    action_speed = segs[0][2]
    assert 1.0 < action_speed <= A.SCREEN_ACTION_MAX


def test_auto_target_screen_floor_divided_by_action_cap():
    runs = [[0.0, 50.0, 2], [50.0, 150.0, 0]]
    plain = A.auto_target(runs)
    screen = A.auto_target(runs, max_action_speed=A.SCREEN_ACTION_MAX)
    assert plain == 50.0 * 1.05
    assert screen == max(A.AUTO_SWEET, 50.0 * 1.05 / A.SCREEN_ACTION_MAX)
    assert screen < plain


def test_window_crop_peels_busy_top_bar():
    # like _screen_frames but with a busy "taskbar" strip at the very
    # top whose mean luminance is close to the window background
    rng = np.random.default_rng(5)
    base = rng.uniform(120, 200, (68, 120))
    base[:, 12:108] = 40.0
    base[0:2, :] = rng.uniform(25, 55, (2, 120))  # busy bar, bg-ish mean
    frames = np.repeat(base[None, :, :], 40, axis=0)
    frames[:, 30:50, 40:80] += rng.normal(0, 25, (40, 20, 40))
    src = {"w": 1920, "h": 1080, "fps": 60, "duration": 60.0,
           "audio": False}
    crop = A.window_crop(frames.astype(np.float32), src)
    assert crop is not None
    x, y, w, h = crop
    assert y >= 16  # the 2-row bar (~32px) is peeled, minus padding


def test_window_crop_never_slices_a_second_window():
    # split-screen demo: bright "browser" fills the left half (static),
    # dark terminal with the motion on the right — the browser is
    # content, not chrome, and must survive
    rng = np.random.default_rng(9)
    base = np.full((68, 120), 200.0)        # browser (white page)
    base[:, 60:] = 40.0                     # terminal half
    frames = np.repeat(base[None, :, :], 40, axis=0)
    frames[:, 20:50, 70:110] += rng.normal(0, 25, (40, 30, 40))
    src = {"w": 1920, "h": 1080, "fps": 60, "duration": 60.0,
           "audio": False}
    crop = A.window_crop(frames.astype(np.float32), src)
    # either no crop, or one that keeps the left (browser) half
    assert crop is None or crop[0] < 1920 * 0.1


def test_zoom_crop_tighter_shrinks_around_center():
    src = {"w": 1920, "h": 1080}
    box = A.zoom_crop((400, 200, 1000, 600), src, 1.25)
    assert box is not None
    x, y, w, h = box
    assert w < 1000 and h < 600
    assert abs((x + w / 2) - 900) <= 4   # center preserved
    assert abs((y + h / 2) - 500) <= 4
    assert w % 2 == 0 and h % 2 == 0


def test_zoom_crop_punches_into_full_frame():
    src = {"w": 1920, "h": 1080}
    box = A.zoom_crop(None, src, 1.3)
    assert box is not None and box[2] < 1920 and box[3] < 1080


def test_zoom_crop_wider_clamps_to_frame():
    src = {"w": 1920, "h": 1080}
    # pulling wider than the frame allows = honest no-crop
    assert A.zoom_crop((400, 200, 1000, 600), src, 0.5) is None


def test_zoom_crop_neutral_is_passthrough():
    src = {"w": 1920, "h": 1080}
    assert A.zoom_crop(None, src, 1.0) is None
    assert A.zoom_crop((10, 10, 200, 200), src, 1.0) == (10, 10, 200, 200)
