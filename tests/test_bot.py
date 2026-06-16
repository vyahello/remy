import pytest

from remy.bot.config import is_allowed, load_config
from remy.bot.pipeline import derive_caption, format_plan


def test_load_config_ok():
    cfg = load_config({"TELEGRAM_BOT_TOKEN": "tok",
                       "REMY_ALLOWED_USER_ID": "42"})
    assert cfg.telegram_token == "tok"
    assert cfg.allowed_user_id == 42
    assert cfg.default_target is None  # auto length
    assert cfg.claude_judge is True


def test_load_config_legacy_tokcut_env_still_works():
    # a server provisioned before the Remy rebrand sets TOKCUT_* — these
    # must keep working as a fallback so deploys don't need an env rewrite
    cfg = load_config({"TELEGRAM_BOT_TOKEN": "tok",
                       "TOKCUT_ALLOWED_USER_ID": "42",
                       "TOKCUT_TARGET": "45",
                       "TOKCUT_CLAUDE": "off"})
    assert cfg.allowed_user_id == 42
    assert cfg.default_target == 45.0
    assert cfg.claude_judge is False


def test_load_config_remy_env_takes_precedence_over_legacy():
    cfg = load_config({"TELEGRAM_BOT_TOKEN": "tok",
                       "REMY_ALLOWED_USER_ID": "7",
                       "TOKCUT_ALLOWED_USER_ID": "42"})
    assert cfg.allowed_user_id == 7  # REMY_ wins when both are set


def test_load_config_target_auto_and_explicit():
    base = {"TELEGRAM_BOT_TOKEN": "t", "REMY_ALLOWED_USER_ID": "1"}
    assert load_config({**base, "REMY_TARGET": "auto"}).default_target \
        is None
    assert load_config({**base, "REMY_TARGET": "45"}).default_target \
        == 45.0


def test_load_config_claude_off():
    cfg = load_config({"TELEGRAM_BOT_TOKEN": "tok",
                       "REMY_ALLOWED_USER_ID": "42",
                       "REMY_CLAUDE": "off"})
    assert cfg.claude_judge is False


def test_load_config_missing_token():
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        load_config({"REMY_ALLOWED_USER_ID": "42"})


def test_load_config_missing_user():
    with pytest.raises(RuntimeError, match="REMY_ALLOWED_USER_ID"):
        load_config({"TELEGRAM_BOT_TOKEN": "tok"})


def test_load_config_bad_user_id():
    with pytest.raises(RuntimeError, match="integer"):
        load_config({"TELEGRAM_BOT_TOKEN": "tok",
                     "REMY_ALLOWED_USER_ID": "abc"})


def test_load_config_custom_target_and_workdir():
    cfg = load_config({
        "TELEGRAM_BOT_TOKEN": "t",
        "REMY_ALLOWED_USER_ID": "1",
        "REMY_TARGET": "40",
        "REMY_WORKDIR": "/tmp/x",
    })
    assert cfg.default_target == 40.0
    assert cfg.workdir == "/tmp/x"


def test_load_config_bad_target():
    with pytest.raises(RuntimeError, match="REMY_TARGET"):
        load_config({"TELEGRAM_BOT_TOKEN": "t",
                     "REMY_ALLOWED_USER_ID": "1",
                     "REMY_TARGET": "soon"})


def test_load_config_no_local_mode_by_default():
    cfg = load_config({"TELEGRAM_BOT_TOKEN": "t",
                       "REMY_ALLOWED_USER_ID": "1"})
    assert cfg.local_mode is False
    assert cfg.bot_api_base_url == ""
    assert cfg.bot_api_base_file_url == ""
    assert cfg.max_file_mb == 50


def test_load_config_local_bot_api():
    cfg = load_config({
        "TELEGRAM_BOT_TOKEN": "t",
        "REMY_ALLOWED_USER_ID": "1",
        "REMY_BOT_API_URL": "http://127.0.0.1:8081/",  # trailing slash
    })
    assert cfg.local_mode is True
    assert cfg.bot_api_base_url == "http://127.0.0.1:8081/bot"
    assert cfg.bot_api_base_file_url == "http://127.0.0.1:8081/file/bot"
    assert cfg.max_file_mb == 2000


def test_load_config_bad_bot_api_url():
    with pytest.raises(RuntimeError, match="http"):
        load_config({"TELEGRAM_BOT_TOKEN": "t",
                     "REMY_ALLOWED_USER_ID": "1",
                     "REMY_BOT_API_URL": "127.0.0.1:8081"})


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


def test_friendly_progress_translates_key_lines():
    from remy.bot.pipeline import friendly_progress as fp
    plan = ("edit plan (8 segments, ~50.0s output):\n"
            "   72.15 -   73.45  HOOK   1.0x (cold open)")
    assert fp(plan) == "✂️ cutting to ~50s (8 pieces)"
    assert "native resolution" in fp("landscape source: native ...")
    assert fp("rendering…").startswith("🎬")
    assert fp("audio: muted (add a TikTok sound in-app)").startswith("🔇")
    assert fp("music: synthesized phonk @ 132bpm") == \
        "🎵 synthesized phonk @ 132bpm"
    assert fp("beat-align: cuts snapped to the 132bpm grid").startswith("🥁")


def test_friendly_progress_hides_technical_lines():
    from remy.bot.pipeline import friendly_progress as fp
    assert fp("source: 1920x1080  76.5s @ 60fps (bt709 transfer)") is None
    assert fp("caption at y=1277 (auto)") is None
    assert fp("   8.33 -   41.50  FAST  2.19x") is None


def test_load_config_preset():
    base = {"TELEGRAM_BOT_TOKEN": "t", "REMY_ALLOWED_USER_ID": "1"}
    assert load_config(base).preset == "medium"
    assert load_config({**base, "REMY_PRESET": "fast"}).preset == "fast"
    with pytest.raises(RuntimeError, match="REMY_PRESET"):
        load_config({**base, "REMY_PRESET": "warp9"})


def test_delivery_name_uses_original_stem():
    from remy.bot.pipeline import delivery_name
    assert delivery_name("gping demo.mp4", 2) == "gping_demo_take2.mp4"


def test_delivery_name_falls_back_to_date():
    import datetime

    from remy.bot.pipeline import delivery_name
    assert delivery_name("", 1) == (
        f"remy_{datetime.date.today():%Y-%m-%d}_take1.mp4")


def test_sweep_workdir_removes_files_keeps_marker(tmp_path):
    from remy.bot.pipeline import sweep_workdir
    (tmp_path / "clip.mp4").write_bytes(b"x" * 100)
    (tmp_path / "clip_remy_r1.mp4").write_bytes(b"y" * 50)
    (tmp_path / ".rendering").write_text("123")
    (tmp_path / "subdir").mkdir()
    removed, freed = sweep_workdir(str(tmp_path))
    assert removed == 2
    assert freed == 150
    assert (tmp_path / ".rendering").exists()   # deploy-drain marker kept
    assert (tmp_path / "subdir").exists()        # dirs untouched
    assert not (tmp_path / "clip.mp4").exists()


def test_sweep_workdir_missing_dir():
    from remy.bot.pipeline import sweep_workdir
    assert sweep_workdir("/nonexistent/remy/work") == (0, 0)


# ------------------------------------------------------ setup-phase picker

def _setup_session(vertical: bool, caption: str = "",
                   choices=("idea one", "idea two")):
    from remy.bot.session import EditSession
    return EditSession(source="x.mp4", file_name="x.mp4", caption=caption,
                       vertical=vertical, caption_choices=list(choices))


def _datas(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row]


def _app():
    # remy.bot.app imports python-telegram-bot (the [bot] extra); skip the
    # picker tests cleanly when only [dev] is installed.
    pytest.importorskip("telegram")
    from remy.bot import app
    return app


def test_setup_keyboard_vertical_has_caption_choices_and_render():
    app = _app()
    kb = app.setup_keyboard(_setup_session(vertical=True))
    datas = _datas(kb)
    assert app.SETCAP + "0" in datas and app.SETCAP + "1" in datas
    assert app.OWNCAP in datas and app.NOCAP in datas
    assert app.OPT + "hook" in datas and app.RENDER in datas
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Cold open: off" in t for t in labels)  # default off, in words
    # exactly one caption choice is marked active; default = No caption
    assert any(t == "✅ No caption" for t in labels)
    assert any(t == "✅ Mute" for t in labels)  # music muted by default


def test_setup_keyboard_landscape_has_no_caption_buttons():
    app = _app()
    kb = app.setup_keyboard(_setup_session(vertical=False))
    datas = _datas(kb)
    assert not any(d.startswith(app.SETCAP) for d in datas)
    assert app.OWNCAP not in datas and app.NOCAP not in datas
    assert app.RENDER in datas and app.OPT + "hook" in datas


def test_setup_keyboard_marks_selected_caption():
    app = _app()
    kb = app.setup_keyboard(_setup_session(vertical=True, caption="idea one"))
    first = kb.inline_keyboard[0][0]
    assert first.callback_data == app.SETCAP + "0" and "✅" in first.text


def test_setup_text_reflects_state():
    app = _app()
    s = _setup_session(vertical=True)
    assert "caption: none" in app.setup_text(s)
    assert "cold open off" in app.setup_text(s)
    s.caption = "my cap"
    assert "my cap" in app.setup_text(s)
    assert "no baked caption" in app.setup_text(_setup_session(vertical=False))


def test_format_post_kit_combines_description_and_tags():
    app = _app()
    out = app.format_post_kit(
        {"description": "Quick IPython demo ⚡", "hashtags": ["#ipython",
                                                             "#python"]})
    # bare body (no heading) so it can go inside a copyable <pre> block
    assert out.startswith("Quick IPython demo ⚡")
    assert "#ipython #python" in out


def test_format_post_kit_empty_returns_blank():
    app = _app()
    assert app.format_post_kit({"description": "", "hashtags": []}) == ""


def test_session_defaults_to_setup_phase_hook_off():
    from remy.bot.session import EditParams, EditSession
    assert EditParams().hook is False  # cold open opt-in
    s = EditSession(source="x", file_name="x", caption="")
    assert s.phase == "setup" and s.vertical is True
    assert s.caption_choices == []
