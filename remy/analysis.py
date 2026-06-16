"""Source probing, motion scoring and the edit decision list."""

import json
import subprocess

import numpy as np

from .types import Segment, SourceInfo, SpeedSegment

SAMPLE_FPS = 6          # motion-analysis sampling rate
ANALYZE_W = 120         # analysis frame width (tiny = fast)
SMOOTH_SEC = 1.5        # smoothing window for motion scores
MIN_SEG_SEC = 1.4       # shorter runs get merged into a neighbour
PCT_LOW, PCT_HIGH = 45, 80   # adaptive tier thresholds (percentiles)
SPEED_DEAD, SPEED_LAG, SPEED_ACTION = 3.2, 1.7, 1.0
MAX_SPEED = 6.0


def probe(path: str) -> SourceInfo:
    """Return dict with w/h (rotation-corrected), duration, fps, audio."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams",
         "-of", "json", path],
        capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    w, h = int(v["width"]), int(v["height"])
    for sd in v.get("side_data_list", []):
        rot = sd.get("rotation")
        if rot is not None and int(rot) % 180 != 0:
            w, h = h, w
    dur = float(v.get("duration") or info["format"]["duration"])
    num, den = v.get("avg_frame_rate", "60/1").split("/")
    fps = float(num) / float(den) if float(den) else 60.0
    return {"w": w, "h": h, "duration": dur, "fps": fps, "audio": has_audio,
            "transfer": v.get("color_transfer", "") or "",
            "primaries": v.get("color_primaries", "") or ""}


def motion_scores(
    path: str, src: SourceInfo
) -> tuple[np.ndarray, np.ndarray]:
    """Sample tiny gray frames; return (per-frame motion score, frames)."""
    aw = ANALYZE_W
    ah = max(2, int(round(src["h"] * aw / src["w"] / 2)) * 2)
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", path,
         "-vf", f"fps={SAMPLE_FPS},scale={aw}:{ah},format=gray",
         "-f", "rawvideo", "pipe:1"],
        stdout=subprocess.PIPE)
    assert proc.stdout is not None
    raw = proc.stdout.read()
    proc.wait()
    n = len(raw) // (aw * ah)
    if n < 2:
        raise ValueError(
            "could not decode at least two analysis frames — "
            "is this a valid video file?")
    frames = np.frombuffer(raw[: n * aw * ah], dtype=np.uint8)
    frames = frames.reshape(n, ah, aw).astype(np.int16)
    diffs = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    return np.concatenate([[diffs[0]], diffs]), frames


def saliency_map(frames: np.ndarray) -> np.ndarray:
    """Where to keep a caption OFF, per pixel, over the whole video.

    Three cues, each a reason not to cover a region:
    - **temporal motion** — where pixels CHANGE (typing, scrolling, a
      moving hand). Both the average change and the per-pixel PEAK change
      count, so a spot that's busy only while you type still reads as
      active instead of being averaged away over a mostly-still clip.
    - **brightness** — in dark-room desk footage the content the viewer
      must see (a screen, a device display) is what glows.
    - **edges** — fine detail like on-screen text.

    Motion and brightness are weighted co-equally so the caption avoids
    what's *changing* as much as what's *bright* — it settles on the
    region that is both still and dark.
    """
    f = frames.astype(np.float32)
    diff = np.abs(np.diff(f, axis=0))
    motion = 0.6 * diff.mean(axis=0) + 0.4 * diff.max(axis=0)
    mean_frame = f.mean(axis=0)
    gy, gx = np.gradient(mean_frame)
    edges = np.hypot(gx, gy)

    def norm(a: np.ndarray) -> np.ndarray:
        m = np.percentile(a, 98)
        return np.clip(a / m, 0, 1) if m > 0 else a

    return 0.4 * norm(motion) + 0.15 * norm(edges) + 0.45 * norm(mean_frame)


def smooth(scores: np.ndarray) -> np.ndarray:
    win = max(1, int(SMOOTH_SEC * SAMPLE_FPS))
    kernel = np.ones(win) / win
    return np.convolve(scores, kernel, mode="same")


def classify(scores: np.ndarray) -> np.ndarray:
    """Per-sample tier: 0=dead, 1=lag, 2=action."""
    lo, hi = np.percentile(scores, [PCT_LOW, PCT_HIGH])
    # near-uniform motion (e.g. a constantly-updating screen recording):
    # percentile tiers would just amplify noise — treat it as one tier and
    # let the target-duration solver apply a single global speed instead.
    if hi - lo < 1e-6 or hi < lo * 1.3:
        return np.full(len(scores), 1, dtype=int)
    tiers = np.full(len(scores), 1, dtype=int)
    tiers[scores < lo] = 0
    tiers[scores > hi] = 2
    return tiers


def to_segments(
    tiers: np.ndarray,
    sample_fps: int = SAMPLE_FPS,
    duration: float | None = None,
) -> list[Segment]:
    """Collapse per-sample tiers into [start, end, tier] runs (seconds)."""
    segs: list[Segment] = []
    start = 0
    for i in range(1, len(tiers) + 1):
        if i == len(tiers) or tiers[i] != tiers[start]:
            segs.append([start / sample_fps, i / sample_fps,
                         int(tiers[start])])
            start = i
    merged: list[Segment] = []
    for seg in segs:
        if merged and (seg[1] - seg[0] < MIN_SEG_SEC
                       or seg[2] == merged[-1][2]):
            merged[-1][1] = seg[1]
        else:
            merged.append(seg)
    if len(merged) > 1 and merged[0][1] - merged[0][0] < MIN_SEG_SEC:
        merged[1][0] = merged[0][0]
        merged.pop(0)
    if duration is not None:
        # sampling rounds the tail up past the real end of the window;
        # drop runs that start beyond it entirely, then clamp the last
        while merged and merged[-1][0] >= duration:
            merged.pop()
        if merged:
            merged[-1][1] = min(merged[-1][1], duration)
    return merged


def trim_dead_ends(segs: list[Segment]) -> list[Segment]:
    """Open and close on action: hard-trim boring footage at the edges.

    A boring opener kills retention in the first second and a boring
    ending kills the loop/rewatch. Leading dead footage is cut to a short
    1.5s beat of context. At the tail, short non-action segments (the
    "stop the recording" shuffle) are dropped entirely so the video ends
    on the win — long ones are cut to a 1.0s beat instead.
    """
    if segs and segs[0][2] == 0 and segs[0][1] - segs[0][0] > 2.0:
        segs[0][0] = segs[0][1] - 1.5
    if any(s[2] == 2 for s in segs):
        while (len(segs) > 1 and segs[-1][2] != 2
               and segs[-1][1] - segs[-1][0] <= 6.0):
            segs.pop()
    if segs and segs[-1][2] == 0 and segs[-1][1] - segs[-1][0] > 1.5:
        segs[-1][1] = segs[-1][0] + 1.0
    return segs


def pick_hook(
    scores: np.ndarray,
    duration: float,
    hook_sec: float = 1.3,
    skip_head: float = 4.0,
    sample_fps: int = SAMPLE_FPS,
) -> tuple[float, float] | None:
    """Find the cold-open moment: the strongest beat, biased late.

    Returns a (start, end) window to prepend as a teaser — "show the
    payoff first, then how I got there". The payoff of a process video
    usually lives near the end, so later peaks are weighted up; the
    opening seconds are skipped entirely (a hook from the existing
    opening adds nothing). None when the video is too short to bother.
    """
    if duration < skip_head + 2 * hook_sec:
        return None
    # progress ramp: a peak at the end weighs 2.5x one at the start
    weighted = scores * np.linspace(0.4, 1.0, len(scores))
    lo = int(skip_head * sample_fps)
    peak = lo + int(np.argmax(weighted[lo:]))
    t_peak = peak / sample_fps
    start = min(max(0.0, t_peak - hook_sec / 2), duration - hook_sec)
    return start, start + hook_sec


def _mass_bounds(marginal: np.ndarray, keep: float) -> tuple[int, int]:
    """Smallest [lo, hi] index range holding `keep` of the marginal mass.

    Trims whichever end currently contributes less, so faint widespread
    motion (animated wallpaper, noise) is shaved while the bulk of the
    action is kept.
    """
    lo, hi = 0, len(marginal) - 1
    budget = (1.0 - keep) * float(marginal.sum())
    removed = 0.0
    while lo < hi:
        side = lo if marginal[lo] <= marginal[hi] else hi
        if removed + marginal[side] > budget:
            break
        removed += float(marginal[side])
        if side == lo:
            lo += 1
        else:
            hi -= 1
    return lo, hi


def _expand_to_seam(marginal: np.ndarray, idx: int, step: int,
                    thr_frac: float = 0.12) -> int:
    """Push a crop edge outward until it sits on a visually quiet seam.

    `marginal` is per-column (or per-row) static detail; an edge resting
    on a detailed column would slice through text/UI, so it moves in
    `step` direction (-1 left/up, +1 right/down) until the detail drops
    below thr_frac of the peak — or the frame edge.
    """
    thr = thr_frac * float(marginal.max() or 1.0)
    i = idx
    while 0 <= i + step < len(marginal) and marginal[i] > thr:
        i += step
    return i


def content_crop(
    frames: np.ndarray,
    src: SourceInfo,
    min_keep: float = 0.55,
    keep_mass: float = 0.96,
    pad_px: int = 16,
    protect_text: bool = False,
) -> tuple[int, int, int, int] | None:
    """Zoom into where the action happens.

    Screen recordings and wide shots waste pixels on static margins
    (desktop wallpaper, window chrome); on a 1080x1920 canvas that makes
    the content small and unreadable. This finds the smallest box holding
    ~keep_mass of the video's total motion energy per axis and returns an
    (x, y, w, h) crop in source pixels — or None when cropping wouldn't
    gain at least ~10% (an honest no-crop beats a silly one).

    `protect_text` (screen-recording content): motion only marks
    *changing* pixels, but a terminal full of still text is content too —
    each crop edge is pushed outward to the nearest low-detail seam so
    static text/UI is never cut mid-character. Camera footage skips this
    (sensor noise/texture makes everything "detail", which would veto
    every crop).
    """
    f = frames.astype(np.float32)
    motion = np.abs(np.diff(f, axis=0)).mean(axis=0)
    if float(motion.sum()) <= 0:
        return None
    r0, r1 = _mass_bounds(motion.sum(axis=1), keep_mass)
    c0, c1 = _mass_bounds(motion.sum(axis=0), keep_mass)

    if protect_text:
        # static detail map: gradients of the time-averaged frame (still
        # text/UI stays sharp in the mean; moving content blurs out)
        mean_f = f.mean(axis=0)
        detail = (np.abs(np.diff(mean_f, axis=1,
                                 prepend=mean_f[:, :1]))
                  + np.abs(np.diff(mean_f, axis=0,
                                   prepend=mean_f[:1, :])))
        col_det = detail.sum(axis=0)
        row_det = detail.sum(axis=1)
        c0 = _expand_to_seam(col_det, c0, -1)
        c1 = _expand_to_seam(col_det, c1, +1)
        r0 = _expand_to_seam(row_det, r0, -1)
        r1 = _expand_to_seam(row_det, r1, +1)

    return _finalize_crop(c0, c1, r0, r1, motion.shape, src,
                          min_keep, pad_px)


def _finalize_crop(
    c0: int, c1: int, r0: int, r1: int,
    shape: tuple[int, ...], src: SourceInfo,
    min_keep: float, pad_px: int,
    max_keep_frac: float = 0.90,
) -> tuple[int, int, int, int] | None:
    """Scale analysis-grid bounds to padded, clamped source pixels.

    Enforces the never-zoom-absurdly-far floor (min_keep per axis) and
    the minimum-gain rule: a crop keeping more than max_keep_frac of the
    frame isn't worth re-framing for (an honest no-crop beats a silly
    one).
    """
    ah, aw = shape
    sx, sy = src["w"] / aw, src["h"] / ah
    x0 = max(0, int(c0 * sx) - pad_px)
    x1 = min(src["w"], int((c1 + 1) * sx) + pad_px)
    y0 = max(0, int(r0 * sy) - pad_px)
    y1 = min(src["h"], int((r1 + 1) * sy) + pad_px)

    min_w, min_h = int(src["w"] * min_keep), int(src["h"] * min_keep)
    if x1 - x0 < min_w:
        cx = (x0 + x1) // 2
        x0 = max(0, min(cx - min_w // 2, src["w"] - min_w))
        x1 = x0 + min_w
    if y1 - y0 < min_h:
        cy = (y0 + y1) // 2
        y0 = max(0, min(cy - min_h // 2, src["h"] - min_h))
        y1 = y0 + min_h

    w = (x1 - x0) // 2 * 2
    h = (y1 - y0) // 2 * 2
    if w * h >= max_keep_frac * src["w"] * src["h"]:
        return None  # not enough gain to be worth a crop
    return x0, y0, w, h


def _run_with_gaps(good: np.ndarray, center: int,
                   max_gap: int) -> tuple[int, int]:
    """Contiguous run of True around `center`, tolerating small gaps.

    Window content isn't perfectly uniform (title bars, separators), so
    up to max_gap consecutive False entries are stepped over.
    """
    lo = hi = center
    gap, i = 0, center
    while i - 1 >= 0:
        i -= 1
        if good[i]:
            lo, gap = i, 0
        else:
            gap += 1
            if gap > max_gap:
                break
    gap, i = 0, center
    while i + 1 < len(good):
        i += 1
        if good[i]:
            hi, gap = i, 0
        else:
            gap += 1
            if gap > max_gap:
                break
    return lo, hi


def window_crop(
    frames: np.ndarray,
    src: SourceInfo,
    min_keep: float = 0.55,
    pad_px: int = 8,
    bg_tol: float = 10.0,
    frac: float = 0.65,
) -> tuple[int, int, int, int] | None:
    """Crop a screen recording to the window hosting the action.

    Terminal demos play out in one application window surrounded by
    desktop chrome (wallpaper strips, docks, taskbars). Motion finds the
    hot spot; the window around it is the contiguous block of rows and
    columns dominated by the window's own background color (sampled
    around the motion region). Wallpaper and bars read as foreign colors
    and fall away — including their static text, which a motion-only box
    would slice. Returns (x, y, w, h) in source pixels, or None when the
    window effectively fills the frame.
    """
    f = frames.astype(np.float32)
    motion = np.abs(np.diff(f, axis=0)).mean(axis=0)
    if float(motion.sum()) <= 0:
        return None
    row_m, col_m = motion.sum(axis=1), motion.sum(axis=0)
    mr0, mr1 = _mass_bounds(row_m, 0.8)
    mc0, mc1 = _mass_bounds(col_m, 0.8)

    mean_f = f.mean(axis=0)
    bg = float(np.median(mean_f[mr0:mr1 + 1, mc0:mc1 + 1]))
    is_bg = np.abs(mean_f - bg) < bg_tol

    cy = int((row_m * np.arange(len(row_m))).sum() / row_m.sum())
    cx = int((col_m * np.arange(len(col_m))).sum() / col_m.sum())
    ah, aw = mean_f.shape
    row_frac = is_bg.mean(axis=1)
    r0, r1 = _run_with_gaps(row_frac > frac, cy,
                            max_gap=max(1, ah // 24))
    c0, c1 = _run_with_gaps(is_bg.mean(axis=0) > frac, cx,
                            max_gap=max(1, aw // 24))

    # peel taskbars/title strips off the top and bottom: bars hug the
    # window edges and are busy (icons, clock, menus — high per-row
    # variance) or not clean background; never peel deeper than ~10%
    row_std = mean_f.std(axis=1)
    busy = 2.0 * float(np.median(row_std)) + 1.0
    shave = max(1, ah // 10)
    top_lim, bot_lim = r0 + shave, r1 - shave
    while r0 < r1 and r0 < top_lim and (row_frac[r0] < 0.85
                                        or row_std[r0] > busy):
        r0 += 1
    while r1 > r0 and r1 > bot_lim and (row_frac[r1] < 0.85
                                        or row_std[r1] > busy):
        r1 -= 1

    # desktop chrome is narrow — wanting to cut deeper than ~20% from a
    # side means we're about to slice another content window (browser +
    # terminal split-screen demos): keep that side whole instead
    max_cut_c, max_cut_r = int(aw * 0.2), int(ah * 0.2)
    if c0 > max_cut_c:
        c0 = 0
    if aw - 1 - c1 > max_cut_c:
        c1 = aw - 1
    if r0 > max_cut_r:
        r0 = 0
    if ah - 1 - r1 > max_cut_r:
        r1 = ah - 1

    # shaving even narrow desktop strips is worth it — relax the
    # minimum-gain rule relative to the motion crop
    return _finalize_crop(c0, c1, r0, r1, mean_f.shape, src,
                          min_keep, pad_px, max_keep_frac=0.97)


def zoom_crop(
    crop: tuple[int, int, int, int] | None,
    src: SourceInfo,
    zoom: float,
) -> tuple[int, int, int, int] | None:
    """Scale a crop box around its center by the creator's zoom dial.

    `zoom` > 1 frames tighter, < 1 pulls wider; 1.0 keeps the auto
    framing untouched. Applied on top of the auto crop (or the full
    frame when no auto crop was chosen), clamped to the frame. Returns
    None when the result is effectively the whole frame.
    """
    if abs(zoom - 1.0) < 1e-3:
        return crop
    x, y, w, h = crop if crop else (0, 0, src["w"], src["h"])
    cx, cy = x + w / 2, y + h / 2
    nw = min(src["w"], max(64, int(w / zoom)))
    nh = min(src["h"], max(64, int(h / zoom)))
    nx = int(min(max(0.0, cx - nw / 2), src["w"] - nw))
    ny = int(min(max(0.0, cy - nh / 2), src["h"] - nh))
    nw, nh = nw // 2 * 2, nh // 2 * 2
    if nw >= src["w"] - 2 and nh >= src["h"] - 2:
        return None
    return nx, ny, nw, nh


def assign_speeds(
    segs: list[Segment], target: float | None = None,
    max_action_speed: float = 1.0,
) -> tuple[list[SpeedSegment], float]:
    """Map tiers to speeds; optionally solve for a target duration.

    `max_action_speed` lets the action tier rise above real-time when
    the fast tiers alone can't reach the target — screen-recording
    content (typing, scrolling output) stays perfectly followable at a
    mild speed-up, unlike camera footage, which keeps 1.0x.
    Returns ([(start, end, speed)], estimated_output_duration).
    """
    speeds = {0: SPEED_DEAD, 1: SPEED_LAG, 2: SPEED_ACTION}

    def out_dur(sp: dict[int, float]) -> float:
        return sum((e - s) / sp[int(t)] for s, e, t in segs)

    if target:
        # binary-search a multiplier applied to the dead/lag speeds
        lo_m, hi_m = 0.4, 3.0
        for _ in range(40):
            m = (lo_m + hi_m) / 2
            sp = {0: min(MAX_SPEED, max(1.0, SPEED_DEAD * m)),
                  1: min(MAX_SPEED, max(1.0, SPEED_LAG * m)),
                  2: min(max_action_speed,
                         max(1.0, SPEED_ACTION * m))}
            if out_dur(sp) > target:
                lo_m = m
            else:
                hi_m = m
        speeds = sp
    out = [(s, e, speeds[int(t)]) for s, e, t in segs]
    return out, out_dur(speeds)


# Recording-tool edges: laptop screen recordings (OBS & friends) tend to
# open on the recorder UI and close on reaching for the stop button, so
# landscape sources lose a harder head/tail than phone clips.
OBS_HEAD = 1.5
OBS_TAIL = 3.0


def edit_window(
    duration: float, landscape: bool,
    trim_start: float = 0.0, trim_end: float = 0.0,
) -> tuple[float, float]:
    """Usable (head, tail) window of the source in seconds.

    Short clips are kept whole. Longer ones always lose the last beat
    (the stop-the-recording shuffle); landscape screen recordings also
    lose a head/tail slice where the capture tool's own UI shows up.

    `trim_start` / `trim_end` are the creator's explicit cuts (seconds off
    the raw head/tail). They STACK on the automatic window — whichever
    removes more wins — so "cut the first 3s" still works when the auto
    trim already took 1.5s. A minimum 1s window is always kept.
    """
    if duration <= 20.0:
        head, tail = 0.0, duration
    elif landscape:
        head, tail = OBS_HEAD, duration - OBS_TAIL
    else:
        head, tail = 0.0, duration - 2.0
    head = max(head, max(0.0, trim_start))
    tail = min(tail, duration - max(0.0, trim_end))
    head = min(head, max(0.0, tail - 1.0))  # never cross; keep ≥1s
    return head, tail


# TikTok's main ranking signal is completion rate, so shorter wins —
# ~25-40s is the sweet spot for screen/tutorial content. Aim for the
# low end, but never compress real-time action to get there.
AUTO_SWEET = 30.0  # output length to aim for when compressing
AUTO_MAX = 35.0    # natural pacing up to this long is left alone
# screen-recording action (typing, scrolling output) stays followable
# at a mild speed-up; camera action keeps strict real time
SCREEN_ACTION_MAX = 1.5


def auto_target(runs: list[Segment],
                max_action_speed: float = 1.0) -> float | None:
    """Pick a TikTok-friendly output length, or None to keep base speeds.

    If the natural pacing (base tier speeds) already lands within
    AUTO_MAX, no solving is needed. Otherwise compress toward AUTO_SWEET
    — floored by the action time at `max_action_speed` (1.0 for camera
    footage: real action is never sped up; SCREEN_ACTION_MAX for screen
    recordings, where a mild speed-up stays followable).
    """
    _, natural = assign_speeds(runs)
    if natural <= AUTO_MAX:
        return None
    action = sum(e - s for s, e, t in runs if int(t) == 2)
    return max(AUTO_SWEET, action * 1.05 / max_action_speed)


MIN_SEG_SRC = 0.3  # never align a segment below this many source-seconds


def beat_align(
    segs: list[SpeedSegment], bpm: float, duration: float
) -> list[SpeedSegment]:
    """Nudge cut points so they land on the music's beat grid.

    The synthesized track runs at a fixed bpm with a beat at t=0, so the
    beats sit at exact multiples of 60/bpm in *output* time — no beat
    detection needed. Each segment's source end is nudged (and the next
    segment's start follows, when contiguous) so the cut falls on the
    nearest beat; the final cut snaps to a beat the source can still
    reach, so the video ends on one too. Boundaries that would squeeze a
    segment under MIN_SEG_SRC source-seconds are left where they are.
    """
    beat = 60.0 / bpm
    out: list[SpeedSegment] = []
    t_out = 0.0  # output time at the last aligned boundary
    i = 0
    while i < len(segs):
        s, e, v = segs[i]
        t_cut = t_out + (e - s) / v
        # nearest beat, but never one that empties this segment
        target = max(round(t_cut / beat), 1) * beat
        if i == len(segs) - 1:
            # final cut: the source must still reach it
            room = duration - s
            while (target - t_out) * v > room and target - beat > t_out:
                target -= beat
        new_e = s + (target - t_out) * v
        next_contig = (i + 1 < len(segs) and segs[i + 1][0] == segs[i][1])
        if new_e - s < MIN_SEG_SRC or new_e > duration or (
                next_contig and segs[i + 1][1] - new_e < MIN_SEG_SRC):
            new_e, target = e, t_cut  # can't move this cut — leave it
        out.append((s, new_e, v))
        if next_contig:
            nxt = segs[i + 1]
            segs = [*segs[:i + 1], (new_e, nxt[1], nxt[2]),
                    *segs[i + 2:]]
        t_out = target
        i += 1
    return out
