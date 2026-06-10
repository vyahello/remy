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
    return {"w": w, "h": h, "duration": dur, "fps": fps, "audio": has_audio}


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
    tiers = np.full(len(scores), 1, dtype=int)
    tiers[scores < lo] = 0
    tiers[scores > hi] = 2
    return tiers


def to_segments(
    tiers: np.ndarray, sample_fps: int = SAMPLE_FPS
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
    return merged


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
