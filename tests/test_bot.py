import pytest

from tokcut.bot.config import is_allowed, load_config
from tokcut.bot.pipeline import derive_caption, format_plan


def test_load_config_ok():
    cfg = load_config({"TELEGRAM_BOT_TOKEN": "tok",
                       "TOKCUT_ALLOWED_USER_ID": "42"})
    assert cfg.telegram_token == "tok"
    assert cfg.allowed_user_id == 42
    assert cfg.default_target is None  # auto length
    assert cfg.claude_judge is True


def test_load_config_target_auto_and_explicit():
    base = {"TELEGRAM_BOT_TOKEN": "t", "TOKCUT_ALLOWED_USER_ID": "1"}
    assert load_config({**base, "TOKCUT_TARGET": "auto"}).default_target \
        is None
    assert load_config({**base, "TOKCUT_TARGET": "45"}).default_target \
        == 45.0


def test_load_config_claude_off():
    cfg = load_config({"TELEGRAM_BOT_TOKEN": "tok",
                       "TOKCUT_ALLOWED_USER_ID": "42",
                       "TOKCUT_CLAUDE": "off"})
    assert cfg.claude_judge is False


def test_load_config_missing_token():
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        load_config({"TOKCUT_ALLOWED_USER_ID": "42"})


def test_load_config_missing_user():
    with pytest.raises(RuntimeError, match="TOKCUT_ALLOWED_USER_ID"):
        load_config({"TELEGRAM_BOT_TOKEN": "tok"})


def test_load_config_bad_user_id():
    with pytest.raises(RuntimeError, match="integer"):
        load_config({"TELEGRAM_BOT_TOKEN": "tok",
                     "TOKCUT_ALLOWED_USER_ID": "abc"})


def test_load_config_custom_target_and_workdir():
    cfg = load_config({
        "TELEGRAM_BOT_TOKEN": "t",
        "TOKCUT_ALLOWED_USER_ID": "1",
        "TOKCUT_TARGET": "40",
        "TOKCUT_WORKDIR": "/tmp/x",
    })
    assert cfg.default_target == 40.0
    assert cfg.workdir == "/tmp/x"


def test_load_config_bad_target():
    with pytest.raises(RuntimeError, match="TOKCUT_TARGET"):
        load_config({"TELEGRAM_BOT_TOKEN": "t",
                     "TOKCUT_ALLOWED_USER_ID": "1",
                     "TOKCUT_TARGET": "soon"})


def test_load_config_no_local_mode_by_default():
    cfg = load_config({"TELEGRAM_BOT_TOKEN": "t",
                       "TOKCUT_ALLOWED_USER_ID": "1"})
    assert cfg.local_mode is False
    assert cfg.bot_api_base_url == ""
    assert cfg.bot_api_base_file_url == ""
    assert cfg.max_file_mb == 50


def test_load_config_local_bot_api():
    cfg = load_config({
        "TELEGRAM_BOT_TOKEN": "t",
        "TOKCUT_ALLOWED_USER_ID": "1",
        "TOKCUT_BOT_API_URL": "http://127.0.0.1:8081/",  # trailing slash
    })
    assert cfg.local_mode is True
    assert cfg.bot_api_base_url == "http://127.0.0.1:8081/bot"
    assert cfg.bot_api_base_file_url == "http://127.0.0.1:8081/file/bot"
    assert cfg.max_file_mb == 2000


def test_load_config_bad_bot_api_url():
    with pytest.raises(RuntimeError, match="http"):
        load_config({"TELEGRAM_BOT_TOKEN": "t",
                     "TOKCUT_ALLOWED_USER_ID": "1",
                     "TOKCUT_BOT_API_URL": "127.0.0.1:8081"})


def test_is_allowed():
    assert is_allowed(42, 42)
    assert not is_allowed(7, 42)
    assert not is_allowed(None, 42)


def test_derive_caption_prefers_user_text():
    assert derive_caption("  my caption ⚡ ", "x.mp4") == "my caption ⚡"


def test_derive_caption_falls_back_to_filename():
    assert derive_caption(None, "my_demo-v2.mp4") == "my demo v2"
    assert derive_caption("", "btop.mp4") == "btop"


def test_derive_caption_last_resort():
    assert derive_caption(None, None) == "watch this ⚡"
    assert derive_caption(" ", "___.mp4") == "watch this ⚡"


def test_format_plan_renders_segments():
    src = {"w": 1038, "h": 1616, "duration": 95.5, "fps": 60, "audio": True}
    segs = [(0.0, 6.0, 3.2), (6.0, 10.0, 1.0)]
    text = format_plan(src, segs, 53.0)
    assert "53.0s" in text
    assert "2 segments" in text
    assert "1.00x" in text   # action segment
    assert "3.20x" in text   # fast segment
