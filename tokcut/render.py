"""ffmpeg render: trim/speed/concat, caption overlay, audio mix, encode."""

import contextlib
import os
import subprocess
import tempfile

from .layout import OUT_H, OUT_W
from .types import HookCard, Layout, SourceInfo, SpeedSegment

# The single ffmpeg graph opens one live seek-decoder per segment, all at
# once — each a full HEVC decoder context holding a DPB of reference frames.
# Peak memory therefore scales with *simultaneous decoders × per-frame decode
# cost*, not with the segment count alone: a 5-segment 1080x1920 60fps 10-bit
# HLG clip once livelocked a 3.7 GB VPS into swap (held forever, never
# OOM-killed). So the switch to the bounded two-pass (one decoder at a time)
# is driven by a memory *budget* measured in "decoder-equivalents" — one unit
# = a 1080p30 8-bit decoder — plus a hard ceiling on input count.
MAX_CONCAT_INPUTS = 12            # hard cap on simultaneous ffmpeg inputs
SINGLE_PASS_DECODE_BUDGET = 6.0   # decoder-equivalents a small box absorbs

# audio mix levels + the TikTok loudness target, shared by the single- and
# two-pass paths so they stay identical
MUSIC_VOL = 0.8
AMBIENT_VOL = 1.4
AMIX = "amix=inputs=2:duration=first:normalize=0:dropout_transition=0"
LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11"

# Animated hook card (vertical only, opt-in): a bigger caption that fades
# in/out over the opening with a small scale ramp, on a dimmed backing so
# it reads against busy footage. Timed in *output* seconds, so in the
# single-pass graph it rides the concatenated stream and in the two-pass
# graph it is applied only to the first segment.
HOOK_CARD_DUR = 1.6      # seconds the card is on screen
HOOK_CARD_FADE = 0.3     # alpha fade-in / fade-out (also the scale ramp)
HOOK_CARD_DIM = 0.35     # darkness of the legibility backing box
HOOK_CARD_SCALE0 = 0.92  # card scale at t=0, ramping to 1.0 over the fade-in


def atempo_chain(speed: float) -> str:
    """ffmpeg atempo accepts 0.5..2.0; chain factors for larger speeds."""
    parts: list[float] = []
    s = speed
    while s > 2.0:
        parts.append(2.0)
        s /= 2.0
    parts.append(s)
    return ",".join(f"atempo={p:.6f}" for p in parts)


def color_args(src: SourceInfo) -> list[str]:
    """Color metadata for the encode, matched to the source.

    HDR sources (iPhone HLG / PQ) keep their wide-gamut tags; everything
    else is tagged plain SDR bt709. Tagging SDR content as HLG (the old
    hardcoded behavior) made it look washed out on phones.
    """
    transfer = src.get("transfer", "")
    if transfer in ("arib-std-b67", "smpte2084"):
        return ["-color_primaries", "bt2020", "-color_trc", transfer,
                "-colorspace", "bt2020nc"]
    return ["-color_primaries", "bt709", "-color_trc", "bt709",
            "-colorspace", "bt709"]


def look_filter(src: SourceInfo, screen: bool) -> str:
    """A finishing grade — punchy, never garish.

    Screen recordings are mostly monochrome (terminal text on a dark
    background), so saturation buys nothing there — the budget goes to
    contrast, a slight gamma lift that keeps shadows from crushing, and
    a strong sharpen so UI text pops at phone size. SDR camera footage
    gets a contrast/saturation lift; HDR (HLG/PQ) is treated gently —
    its transfer curve already carries the look, heavy eq would break
    it. All filters are 10-bit safe.
    """
    hdr = src.get("transfer") in ("arib-std-b67", "smpte2084")
    if screen:
        return ("eq=contrast=1.12:gamma=1.05,"
                "unsharp=5:5:0.8:5:5:0.0")
    if hdr:
        return "eq=contrast=1.03:saturation=1.08"
    return "eq=contrast=1.06:brightness=0.015:saturation=1.18"


def encoder_params(screen: bool) -> str:
    """x265 tuning for the content type.

    aq-mode=3 biases bits toward dark regions — exactly where terminal
    footage and dark-room desk shots live (the x265 default starves
    them). Screen content also relaxes the deblocker so text edges
    aren't smoothed away.
    """
    return "aq-mode=3:deblock=-1,-1" if screen else "aq-mode=3"


def _hook_card_chain(cap_label: str, card_label: str,
                     lay: Layout, card: HookCard) -> str:
    """The post-`[base]` chain when an animated hook card is overlaid.

    Consumes `[base]`, produces `[vout]`: optional footage push-in, the
    card stream (alpha fade in/out + a 0.92→1.0 scale ramp, alpha-safe so
    the rounded box stays transparent), a dimmed backing box, the card
    overlay (recentered as it scales), then the persistent caption — gated
    to start only after the card fades so just one text block shows in the
    opening second.
    """
    dur, fade = HOOK_CARD_DUR, HOOK_CARD_FADE
    ramp = f"{HOOK_CARD_SCALE0}+{1.0 - HOOK_CARD_SCALE0:.2f}*min(t/{fade},1)"
    parts: list[str] = []
    base = "[base]"
    if card["pushin"]:
        z = f"1+0.04*(1-min(t/{dur},1))"  # 1.04 → 1.0 over the card, then 1.0
        parts.append(f"[base]scale=w='iw*({z})':h='ih*({z})':eval=frame,"
                     f"crop={OUT_W}:{OUT_H}[pbase]")
        base = "[pbase]"
    parts.append(
        f"{card_label}format=rgba,"
        f"fade=t=in:st=0:d={fade}:alpha=1,"
        f"fade=t=out:st={dur - fade:.2f}:d={fade}:alpha=1,"
        f"scale=w='iw*({ramp})':h='ih*({ramp})':eval=frame[hcard]")
    cw, ch, cy = card["w"], card["h"], card["y"]
    pad = 14
    bx = max(0, (OUT_W - cw) // 2 - pad)
    by = max(0, cy - pad)
    bw = min(OUT_W - bx, cw + 2 * pad)
    parts.append(
        f"{base}drawbox=x={bx}:y={by}:w={bw}:h={ch + 2 * pad}:"
        f"color=black@{HOOK_CARD_DIM}:t=fill:enable='lte(t,{dur})'[dim]")
    parts.append(
        f"[dim][hcard]overlay=x=(W-w)/2:y={cy}:eval=frame:"
        f"enable='lte(t,{dur})'[wc]")
    if cap_label:
        parts.append(
            f"[wc]{cap_label}overlay={lay['cap_x']}:{lay['cap_y']}:"
            f"enable='gt(t,{dur})',format=yuv420p10le[vout]")
    else:
        parts.append("[wc]format=yuv420p10le[vout]")
    return ";".join(parts)


def _format_video(in_label: str, src: SourceInfo, lay: Layout | None,
                  crop: tuple[int, int, int, int] | None, look: str,
                  fps: int, cap_label: str, card_label: str = "",
                  card: HookCard | None = None) -> str:
    """Crop → fps → scale → grade → (pad + caption) chain to `[vout]`.

    Shared by the single-pass graph (fed the concatenated `[vc]`) and the
    per-segment graph (fed one trimmed segment) so framing/scaling/caption
    placement is bit-for-bit identical either way. `lay=None` is landscape
    (native resolution, no caption); otherwise the video is scaled into
    the layout box, padded to 1080x1920 and the caption is overlaid. When
    `card` is given (vertical only) the animated hook card rides on top of
    the opening — see `_hook_card_chain`.
    """
    crop_f = f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}," if crop else ""
    look_f = f"{look}," if look else ""
    if lay is None:
        return (f"{in_label}{crop_f}fps={fps},"
                f"scale=trunc(iw/2)*2:trunc(ih/2)*2:flags=lanczos,"
                f"{look_f}format=yuv420p10le[vout]")
    vw, vh, vx, vy = lay["vw"], lay["vh"], lay["vx"], lay["vy"]
    # grade before the pad so black bars stay pure black
    base = (f"{in_label}{crop_f}fps={fps},scale={vw}:{vh}:flags=lanczos,"
            f"{look_f}pad={OUT_W}:{OUT_H}:{vx}:{vy}:black[base]")
    if card is not None and card_label:
        return base + ";" + _hook_card_chain(cap_label, card_label, lay, card)
    if cap_label:  # vertical with a baked caption
        return (f"{base};[base]{cap_label}overlay="
                f"{lay['cap_x']}:{lay['cap_y']},format=yuv420p10le[vout]")
    return f"{base};[base]format=yuv420p10le[vout]"  # vertical, no caption


def _mix_and_norm(fc: list[str], ambient: str | None,
                  music: str | None) -> str | None:
    """Append the audio mix + loudness-normalize filters.

    `ambient`/`music` are input labels (e.g. `[amb]`, `[2:a]`) or None.
    Music sits on top with ambient ducked just under it; the result is
    normalized to TikTok's -14 LUFS. Returns the final label, or None
    when there is no audio (muted export). Shared by both render paths.
    """
    out: str | None = None
    if music:
        # normalize=0 keeps levels as-authored instead of amix halving them
        if ambient:
            fc.append(f"{music}volume={MUSIC_VOL}[mus]")
            fc.append(f"{ambient}volume={AMBIENT_VOL}[amb2];"
                      f"[amb2][mus]{AMIX}[aout]")
        else:
            fc.append(f"{music}volume={MUSIC_VOL}[aout]")
        out = "[aout]"
    elif ambient:
        out = ambient
    if out:
        fc.append(f"{out}{LOUDNORM}[anorm]")
        out = "[anorm]"
    return out


def build_filtergraph(
    segs: list[SpeedSegment],
    src: SourceInfo,
    lay: Layout | None,
    fps: int,
    with_music: bool = False,
    keep_audio: bool = False,
    crop: tuple[int, int, int, int] | None = None,
    look: str = "",
    hook_card: HookCard | None = None,
    has_caption: bool = True,
) -> tuple[str, str, str | None]:
    """Return (filter_complex string, video_label, audio_label|None).

    `look` is an optional finishing-grade filter snippet (look_filter),
    applied after the scale.

    Each segment is its own seek-decoded ffmpeg input (`-ss A -to B -i`).
    ffmpeg opens all of them at once, so each is a live HEVC decoder
    context — fine for a handful of segments, but past MAX_CONCAT_INPUTS
    the memory adds up and `render` switches to the bounded two-pass path
    instead (see `_render_segmented`).

    `lay=None` is landscape mode: the source keeps its native (post-crop)
    resolution and no caption is overlaid — there is no caption input.

    Input layout: inputs 0..n-1 are the segments, n is the caption PNG
    (when lay is given), then the music track (optional). Audio is muted
    by default (the export is meant to receive a TikTok sound in-app);
    `keep_audio` retains the original ambient track, `with_music` mixes
    in music.
    """
    want_ambient = src["audio"] and (with_music or keep_audio)
    n = len(segs)
    # input order: segments, caption (vertical+caption), hook card
    # (vertical+card), then music. Each index is only consumed when that
    # input is actually present, so a vertical clip with no caption skips
    # the caption slot entirely.
    cap_present = lay is not None and has_caption
    has_card = lay is not None and hook_card is not None
    idx = n
    cap_idx = idx
    if cap_present:
        idx += 1
    card_idx = idx
    if has_card:
        idx += 1
    mus_idx = idx

    fc: list[str] = []
    vlabels: list[str] = []
    alabels: list[str] = []
    for i, (_s, _e, sp) in enumerate(segs):
        fc.append(f"[{i}:v]setpts=(PTS-STARTPTS)/{sp:.4f}[v{i}]")
        vlabels.append(f"[v{i}]")
        if want_ambient:
            fc.append(f"[{i}:a]asetpts=PTS-STARTPTS,"
                      f"{atempo_chain(sp)}[a{i}]")
            alabels.append(f"[a{i}]")

    if want_ambient:
        pairs = "".join(v + a for v, a in zip(vlabels, alabels))
        fc.append(f"{pairs}concat=n={n}:v=1:a=1[vc][amb]")
        ambient = "[amb]"
    else:
        fc.append(f"{''.join(vlabels)}concat=n={n}:v=1[vc]")
        ambient = None

    fc.append(_format_video(
        "[vc]", src, lay, crop, look, fps,
        f"[{cap_idx}:v]" if cap_present else "",
        f"[{card_idx}:v]" if has_card else "",
        hook_card if lay is not None else None))
    audio_out = _mix_and_norm(
        fc, ambient, f"[{mus_idx}:a]" if with_music else None)
    return ";".join(fc), "[vout]", audio_out


def _run(cmd: list[str], out_path: str) -> None:
    """Run ffmpeg; on failure delete the half-written output and re-raise."""
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        if os.path.exists(out_path):
            os.remove(out_path)  # no truncated, unplayable file left behind
        raise


def _x265_args(src: SourceInfo, lay: Layout | None, crf: int,
               preset: str) -> list[str]:
    return ["-c:v", "libx265", "-crf", str(crf), "-preset", preset,
            "-x265-params", encoder_params(screen=lay is None),
            "-profile:v", "main10", "-tag:v", "hvc1", *color_args(src)]


def decode_weight(src: SourceInfo) -> float:
    """Per-input decode cost relative to a 1080p30 8-bit baseline.

    A seek-decoded input keeps a whole decoder context resident (a DPB of
    reference frames); that footprint grows with resolution, frame rate and
    bit depth. HLG/PQ sources are 10-bit, so their frames cost ~1.6x the
    bytes of an 8-bit frame. The single-pass graph holds one of these per
    segment simultaneously, so this weight is what the budget counts.
    """
    base = 1920 * 1080 * 30
    w = src.get("w", 1920) or 1920
    h = src.get("h", 1080) or 1080
    fps = max(1.0, src.get("fps", 30.0) or 30.0)
    weight = (w * h * fps) / base
    if src.get("transfer", "") in ("arib-std-b67", "smpte2084"):
        weight *= 1.6  # 10-bit HDR frames are larger than 8-bit
    return weight


def use_two_pass(segs: list[SpeedSegment], src: SourceInfo) -> bool:
    """Whether to take the bounded two-pass instead of the single graph.

    True past the hard input cap, or when the simultaneous decoders the
    single-pass graph would open exceed the small-box memory budget. Count
    alone is not enough: a heavy source (high res / fps / 10-bit) blows the
    budget at far fewer segments than a light one (the 5-segment 60fps
    10-bit wedge that livelocked the VPS would have passed a count-only
    gate).
    """
    if len(segs) > MAX_CONCAT_INPUTS:
        return True
    return len(segs) * decode_weight(src) > SINGLE_PASS_DECODE_BUDGET


def render(
    path: str,
    segs: list[SpeedSegment],
    caption_png: str | None,
    src: SourceInfo,
    lay: Layout | None,
    out_path: str,
    crf: int = 18,
    preset: str = "medium",
    music_path: str | None = None,
    keep_audio: bool = False,
    crop: tuple[int, int, int, int] | None = None,
    look: str = "",
    hook_card: HookCard | None = None,
    hook_card_png: str | None = None,
) -> str:
    """Encode the edit. Picks the single-pass graph when its simultaneous
    decoders fit the memory budget, else the bounded two-pass (one decoder
    at a time) for heavy or heavily-cut clips — see `use_two_pass`."""
    if use_two_pass(segs, src):
        return _render_segmented(
            path, segs, caption_png, src, lay, out_path, crf, preset,
            music_path, keep_audio, crop, look, hook_card, hook_card_png)
    return _render_single(
        path, segs, caption_png, src, lay, out_path, crf, preset,
        music_path, keep_audio, crop, look, hook_card, hook_card_png)


def _hook_card_input(fps: int, hook_card_png: str) -> list[str]:
    """ffmpeg input args for the looped card PNG (gives it a timeline so
    fade/scale animate; the enable gate hides it past HOOK_CARD_DUR)."""
    return ["-loop", "1", "-framerate", str(fps), "-t",
            f"{HOOK_CARD_DUR + 0.2:.2f}", "-i", hook_card_png]


def _render_single(
    path: str, segs: list[SpeedSegment], caption_png: str | None,
    src: SourceInfo, lay: Layout | None, out_path: str, crf: int,
    preset: str, music_path: str | None, keep_audio: bool,
    crop: tuple[int, int, int, int] | None, look: str,
    hook_card: HookCard | None = None, hook_card_png: str | None = None,
) -> str:
    """One ffmpeg graph: every segment a seek-decoded input, concat, encode.

    Fast and simple, but every input is a live decoder — only used when
    those simultaneous decoders fit the memory budget (see `use_two_pass`).
    """
    fps = min(60, round(src["fps"]))
    has_card = (lay is not None and hook_card is not None
                and bool(hook_card_png))
    fc, vlabel, alabel = build_filtergraph(
        segs, src, lay, fps, with_music=bool(music_path),
        keep_audio=keep_audio, crop=crop, look=look,
        hook_card=hook_card if has_card else None,
        has_caption=bool(caption_png) and lay is not None)

    cmd: list[str] = ["ffmpeg", "-y", "-v", "warning", "-stats"]
    for s, e, _sp in segs:
        cmd += ["-ss", f"{s:.3f}", "-to", f"{e:.3f}", "-i", path]
    if caption_png and lay is not None:
        cmd += ["-i", caption_png]
    if has_card:
        assert hook_card_png is not None
        cmd += _hook_card_input(fps, hook_card_png)
    if music_path:
        cmd += ["-stream_loop", "-1", "-i", music_path]
    cmd += ["-filter_complex", fc, "-map", vlabel]
    if alabel:
        cmd += ["-map", alabel, "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    else:
        cmd += ["-an"]  # muted export — add a TikTok sound in-app
    if music_path:
        # the music is looped infinitely (-stream_loop -1); cap the output
        # to the (finite) video so it doesn't mux forever when there's no
        # ambient track to bound the amix
        cmd += ["-shortest"]
    cmd += [*_x265_args(src, lay, crf, preset),
            "-movflags", "+faststart", out_path]
    _run(cmd, out_path)
    return out_path


def _render_segmented(
    path: str, segs: list[SpeedSegment], caption_png: str | None,
    src: SourceInfo, lay: Layout | None, out_path: str, crf: int,
    preset: str, music_path: str | None, keep_audio: bool,
    crop: tuple[int, int, int, int] | None, look: str,
    hook_card: HookCard | None = None, hook_card_png: str | None = None,
) -> str:
    """Bounded two-pass render for long, heavily-cut clips.

    Pass 1 encodes each segment on its own (one decoder + one encoder at a
    time — memory is flat regardless of segment count), already framed and
    captioned into the final picture. Pass 2 stitches the parts with the
    concat *demuxer* (sequential read, no re-decode of video) and layers in
    the music bed + loudness pass. This avoids the single-pass graph's
    N-simultaneous-decoders memory blow-up on a small box. The animated
    hook card rides only the first segment (output time t=0), so it uses
    the same `_format_video` card branch as the single-pass path.
    """
    fps = min(60, round(src["fps"]))
    want_ambient = src["audio"] and (bool(music_path) or keep_audio)
    tmp = tempfile.mkdtemp(prefix="tokcut_seg_")
    try:
        # --- pass 1: each segment -> a fully-formatted intermediate ---
        parts: list[str] = []
        for i, (s, e, sp) in enumerate(segs):
            seg_out = os.path.join(tmp, f"seg{i:04d}.mp4")
            # input [0] is the segment video; the caption (if any) and the
            # hook card (opening segment only) follow, so their labels shift
            # when there is no caption to skip.
            cap_present = bool(caption_png) and lay is not None
            seg_card = (hook_card if i == 0 and lay is not None
                        and hook_card_png else None)
            inp = 1
            cap_label = ""
            if cap_present:
                cap_label = f"[{inp}:v]"
                inp += 1
            card_label = ""
            if seg_card:
                card_label = f"[{inp}:v]"
                inp += 1
            fc = (f"[0:v]setpts=(PTS-STARTPTS)/{sp:.4f}[vt];"
                  + _format_video("[vt]", src, lay, crop, look, fps,
                                  cap_label, card_label, seg_card))
            cmd = ["ffmpeg", "-y", "-v", "error",
                   "-ss", f"{s:.3f}", "-to", f"{e:.3f}", "-i", path]
            if cap_present:
                assert caption_png is not None
                cmd += ["-i", caption_png]
            if seg_card:
                assert hook_card_png is not None
                cmd += _hook_card_input(fps, hook_card_png)
            maps = ["-map", "[vout]"]
            if want_ambient:
                fc += (f";[0:a]asetpts=PTS-STARTPTS,"
                       f"{atempo_chain(sp)}[aout]")
                maps += ["-map", "[aout]", "-c:a", "aac",
                         "-b:a", "192k", "-ar", "48000"]
            else:
                maps += ["-an"]
            cmd += ["-filter_complex", fc, *maps,
                    *_x265_args(src, lay, crf, preset),
                    # identical timebase keeps the concat demuxer happy
                    "-video_track_timescale", "90000", seg_out]
            _run(cmd, seg_out)
            parts.append(seg_out)

        # --- pass 2: concat-demux the parts, add music + loudnorm ---
        listf = os.path.join(tmp, "concat.txt")
        with open(listf, "w") as fh:
            for p in parts:
                # single quotes escaped per concat-demuxer syntax
                fh.write(f"file '{p}'\n")

        cmd = ["ffmpeg", "-y", "-v", "warning", "-stats",
               "-f", "concat", "-safe", "0", "-i", listf]
        if music_path:
            cmd += ["-stream_loop", "-1", "-i", music_path]
        fc2: list[str] = []
        alabel = _mix_and_norm(
            fc2, "[0:a]" if want_ambient else None,
            "[1:a]" if music_path else None)
        cmd += ["-map", "0:v:0", "-c:v", "copy"]  # parts are already encoded
        if alabel:
            cmd += ["-filter_complex", ";".join(fc2),
                    "-map", alabel, "-c:a", "aac",
                    "-b:a", "192k", "-ar", "48000"]
        else:
            cmd += ["-an"]
        if music_path:
            cmd += ["-shortest"]  # looped music: bound to the video
        cmd += ["-tag:v", "hvc1", "-movflags", "+faststart", out_path]
        _run(cmd, out_path)
    finally:
        for p in os.listdir(tmp):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(tmp, p))
        with contextlib.suppress(OSError):
            os.rmdir(tmp)
    return out_path
