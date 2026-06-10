"""Shared type aliases and TypedDicts for the pipeline."""

from typing import TypedDict


class SourceInfo(TypedDict, total=False):
    """Probed properties of a source video (see analysis.probe)."""

    w: int
    h: int
    duration: float
    fps: float
    audio: bool
    transfer: str    # color transfer (e.g. "bt709", "arib-std-b67")
    primaries: str   # color primaries (e.g. "bt709", "bt2020")


class Layout(TypedDict):
    """Video rectangle + caption position on the 1080x1920 canvas."""

    vw: int
    vh: int
    vx: int
    vy: int
    cap_x: int
    cap_y: int


# A timeline run: [start_sec, end_sec, tier] where tier is 0/1/2.
# Stored as a list because to_segments mutates the boundaries in place.
Segment = list[float]

# A render instruction: (start_sec, end_sec, speed_factor).
SpeedSegment = tuple[float, float, float]
