"""Per-chat edit session state and parameter validation.

Pure Python — no Telegram, no Claude — so the redo logic is testable.
Claude proposes parameter updates as a loose JSON dict; validate_updates
is the hard gate that clamps and whitelists them before they touch the
render.
"""

import os
from dataclasses import dataclass, field

from ..analysis import AUTO_SWEET
from ..caption import DEFAULT_STYLE, STYLES

VALID_CAPTION_POS = ("auto", "top", "bottom")
VALID_MUSIC = ("synthwave", "phonk", "off")
MIN_TARGET, MAX_TARGET = 10.0, 120.0


@dataclass
class EditParams:
    target: float | None = None  # None = auto (TikTok-friendly length)
    style: str = DEFAULT_STYLE
    caption_pos: str = "auto"
    hook: bool = True
    crop: bool = True
    keep_audio: bool = False
    music_style: str | None = None  # None = muted export


@dataclass
class EditSession:
    source: str            # downloaded source clip path
    file_name: str         # original Telegram file name
    caption: str
    subject: str = ""
    params: EditParams = field(default_factory=EditParams)
    revision: int = 0
    history: list[str] = field(default_factory=list)
    awaiting_feedback: bool = False
    past_captions: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)  # rendered revisions

    def summary(self) -> str:
        p = self.params
        music = p.music_style or "muted"
        target = "auto" if p.target is None else f"{p.target:.0f}s"
        return (f'caption="{self.caption}" target={target} '
                f"style={p.style} caption_pos={p.caption_pos} "
                f"hook={p.hook} crop={p.crop} audio="
                f"{'ambient' if p.keep_audio else music}")


def cleanup_files(session: EditSession) -> tuple[int, int]:
    """Delete the session's source clip and every rendered revision.

    Safe to call after delivery — the approved render (and the original)
    already live in Telegram. Missing files are skipped silently.
    Returns (files_removed, bytes_freed).
    """
    removed = 0
    freed = 0
    for path in (session.source, *session.outputs):
        try:
            size = os.path.getsize(path)
            os.remove(path)
        except OSError:
            continue
        removed += 1
        freed += size
    return removed, freed


def validate_updates(raw: dict) -> dict:
    """Whitelist + clamp Claude's proposed updates. Drops everything else.

    Returns only the keys that are present, valid, and non-null.
    """
    out: dict = {}

    caption = raw.get("caption")
    if isinstance(caption, str) and caption.strip():
        out["caption"] = caption.strip()

    if raw.get("regenerate_caption") is True:
        out["regenerate_caption"] = True

    target = raw.get("target")
    if isinstance(target, (int, float)) and not isinstance(target, bool):
        out["target"] = min(MAX_TARGET, max(MIN_TARGET, float(target)))

    pos = raw.get("caption_pos")
    if isinstance(pos, str) and pos in VALID_CAPTION_POS:
        out["caption_pos"] = pos

    style = raw.get("style")
    if isinstance(style, str) and style in STYLES:
        out["style"] = style

    for key in ("hook", "crop", "keep_audio"):
        val = raw.get(key)
        if isinstance(val, bool):
            out[key] = val

    music = raw.get("music")
    if isinstance(music, str) and music in VALID_MUSIC:
        out["music_style"] = None if music == "off" else music

    return out


def apply_updates(session: EditSession, updates: dict) -> list[str]:
    """Apply validated updates to the session. Returns change descriptions.

    `regenerate_caption` is not applied here — the caller handles it
    (it needs a Claude round-trip).
    """
    changes: list[str] = []
    p = session.params
    if "caption" in updates and updates["caption"] != session.caption:
        session.past_captions.append(session.caption)
        session.caption = updates["caption"]
        changes.append(f'caption → "{session.caption}"')
    if "target" in updates and updates["target"] != p.target:
        p.target = updates["target"]
        changes.append(f"length → ~{p.target:.0f}s")
    if "caption_pos" in updates and updates["caption_pos"] != p.caption_pos:
        p.caption_pos = updates["caption_pos"]
        changes.append(f"caption position → {p.caption_pos}")
    if "style" in updates and updates["style"] != p.style:
        p.style = updates["style"]
        changes.append(f"caption style → {p.style}")
    for key, label in (("hook", "hook"), ("crop", "auto-zoom"),
                       ("keep_audio", "ambient audio")):
        if key in updates and updates[key] != getattr(p, key):
            setattr(p, key, updates[key])
            changes.append(f"{label} → {'on' if updates[key] else 'off'}")
    if "music_style" in updates and updates["music_style"] != p.music_style:
        p.music_style = updates["music_style"]
        changes.append(f"music → {p.music_style or 'off'}")
    return changes


def tweak_updates(key: str, params: EditParams) -> dict:
    """Map a quick-tap tweak button onto raw setting updates.

    Pure and deterministic — no Claude round-trip, so button tweaks
    apply instantly. Unknown keys return {} (caller reports no-op).
    """
    base = params.target if params.target is not None else AUTO_SWEET
    if key == "shorter":
        return {"target": base * 0.8}
    if key == "longer":
        return {"target": base * 1.25}
    if key == "hook":
        return {"hook": not params.hook}
    if key == "crop":
        return {"crop": not params.crop}
    if key in ("phonk", "synthwave"):
        return {"music": key}
    if key == "nomusic":
        return {"music": "off"}
    if key == "style":
        order = list(STYLES)
        idx = order.index(params.style) if params.style in order else 0
        return {"style": order[(idx + 1) % len(order)]}
    if key == "newcaption":
        return {"regenerate_caption": True}
    return {}


def fallback_updates(feedback: str,
                     current_target: float | None) -> dict:
    """Tiny deterministic interpretation when Claude is unavailable."""
    base = current_target if current_target is not None else AUTO_SWEET
    low = feedback.lower()
    if "short" in low:
        return {"target": base * 0.8}
    if "long" in low:
        return {"target": base * 1.2}
    return {}
