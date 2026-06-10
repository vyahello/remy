"""Command-line entry point: python -m tokcut ..."""

import argparse
import os
import shutil
import sys
import tempfile

from . import __version__
from .analysis import (
    assign_speeds,
    classify,
    motion_scores,
    probe,
    saliency_map,
    smooth,
    to_segments,
)
from .caption import check_caption, make_caption
from .layout import compute_layout
from .music import generate, write_wav
from .render import render


def build_parser():
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


def plan(input_path, caption, target):
    """Run analysis only; return (src, segments, est_duration, frames)."""
    src = probe(input_path)
    raw_scores, frames = motion_scores(input_path, src)
    segs, est = assign_speeds(
        to_segments(classify(smooth(raw_scores))), target)
    return src, segs, est, frames


def main(argv=None):
    args = build_parser().parse_args(argv)
    out = args.output or os.path.splitext(args.input)[0] + "_tokcut.mp4"

    for warning in check_caption(args.caption):
        print(f"⚠ caption check: {warning}", file=sys.stderr)

    src, segs, est, frames = plan(args.input, args.caption, args.target)
    print(f"source: {src['w']}x{src['h']}  {src['duration']:.1f}s "
          f"@ {src['fps']:.0f}fps")
    print(f"edit plan ({len(segs)} segments, ~{est:.1f}s output):")
    for s, e, sp in segs:
        tag = "ACTION 1.0x" if round(sp, 2) == 1.0 else f"FAST  {sp:.2f}x"
        print(f"  {s:7.2f} - {e:7.2f}  {tag}")
    if args.dry_run:
        return 0

    tmp = tempfile.mkdtemp(prefix="tokcut_")
    try:
        cap_png = os.path.join(tmp, "caption.png")
        cap_size = make_caption(args.caption, cap_png)
        sal = saliency_map(frames) if args.caption_pos == "auto" else None
        lay = compute_layout(src, cap_size, args.caption_pos, sal)
        print(f"caption at y={lay['cap_y']} ({args.caption_pos})")

        music_path = None
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
               args.crf, args.preset, music_path, args.keep_audio)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"done: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
