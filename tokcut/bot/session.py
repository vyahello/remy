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
from ..music import STYLE_BPM

VALID_CAPTION_POS = ("auto", "top", "bottom")
VALID_MUSIC = ("synthwave", "phonk", "off")
MIN_TARGET, MAX_TARGET = 10.0, 120.0
MIN_ZOOM, MAX_ZOOM = 0.5, 2.5
ZOOM_STEP = 1.15  # one tap of Tighter/Wider
MIN_BPM, MAX_BPM = 60, 180
TEMPO_STEP = 1.12  # one tap of Faster/Slower beat


@dataclass
class EditParams:
    target: float | None = None  # None = auto (TikTok-friendly length)
    style: str = DEFAULT_STYLE
    caption_pos: str = "auto"
    hook: bool = False  # cold-open teaser — opt-in (default off)
    crop: bool = True
    zoom: float = 1.0  # framing dial on top of the auto-zoom
    look: bool = True  # finishing grade (contrast/saturation pop)
    keep_audio: bool = False
    music_style: str | None = None  # None = muted export
    music_bpm: int | None = None    # None = the style's natural tempo
    music_seed: int = 0             # bump to re-roll the composition


def default_bpm(style: str | None) -> int:
    """The natural tempo for a style (phonk 132, synthwave 84)."""
    return STYLE_BPM.get(style or "synthwave", 84)


@dataclass
class EditSession:
    source: str            # downloaded source clip path
    file_name: str         # original Telegram file name
    caption: str
    subject: str = ""
    vertical: bool = True  # vertical clips can bake a caption; landscape can't
    phase: str = "setup"   # "setup" (pre-render picker) → "review" (redo loop)
    caption_choices: list[str] = field(default_factory=list)  # Claude's ideas
    params: EditParams = field(default_factory=EditParams)
    revision: int = 0
    history: list[str] = field(default_factory=list)
    awaiting_feedback: bool = False
    past_captions: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)  # rendered revisions

    def summary(self) -> str:
        p = self.params
        target = "auto" if p.target is None else f"{p.target:.0f}s"
        if p.keep_audio:
            audio = "ambient"
        elif p.music_style:
            bpm = p.music_bpm or default_bpm(p.music_style)
            audio = f"{p.music_style}@{bpm}bpm#{p.music_seed}"
        else:
            audio = "muted"
        return (f'caption="{self.caption}" target={target} '
                f"style={p.style} caption_pos={p.caption_pos} "
                f"hook={p.hook} crop={p.crop} zoom={p.zoom:.2f} "
                f"audio={audio}")


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

    zoom = raw.get("zoom")
    if isinstance(zoom, (int, float)) and not isinstance(zoom, bool):
        out["zoom"] = round(min(MAX_ZOOM, max(MIN_ZOOM, float(zoom))), 3)

    pos = raw.get("caption_pos")
    if isinstance(pos, str) and pos in VALID_CAPTION_POS:
        out["caption_pos"] = pos

    style = raw.get("style")
    if isinstance(style, str) and style in STYLES:
        out["style"] = style

    for key in ("hook", "crop", "look", "keep_audio"):
        val = raw.get(key)
        if isinstance(val, bool):
            out[key] = val

    music = raw.get("music")
    if isinstance(music, str) and music in VALID_MUSIC:
        out["music_style"] = None if music == "off" else music

    bpm = raw.get("music_bpm")
    if isinstance(bpm, (int, float)) and not isinstance(bpm, bool):
        out["music_bpm"] = int(min(MAX_BPM, max(MIN_BPM, bpm)))

    if raw.get("new_music_mix") is True:
        out["new_music_mix"] = True

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
    if "zoom" in updates and updates["zoom"] != p.zoom:
        direction = "tighter" if updates["zoom"] > p.zoom else "wider"
        p.zoom = updates["zoom"]
        changes.append(f"framing → {p.zoom:.2f}x ({direction})")
    for key, label in (("hook", "hook"), ("crop", "auto-zoom"),
                       ("look", "color grade"),
                       ("keep_audio", "ambient audio")):
        if key in updates and updates[key] != getattr(p, key):
            setattr(p, key, updates[key])
            changes.append(f"{label} → {'on' if updates[key] else 'off'}")
    if "music_style" in updates and updates["music_style"] != p.music_style:
        p.music_style = updates["music_style"]
        changes.append(f"music → {p.music_style or 'off'}")
    if "music_bpm" in updates and updates["music_bpm"] != p.music_bpm:
        faster = updates["music_bpm"] > (p.music_bpm or default_bpm(
            p.music_style))
        p.music_bpm = updates["music_bpm"]
        changes.append(f"music tempo → {p.music_bpm} bpm "
                       f"({'faster' if faster else 'slower'})")
    if updates.get("new_music_mix"):
        p.music_seed += 1
        changes.append("music → fresh mix")
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
    if key == "tighter":
        return {"zoom": params.zoom * ZOOM_STEP}
    if key == "wider":
        return {"zoom": params.zoom / ZOOM_STEP}
    if key == "look":
        return {"look": not params.look}
    if key in ("phonk", "synthwave"):
        return {"music": key}
    if key == "nomusic":
        return {"music": "off"}
    if key in ("faster", "slower"):
        # tempo only matters with music on — enable phonk if it's off so
        # the tap is audible
        style = params.music_style or "phonk"
        base = params.music_bpm or default_bpm(style)
        factor = TEMPO_STEP if key == "faster" else 1.0 / TEMPO_STEP
        out: dict = {"music_bpm": round(base * factor)}
        if params.music_style is None:
            out["music"] = style
        return out
    if key == "remix":
        out = {"new_music_mix": True}
        if params.music_style is None:
            out["music"] = "phonk"
        return out
    if key == "style":
        order = list(STYLES)
        idx = order.index(params.style) if params.style in order else 0
        return {"style": order[(idx + 1) % len(order)]}
    if key == "newcaption":
        return {"regenerate_caption": True}
    return {}


def fallback_updates(feedback: str, params: EditParams) -> dict:
    """Tiny deterministic interpretation when Claude is unavailable."""
    base = params.target if params.target is not None else AUTO_SWEET
    low = feedback.lower()
    if "short" in low:
        return {"target": base * 0.8}
    if "long" in low:
        return {"target": base * 1.2}
    if any(w in low for w in ("wider", "zoom out", "too close",
                              "show more")):
        return {"zoom": params.zoom / ZOOM_STEP}
    if any(w in low for w in ("tighter", "zoom in", "closer", "zoom")):
        return {"zoom": params.zoom * ZOOM_STEP}
    if any(w in low for w in ("music", "beat", "track", "song", "tune")):
        style = params.music_style or "phonk"
        base = params.music_bpm or default_bpm(style)
        enable = {"music": style} if params.music_style is None else {}
        if any(w in low for w in ("fast", "quick", "hype", "energ")):
            return {"music_bpm": round(base * TEMPO_STEP), **enable}
        if any(w in low for w in ("slow", "chill", "calm")):
            return {"music_bpm": round(base / TEMPO_STEP), **enable}
        if any(w in low for w in ("different", "another", "new", "fresh",
                                  "change", "remix")):
            return {"new_music_mix": True, **enable}
    return {}
