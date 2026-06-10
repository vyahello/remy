"""ffmpeg render: trim/speed/concat, caption overlay, audio mix, encode."""

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


def build_filtergraph(
    segs: list[SpeedSegment],
    src: SourceInfo,
    lay: Layout,
    fps: int,
    with_music: bool = False,
    keep_audio: bool = False,
) -> tuple[str, str, str | None]:
    """Return (filter_complex string, video_label, audio_label|None).

    Audio is muted by default (the export is meant to receive a TikTok
    sound in-app). `keep_audio` retains the original ambient track;
    `with_music` mixes in the music input.
    """
    want_ambient = src["audio"] and (with_music or keep_audio)

    fc: list[str] = []
    vlabels: list[str] = []
    alabels: list[str] = []
    for i, (s, e, sp) in enumerate(segs):
        fc.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},"
                  f"setpts=(PTS-STARTPTS)/{sp:.4f}[v{i}]")
        vlabels.append(f"[v{i}]")
        if want_ambient:
            fc.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},"
                      f"asetpts=PTS-STARTPTS,{atempo_chain(sp)}[a{i}]")
            alabels.append(f"[a{i}]")

    n = len(segs)
    if want_ambient:
        pairs = "".join(v + a for v, a in zip(vlabels, alabels))
        fc.append(f"{pairs}concat=n={n}:v=1:a=1[vc][amb]")
        ambient = "[amb]"
    else:
        fc.append(f"{''.join(vlabels)}concat=n={n}:v=1[vc]")
        ambient = None

    vw, vh, vx, vy = lay["vw"], lay["vh"], lay["vx"], lay["vy"]
    fc.append(f"[vc]fps={fps},scale={vw}:{vh}:flags=lanczos,"
              f"pad={OUT_W}:{OUT_H}:{vx}:{vy}:black[base]")
    fc.append(f"[base][1:v]overlay={lay['cap_x']}:{lay['cap_y']},"
              f"format=yuv420p10le[vout]")

    audio_out = None
    if with_music:
        # music is input #2. normalize=0 keeps levels as-authored instead
        # of amix halving them; ambient sits just under the music bed.
        if ambient:
            fc.append("[2:a]volume=0.8[mus]")
            fc.append(f"{ambient}volume=1.4[amb2];"
                      f"[amb2][mus]amix=inputs=2:duration=first:"
                      f"normalize=0:dropout_transition=0[aout]")
        else:
            fc.append("[2:a]volume=0.8[aout]")
        audio_out = "[aout]"
    elif ambient:
        audio_out = ambient
    return ";".join(fc), "[vout]", audio_out


def render(
    path: str,
    segs: list[SpeedSegment],
    caption_png: str,
    src: SourceInfo,
    lay: Layout,
    out_path: str,
    crf: int = 18,
    preset: str = "medium",
    music_path: str | None = None,
    keep_audio: bool = False,
) -> str:
    fps = min(60, round(src["fps"]))
    fc, vlabel, alabel = build_filtergraph(
        segs, src, lay, fps, with_music=bool(music_path),
        keep_audio=keep_audio)

    cmd: list[str] = ["ffmpeg", "-y", "-v", "warning", "-stats",
                      "-i", path, "-i", caption_png]
    if music_path:
        cmd += ["-stream_loop", "-1", "-i", music_path]
    cmd += ["-filter_complex", fc, "-map", vlabel]
    if alabel:
        cmd += ["-map", alabel, "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    else:
        cmd += ["-an"]  # muted export — add a TikTok sound in-app
    cmd += ["-c:v", "libx265", "-crf", str(crf), "-preset", preset,
            "-profile:v", "main10", "-tag:v", "hvc1",
            "-color_primaries", "bt2020", "-color_trc", "arib-std-b67",
            "-colorspace", "bt2020nc",
            "-movflags", "+faststart", out_path]
    subprocess.run(cmd, check=True)
    return out_path
