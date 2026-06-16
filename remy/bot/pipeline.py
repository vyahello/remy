"""Deterministic helpers the bot runs in-process — no Telegram, no Claude.

Thin wrappers over remy's analysis plus small pure helpers for the bot.
The actual editing goes through cli.edit() (run in a worker thread by
app.py); in step 3 Claude Code takes over the caption wording.
"""

import os
import re

from ..analysis import (
    assign_speeds,
    classify,
    motion_scores,
    probe,
    smooth,
    to_segments,
)
from ..types import SourceInfo, SpeedSegment


def derive_caption(user_caption: str | None, filename: str | None) -> str:
    """Caption for the clip: the Telegram message caption, else the
    filename stem tidied up ("my_demo-v2.mp4" → "my demo v2")."""
    if user_caption and user_caption.strip():
        return user_caption.strip()
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    tidy = stem.replace("_", " ").replace("-", " ").strip()
    return tidy or "watch this ⚡"


def dry_run_plan(
    input_path: str, target: float | None = None
) -> tuple[SourceInfo, list[SpeedSegment], float]:
    """Probe + score + solve speeds. Returns (src, segments, est_seconds)."""
    src = probe(input_path)
    raw_scores, _frames = motion_scores(input_path, src)
    segs, est = assign_speeds(
        to_segments(classify(smooth(raw_scores))), target)
    return src, segs, est


def format_plan(
    src: SourceInfo, segs: list[SpeedSegment], est: float
) -> str:
    """Render the edit decision list as a Telegram-friendly message."""
    lines = [
        f"📹 {src['w']}x{src['h']} · {src['duration']:.1f}s "
        f"@ {src['fps']:.0f}fps",
        f"✂️ {len(segs)} segments → ~{est:.1f}s output",
        "",
    ]
    for s, e, sp in segs:
        tag = "▶️ 1.00x" if round(sp, 2) == 1.0 else f"⏩ {sp:.2f}x"
        lines.append(f"`{s:6.1f}–{e:6.1f}`  {tag}")
    return "\n".join(lines)


def friendly_progress(line: str) -> str | None:
    """Translate a pipeline log line into a short human update.

    The edit pipeline streams technical status lines (the full edit
    decision list, probe data); in chat we only surface what a creator
    cares about. Returns None for lines that should stay in the logs.
    """
    if line.startswith("edit plan"):
        m = re.search(r"\((\d+) segments, ~([\d.]+)s output\)", line)
        if m:
            return (f"✂️ cutting to ~{float(m.group(2)):.0f}s "
                    f"({m.group(1)} pieces)")
        return "✂️ cut plan ready"
    if line.startswith("landscape source"):
        return "🖥️ native resolution kept — overlay your own caption"
    if line.startswith("crop:"):
        return "🔍 zoomed into the action"
    if line.startswith("beat-align"):
        return "🥁 cuts snapped to the beat"
    if line.startswith("look:"):
        return "✨ finishing grade applied"
    if line.startswith("music:"):
        return "🎵" + line.removeprefix("music:")
    if line.startswith("audio: muted"):
        return "🔇 muted — add a trending sound in-app"
    if line.startswith("audio:"):
        return "🔊" + line.removeprefix("audio:")
    if line.startswith("rendering"):
        return "🎬 encoding… (takes a couple of minutes)"
    return None  # probe data, segment rows, caption y — log-only


def sweep_workdir(workdir: str) -> tuple[int, int]:
    """Delete leftover working files at startup. Returns (removed, bytes).

    Sessions live only in the running process's memory, so a restart
    (every deploy) orphans whatever was in the workdir — no Approve or
    new-clip sweep can ever reclaim it. At startup nothing is rendering,
    so every regular file here is orphaned and safe to drop. The
    `.rendering` deploy-drain marker is handled separately by the caller.
    """
    removed = 0
    freed = 0
    try:
        entries = os.listdir(workdir)
    except OSError:
        return 0, 0
    for name in entries:
        if name == ".rendering":
            continue
        path = os.path.join(workdir, name)
        try:
            if not os.path.isfile(path):
                continue
            size = os.path.getsize(path)
            os.remove(path)
        except OSError:
            continue
        removed += 1
        freed += size
    return removed, freed


def delivery_name(file_name: str | None, rev: int) -> str:
    """Human filename for a delivered take.

    On disk everything is keyed by Telegram's file_unique_id (collision
    proof, ugly); the file the creator receives is named after their
    original upload, or the date when the upload had no name.
    """
    import datetime
    stem = os.path.splitext(file_name or "")[0]
    stem = re.sub(r"[^\w\- ]", "", stem).strip().replace(" ", "_")
    if not stem:
        stem = f"remy_{datetime.date.today():%Y-%m-%d}"
    return f"{stem}_take{rev}.mp4"
