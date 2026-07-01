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


class CaptionSpec(TypedDict):
    """One time-ranged caption for dynamic (changing) captions.

    `png` is a pre-rendered pill; `x`/`y` its top-left on the 1080x1920
    canvas; `start`/`end` the OUTPUT-second window it is visible. Static
    mode uses a single caption baked for the whole clip and does not go
    through this; dynamic mode passes a list, one visible at a time.
    """

    png: str
    x: int
    y: int
    start: float
    end: float


class HookCard(TypedDict):
    """Animated cold-open text card overlaid on the opening (vertical only).

    `w`/`h` are the rendered PNG size at full (1.0x) scale; `y` is the
    top-of-frame overlay position; `pushin` also eases the footage in
    under the card while it is visible.
    """

    w: int
    h: int
    y: int
    pushin: bool


# A timeline run: [start_sec, end_sec, tier] where tier is 0/1/2.
# Stored as a list because to_segments mutates the boundaries in place.
Segment = list[float]

# A render instruction: (start_sec, end_sec, speed_factor).
SpeedSegment = tuple[float, float, float]
