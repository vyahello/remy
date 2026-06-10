"""Tests for the redo-session state and parameter validation."""

from tokcut.bot.session import (
    EditSession,
    apply_updates,
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


# ------------------------------------------------------------- fallback

def test_fallback_shorter_longer():
    assert fallback_updates("make it shorter", 50.0) == {"target": 40.0}
    assert fallback_updates("a bit longer please", 50.0) == {"target": 60.0}
    assert fallback_updates("different caption", 50.0) == {}
