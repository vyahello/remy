"""Tests for the redo-session state and parameter validation."""

from remy.bot.session import (
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

def test_validate_accepts_good_updates(monkeypatch):
    monkeypatch.setenv("REMY_AUDIO", "on")  # audio plumbing under test
    out = validate_updates({"caption": " new cap ", "target": 35,
                            "caption_pos": "top", "hook": False,
                            "crop": True, "keep_audio": True,
                            "music": "phonk"})
    assert out == {"caption": "new cap", "target": 35.0,
                   "caption_pos": "top", "hook": False, "crop": True,
                   "keep_audio": True, "music_style": "phonk"}


def test_validate_drops_audio_when_parked(monkeypatch):
    # default: audio is parked (REMY_AUDIO unset) → every audio update is
    # dropped, but the non-audio settings still come through
    monkeypatch.delenv("REMY_AUDIO", raising=False)
    out = validate_updates({"crop": True, "keep_audio": True,
                            "music": "phonk", "music_bpm": 140,
                            "new_music_mix": True})
    assert out == {"crop": True}


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


def test_validate_music_off_maps_to_none(monkeypatch):
    monkeypatch.setenv("REMY_AUDIO", "on")  # audio plumbing under test
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
    assert s.params.hook is False  # on by default, so turning off is a change
    assert len(changes) == 3


def test_apply_updates_noop_reports_nothing():
    s = _session()
    assert apply_updates(s, {"caption": "original caption",
                             "hook": True}) == []  # hook on is the default


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
    # "yellow" is a non-default style, so switching to it is a real change
    # (the default is now the dark-glass "black" style)
    s = _session()
    changes = apply_updates(s, {"style": "yellow"})
    assert s.params.style == "yellow"
    assert changes == ["caption style → yellow"]
    assert "style=yellow" in s.summary()


# ------------------------------------------------------------- cleanup

def test_cleanup_removes_source_and_outputs(tmp_path):
    src = tmp_path / "clip.mov"
    r1 = tmp_path / "clip_remy_r1.mp4"
    r2 = tmp_path / "clip_remy_r2.mp4"
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
    from remy.bot.session import EditParams
    p = EditParams(target=50.0)
    assert fallback_updates("make it shorter", p) == {"target": 40.0}
    assert fallback_updates("a bit longer please", p) == {"target": 60.0}
    assert fallback_updates("different caption", p) == {}


def test_fallback_zoom():
    from remy.bot.session import ZOOM_STEP, EditParams
    p = EditParams()
    assert fallback_updates("zoom in closer", p) == {"zoom": ZOOM_STEP}
    assert fallback_updates("too close, show more",
                            p) == {"zoom": 1.0 / ZOOM_STEP}


def test_fallback_caption_placement():
    from remy.bot.session import EditParams
    p = EditParams()
    assert fallback_updates("move the caption on my hand",
                            p) == {"caption_pos": "bottom"}
    assert fallback_updates("put it lower", p) == {"caption_pos": "bottom"}
    assert fallback_updates("move it up to the black bar",
                            p) == {"caption_pos": "top"}
    assert fallback_updates("find a clear spot for it",
                            p) == {"caption_pos": "auto"}


def test_post_copy_stale_only_on_content_change():
    from remy.bot.session import post_copy_stale
    # only a trim cuts content in/out -> regenerate the TikTok post copy
    assert post_copy_stale({"trim_start": 3.0})
    assert post_copy_stale({"trim_end": 2.0})
    # accepts a set of changed keys too (the redo staging path)
    assert post_copy_stale({"trim_end", "zoom"})
    # length / framing / caption / look / music / audio leave the subject
    # unchanged -> reuse the cached copy (no wasteful regeneration)
    assert not post_copy_stale({"caption": "new"})
    assert not post_copy_stale({"target": 30.0})
    assert not post_copy_stale({"crop": True})
    assert not post_copy_stale({"zoom": 1.3})
    assert not post_copy_stale({"hook": True})
    assert not post_copy_stale({"caption_pos": "bottom"})
    assert not post_copy_stale({"style": "yellow"})
    assert not post_copy_stale({"music_style": "phonk"})
    assert not post_copy_stale({"keep_audio": True})
    assert not post_copy_stale({})
    assert not post_copy_stale(set())


def test_validate_and_apply_trim():
    from remy.bot.session import EditSession, apply_updates, validate_updates
    out = validate_updates({"trim_start": 3.0, "trim_end": 90.0})
    assert out["trim_start"] == 3.0
    assert out["trim_end"] == 60.0  # clamped to MAX_TRIM
    assert validate_updates({"trim_start": -2.0})["trim_start"] == 0.0
    assert validate_updates({"trim_start": True}) == {}  # bool rejected
    s = EditSession(source="x", file_name="x", caption="c")
    changes = apply_updates(s, out)
    assert s.params.trim_start == 3.0 and s.params.trim_end == 60.0
    assert any("trim start" in c for c in changes)
    assert any("trim end" in c for c in changes)


def test_fallback_trim_parsing():
    from remy.bot.session import EditParams
    p = EditParams()
    assert fallback_updates("remove the first 3 seconds",
                            p) == {"trim_start": 3.0}
    assert fallback_updates("cut 4s off the end", p) == {"trim_end": 4.0}
    assert fallback_updates("trim the OBS intro", p)["trim_start"] > 0
    # both ends in one go
    both = fallback_updates("trim 2 seconds from the start and the end", p)
    assert both == {"trim_start": 2.0, "trim_end": 2.0}


def test_tweak_trim_buttons_add_a_step():
    from remy.bot.session import TRIM_STEP, EditParams, tweak_updates
    p = EditParams(trim_start=2.0)
    assert tweak_updates("trimstart", p) == {"trim_start": 2.0 + TRIM_STEP}
    assert tweak_updates("trimend", p) == {"trim_end": TRIM_STEP}


# ------------------------------------------------------------- tweaks

def test_tweak_updates_length():
    from remy.bot.session import EditParams, tweak_updates
    p = EditParams(target=40.0)
    assert tweak_updates("shorter", p) == {"target": 32.0}
    assert tweak_updates("longer", p) == {"target": 50.0}


def test_tweak_updates_auto_target_uses_sweet_spot():
    from remy.analysis import AUTO_SWEET
    from remy.bot.session import EditParams, tweak_updates
    p = EditParams()  # target None = auto
    assert tweak_updates("shorter", p) == {"target": AUTO_SWEET * 0.8}


def test_tweak_updates_toggles_and_music():
    from remy.bot.session import EditParams, tweak_updates
    p = EditParams()
    assert tweak_updates("hook", p) == {"hook": False}  # default on → off
    assert tweak_updates("crop", p) == {"crop": False}
    assert tweak_updates("phonk", p) == {"music": "phonk"}
    assert tweak_updates("nomusic", p) == {"music": "off"}


def test_tweak_updates_zoom_dial():
    from remy.bot.session import ZOOM_STEP, EditParams, tweak_updates
    p = EditParams()
    assert tweak_updates("tighter", p) == {"zoom": ZOOM_STEP}
    assert tweak_updates("wider", p) == {"zoom": 1.0 / ZOOM_STEP}
    p.zoom = 2.0
    assert tweak_updates("tighter", p) == {"zoom": 2.0 * ZOOM_STEP}


def test_validate_zoom_clamps():
    assert validate_updates({"zoom": 1.3}) == {"zoom": 1.3}
    assert validate_updates({"zoom": 99})["zoom"] == 2.5
    assert validate_updates({"zoom": 0.1})["zoom"] == 0.5
    assert validate_updates({"zoom": True}) == {}
    assert validate_updates({"zoom": "big"}) == {}


def test_apply_zoom_describes_direction():
    s = _session()
    changes = apply_updates(s, {"zoom": 1.15})
    assert s.params.zoom == 1.15
    assert changes == ["framing → 1.15x (tighter)"]
    assert apply_updates(s, {"zoom": 1.0}) == ["framing → 1.00x (wider)"]


def test_tweak_updates_style_cycles():
    from remy.bot.session import EditParams, tweak_updates
    from remy.caption import STYLES
    order = list(STYLES)
    p = EditParams(style=order[0])
    assert tweak_updates("style", p) == {"style": order[1]}
    p.style = order[-1]
    assert tweak_updates("style", p) == {"style": order[0]}


def test_tweak_updates_unknown_key():
    from remy.bot.session import EditParams, tweak_updates
    assert tweak_updates("explode", EditParams()) == {}


def test_tweaks_pass_validation(monkeypatch):
    # every tweak button the UI can emit must survive validation; the music
    # buttons only exist when audio is enabled, so test with it on
    monkeypatch.setenv("REMY_AUDIO", "on")
    from remy.bot.session import EditParams, tweak_updates
    p = EditParams(target=15.0)
    for key in ("shorter", "longer", "tighter", "wider", "hook", "crop",
                "phonk", "synthwave", "faster", "slower", "remix",
                "nomusic", "style", "newcaption"):
        raw = tweak_updates(key, p)
        assert validate_updates(raw), f"{key} produced nothing valid"


# ------------------------------------------------------- music tempo/mix

def test_tweak_faster_slower_sets_bpm_and_enables_music():
    from remy.bot.session import EditParams, default_bpm, tweak_updates
    p = EditParams()  # music off
    up = tweak_updates("faster", p)
    assert up["music"] == "phonk"                  # enabled so it's audible
    assert up["music_bpm"] > default_bpm("phonk")  # faster than default
    p2 = EditParams(music_style="synthwave", music_bpm=84)
    assert tweak_updates("slower", p2)["music_bpm"] < 84
    assert "music" not in tweak_updates("slower", p2)  # already on


def test_tweak_remix_bumps_mix():
    from remy.bot.session import EditParams, tweak_updates
    assert tweak_updates("remix", EditParams(music_style="phonk")) == {
        "new_music_mix": True}
    # off -> also enable
    assert tweak_updates("remix", EditParams())["music"] == "phonk"


def test_validate_music_bpm_clamps(monkeypatch):
    monkeypatch.setenv("REMY_AUDIO", "on")  # audio plumbing under test
    assert validate_updates({"music_bpm": 140})["music_bpm"] == 140
    assert validate_updates({"music_bpm": 999})["music_bpm"] == 180
    assert validate_updates({"music_bpm": 10})["music_bpm"] == 60
    assert validate_updates({"music_bpm": True}) == {}
    assert validate_updates({"new_music_mix": True}) == {"new_music_mix": True}
    assert validate_updates({"new_music_mix": False}) == {}


def test_apply_music_bpm_and_mix():
    from remy.bot.session import EditParams, EditSession
    s = EditSession(source="x", file_name="x.mp4", caption="c",
                    params=EditParams(music_style="phonk"))
    ch = apply_updates(s, {"music_bpm": 150})
    assert s.params.music_bpm == 150
    assert "faster" in ch[0]
    seed0 = s.params.music_seed
    apply_updates(s, {"new_music_mix": True})
    assert s.params.music_seed == seed0 + 1


def test_fallback_music_tempo_and_mix():
    from remy.bot.session import EditParams, default_bpm
    p = EditParams(music_style="phonk", music_bpm=None)
    assert fallback_updates("make the music faster", p)["music_bpm"] > \
        default_bpm("phonk")
    assert fallback_updates("different beat please", p) == {
        "new_music_mix": True}
    # plain "faster" without a music word stays out of music territory
    assert "music_bpm" not in fallback_updates("faster", p)


def test_caption_mode_toggle():
    from remy.bot.session import tweak_updates, validate_updates
    p = _session().params
    assert p.caption_mode == "static"
    upd = tweak_updates("captionmode", p)          # button flips it
    assert upd == {"caption_mode": "dynamic"}
    assert validate_updates(upd) == {"caption_mode": "dynamic"}
    # invalid modes are dropped by the gate
    assert validate_updates({"caption_mode": "sideways"}) == {}


def test_apply_caption_mode_change():
    s = _session()
    changes = apply_updates(s, {"caption_mode": "dynamic"})
    assert s.params.caption_mode == "dynamic"
    assert changes == ["caption mode → dynamic"]
    assert "caption_mode=dynamic" in s.summary()
