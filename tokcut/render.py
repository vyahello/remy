"""ffmpeg render: trim/speed/concat, caption overlay, audio mix, encode."""

import os
import subprocess

from .layout import OUT_H, OUT_W
from .types import Layout, SourceInfo, SpeedSegment


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
    """A subtle finishing grade — punchy, never garish.

    Screen recordings get contrast + saturation and a mild sharpen so
    UI text stays crisp after the lanczos scale. SDR camera footage
    gets a touch more pop; HDR (HLG/PQ) is treated gently — its
    transfer curve already carries the look, heavy eq would break it.
    All filters are 10-bit safe.
    """
    hdr = src.get("transfer") in ("arib-std-b67", "smpte2084")
    if screen:
        return ("eq=contrast=1.05:saturation=1.15,"
                "unsharp=5:5:0.35:5:5:0.0")
    if hdr:
        return "eq=contrast=1.03:saturation=1.08"
    return "eq=contrast=1.06:brightness=0.015:saturation=1.18"


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

    Each segment is its own seek-decoded ffmpeg input (`-ss A -to B -i`),
    so segments decode one at a time as concat consumes them. The earlier
    single-input design fanned `[0:v]` out into N trim branches, which
    queued the whole decoded video in memory and got ffmpeg OOM-killed on
    1080p60 sources.

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

    crop_f = f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}," if crop else ""
    look_f = f"{look}," if look else ""
    if lay is None:
        # landscape: native (post-crop) resolution, even dims, no caption
        fc.append(f"[vc]{crop_f}fps={fps},"
                  f"scale=trunc(iw/2)*2:trunc(ih/2)*2:flags=lanczos,"
                  f"{look_f}format=yuv420p10le[vout]")
    else:
        vw, vh, vx, vy = lay["vw"], lay["vh"], lay["vx"], lay["vy"]
        # grade before the pad so black bars stay pure black
        fc.append(f"[vc]{crop_f}fps={fps},scale={vw}:{vh}:flags=lanczos,"
                  f"{look_f}pad={OUT_W}:{OUT_H}:{vx}:{vy}:black[base]")
        fc.append(
            f"[base][{cap_idx}:v]overlay={lay['cap_x']}:{lay['cap_y']},"
            f"format=yuv420p10le[vout]")

    audio_out = None
    if with_music:
        # normalize=0 keeps levels as-authored instead of amix halving
        # them; ambient sits just under the music bed.
        if ambient:
            fc.append(f"[{mus_idx}:a]volume=0.8[mus]")
            fc.append(f"{ambient}volume=1.4[amb2];"
                      f"[amb2][mus]amix=inputs=2:duration=first:"
                      f"normalize=0:dropout_transition=0[aout]")
        else:
            fc.append(f"[{mus_idx}:a]volume=0.8[aout]")
        audio_out = "[aout]"
    elif ambient:
        audio_out = ambient
    return ";".join(fc), "[vout]", audio_out


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
    cmd += ["-c:v", "libx265", "-crf", str(crf), "-preset", preset,
            "-profile:v", "main10", "-tag:v", "hvc1",
            *color_args(src),
            "-movflags", "+faststart", out_path]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        # don't leave a truncated, unplayable file behind
        if os.path.exists(out_path):
            os.remove(out_path)
        raise
    return out_path
