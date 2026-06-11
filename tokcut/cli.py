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
    assign_speeds,
    classify,
    content_crop,
    motion_scores,
    pick_hook,
    probe,
    saliency_map,
    smooth,
    to_segments,
    trim_dead_ends,
)
from .caption import DEFAULT_STYLE, STYLES, check_caption, make_caption
from .layout import compute_layout
from .music import generate, write_wav
from .render import render
from .types import SourceInfo, SpeedSegment


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="tokcut", description="Auto-editor for vertical TikTok clips")
    ap.add_argument("input")
    ap.add_argument("-c", "--caption", required=True,
                    help="Persistent caption text (emoji supported)")
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--target", type=float, default=None,
                    help="Target output duration in seconds")
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
    ap.add_argument("--music-bpm", type=int, default=84)
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--preset", default="medium")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the edit decision list and exit")
    ap.add_argument("--version", action="version",
                    version=f"tokcut {__version__}")
    return ap


def plan(
    input_path: str, target: float | None, hook: bool = True
) -> tuple[SourceInfo, list[SpeedSegment], float, np.ndarray,
           tuple[float, float] | None]:
    """Analysis + edit decisions.

    Returns (src, segments, est, frames, hook_window). When a hook is
    chosen, the first segment is a 1x cold open of the video's best beat;
    leading/trailing dead footage is hard-trimmed either way.
    """
    src = probe(input_path)
    raw_scores, frames = motion_scores(input_path, src)
    scores = smooth(raw_scores)
    # the last beat of a recording is usually the stop-the-recording
    # shuffle (alt-tab, reaching for the hotkey) — never include it
    dur = src["duration"]
    dur_eff = dur - 2.0 if dur > 20.0 else dur
    runs = trim_dead_ends(
        to_segments(classify(scores), duration=dur_eff))

    hook_win = pick_hook(scores, dur_eff) if hook else None
    solve_target = (target - (hook_win[1] - hook_win[0])
                    if target and hook_win else target)
    segs, est = assign_speeds(runs, solve_target)
    if hook_win:
        segs = [(hook_win[0], hook_win[1], 1.0)] + segs
        est += hook_win[1] - hook_win[0]
    return src, segs, est, frames, hook_win


def edit(
    input_path: str,
    caption: str,
    *,
    output: str | None = None,
    target: float | None = None,
    style: str = DEFAULT_STYLE,
    caption_pos: str = "auto",
    hook: bool = True,
    crop_enabled: bool = True,
    keep_audio: bool = False,
    music: str | None = None,
    music_style: str = "synthwave",
    music_bpm: int = 84,
    crf: int = 18,
    preset: str = "medium",
    dry_run: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> str:
    """Full edit pipeline: analyze → decide → render. Returns output path.

    The reusable core behind both the CLI and the Telegram bot.
    `on_progress` receives short human-readable status lines.
    """
    notify = on_progress or (lambda _line: None)
    out = output or os.path.splitext(input_path)[0] + "_tokcut.mp4"

    src, segs, est, frames, hook_win = plan(input_path, target, hook)
    notify(f"source: {src['w']}x{src['h']}  {src['duration']:.1f}s "
           f"@ {src['fps']:.0f}fps  "
           f"({src.get('transfer') or 'unknown'} transfer)")

    crop = content_crop(frames, src) if crop_enabled else None
    if crop:
        notify(f"crop: zoom into {crop[2]}x{crop[3]} "
               f"at ({crop[0]},{crop[1]})")

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
        sal = saliency_map(lay_frames) if caption_pos == "auto" else None
        lay = compute_layout(lay_src, cap_size, caption_pos, sal)
        notify(f"caption at y={lay['cap_y']} ({caption_pos})")

        music_path: str | None = None
        if music == "__auto__":
            music_path = os.path.join(tmp, "music.wav")
            write_wav(generate(max(est, 1.0) + 2, bpm=music_bpm,
                               style=music_style), music_path)
            notify(f"music: synthesized {music_style} @ {music_bpm}bpm")
        elif music:
            music_path = music
            notify(f"music: {music_path}")

        if not music_path:
            notify("audio: original ambient" if keep_audio
                   else "audio: muted (add a TikTok sound in-app)")

        notify("rendering…")
        render(input_path, segs, cap_png, src, lay, out,
               crf, preset, music_path, keep_audio, crop=crop)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    for warning in check_caption(args.caption):
        print(f"⚠ caption check: {warning}", file=sys.stderr)

    out = edit(
        args.input,
        args.caption,
        output=args.output,
        target=args.target,
        style=args.style,
        caption_pos=args.caption_pos,
        hook=args.hook,
        crop_enabled=args.crop,
        keep_audio=args.keep_audio,
        music=args.music,
        music_style=args.music_style,
        music_bpm=args.music_bpm,
        crf=args.crf,
        preset=args.preset,
        dry_run=args.dry_run,
        on_progress=print,
    )
    if not args.dry_run:
        print(f"done: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
