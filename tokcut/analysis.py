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
    """Where the action lives, averaged over the video.

    Brightness dominates: in dark-room desk footage the content the
    viewer must see (screen, device display) is what glows.
    """
    f = frames.astype(np.float32)
    motion = np.abs(np.diff(f, axis=0)).mean(axis=0)
    mean_frame = f.mean(axis=0)
    gy, gx = np.gradient(mean_frame)
    edges = np.hypot(gx, gy)

    def norm(a: np.ndarray) -> np.ndarray:
        m = np.percentile(a, 98)
        return np.clip(a / m, 0, 1) if m > 0 else a

    return 0.3 * norm(motion) + 0.2 * norm(edges) + 0.5 * norm(mean_frame)


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
    if duration is not None and merged:
        # sampling rounds the tail up past the real end of the file
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


def content_crop(
    frames: np.ndarray,
    src: SourceInfo,
    min_keep: float = 0.55,
    keep_mass: float = 0.96,
    pad_px: int = 16,
) -> tuple[int, int, int, int] | None:
    """Zoom into where the action happens.

    Screen recordings and wide shots waste pixels on static margins
    (desktop wallpaper, window chrome); on a 1080x1920 canvas that makes
    the content small and unreadable. This finds the smallest box holding
    ~keep_mass of the video's total motion energy per axis and returns an
    (x, y, w, h) crop in source pixels — or None when cropping wouldn't
    gain at least ~10% (an honest no-crop beats a silly one).
    """
    f = frames.astype(np.float32)
    motion = np.abs(np.diff(f, axis=0)).mean(axis=0)
    if float(motion.sum()) <= 0:
        return None
    r0, r1 = _mass_bounds(motion.sum(axis=1), keep_mass)
    c0, c1 = _mass_bounds(motion.sum(axis=0), keep_mass)

    ah, aw = motion.shape
    sx, sy = src["w"] / aw, src["h"] / ah
    x0 = max(0, int(c0 * sx) - pad_px)
    x1 = min(src["w"], int((c1 + 1) * sx) + pad_px)
    y0 = max(0, int(r0 * sy) - pad_px)
    y1 = min(src["h"], int((r1 + 1) * sy) + pad_px)

    # never zoom in absurdly far — keep at least min_keep of each axis
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
    if w * h >= 0.90 * src["w"] * src["h"]:
        return None  # not enough gain to be worth a crop
    return x0, y0, w, h


def assign_speeds(
    segs: list[Segment], target: float | None = None
) -> tuple[list[SpeedSegment], float]:
    """Map tiers to speeds; optionally solve for a target duration.

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
                  2: SPEED_ACTION}
            if out_dur(sp) > target:
                lo_m = m
            else:
                hi_m = m
        speeds = sp
    out = [(s, e, speeds[int(t)]) for s, e, t in segs]
    return out, out_dur(speeds)
