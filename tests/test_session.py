"""Tests for the redo-session state and parameter validation."""

from tokcut.bot.session import (
    EditSession,
    apply_updates,
    cleanup_files,
    fallback_updates,
    validate_updates,
)


def _session() -> EditSession:
    return EditSession(source="/tmp/x.mp4", file_name="x.mp4",
                       caption="original caption")


# ------------------------------------------------------------- validate

def test_validate_accepts_good_updates():
    out = validate_updates({"caption": " new cap ", "target": 35,
                            "caption_pos": "top", "hook": False,
                            "crop": True, "keep_audio": True,
                            "music": "phonk"})
    assert out == {"caption": "new cap", "target": 35.0,
                   "caption_pos": "top", "hook": False, "crop": True,
                   "keep_audio": True, "music_style": "phonk"}


def test_validate_clamps_target():
    assert validate_updates({"target": 3})["target"] == 10.0
    assert validate_updates({"target": 500})["target"] == 120.0


def test_validate_drops_nulls_and_junk():
    out = validate_updates({"caption": None, "target": None,
                            "caption_pos": "sideways", "hook": "yes",
                            "music": "dubstep", "extra": 1})
    assert out == {}


def test_validate_target_bool_rejected():
    # True is an int subclass — must not be accepted as a duration
    assert validate_updates({"target": True}) == {}


def test_validate_music_off_maps_to_none():
    assert validate_updates({"music": "off"}) == {"music_style": None}


def test_validate_regenerate_flag():
    assert validate_updates({"regenerate_caption": True}) == {
        "regenerate_caption": True}
    assert validate_updates({"regenerate_caption": False}) == {}


# ---------------------------------------------------------------- apply

def test_apply_updates_changes_and_describes():
    s = _session()
    changes = apply_updates(s, {"caption": "better caption", "target": 30.0,
                                "hook": False})
    assert s.caption == "better caption"
    assert s.past_captions == ["original caption"]
    assert s.params.target == 30.0
    assert s.params.hook is False
    assert len(changes) == 3


def test_apply_updates_noop_reports_nothing():
    s = _session()
    assert apply_updates(s, {"caption": "original caption",
                             "hook": True}) == []


def test_session_summary_mentions_state():
    s = _session()
    s.params.music_style = "phonk"
    text = s.summary()
    assert "original caption" in text
    assert "phonk" in text


def test_validate_style():
    assert validate_updates({"style": "yellow"}) == {"style": "yellow"}
    assert validate_updates({"style": "comic-sans"}) == {}
    assert validate_updates({"style": 7}) == {}


def test_apply_style_change():
    s = _session()
    changes = apply_updates(s, {"style": "black"})
    assert s.params.style == "black"
    assert changes == ["caption style → black"]
    assert "style=black" in s.summary()


# ------------------------------------------------------------- cleanup

def test_cleanup_removes_source_and_outputs(tmp_path):
    src = tmp_path / "clip.mov"
    r1 = tmp_path / "clip_tokcut_r1.mp4"
    r2 = tmp_path / "clip_tokcut_r2.mp4"
    for f in (src, r1, r2):
        f.write_bytes(b"x" * 100)
    s = EditSession(source=str(src), file_name="clip.mov", caption="c",
                    outputs=[str(r1), str(r2)])
    removed, freed = cleanup_files(s)
    assert removed == 3
    assert freed == 300
    assert not src.exists() and not r1.exists() and not r2.exists()


def test_cleanup_tolerates_missing_files(tmp_path):
    r1 = tmp_path / "only_render.mp4"
    r1.write_bytes(b"x" * 7)
    s = EditSession(source=str(tmp_path / "gone.mov"), file_name="g.mov",
                    caption="c", outputs=[str(r1), "/nonexistent/r2.mp4"])
    removed, freed = cleanup_files(s)
    assert removed == 1
    assert freed == 7
    assert not r1.exists()


# ------------------------------------------------------------- fallback

def test_fallback_shorter_longer():
    assert fallback_updates("make it shorter", 50.0) == {"target": 40.0}
    assert fallback_updates("a bit longer please", 50.0) == {"target": 60.0}
    assert fallback_updates("different caption", 50.0) == {}


# ------------------------------------------------------------- tweaks

def test_tweak_updates_length():
    from tokcut.bot.session import EditParams, tweak_updates
    p = EditParams(target=40.0)
    assert tweak_updates("shorter", p) == {"target": 32.0}
    assert tweak_updates("longer", p) == {"target": 50.0}


def test_tweak_updates_auto_target_uses_sweet_spot():
    from tokcut.analysis import AUTO_SWEET
    from tokcut.bot.session import EditParams, tweak_updates
    p = EditParams()  # target None = auto
    assert tweak_updates("shorter", p) == {"target": AUTO_SWEET * 0.8}


def test_tweak_updates_toggles_and_music():
    from tokcut.bot.session import EditParams, tweak_updates
    p = EditParams()
    assert tweak_updates("hook", p) == {"hook": False}
    assert tweak_updates("crop", p) == {"crop": False}
    assert tweak_updates("phonk", p) == {"music": "phonk"}
    assert tweak_updates("nomusic", p) == {"music": "off"}


def test_tweak_updates_style_cycles():
    from tokcut.bot.session import EditParams, tweak_updates
    from tokcut.caption import STYLES
    order = list(STYLES)
    p = EditParams(style=order[0])
    assert tweak_updates("style", p) == {"style": order[1]}
    p.style = order[-1]
    assert tweak_updates("style", p) == {"style": order[0]}


def test_tweak_updates_unknown_key():
    from tokcut.bot.session import EditParams, tweak_updates
    assert tweak_updates("explode", EditParams()) == {}


def test_tweaks_pass_validation():
    from tokcut.bot.session import EditParams, tweak_updates
    p = EditParams(target=15.0)
    for key in ("shorter", "longer", "hook", "crop", "phonk",
                "synthwave", "nomusic", "style", "newcaption"):
        raw = tweak_updates(key, p)
        assert validate_updates(raw), f"{key} produced nothing valid"
