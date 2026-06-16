"""Bot configuration from environment + the allow-list check.

No Telegram or Claude imports here so it stays trivially testable.

Env vars are read under the ``REMY_`` prefix, falling back to the legacy
``TOKCUT_`` names so a server provisioned before the rebrand keeps working
without touching its ``/etc/…/env`` file. ``TELEGRAM_*`` and
``CLAUDE_CODE_OAUTH_TOKEN`` are unprefixed and unchanged.
"""

import os
from dataclasses import dataclass

DEFAULT_WORKDIR = os.path.expanduser("~/.remy/work")


@dataclass(frozen=True)
class BotConfig:
    telegram_token: str
    allowed_user_id: int
    workdir: str
    default_target: float | None  # None = auto (TikTok-friendly length)
    claude_judge: bool
    # x265 preset — "medium" for quality boxes, "fast"/"faster" to halve
    # encode times on small shared VPSes (marginal quality cost at crf 18)
    preset: str = "medium"
    # Local Bot API server (step 5) — empty unless REMY_BOT_API_URL is set.
    # When set, the bot talks to a self-hosted telegram-bot-api instance,
    # lifting the 50 MB up/download cap to 2 GB so full iPhone clips work.
    bot_api_base_url: str = ""
    bot_api_base_file_url: str = ""
    local_mode: bool = False

    @property
    def max_file_mb(self) -> int:
        """Telegram's up/download cap for the active API endpoint."""
        return 2000 if self.local_mode else 50


def _get(src: dict[str, str], suffix: str, default: str = "") -> str:
    """Read REMY_<suffix>, falling back to the legacy TOKCUT_<suffix>."""
    val = src.get("REMY_" + suffix)
    if val is None:
        val = src.get("TOKCUT_" + suffix)
    return default if val is None else val


def load_config(env: dict[str, str] | None = None) -> BotConfig:
    """Build a BotConfig from environment variables.

    Raises RuntimeError with an actionable message on missing/invalid vars.
    """
    src = dict(os.environ if env is None else env)

    token = src.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    raw_id = _get(src, "ALLOWED_USER_ID").strip()
    if not raw_id:
        raise RuntimeError("REMY_ALLOWED_USER_ID is not set")
    try:
        allowed_user_id = int(raw_id)
    except ValueError as exc:
        raise RuntimeError(
            "REMY_ALLOWED_USER_ID must be an integer Telegram user id"
        ) from exc

    workdir = os.path.expanduser(
        _get(src, "WORKDIR").strip() or DEFAULT_WORKDIR)

    target_raw = _get(src, "TARGET").strip().lower()
    default_target: float | None = None  # auto: solved from the content
    if target_raw and target_raw != "auto":
        try:
            default_target = float(target_raw)
        except ValueError as exc:
            raise RuntimeError(
                'REMY_TARGET must be a number or "auto"') from exc

    claude_judge = _get(src, "CLAUDE", "on").strip().lower() not in (
        "off", "0", "false")

    preset = _get(src, "PRESET").strip().lower() or "medium"
    if preset not in ("ultrafast", "superfast", "veryfast", "faster",
                      "fast", "medium", "slow", "slower", "veryslow"):
        raise RuntimeError(f"REMY_PRESET: unknown x265 preset {preset!r}")

    api_url = _get(src, "BOT_API_URL").strip().rstrip("/")
    base_url = base_file_url = ""
    local_mode = False
    if api_url:
        if not api_url.startswith(("http://", "https://")):
            raise RuntimeError(
                "REMY_BOT_API_URL must be an http(s) URL, e.g. "
                "http://127.0.0.1:8081")
        base_url = f"{api_url}/bot"
        base_file_url = f"{api_url}/file/bot"
        local_mode = True

    return BotConfig(token, allowed_user_id, workdir, default_target,
                     claude_judge, preset, base_url, base_file_url,
                     local_mode)


def is_allowed(user_id: int | None, allowed_user_id: int) -> bool:
    """True only for the single allow-listed user. The bot is private."""
    return user_id is not None and user_id == allowed_user_id
