"""Command-line entry point: python -m remy ..."""

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
    ACTION_FIT_MAX,
    AUTO_HARD_MAX,
    BRIGHT_TAIL_RATIO,
    CAMERA_FAST_MAX,
    MAX_SPEED,
    SAMPLE_FPS,
    assign_speeds,
    auto_target,
    beat_align,
    caption_windows,
    classify,
    content_crop,
    cut_spans,
    edit_window,
    motion_scores,
    pick_hook,
    probe,
    saliency_map,
    smooth,
    to_segments,
    trim_dead_ends,
    window_crop,
    zoom_crop,
)
from .caption import (
    DEFAULT_STYLE,
    STYLES,
    check_caption,
    make_caption,
    make_hook_card,
)
from .layout import OUT_W, compute_layout, hook_card_y
from .music import STYLE_BPM, generate, write_wav
from .render import HOOK_CARD_DUR, look_filter, render
from .types import CaptionSpec, HookCard, Layout, SourceInfo, SpeedSegment


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
        prog="remy", description="Auto-editor for vertical TikTok clips")
    ap.add_argument("input")
    ap.add_argument("-c", "--caption", default="",
                    help="Persistent caption text (emoji supported). "
                         "Optional — omit it for a clean vertical export "
                         "with no baked caption; landscape sources never "
                         "get one (overlay your own)")
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
    ap.add_argument("--caption-mode", choices=["static", "dynamic"],
                    default="static",
                    help="static = one caption; dynamic = changing step "
                         "labels (needs the claude CLI to label the steps)")
    ap.add_argument("--hook", action=argparse.BooleanOptionalAction,
                    default=False,
                    help="Cold-open on the most action-packed beat of the "
                         "video before the chronological cut (default off)")
    ap.add_argument("--look", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="finishing grade: contrast/saturation pop, "
                         "crisper text on screen recordings (default on)")
    ap.add_argument("--hook-card", action=argparse.BooleanOptionalAction,
                    default=False,
                    help="Animated text card over the opening 1.6s "
                         "(vertical only, default OFF). Reuses the caption "
                         "text unless --hook-card-text is given.")
    ap.add_argument("--hook-card-text", default=None,
                    help="Override the hook card text (defaults to the "
                         "caption)")
    ap.add_argument("--hook-card-pushin",
                    action=argparse.BooleanOptionalAction, default=False,
                    help="Also ease the footage in under the card "
                         "(default off)")
    ap.add_argument("--crop", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="Auto-zoom into the active region, dropping "
                         "static margins (default on)")
    ap.add_argument("--zoom", type=float, default=1.0,
                    help="framing dial on top of the auto-zoom: >1 "
                         "tighter, <1 wider (default 1.0 = auto)")
    ap.add_argument("--trim-start", type=float, default=0.0, metavar="SEC",
                    help="hard-cut this many seconds off the source head "
                         "(e.g. a recorder-UI intro); stacks on auto-trim")
    ap.add_argument("--trim-end", type=float, default=0.0, metavar="SEC",
                    help="hard-cut this many seconds off the source tail "
                         "(e.g. a redundant outro); stacks on auto-trim")
    ap.add_argument("--cut-mistakes", action="store_true",
                    help="have Claude watch the clip and delete mistyped "
                         "commands / terminal errors / fumbles before the "
                         "retype (needs the claude CLI; best-effort)")
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
    ap.add_argument("--music-seed", type=int, default=0,
                    help="Composition seed — change it for a different "
                         "track in the same style (default: 0)")
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--preset", default="medium")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the edit decision list and exit")
    ap.add_argument("--version", action="version",
                    version=f"remy {__version__}")
    return ap


def plan(
    input_path: str, target: float | str | None, hook: bool = True,
    trim_start: float = 0.0, trim_end: float = 0.0,
    cut_spans_src: list[tuple[float, float]] | None = None,
) -> tuple[SourceInfo, list[SpeedSegment], float, np.ndarray,
           tuple[float, float] | None]:
    """Analysis + edit decisions.

    `target` is seconds, None (base tier speeds), or "auto" — solve a
    TikTok-friendly length from the content (completion-rate sweet spot,
    floored by the real-time action). `cut_spans_src` are source-second
    spans to delete outright (e.g. a mistyped command and the error it
    threw — see judge.detect_mistakes). Returns (src, segments, est,
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
    head, dur_eff = edit_window(dur, is_landscape(src), trim_start, trim_end)
    # mean luma of a segment from the analysis frames (sampled at SAMPLE_FPS
    # over the full source) — lets trim_dead_ends keep a bright payoff tail
    # (a results screen / reveal) instead of mistaking it for dead time.
    afps = frames.shape[0] / dur if dur > 0 else SAMPLE_FPS

    def seg_brightness(seg: list[float]) -> float:
        a = int(seg[0] * afps)
        b = max(a + 1, int(seg[1] * afps))
        return float(frames[a:b].mean())

    # edit_window blindly drops a trailing beat as the stop-recording
    # shuffle. When that tail is actually bright content — a held result, a
    # highlighted answer — give it back; only a dark put-down / empty-prompt
    # tail should auto-trim (a screen recorder already self-trims its tail,
    # so a second blind cut here would eat the payoff). Explicit --trim-end
    # is always honoured.
    if not is_landscape(src) and trim_end == 0.0 and 0.0 < dur_eff < dur:
        dark = float(np.percentile(frames.mean(axis=(1, 2)), 10))
        if seg_brightness([dur_eff, dur]) > dark * BRIGHT_TAIL_RATIO:
            dur_eff = max(dur_eff, dur - 0.3)

    runs = trim_dead_ends(
        to_segments(classify(scores), duration=dur_eff), seg_brightness)
    if head:
        runs = [[max(s, head), e, t] for s, e, t in runs if e > head]
    # drop the fumbles — a mistyped command, its error, the retype before
    # the good take — so the export is clean live coding (judge-detected
    # source spans; empty/None when there's nothing to cut)
    if cut_spans_src:
        runs = cut_spans(runs, cut_spans_src)
    # Live coding stays at real time: the typing IS the content, so the
    # action tier is never accelerated, screen recording or not. The
    # dead/lag fast-forward is still capped lower for camera footage (it
    # blurs past ~4x) than for screen content (legible at full MAX_SPEED).
    screen = is_landscape(src)
    max_action = 1.0
    max_fast = MAX_SPEED if screen else CAMERA_FAST_MAX
    if target == "auto":
        target = auto_target(runs, max_action)
    target = cast("float | None", target)
    # Keep a finished TikTok under ~2 min. Camera footage caps its fast
    # tiers at CAMERA_FAST_MAX because a phone-filmed desk blurs past ~4x —
    # but that cap can floor a long clip well over the ceiling. Escalate in
    # two stages, quality-preserving first:
    #   1) let the IDLE (dead/lag) stretches fast-forward up to MAX_SPEED
    #      (blurring the waiting is fine);
    #   2) only if that still overshoots, let the CODING speed up too — up
    #      to ACTION_FIT_MAX, aiming AT the ceiling so it's sped as little as
    #      needed rather than to the short auto target.
    if target and not screen:
        if max_fast < MAX_SPEED:
            _, est = assign_speeds(runs, target, max_action, max_fast)
            if est > AUTO_HARD_MAX:
                max_fast = MAX_SPEED
        _, est = assign_speeds(runs, target, max_action, max_fast)
        if est > AUTO_HARD_MAX:
            max_action = ACTION_FIT_MAX
            target = AUTO_HARD_MAX

    hook_win = pick_hook(scores, dur_eff) if hook else None
    solve_target = (target - (hook_win[1] - hook_win[0])
                    if target and hook_win else target)
    segs, est = assign_speeds(runs, solve_target, max_action, max_fast)
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
    caption_mode: str = "static",
    sections: list[tuple[float, str]] | None = None,
    hook: bool = False,
    trim_start: float = 0.0,
    trim_end: float = 0.0,
    cut_spans_src: list[tuple[float, float]] | None = None,
    crop_enabled: bool = True,
    zoom: float = 1.0,
    look_enabled: bool = True,
    hook_card: bool = False,
    hook_card_text: str | None = None,
    hook_card_pushin: bool = False,
    keep_audio: bool = False,
    music: str | None = None,
    music_style: str = "synthwave",
    music_bpm: int | None = None,
    music_seed: int = 0,
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
    out = output or os.path.splitext(input_path)[0] + "_remy.mp4"

    src, segs, est, frames, hook_win = plan(
        input_path, target, hook, trim_start, trim_end, cut_spans_src)
    if trim_start or trim_end:
        notify(f"manual trim: −{trim_start:.1f}s head / −{trim_end:.1f}s tail")
    if cut_spans_src:
        cut_total = sum(b - a for a, b in cut_spans_src)
        notify(f"mistakes cut: {len(cut_spans_src)} span(s), "
               f"−{cut_total:.1f}s of fumbles/errors")
    landscape = is_landscape(src)
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
    # the creator's framing dial sits on top of the auto framing — it
    # applies even with the auto-crop off (a deliberate centered punch-in)
    crop = zoom_crop(crop, src, zoom)
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
    card_text = (hook_card_text or caption).strip()
    use_card = hook_card and not landscape and bool(card_text)
    if use_card:
        notify(f'hook card: "{card_text}" '
               f"(0.0–{HOOK_CARD_DUR}s, fade-in/out)")
    if dry_run:
        return out

    tmp = tempfile.mkdtemp(prefix="remy_")
    try:
        cap_png: str | None = None
        captions: list[CaptionSpec] | None = None
        lay: Layout | None = None
        if landscape:
            notify("landscape source: native resolution kept, no caption "
                   "(overlay your own)")
        else:
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
            windows = _dynamic_windows(
                caption_mode, sections, input_path, src["duration"], segs,
                hook_win) if caption.strip() else None
            if windows:
                sal = (saliency_map(lay_frames)
                       if caption_pos == "auto" else None)
                captions = []
                for i, (ws, we, label) in enumerate(windows):
                    png = os.path.join(tmp, f"capdyn{i}.png")
                    cw, ch = make_caption(label, png, style=style)
                    if lay is None:  # one layout for the whole run (same y)
                        lay = compute_layout(
                            lay_src, (cw, ch), caption_pos, sal)
                    captions.append({"png": png, "x": (OUT_W - cw) // 2,
                                     "y": lay["cap_y"], "start": ws,
                                     "end": we})
                notify(f"dynamic captions: {len(captions)} step labels "
                       f"at y={lay['cap_y']}")  # type: ignore[index]
            elif caption.strip():
                cap_png = os.path.join(tmp, "caption.png")
                cap_size = make_caption(caption, cap_png, style=style)
                sal = (saliency_map(lay_frames)
                       if caption_pos == "auto" else None)
                lay = compute_layout(lay_src, cap_size, caption_pos, sal)
                notify(f"caption at y={lay['cap_y']} ({caption_pos})")
            else:
                # clean vertical: center the video, bake no caption
                lay = compute_layout(lay_src, (0, 0), "top")
                notify("no caption — centered vertical export")

        hcard: HookCard | None = None
        hcard_png: str | None = None
        if use_card and lay is not None:
            hcard_png = os.path.join(tmp, "hookcard.png")
            cw, ch = make_hook_card(card_text, hcard_png, style=style)
            hcard = {"w": cw, "h": ch, "y": hook_card_y(),
                     "pushin": hook_card_pushin}
            notify(f"hook card: baked over the opening ({cw}x{ch})")

        music_path: str | None = None
        if music == "__auto__":
            music_path = os.path.join(tmp, "music.wav")
            write_wav(generate(max(est, 1.0) + 2, bpm=bpm,
                               style=music_style, seed=music_seed),
                      music_path)
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
               crf, preset, music_path, keep_audio, crop=crop, look=look,
               hook_card=hcard, hook_card_png=hcard_png, captions=captions)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def _dynamic_windows(
    caption_mode: str,
    sections: list[tuple[float, str]] | None,
    input_path: str,
    duration: float,
    segs: list[SpeedSegment],
    hook_win: tuple[float, float] | None,
) -> list[tuple[float, float, str]] | None:
    """Output-time (start, end, label) windows for dynamic captions, or None.

    Returns None (→ the caller falls back to the single static caption) when
    the mode is static or no section labels are available. The section
    markers are mapped to output time through the BODY segments; a cold-open
    hook (prepended, out of source order) is handled by mapping without it
    and shifting every window past the teaser, which the first label covers.
    """
    if caption_mode != "dynamic":
        return None
    secs = sections
    if secs is None:
        secs = _detect_sections_cli(input_path, duration)
    if not secs:
        return None
    body = segs[1:] if hook_win else segs
    windows = caption_windows(body, secs)
    if not windows:
        return None
    if hook_win:
        hl = hook_win[1] - hook_win[0]
        windows = [(s + hl, e + hl, lbl) for s, e, lbl in windows]
        windows[0] = (0.0, windows[0][1], windows[0][2])  # cover the teaser
    return windows


def _detect_sections_cli(
    input_path: str, duration: float
) -> list[tuple[float, str]] | None:
    """Run the Claude section pass for dynamic captions (CLI best-effort)."""
    from .judge import JudgeUnavailable, claude_available, detect_sections
    if not claude_available():
        print("⚠ dynamic captions need the claude CLI on PATH; "
              "using one static caption", file=sys.stderr)
        return None
    try:
        secs = detect_sections(input_path, duration)
    except (JudgeUnavailable, ValueError) as exc:
        print(f"⚠ section detection unavailable: {exc}", file=sys.stderr)
        return None
    return secs or None


def _detect_mistakes_cli(input_path: str) -> list[tuple[float, float]] | None:
    """Run the Claude mistake-detection pass for the CLI's --cut-mistakes.

    Best-effort: prints a note and returns None on any failure (missing
    CLI, auth, unparseable reply) so the edit still runs uncut.
    """
    from .judge import JudgeUnavailable, claude_available, detect_mistakes
    if not claude_available():
        print("⚠ --cut-mistakes needs the claude CLI on PATH; skipping",
              file=sys.stderr)
        return None
    try:
        dur = probe(input_path)["duration"]
        spans = detect_mistakes(input_path, dur)
    except (JudgeUnavailable, ValueError) as exc:
        print(f"⚠ mistake detection unavailable: {exc}", file=sys.stderr)
        return None
    if not spans:
        print("✓ no clear mistakes to cut", file=sys.stderr)
    return spans or None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.caption:
        for warning in check_caption(args.caption):
            print(f"⚠ caption check: {warning}", file=sys.stderr)

    cut_spans_src: list[tuple[float, float]] | None = None
    if args.cut_mistakes:
        cut_spans_src = _detect_mistakes_cli(args.input)

    try:
        out = edit(
            args.input,
            args.caption,
            output=args.output,
            target=args.target,
            style=args.style,
            caption_pos=args.caption_pos,
            caption_mode=args.caption_mode,
            hook=args.hook,
            trim_start=args.trim_start,
            trim_end=args.trim_end,
            cut_spans_src=cut_spans_src,
            crop_enabled=args.crop,
            zoom=args.zoom,
            look_enabled=args.look,
            hook_card=args.hook_card,
            hook_card_text=args.hook_card_text,
            hook_card_pushin=args.hook_card_pushin,
            keep_audio=args.keep_audio,
            music=args.music,
            music_style=args.music_style,
            music_bpm=args.music_bpm,
            music_seed=args.music_seed,
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
