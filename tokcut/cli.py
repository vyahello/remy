"""Command-line entry point: python -m tokcut ..."""

import argparse
import os
import shutil
import sys
import tempfile
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
from .caption import check_caption, make_caption
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
    runs = trim_dead_ends(
        to_segments(classify(scores), duration=src["duration"]))

    hook_win = pick_hook(scores, src["duration"]) if hook else None
    solve_target = (target - (hook_win[1] - hook_win[0])
                    if target and hook_win else target)
    segs, est = assign_speeds(runs, solve_target)
    if hook_win:
        segs = [(hook_win[0], hook_win[1], 1.0)] + segs
        est += hook_win[1] - hook_win[0]
    return src, segs, est, frames, hook_win


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = args.output or os.path.splitext(args.input)[0] + "_tokcut.mp4"

    for warning in check_caption(args.caption):
        print(f"⚠ caption check: {warning}", file=sys.stderr)

    src, segs, est, frames, hook_win = plan(
        args.input, args.target, args.hook)
    print(f"source: {src['w']}x{src['h']}  {src['duration']:.1f}s "
          f"@ {src['fps']:.0f}fps  "
          f"({src.get('transfer') or 'unknown'} transfer)")

    crop = content_crop(frames, src) if args.crop else None
    if crop:
        print(f"crop: zoom into {crop[2]}x{crop[3]} at ({crop[0]},{crop[1]})")

    print(f"edit plan ({len(segs)} segments, ~{est:.1f}s output):")
    for i, (s, e, sp) in enumerate(segs):
        if hook_win and i == 0:
            tag = "HOOK   1.0x (cold open)"
        elif round(sp, 2) == 1.0:
            tag = "ACTION 1.0x"
        else:
            tag = f"FAST  {sp:.2f}x"
        print(f"  {s:7.2f} - {e:7.2f}  {tag}")
    if args.dry_run:
        return 0

    tmp = tempfile.mkdtemp(prefix="tokcut_")
    try:
        cap_png = os.path.join(tmp, "caption.png")
        cap_size = make_caption(args.caption, cap_png)

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
               if args.caption_pos == "auto" else None)
        lay = compute_layout(lay_src, cap_size, args.caption_pos, sal)
        print(f"caption at y={lay['cap_y']} ({args.caption_pos})")

        music_path: str | None = None
        if args.music == "__auto__":
            music_path = os.path.join(tmp, "music.wav")
            write_wav(generate(max(est, 1.0) + 2, bpm=args.music_bpm,
                               style=args.music_style), music_path)
            print(f"music: synthesized {args.music_style} @ "
                  f"{args.music_bpm}bpm")
        elif args.music:
            music_path = args.music
            print(f"music: {music_path}")

        if not music_path:
            print("audio: original ambient (--keep-audio)" if args.keep_audio
                  else "audio: muted (add a TikTok sound in-app)")

        render(args.input, segs, cap_png, src, lay, out,
               args.crf, args.preset, music_path, args.keep_audio,
               crop=crop)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"done: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
