"""Tests for the deterministic parts of the Claude judgment layer."""

import pytest

from tokcut import judge as J


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


# ----------------------------------------------------------- post copy

def test_clean_hashtags_normalizes_and_dedupes():
    out = J.clean_hashtags(["#IPython", "python coding", "#python", "#tech!"])
    assert out == ["#ipython", "#pythoncoding", "#python", "#tech"]


def test_clean_hashtags_drops_flagged_terms():
    # 'hack' is a moderation-flagged term — must not survive as a hashtag
    assert "#hacking" not in J.clean_hashtags(["#hacking", "#coding"])
    assert J.clean_hashtags(["#coding"]) == ["#coding"]


def test_clean_hashtags_caps_at_eight_and_handles_junk():
    many = [f"#tag{i}" for i in range(20)]
    assert len(J.clean_hashtags(many)) == 8
    assert J.clean_hashtags("not a list") == []
    assert J.clean_hashtags([1, "", "#"]) == []
