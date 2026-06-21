"""Runtime feature flags for the bot — env-overridable and reversible.

Kept tiny and import-light (just os) so every module — including the pure,
Telegram-free ones — can read a flag without pulling in heavy deps.
"""

import os


def audio_enabled() -> bool:
    """Whether the music / ambient-audio features are offered.

    Audio is parked for now: exports are silent and the bot hides every
    music/ambient control, so a creator can't half-enable a feature we're
    still reworking. None of the audio CODE is removed — flip
    ``REMY_AUDIO=on`` (or 1/true/yes) to bring the whole music UI and
    baked-audio path straight back.
    """
    return os.environ.get("REMY_AUDIO", "").strip().lower() in (
        "1", "on", "true", "yes")
