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


def test_spread_times_zero_margin_stays_clear_of_eof():
    # a seek AT duration decodes nothing — even margin=0 must end early
    times = J.spread_times(416.87, n=16, margin=0.0)
    assert times[0] == pytest.approx(0.0)
    assert times[-1] <= 416.87 - 0.7
    assert times == sorted(times)


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


def test_clean_description_strips_markdown_keeps_emoji_and_snake_case():
    # backticks/asterisks/wrapping quotes are literal junk in TikTok's box
    out = J.clean_description('"Run `btop` for a **live** monitor 📊"')
    assert "`" not in out and "*" not in out
    assert not out.startswith('"') and not out.endswith('"')
    assert out == "Run btop for a live monitor 📊"
    # snake_case survives (no markdown underscores stripped)
    assert J.clean_description("theme solarized_dark") == \
        "theme solarized_dark"


def test_clean_description_strips_trailing_command_tail():
    # a labelled "Try: <command>" paste is dropped, the tip + emoji kept
    out = J.clean_description(
        'sd is the find & replace sed should have been — use -s for literal '
        'strings (no escaping!) or plain regex. Try: echo "x 47" | sd "\\d+$"'
        ' "" 🦀')
    assert "Try:" not in out and "echo" not in out and "sd \"" not in out
    assert out.startswith("sd is the find & replace")
    assert "use -s for literal strings" in out  # real teaching content kept
    assert out.endswith("🦀")                    # trailing emoji reattached
    # "Just run: btop" tail goes too
    assert J.clean_description(
        "btop shows CPU and RAM at a glance. Just run: btop") == \
        "btop shows CPU and RAM at a glance"


def test_clean_description_keeps_prose_with_verbs():
    # bare verbs in normal prose (no label colon) must NOT be stripped
    assert J.clean_description("Stop using grep, run ripgrep instead 🚀") == \
        "Stop using grep, run ripgrep instead 🚀"
    assert J.clean_description("use -s for literal strings ⚡") == \
        "use -s for literal strings ⚡"


def test_clean_hashtags_drops_tiktok_suppressed_tags():
    # #commandline (and #cli/#command/#fyp) are dead weight on TikTok
    out = J.clean_hashtags(
        ["#sd", "#rustlang", "#commandline", "#linux", "#coding"])
    assert "#commandline" not in out
    assert out == ["#sd", "#rustlang", "#linux", "#coding"]
    spammy = J.clean_hashtags(["#cli", "#command", "#fyp", "#viral", "#bash"])
    assert spammy == ["#bash"]


def test_clean_window_normal():
    # demo runs 10s..50s of a 60s clip -> trim 10 off head, 10 off tail
    assert J.clean_window({"start": 10, "end": 50}, 60.0) == (10.0, 10.0)


def test_clean_window_whole_clip_is_content():
    assert J.clean_window({"start": 0, "end": 60}, 60.0) == (0.0, 0.0)


def test_clean_window_rejects_nonsense_and_overaggressive():
    # end <= start, or a sub-MIN_CONTENT window -> no trim
    assert J.clean_window({"start": 40, "end": 10}, 60.0) == (0.0, 0.0)
    assert J.clean_window({"start": 10, "end": 12}, 60.0) == (0.0, 0.0)
    # cutting more than half off an edge is too aggressive to trust
    assert J.clean_window({"start": 40, "end": 58}, 60.0) == (0.0, 0.0)
    # garbage types fall back to no trim
    assert J.clean_window({"start": "x", "end": None}, 60.0) == (0.0, 0.0)
    assert J.clean_window({}, 60.0) == (0.0, 0.0)


def test_clean_hashtags_caps_at_five_keeping_order():
    many = [f"#tag{i}" for i in range(20)]
    out = J.clean_hashtags(many)
    assert out == ["#tag0", "#tag1", "#tag2", "#tag3", "#tag4"]  # first 5
    assert J.clean_hashtags("not a list") == []
    assert J.clean_hashtags([1, "", "#"]) == []


# ----------------------------------------------------------- clean_cut_spans

def test_clean_cut_spans_clamps_and_sorts():
    reply = {"cuts": [{"start": 18.0, "end": 25.0},
                      {"start": -3.0, "end": 4.0}]}
    spans = J.clean_cut_spans(reply, duration=20.0)
    # second span clamps to [0,4]; ordered; out-of-range end clamps to 20
    assert spans == [(0.0, 4.0), (18.0, 20.0)]


def test_clean_cut_spans_merges_overlaps():
    reply = {"cuts": [{"start": 2.0, "end": 6.0},
                      {"start": 5.0, "end": 9.0}]}
    assert J.clean_cut_spans(reply, duration=30.0) == [(2.0, 9.0)]


def test_clean_cut_spans_drops_tiny_and_reversed():
    reply = {"cuts": [{"start": 5.0, "end": 5.1},   # sub-min span
                      {"start": 9.0, "end": 3.0}]}   # reversed -> empty
    assert J.clean_cut_spans(reply, duration=30.0) == []


def test_clean_cut_spans_caps_total_removed():
    # three 5s cuts on a 20s clip: budget is half (10s) -> only two survive
    reply = {"cuts": [{"start": 0.0, "end": 5.0},
                      {"start": 6.0, "end": 11.0},
                      {"start": 12.0, "end": 17.0}]}
    spans = J.clean_cut_spans(reply, duration=20.0)
    assert spans == [(0.0, 5.0), (6.0, 11.0)]


def test_clean_cut_spans_handles_garbage():
    assert J.clean_cut_spans({}, 20.0) == []
    assert J.clean_cut_spans({"cuts": "nope"}, 20.0) == []
    assert J.clean_cut_spans({"cuts": [{"start": "x"}]}, 20.0) == []


# ----------------------------------------------------------- clean_sections

def test_clean_sections_sorts_clamps_and_caps():
    reply = {"sections": [
        {"start": 40, "label": "Draw Hello World"},
        {"start": 0, "label": "Require display + device"},
        {"start": 999, "label": "Runs on hardware ⚡"},   # clamped to duration
    ]}
    out = J.clean_sections(reply, 120.0)
    assert [lbl for _, lbl in out] == [
        "Require display + device", "Draw Hello World", "Runs on hardware ⚡"]
    assert out[0][0] == 0.0
    assert out[-1][0] == 120.0            # clamped into the clip


def test_clean_sections_drops_risky_and_overlong():
    reply = {"sections": [
        {"start": 0, "label": "how to hack wifi"},   # risky -> dropped
        {"start": 5, "label": "x" * 80},             # too long -> dropped
        {"start": 10, "label": "Push it to the device"},
    ]}
    out = J.clean_sections(reply, 60.0)
    assert out == [(10.0, "Push it to the device")]


def test_clean_sections_handles_garbage():
    assert J.clean_sections({}, 60.0) == []
    assert J.clean_sections({"sections": "nope"}, 60.0) == []
    assert J.clean_sections({"sections": [1, "x", {}]}, 60.0) == []


def test_clean_payoff_clamps_and_keeps_line():
    span, line = J.clean_payoff(
        {"start": 90, "end": 999, "line": "JS on a real gadget ⚡"}, 120.0)
    assert span == (90.0, 120.0)          # end clamped into the clip
    assert line == "JS on a real gadget ⚡"


def test_clean_payoff_rejects_thin_or_missing_span():
    span, line = J.clean_payoff({"start": 0, "end": 0, "line": "ok"}, 60.0)
    assert span is None and line == "ok"  # a good line survives a bad span
    span, _ = J.clean_payoff({"start": 30, "end": 30.4}, 60.0)
    assert span is None
    span, line = J.clean_payoff({"start": "x", "end": []}, 60.0)
    assert span is None and line == ""


def test_clean_payoff_long_span_keeps_the_climax():
    # a "payoff" covering most of the clip keeps only its final stretch
    span, _ = J.clean_payoff({"start": 0, "end": 100}, 120.0)
    assert span is not None
    start, end = span
    assert end == 100.0
    assert end - start <= min(J.PAYOFF_MAX_SPAN, 60.0) + 1e-6


def test_clean_payoff_drops_risky_or_overlong_line():
    span, line = J.clean_payoff(
        {"start": 10, "end": 20, "line": "how to hack wifi"}, 60.0)
    assert span == (10.0, 20.0)           # span still protects the demo
    assert line == ""
    _, line = J.clean_payoff(
        {"start": 10, "end": 20, "line": "x" * 80}, 60.0)
    assert line == ""
