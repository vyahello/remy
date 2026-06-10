"""Per-chat edit session state and parameter validation.

Pure Python — no Telegram, no Claude — so the redo logic is testable.
Claude proposes parameter updates as a loose JSON dict; validate_updates
is the hard gate that clamps and whitelists them before they touch the
render.
"""

from dataclasses import dataclass, field

VALID_CAPTION_POS = ("auto", "top", "bottom")
VALID_MUSIC = ("synthwave", "phonk", "off")
MIN_TARGET, MAX_TARGET = 10.0, 120.0


@dataclass
class EditParams:
    target: float = 50.0
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

    def summary(self) -> str:
        p = self.params
        music = p.music_style or "muted"
        return (f'caption="{self.caption}" target={p.target:.0f}s '
                f"caption_pos={p.caption_pos} hook={p.hook} "
                f"crop={p.crop} audio="
                f"{'ambient' if p.keep_audio else music}")


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
    for key, label in (("hook", "hook"), ("crop", "auto-zoom"),
                       ("keep_audio", "ambient audio")):
        if key in updates and updates[key] != getattr(p, key):
            setattr(p, key, updates[key])
            changes.append(f"{label} → {'on' if updates[key] else 'off'}")
    if "music_style" in updates and updates["music_style"] != p.music_style:
        p.music_style = updates["music_style"]
        changes.append(f"music → {p.music_style or 'off'}")
    return changes


def fallback_updates(feedback: str, current_target: float) -> dict:
    """Tiny deterministic interpretation when Claude is unavailable."""
    low = feedback.lower()
    if "short" in low:
        return {"target": current_target * 0.8}
    if "long" in low:
        return {"target": current_target * 1.2}
    return {}
