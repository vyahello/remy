"""Tests for the deterministic parts of the Claude judgment layer."""

import pytest

from remy import judge as J


def test_spread_times_even_and_bounded():
    times = J.spread_times(100.0, n=6)
    assert len(times) == 6
    assert times[0] == pytest.approx(5.0)
    assert times[-1] == pytest.approx(95.0)
    assert times == sorted(times)


def test_spread_times_single():
    assert J.spread_times(10.0, n=1) == [5.0]


def test_parse_json_obj_plain():
    assert J.parse_json_obj('{"a": 1}') == {"a": 1}


def test_parse_json_obj_chatty_reply():
    text = 'Sure! Here is the JSON:\n{"caption": "x", "n": 2}\nHope it helps'
    assert J.parse_json_obj(text)["caption"] == "x"


def test_parse_json_obj_no_json_raises():
    with pytest.raises(ValueError):
        J.parse_json_obj("no json here")


def test_pick_valid_caption_first_clean_wins():
    out = J.pick_valid_caption(["hacking your wifi", "btop on Linux 👀"])
    assert out == "btop on Linux 👀"


def test_pick_valid_caption_strips_quotes():
    assert J.pick_valid_caption(['"quoted caption"']) == "quoted caption"


def test_pick_valid_caption_rejects_overlong():
    assert J.pick_valid_caption(["x" * 100]) is None


def test_pick_valid_caption_all_bad_none():
    assert J.pick_valid_caption(["", "hack the planet"]) is None


def test_run_claude_unavailable(monkeypatch):
    monkeypatch.setattr(J, "claude_available", lambda: False)
    with pytest.raises(J.JudgeUnavailable):
        J.run_claude("hi")


def test_run_claude_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(J, "claude_available", lambda: True)
    monkeypatch.setattr(J.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def flaky(prompt, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise J.JudgeUnavailable("transient blip")
        return "recovered"

    monkeypatch.setattr(J, "_run_claude_once", flaky)
    assert J.run_claude("hi") == "recovered"
    assert calls["n"] == 2  # failed once, retried once


def test_run_claude_gives_up_after_attempts(monkeypatch):
    monkeypatch.setattr(J, "claude_available", lambda: True)
    monkeypatch.setattr(J.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def always_fail(prompt, timeout):
        calls["n"] += 1
        raise J.JudgeUnavailable("still down")

    monkeypatch.setattr(J, "_run_claude_once", always_fail)
    with pytest.raises(J.JudgeUnavailable, match="still down"):
        J.run_claude("hi")
    assert calls["n"] == J.CLAUDE_ATTEMPTS


# ----------------------------------------------------------- post copy

def test_clean_hashtags_normalizes_and_dedupes():
    out = J.clean_hashtags(["#IPython", "python coding", "#python", "#tech!"])
    assert out == ["#ipython", "#pythoncoding", "#python", "#tech"]


def test_clean_hashtags_drops_flagged_terms():
    # 'hack' is a moderation-flagged term — must not survive as a hashtag
    assert "#hacking" not in J.clean_hashtags(["#hacking", "#coding"])
    assert J.clean_hashtags(["#coding"]) == ["#coding"]


def test_clean_hashtags_caps_at_five_keeping_order():
    many = [f"#tag{i}" for i in range(20)]
    out = J.clean_hashtags(many)
    assert out == ["#tag0", "#tag1", "#tag2", "#tag3", "#tag4"]  # first 5
    assert J.clean_hashtags("not a list") == []
    assert J.clean_hashtags([1, "", "#"]) == []
