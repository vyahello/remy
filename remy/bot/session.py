"""Per-chat edit session state and parameter validation.

Pure Python — no Telegram, no Claude — so the redo logic is testable.
Claude proposes parameter updates as a loose JSON dict; validate_updates
is the hard gate that clamps and whitelists them before they touch the
render.
"""

import os
import re
from dataclasses import dataclass, field

from ..analysis import AUTO_SWEET
from ..caption import DEFAULT_STYLE, STYLES
from ..music import STYLE_BPM
from .features import audio_enabled

VALID_CAPTION_POS = ("auto", "top", "bottom")
# static = one persistent caption for the whole video; dynamic = a short
# label per section that changes through the clip (a guided walkthrough,
# the classic tutorial-TikTok caption style)
VALID_CAPTION_MODE = ("static", "dynamic")
VALID_MUSIC = ("synthwave", "phonk", "off")
MIN_TARGET, MAX_TARGET = 10.0, 120.0
MIN_ZOOM, MAX_ZOOM = 0.5, 2.5
ZOOM_STEP = 1.15  # one tap of Tighter/Wider
MAX_TRIM = 60.0   # most seconds a single head/tail cut may remove
TRIM_STEP = 1.0   # one tap of Trim start/end
MIN_BPM, MAX_BPM = 60, 180
TEMPO_STEP = 1.12  # one tap of Faster/Slower beat


@dataclass
class EditParams:
    target: float | None = None  # None = auto (TikTok-friendly length)
    style: str = DEFAULT_STYLE
    caption_pos: str = "auto"
    caption_mode: str = "static"  # "static" one line | "dynamic" step labels
    hook: bool = False  # cold-open teaser — opt-in (default off)
    trim_start: float = 0.0  # secs hard-cut off the source head (intro)
    trim_end: float = 0.0    # secs hard-cut off the source tail (outro)
    # source-second spans of mistyped commands / errors to delete outright
    # (judge.detect_mistakes); kept off the head/tail trim path
    mistake_cuts: list[tuple[float, float]] = field(default_factory=list)
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
    # judge.detect_payoff at upload: the demo span (source seconds) is
    # pinned to 1.0x + seeds the cold-open teaser; the line rides the card
    payoff: tuple[float, float] | None = None
    hook_line: str = ""
    params: EditParams = field(default_factory=EditParams)
    revision: int = 0
    history: list[str] = field(default_factory=list)
    awaiting_feedback: bool = False
    past_captions: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)  # rendered revisions
    post_kit: str = ""  # cached TikTok post copy; only re-asked on a
    #                     content change (a trim that cuts what's shown)
    # Redo staging: buttons stack changes here instead of rendering each
    # tap, so several tweaks ("longer + tighter + zoom off") render once.
    staged_keys: set[str] = field(default_factory=set)   # validated keys
    staged_notes: list[str] = field(default_factory=list)  # human changes

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
        trim = ""
        if p.trim_start or p.trim_end:
            trim = f" trim=-{p.trim_start:.1f}s/-{p.trim_end:.1f}s"
        return (f'caption="{self.caption}" target={target} '
                f"style={p.style} caption_pos={p.caption_pos} "
                f"caption_mode={p.caption_mode} "
                f"hook={p.hook} crop={p.crop} zoom={p.zoom:.2f} "
                f"audio={audio}{trim}")


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

    for key in ("trim_start", "trim_end"):
        val = raw.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            out[key] = round(min(MAX_TRIM, max(0.0, float(val))), 2)

    pos = raw.get("caption_pos")
    if isinstance(pos, str) and pos in VALID_CAPTION_POS:
        out["caption_pos"] = pos

    mode = raw.get("caption_mode")
    if isinstance(mode, str) and mode in VALID_CAPTION_MODE:
        out["caption_mode"] = mode

    style = raw.get("style")
    if isinstance(style, str) and style in STYLES:
        out["style"] = style

    for key in ("hook", "crop", "look"):
        val = raw.get(key)
        if isinstance(val, bool):
            out[key] = val

    # Audio (music + ambient) is parked unless REMY_AUDIO is set. While it's
    # off, drop every audio update here — the one hard gate — so a stray
    # "add phonk" from a button or free text can't bake a track onto an
    # export that's meant to stay silent. The music CODE stays intact.
    if audio_enabled():
        if isinstance(raw.get("keep_audio"), bool):
            out["keep_audio"] = raw["keep_audio"]

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
    if ("caption_mode" in updates
            and updates["caption_mode"] != p.caption_mode):
        p.caption_mode = updates["caption_mode"]
        changes.append(f"caption mode → {p.caption_mode}")
    if "style" in updates and updates["style"] != p.style:
        p.style = updates["style"]
        changes.append(f"caption style → {p.style}")
    if "zoom" in updates and updates["zoom"] != p.zoom:
        direction = "tighter" if updates["zoom"] > p.zoom else "wider"
        p.zoom = updates["zoom"]
        changes.append(f"framing → {p.zoom:.2f}x ({direction})")
    if "trim_start" in updates and updates["trim_start"] != p.trim_start:
        p.trim_start = updates["trim_start"]
        changes.append(f"trim start → {p.trim_start:.1f}s")
    if "trim_end" in updates and updates["trim_end"] != p.trim_end:
        p.trim_end = updates["trim_end"]
        changes.append(f"trim end → {p.trim_end:.1f}s")
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


# Validated-update keys that change *what the video is about* — the only
# thing the TikTok post copy (an educational blurb + hashtags grounded in
# the subject) actually depends on. Only a trim cuts content in or out;
# length, framing (zoom/crop), the on-video caption, cold open, look and
# music all leave the subject untouched, so they reuse the cached copy
# (no wasteful regeneration — the user explicitly flagged that double-job).
POST_COPY_KEYS = frozenset({"trim_start", "trim_end"})


def post_copy_stale(updates) -> bool:
    """True if a change alters what the video is about (a trim), so the
    cached TikTok post copy must be regenerated. Accepts a dict of updates
    or any iterable of changed keys."""
    keys = updates.keys() if isinstance(updates, dict) else set(updates)
    return bool(POST_COPY_KEYS & keys)


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
    if key == "trimstart":
        return {"trim_start": params.trim_start + TRIM_STEP}
    if key == "trimend":
        return {"trim_end": params.trim_end + TRIM_STEP}
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
    if key == "captionmode":
        nxt = "dynamic" if params.caption_mode == "static" else "static"
        return {"caption_mode": nxt}
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
    if any(w in low for w in ("trim", "cut", "remove", "chop", "drop",
                              "intro", "outro")):
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:s\b|sec|second)", low) \
            or re.search(r"(\d+(?:\.\d+)?)", low)
        secs = float(m.group(1)) if m else TRIM_STEP * 2
        head = any(w in low for w in ("start", "begin", "first", "intro",
                                      "front", "opening", "head"))
        tail = any(w in low for w in ("end", "last", "outro", "finish",
                                      "ending", "tail"))
        out: dict = {}
        if head:
            out["trim_start"] = secs
        if tail:
            out["trim_end"] = secs
        if not head and not tail:  # direction unclear → assume the intro
            out["trim_start"] = secs
        return out
    if any(w in low for w in ("bottom", "lower", "move it down", "on my hand",
                              "over the keyboard", "below")):
        return {"caption_pos": "bottom"}
    if any(w in low for w in ("at the top", "move it up", "higher",
                              "black bar")):
        return {"caption_pos": "top"}
    if any(w in low for w in ("clear spot", "out of the way", "off the text",
                              "calmest")):
        return {"caption_pos": "auto"}
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
