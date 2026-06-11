"""Command-line entry point: python -m tokcut ..."""

import argparse
import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from typing import cast

import numpy as np

from . import __version__
from .analysis import (
    SCREEN_ACTION_MAX,
    assign_speeds,
    auto_target,
    beat_align,
    classify,
    content_crop,
    edit_window,
    motion_scores,
    pick_hook,
    probe,
    saliency_map,
    smooth,
    to_segments,
    trim_dead_ends,
    window_crop,
)
from .caption import DEFAULT_STYLE, STYLES, check_caption, make_caption
from .layout import compute_layout
from .music import STYLE_BPM, generate, write_wav
from .render import look_filter, render
from .types import Layout, SourceInfo, SpeedSegment


def is_landscape(src: SourceInfo) -> bool:
    """Landscape sources stay native: no vertical canvas, no caption."""
    return src["w"] > src["h"]


def _parse_target(value: str) -> float | str | None:
    """--target accepts seconds, 'auto', or 'none' (base speeds)."""
    low = value.strip().lower()
    if low == "auto":
        return "auto"
    if low in ("none", "full"):
        return None
    return float(value)  # ValueError -> argparse usage error


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="tokcut", description="Auto-editor for vertical TikTok clips")
    ap.add_argument("input")
    ap.add_argument("-c", "--caption", default="",
                    help="Persistent caption text (emoji supported). "
                         "Required for vertical sources; landscape "
                         "sources never get a caption (overlay your own)")
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--target", type=_parse_target, default="auto",
                    help="Output length: seconds, 'auto' (default — solve "
                         "a TikTok-friendly length from the content), or "
                         "'none' (keep base tier speeds)")
    ap.add_argument("--style", choices=sorted(STYLES),
                    default=DEFAULT_STYLE,
                    help="caption style preset (default: %(default)s)")
    ap.add_argument("--caption-pos", choices=["auto", "top", "bottom"],
                    default="auto",
                    help="auto = place over the calmest region (default)")
    ap.add_argument("--hook", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="Cold-open on the most action-packed beat of the "
                         "video before the chronological cut (default on)")
    ap.add_argument("--look", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="finishing grade: contrast/saturation pop, "
                         "crisper text on screen recordings (default on)")
    ap.add_argument("--crop", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="Auto-zoom into the active region, dropping "
                         "static margins (default on)")
    ap.add_argument("--keep-audio", action="store_true",
                    help="Keep the original ambient audio. By default the "
                         "export is muted so you add a TikTok sound in-app.")
    ap.add_argument("--music", nargs="?", const="__auto__", default=None,
                    help="Bake in music (implies sound): bare flag "
                         "synthesizes a track; or pass a path to your "
                         "own audio file. For off-platform posts.")
    ap.add_argument("--music-style", choices=["synthwave", "phonk"],
                    default="synthwave")
    ap.add_argument("--music-bpm", type=int, default=None,
                    help="Tempo of the synthesized track (default: the "
                         "style's own — synthwave 84, phonk 132)")
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--preset", default="medium")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the edit decision list and exit")
    ap.add_argument("--version", action="version",
                    version=f"tokcut {__version__}")
    return ap


def plan(
    input_path: str, target: float | str | None, hook: bool = True
) -> tuple[SourceInfo, list[SpeedSegment], float, np.ndarray,
           tuple[float, float] | None]:
    """Analysis + edit decisions.

    `target` is seconds, None (base tier speeds), or "auto" — solve a
    TikTok-friendly length from the content (completion-rate sweet spot,
    floored by the real-time action). Returns (src, segments, est,
    frames, hook_window). When a hook is chosen, the first segment is a
    1x cold open of the video's best beat; leading/trailing dead footage
    is hard-trimmed either way.
    """
    src = probe(input_path)
    raw_scores, frames = motion_scores(input_path, src)
    scores = smooth(raw_scores)
    # recording edges are never content: the tail is the stop-the-
    # recording shuffle, and landscape screen recordings open/close on
    # the capture tool's UI (OBS & friends) — hard-trim both
    dur = src["duration"]
    head, dur_eff = edit_window(dur, is_landscape(src))
    runs = trim_dead_ends(
        to_segments(classify(scores), duration=dur_eff))
    if head:
        runs = [[max(s, head), e, t] for s, e, t in runs if e > head]
    # screen-recording action stays followable at a mild speed-up;
    # camera action is never accelerated
    max_action = SCREEN_ACTION_MAX if is_landscape(src) else 1.0
    if target == "auto":
        target = auto_target(runs, max_action)
    target = cast("float | None", target)

    hook_win = pick_hook(scores, dur_eff) if hook else None
    solve_target = (target - (hook_win[1] - hook_win[0])
                    if target and hook_win else target)
    segs, est = assign_speeds(runs, solve_target, max_action)
    if hook_win:
        segs = [(hook_win[0], hook_win[1], 1.0)] + segs
        est += hook_win[1] - hook_win[0]
    return src, segs, est, frames, hook_win


def edit(
    input_path: str,
    caption: str,
    *,
    output: str | None = None,
    target: float | str | None = "auto",
    style: str = DEFAULT_STYLE,
    caption_pos: str = "auto",
    hook: bool = True,
    crop_enabled: bool = True,
    look_enabled: bool = True,
    keep_audio: bool = False,
    music: str | None = None,
    music_style: str = "synthwave",
    music_bpm: int | None = None,
    crf: int = 18,
    preset: str = "medium",
    dry_run: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> str:
    """Full edit pipeline: analyze → decide → render. Returns output path.

    The reusable core behind both the CLI and the Telegram bot.
    `on_progress` receives short human-readable status lines.

    Landscape sources keep their native resolution — same cuts, speeds,
    hook, crop and music, but no vertical canvas and **no caption** (a
    landscape video in TikTok can't go fullscreen behind a baked caption;
    the creator overlays their own).
    """
    notify = on_progress or (lambda _line: None)
    out = output or os.path.splitext(input_path)[0] + "_tokcut.mp4"

    src, segs, est, frames, hook_win = plan(input_path, target, hook)
    landscape = is_landscape(src)
    if not landscape and not caption.strip():
        raise ValueError("a caption is required for vertical output (-c)")
    notify(f"source: {src['w']}x{src['h']}  {src['duration']:.1f}s "
           f"@ {src['fps']:.0f}fps  "
           f"({src.get('transfer') or 'unknown'} transfer)")

    # landscape = screen-recording content: crop to the window hosting
    # the action (desktop chrome falls away, text never sliced); camera
    # footage keeps the plain motion box
    crop = None
    if crop_enabled:
        crop = (window_crop(frames, src) if landscape
                else content_crop(frames, src))
    if crop:
        notify(f"crop: zoom into {crop[2]}x{crop[3]} "
               f"at ({crop[0]},{crop[1]})")

    bpm = music_bpm or STYLE_BPM.get(music_style, 84)
    if music == "__auto__":
        # the synthesized track has a known, exact beat grid — snap the
        # cuts onto it so every segment change lands on a beat
        segs = beat_align(segs, bpm, src["duration"])
        est = sum((e - s) / v for s, e, v in segs)
        notify(f"beat-align: cuts snapped to the {bpm}bpm grid")

    lines = [f"edit plan ({len(segs)} segments, ~{est:.1f}s output):"]
    for i, (s, e, sp) in enumerate(segs):
        if hook_win and i == 0:
            tag = "HOOK   1.0x (cold open)"
        elif round(sp, 2) == 1.0:
            tag = "ACTION 1.0x"
        else:
            tag = f"FAST  {sp:.2f}x"
        lines.append(f"  {s:7.2f} - {e:7.2f}  {tag}")
    notify("\n".join(lines))
    if dry_run:
        return out

    tmp = tempfile.mkdtemp(prefix="tokcut_")
    try:
        cap_png: str | None = None
        lay: Layout | None = None
        if landscape:
            notify("landscape source: native resolution kept, no caption "
                   "(overlay your own)")
        else:
            cap_png = os.path.join(tmp, "caption.png")
            cap_size = make_caption(caption, cap_png, style=style)

            # layout works on post-crop dimensions; the caption-placement
            # saliency map must describe the same (cropped) picture
            lay_src = src
            lay_frames = frames
            if crop:
                ah, aw = frames.shape[1], frames.shape[2]
                ax0 = crop[0] * aw // src["w"]
                ay0 = crop[1] * ah // src["h"]
                ax1 = max(ax0 + 2, (crop[0] + crop[2]) * aw // src["w"])
                ay1 = max(ay0 + 2, (crop[1] + crop[3]) * ah // src["h"])
                lay_frames = frames[:, ay0:ay1, ax0:ax1]
                lay_src = cast(SourceInfo, dict(src, w=crop[2], h=crop[3]))
            sal = (saliency_map(lay_frames)
                   if caption_pos == "auto" else None)
            lay = compute_layout(lay_src, cap_size, caption_pos, sal)
            notify(f"caption at y={lay['cap_y']} ({caption_pos})")

        music_path: str | None = None
        if music == "__auto__":
            music_path = os.path.join(tmp, "music.wav")
            write_wav(generate(max(est, 1.0) + 2, bpm=bpm,
                               style=music_style), music_path)
            notify(f"music: synthesized {music_style} @ {bpm}bpm")
        elif music:
            music_path = music
            notify(f"music: {music_path}")

        if not music_path:
            notify("audio: original ambient" if keep_audio
                   else "audio: muted (add a TikTok sound in-app)")

        look = look_filter(src, screen=landscape) if look_enabled else ""
        if look:
            notify("look: finishing grade applied")

        notify("rendering…")
        render(input_path, segs, cap_png, src, lay, out,
               crf, preset, music_path, keep_audio, crop=crop, look=look)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.caption:
        for warning in check_caption(args.caption):
            print(f"⚠ caption check: {warning}", file=sys.stderr)

    try:
        out = edit(
            args.input,
            args.caption,
            output=args.output,
            target=args.target,
            style=args.style,
            caption_pos=args.caption_pos,
            hook=args.hook,
            crop_enabled=args.crop,
            look_enabled=args.look,
            keep_audio=args.keep_audio,
            music=args.music,
            music_style=args.music_style,
            music_bpm=args.music_bpm,
            crf=args.crf,
            preset=args.preset,
            dry_run=args.dry_run,
            on_progress=print,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not args.dry_run:
        print(f"done: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
