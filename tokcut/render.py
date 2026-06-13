"""ffmpeg render: trim/speed/concat, caption overlay, audio mix, encode."""

import contextlib
import os
import subprocess
import tempfile

from .layout import OUT_H, OUT_W
from .types import Layout, SourceInfo, SpeedSegment

# Above this many segments the single ffmpeg graph opens too many
# simultaneous seek-decoded inputs (each a full HEVC decoder context) and
# blows the memory budget on a small box. Past it, render falls back to a
# bounded two-pass: encode each segment alone, then concat-demux them.
MAX_CONCAT_INPUTS = 12

# audio mix levels + the TikTok loudness target, shared by the single- and
# two-pass paths so they stay identical
MUSIC_VOL = 0.8
AMBIENT_VOL = 1.4
AMIX = "amix=inputs=2:duration=first:normalize=0:dropout_transition=0"
LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11"


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


def _format_video(in_label: str, src: SourceInfo, lay: Layout | None,
                  crop: tuple[int, int, int, int] | None, look: str,
                  fps: int, cap_label: str) -> str:
    """Crop → fps → scale → grade → (pad + caption) chain to `[vout]`.

    Shared by the single-pass graph (fed the concatenated `[vc]`) and the
    per-segment graph (fed one trimmed segment) so framing/scaling/caption
    placement is bit-for-bit identical either way. `lay=None` is landscape
    (native resolution, no caption); otherwise the video is scaled into
    the layout box, padded to 1080x1920 and the caption is overlaid.
    """
    crop_f = f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}," if crop else ""
    look_f = f"{look}," if look else ""
    if lay is None:
        return (f"{in_label}{crop_f}fps={fps},"
                f"scale=trunc(iw/2)*2:trunc(ih/2)*2:flags=lanczos,"
                f"{look_f}format=yuv420p10le[vout]")
    vw, vh, vx, vy = lay["vw"], lay["vh"], lay["vx"], lay["vy"]
    # grade before the pad so black bars stay pure black
    return (f"{in_label}{crop_f}fps={fps},scale={vw}:{vh}:flags=lanczos,"
            f"{look_f}pad={OUT_W}:{OUT_H}:{vx}:{vy}:black[base];"
            f"[base]{cap_label}overlay={lay['cap_x']}:{lay['cap_y']},"
            f"format=yuv420p10le[vout]")


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
    cap_idx = n
    mus_idx = n + 1 if lay is not None else n

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

    fc.append(_format_video("[vc]", src, lay, crop, look, fps,
                            f"[{cap_idx}:v]"))
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
) -> str:
    """Encode the edit. Picks the single-pass graph for a normal segment
    count, or the bounded two-pass for long, heavily-cut clips."""
    if len(segs) > MAX_CONCAT_INPUTS:
        return _render_segmented(
            path, segs, caption_png, src, lay, out_path, crf, preset,
            music_path, keep_audio, crop, look)
    return _render_single(
        path, segs, caption_png, src, lay, out_path, crf, preset,
        music_path, keep_audio, crop, look)


def _render_single(
    path: str, segs: list[SpeedSegment], caption_png: str | None,
    src: SourceInfo, lay: Layout | None, out_path: str, crf: int,
    preset: str, music_path: str | None, keep_audio: bool,
    crop: tuple[int, int, int, int] | None, look: str,
) -> str:
    """One ffmpeg graph: every segment a seek-decoded input, concat, encode.

    Fast and simple, but every input is a live decoder — only used up to
    MAX_CONCAT_INPUTS segments (see `render`).
    """
    fps = min(60, round(src["fps"]))
    fc, vlabel, alabel = build_filtergraph(
        segs, src, lay, fps, with_music=bool(music_path),
        keep_audio=keep_audio, crop=crop, look=look)

    cmd: list[str] = ["ffmpeg", "-y", "-v", "warning", "-stats"]
    for s, e, _sp in segs:
        cmd += ["-ss", f"{s:.3f}", "-to", f"{e:.3f}", "-i", path]
    if caption_png and lay is not None:
        cmd += ["-i", caption_png]
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
) -> str:
    """Bounded two-pass render for long, heavily-cut clips.

    Pass 1 encodes each segment on its own (one decoder + one encoder at a
    time — memory is flat regardless of segment count), already framed and
    captioned into the final picture. Pass 2 stitches the parts with the
    concat *demuxer* (sequential read, no re-decode of video) and layers in
    the music bed + loudness pass. This avoids the single-pass graph's
    N-simultaneous-decoders memory blow-up on a small box.
    """
    fps = min(60, round(src["fps"]))
    want_ambient = src["audio"] and (bool(music_path) or keep_audio)
    tmp = tempfile.mkdtemp(prefix="tokcut_seg_")
    try:
        # --- pass 1: each segment -> a fully-formatted intermediate ---
        parts: list[str] = []
        for i, (s, e, sp) in enumerate(segs):
            seg_out = os.path.join(tmp, f"seg{i:04d}.mp4")
            fc = (f"[0:v]setpts=(PTS-STARTPTS)/{sp:.4f}[vt];"
                  + _format_video("[vt]", src, lay, crop, look, fps,
                                  "[1:v]"))
            cmd = ["ffmpeg", "-y", "-v", "error",
                   "-ss", f"{s:.3f}", "-to", f"{e:.3f}", "-i", path]
            if caption_png and lay is not None:
                cmd += ["-i", caption_png]
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
