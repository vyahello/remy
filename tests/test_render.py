import numpy as np
import pytest

from remy import render as R


def test_atempo_chain_simple():
    assert R.atempo_chain(1.5) == "atempo=1.500000"


def test_atempo_chain_splits_large_speed():
    chain = R.atempo_chain(3.2)
    factors = [float(p.split("=")[1]) for p in chain.split(",")]
    product = 1.0
    for f in factors:
        assert 0.5 <= f <= 2.0
        product *= f
    assert abs(product - 3.2) < 1e-3


def test_atempo_chain_extreme():
    chain = R.atempo_chain(6.0)
    factors = [float(p.split("=")[1]) for p in chain.split(",")]
    assert all(f <= 2.0 for f in factors)
    product = 1.0
    for f in factors:
        product *= f
    assert abs(product - 6.0) < 1e-3


SRC = {"w": 1038, "h": 1616, "fps": 60, "audio": True}
LAY = {"vw": 1080, "vh": 1680, "vx": 0, "vy": 120, "cap_x": 191,
       "cap_y": 1277}


def test_filtergraph_concat_count():
    segs = [(0, 5, 1.0), (5, 10, 2.0), (10, 15, 3.2)]
    fc, v, a = R.build_filtergraph(segs, SRC, LAY, 60)
    assert "concat=n=3" in fc
    assert v == "[vout]"


def test_filtergraph_muted_by_default():
    # source has audio, but default export is silent for in-app sound
    segs = [(0, 5, 1.0)]
    fc, v, a = R.build_filtergraph(segs, SRC, LAY, 60)
    assert a is None
    assert "concat=n=1:v=1[vc]" in fc


def test_filtergraph_keep_audio_retains_ambient():
    segs = [(0, 5, 1.0)]
    fc, v, a = R.build_filtergraph(segs, SRC, LAY, 60, keep_audio=True)
    assert a == "[anorm]"  # ambient, loudness-normalized
    assert "concat=n=1:v=1:a=1" in fc


def test_filtergraph_music_adds_amix():
    segs = [(0, 5, 1.0)]
    fc, v, a = R.build_filtergraph(segs, SRC, LAY, 60, with_music=True)
    assert "amix=inputs=2" in fc
    assert a == "[anorm]"


def test_filtergraph_no_audio_source():
    segs = [(0, 5, 1.0)]
    src = dict(SRC, audio=False)
    fc, v, a = R.build_filtergraph(segs, src, LAY, 60, keep_audio=True)
    assert a is None
    assert "concat=n=1:v=1[vc]" in fc


def test_filtergraph_music_no_audio_source_uses_music_only():
    segs = [(0, 5, 1.0)]
    src = dict(SRC, audio=False)
    fc, v, a = R.build_filtergraph(segs, src, LAY, 60, with_music=True)
    assert a == "[anorm]"
    assert "[2:a]volume=0.8[aout]" in fc


def test_filtergraph_landscape_no_caption():
    # lay=None: native resolution, no pad/overlay, no caption input
    segs = [(0, 5, 1.0), (5, 10, 2.0)]
    fc, v, a = R.build_filtergraph(segs, SRC, None, 60)
    assert "overlay" not in fc
    assert "pad=" not in fc
    assert "scale=trunc(iw/2)*2:trunc(ih/2)*2" in fc
    assert v == "[vout]"


def test_filtergraph_landscape_music_index():
    # without a caption input, music is input n (right after segments)
    segs = [(0, 5, 1.0), (5, 10, 2.0)]
    fc, _v, a = R.build_filtergraph(segs, SRC, None, 60, with_music=True)
    assert "[2:a]volume" in fc
    assert a == "[anorm]"


def test_filtergraph_vertical_music_index_unchanged():
    # with a caption input at n, music sits at n+1
    segs = [(0, 5, 1.0), (5, 10, 2.0)]
    fc, _v, _a = R.build_filtergraph(segs, SRC, LAY, 60, with_music=True)
    assert "[3:a]volume" in fc


def test_filtergraph_landscape_keeps_crop():
    segs = [(0, 5, 1.0)]
    fc, _v, _a = R.build_filtergraph(
        segs, SRC, None, 60, crop=(10, 20, 800, 600))
    assert "crop=800:600:10:20" in fc


def test_look_filter_variants():
    sdr_cam = {"transfer": "bt709"}
    hdr = {"transfer": "arib-std-b67"}
    screen = R.look_filter(sdr_cam, screen=True)
    assert "unsharp" in screen
    assert "saturation" not in screen  # mono text: saturation buys nothing
    assert "gamma" in screen           # shadow lift against crushing
    assert "unsharp" not in R.look_filter(hdr, screen=False)
    assert "saturation=1.08" in R.look_filter(hdr, screen=False)
    assert "brightness" in R.look_filter(sdr_cam, screen=False)


def test_filtergraph_applies_look():
    segs = [(0, 5, 1.0)]
    fc, _v, _a = R.build_filtergraph(segs, SRC, None, 60,
                                     look="eq=contrast=1.05")
    assert "eq=contrast=1.05,format" in fc
    fc2, _v, _a = R.build_filtergraph(segs, SRC, LAY, 60,
                                      look="eq=contrast=1.05")
    assert "eq=contrast=1.05,pad" in fc2  # grade before the black bars


def test_filtergraph_no_look_by_default():
    segs = [(0, 5, 1.0)]
    fc, _v, _a = R.build_filtergraph(segs, SRC, None, 60)
    assert "eq=" not in fc


def test_encoder_params_by_content():
    assert R.encoder_params(screen=True) == "aq-mode=3:deblock=-1,-1"
    assert R.encoder_params(screen=False) == "aq-mode=3"


def test_filtergraph_loudnorm_when_audio_kept():
    segs = [(0, 5, 1.0)]
    fc, _v, a = R.build_filtergraph(segs, SRC, LAY, 60, keep_audio=True)
    assert a == "[anorm]"
    assert "loudnorm=I=-14" in fc


def test_filtergraph_no_loudnorm_when_muted():
    segs = [(0, 5, 1.0)]
    fc, _v, a = R.build_filtergraph(segs, SRC, LAY, 60)
    assert a is None
    assert "loudnorm" not in fc


LIGHT = {"w": 640, "h": 360, "fps": 30, "audio": True}   # SDR, cheap to decode
HLG = {"w": 1080, "h": 1920, "fps": 60, "transfer": "arib-std-b67",
       "audio": True}                                    # iPhone 60fps 10-bit


def test_decode_weight_scales_with_source():
    base = {"w": 1920, "h": 1080, "fps": 30}
    assert abs(R.decode_weight(base) - 1.0) < 1e-6
    assert abs(R.decode_weight(dict(base, fps=60)) - 2.0) < 1e-6   # fps x2
    assert R.decode_weight(dict(base, transfer="arib-std-b67")) > 1.5  # 10bit
    assert R.decode_weight({}) > 0                                 # tolerant


def test_use_two_pass_thresholds():
    assert not R.use_two_pass([(0, 1, 1.0)] * 5, LIGHT)   # light: in budget
    assert R.use_two_pass([(0, 1, 1.0)] * 5, HLG)         # heavy: over budget
    assert not R.use_two_pass([(0, 1, 1.0)], HLG)         # one heavy: fine
    # the hard input cap forces two-pass even for a light source
    assert R.use_two_pass([(0, 1, 1.0)] * (R.MAX_CONCAT_INPUTS + 1), LIGHT)


def test_render_dispatches_on_decode_budget(monkeypatch):
    seen = {}
    monkeypatch.setattr(R, "_render_single",
                        lambda *a: seen.setdefault("path", "single"))
    monkeypatch.setattr(R, "_render_segmented",
                        lambda *a: seen.setdefault("path", "segmented"))
    R.render("in.mp4", [(0, 1, 1.0)] * 5, None, LIGHT, None, "out.mp4")
    assert seen["path"] == "single"        # light source within budget
    seen.clear()
    R.render("in.mp4", [(0, 1, 1.0)] * 5, None, HLG, None, "out.mp4")
    assert seen["path"] == "segmented"     # heavy 60fps 10-bit blows budget


def test_mix_and_norm_variants():
    fc: list[str] = []
    assert R._mix_and_norm(fc, None, None) is None
    assert fc == []                       # muted: no audio filters
    fc = []
    assert R._mix_and_norm(fc, "[0:a]", None) == "[anorm]"
    assert any("loudnorm" in x for x in fc)      # ambient only, normalized
    fc = []
    assert R._mix_and_norm(fc, None, "[1:a]") == "[anorm]"
    assert any("volume=0.8[aout]" in x for x in fc)   # music only
    fc = []
    assert R._mix_and_norm(fc, "[0:a]", "[1:a]") == "[anorm]"
    assert any("amix=inputs=2" in x for x in fc)      # ambient + music


def test_format_video_parity_single_vs_segment():
    # the per-segment chain must format identically to the single-pass
    # one (same crop/scale/caption), just fed a different input label
    caps = [("[1:v]", LAY["cap_x"], LAY["cap_y"], "")]
    single = R._format_video("[vc]", SRC, LAY, None, "", 60, caps)
    segment = R._format_video("[vt]", SRC, LAY, None, "", 60, caps)
    assert single.replace("[vc]", "[X]") == segment.replace("[vt]", "[X]")


CARD = {"w": 600, "h": 220, "y": 230, "pushin": False}


def test_filtergraph_hook_card_branch_vertical():
    segs = [(0, 1.3, 1.0), (1.3, 10, 2.0)]
    fc, v, _a = R.build_filtergraph(segs, SRC, LAY, 60, hook_card=CARD)
    assert v == "[vout]"
    # animated card: alpha fade in + out and the scale ramp
    assert "fade=t=in:st=0" in fc and "fade=t=out" in fc
    assert "[hcard]" in fc and "scale=w='iw*(0.92" in fc
    # the card overlay is gated to the opening window
    assert f"enable='lte(t,{R.HOOK_CARD_DUR})'" in fc
    # legibility backing box behind the card
    assert "drawbox=" in fc
    # the persistent caption is suppressed until the card fades out
    assert (f"overlay={LAY['cap_x']}:{LAY['cap_y']}:"
            f"enable='gt(t,{R.HOOK_CARD_DUR})'" in fc)


def test_filtergraph_dynamic_captions_time_ranged():
    # dynamic mode: each label is its own caption input, overlaid only in
    # its output-time window (one shows at a time)
    segs = [(0, 20, 1.0)]
    caps = [
        {"png": "/tmp/a.png", "x": 60, "y": 1300, "start": 0.0, "end": 8.0},
        {"png": "/tmp/b.png", "x": 70, "y": 1300, "start": 8.0, "end": 20.0},
    ]
    fc, v, _a = R.build_filtergraph(segs, SRC, LAY, 60, captions=caps)
    assert v == "[vout]"
    assert "[1:v]overlay=60:1300:enable='between(t,0.000,8.000)'" in fc
    assert "[2:v]overlay=70:1300:enable='between(t,8.000,20.000)'" in fc


def test_filtergraph_no_hook_card_by_default():
    segs = [(0, 5, 1.0)]
    fc, _v, _a = R.build_filtergraph(segs, SRC, LAY, 60)
    assert "fade=t=in" not in fc
    assert "hcard" not in fc and "drawbox" not in fc
    # caption runs the whole video — not gated
    assert "enable='gt(t," not in fc
    assert f"overlay={LAY['cap_x']}:{LAY['cap_y']}," in fc


def test_filtergraph_landscape_hook_card_ignored():
    # landscape carries no baked text — the card must be dropped entirely
    segs = [(0, 5, 1.0)]
    fc, _v, _a = R.build_filtergraph(segs, SRC, None, 60, hook_card=CARD)
    assert "hcard" not in fc and "fade=t=in" not in fc
    assert "overlay" not in fc


def test_filtergraph_hook_card_music_index_after_card():
    # inputs: seg [0], caption [1], card [2], music [3]
    segs = [(0, 5, 1.0)]
    fc, _v, a = R.build_filtergraph(
        segs, SRC, LAY, 60, hook_card=CARD, with_music=True)
    assert a == "[anorm]"
    assert "[3:a]volume" in fc


def test_format_video_card_parity_single_vs_segment():
    # the single-pass concat input and the two-pass per-segment input must
    # produce an identical card chain (only the source label differs)
    caps = [("[1:v]", LAY["cap_x"], LAY["cap_y"], "")]
    single = R._format_video("[vc]", SRC, LAY, None, "", 60,
                             caps, "[2:v]", CARD)
    segment = R._format_video("[vt]", SRC, LAY, None, "", 60,
                              caps, "[2:v]", CARD)
    assert single.replace("[vc]", "[X]") == segment.replace("[vt]", "[X]")
    assert "[hcard]" in single and "enable='lte(t," in single


def test_format_video_card_pushin_adds_base_scale():
    card = dict(CARD, pushin=True)
    caps = [("[1:v]", LAY["cap_x"], LAY["cap_y"], "")]
    fc = R._format_video("[vc]", SRC, LAY, None, "", 60,
                         caps, "[2:v]", card)
    assert "[pbase]" in fc and "crop=1080:1920" in fc


def test_dry_run_prints_hook_card_and_renders_nothing(monkeypatch):
    from remy import cli
    src = {"w": 1080, "h": 1920, "fps": 60, "audio": True,
           "duration": 20.0, "transfer": ""}
    monkeypatch.setattr(
        cli, "plan",
        lambda *a, **k: (src, [(0, 1.3, 1.0), (1.3, 10, 2.0)], 8.0,
                         np.zeros((3, 4, 4)), (0.0, 1.3)))
    called = {"render": False}
    monkeypatch.setattr(
        cli, "render",
        lambda *a, **k: called.__setitem__("render", True))
    lines: list[str] = []
    cli.edit("in.mp4", "My caption", hook_card=True, dry_run=True,
             on_progress=lines.append)
    assert any('hook card: "My caption"' in ln for ln in lines)
    assert called["render"] is False


def _fake_plan_with_hook(src):
    return lambda *a, **k: (src, [(0, 3.0, 1.0), (3.0, 10, 2.0)], 10.0,
                            np.zeros((3, 4, 4)), (0.0, 3.0))


def test_hook_implies_card_and_uses_payoff_line(monkeypatch):
    # the cold open bakes the card by default, worded by the judge line
    from remy import cli
    src = {"w": 1080, "h": 1920, "fps": 60, "audio": True,
           "duration": 20.0, "transfer": ""}
    monkeypatch.setattr(cli, "plan", _fake_plan_with_hook(src))
    lines: list[str] = []
    cli.edit("in.mp4", "My caption", hook=True, hook_line="JS on a gadget",
             dry_run=True, on_progress=lines.append)
    assert any('hook card: "JS on a gadget"' in ln for ln in lines)
    # without a judge line the card falls back to the caption
    lines.clear()
    cli.edit("in.mp4", "My caption", hook=True, dry_run=True,
             on_progress=lines.append)
    assert any('hook card: "My caption"' in ln for ln in lines)
    # explicit off wins over the hook default
    lines.clear()
    cli.edit("in.mp4", "My caption", hook=True, hook_card=False,
             dry_run=True, on_progress=lines.append)
    assert not any("hook card:" in ln for ln in lines)


def test_plan_protects_payoff_and_teases_it(monkeypatch):
    # end-to-end plan(): the judge span plays 1.0x in the body and the
    # cold-open teaser is picked from inside it
    from remy import cli
    from remy.analysis import SAMPLE_FPS
    dur = 100.0
    src = {"w": 1080, "h": 1920, "fps": 60, "audio": True,
           "duration": dur, "transfer": ""}
    n = int(dur * SAMPLE_FPS)
    scores = np.ones(n)
    scores[80 * SAMPLE_FPS:90 * SAMPLE_FPS] = 30.0  # the demo lights up
    frames = np.full((n, 4, 4), 50, dtype=np.int16)
    monkeypatch.setattr(cli, "probe", lambda p: src)
    monkeypatch.setattr(cli, "motion_scores", lambda p, s: (scores, frames))
    _src, segs, _est, _fr, hook_win = cli.plan(
        "in.mp4", "auto", hook=True, payoff=(80.0, 90.0))
    assert hook_win is not None
    assert 76.0 <= hook_win[0] and hook_win[1] <= 92.0  # teased from the demo
    body = segs[1:]
    assert any(abs(s - 80.0) < 0.5 and abs(e - 90.0) < 0.5
               and sp == pytest.approx(1.0) for s, e, sp in body)


def test_plan_hook_falls_back_to_content_tail_not_a_spike(monkeypatch):
    # no judge payoff: the cold open must still open on the END of the
    # content (a build video's result lives there), never the chaotic
    # mid-clip motion spike that the global "most motion" pick would grab
    from remy import cli
    from remy.analysis import SAMPLE_FPS
    dur = 100.0
    src = {"w": 1080, "h": 1920, "fps": 60, "audio": True,
           "duration": dur, "transfer": ""}
    n = int(dur * SAMPLE_FPS)
    scores = np.full(n, 10.0)             # active throughout
    scores[38 * SAMPLE_FPS:42 * SAMPLE_FPS] = 500.0  # huge mid-clip spike
    frames = np.full((n, 4, 4), 50, dtype=np.int16)
    monkeypatch.setattr(cli, "probe", lambda p: src)
    monkeypatch.setattr(cli, "motion_scores", lambda p, s: (scores, frames))
    _src, segs, _est, _fr, hook = cli.plan(
        "in.mp4", "auto", hook=True, payoff=None)
    assert hook is not None
    assert hook[0] > 60.0                 # tail-anchored, not the 40s spike
    assert hook[1] <= dur


def test_filtergraph_vertical_no_caption():
    segs = [(0, 5, 1.0)]
    fc, v, _a = R.build_filtergraph(segs, SRC, LAY, 60, has_caption=False)
    assert v == "[vout]"
    assert "overlay" not in fc   # nothing baked over the video
    assert "pad=" in fc          # still boxed onto the 1080x1920 canvas
    assert "[base]format=yuv420p10le[vout]" in fc


def test_filtergraph_vertical_no_caption_music_index():
    # caption slot is skipped, so music is input [1] (right after the segment)
    segs = [(0, 5, 1.0)]
    fc, _v, a = R.build_filtergraph(
        segs, SRC, LAY, 60, has_caption=False, with_music=True)
    assert a == "[anorm]"
    assert "[1:a]volume" in fc


def test_format_video_vertical_no_caption_no_overlay():
    fc = R._format_video("[vc]", SRC, LAY, None, "", 60, "")
    assert "overlay" not in fc
    assert fc.endswith("[base]format=yuv420p10le[vout]")


def test_dry_run_vertical_no_caption_does_not_raise(monkeypatch):
    from remy import cli
    src = {"w": 1080, "h": 1920, "fps": 60, "audio": True,
           "duration": 20.0, "transfer": ""}
    monkeypatch.setattr(
        cli, "plan",
        lambda *a, **k: (src, [(0, 2, 1.0), (2, 10, 2.0)], 8.0,
                         np.zeros((3, 4, 4)), None))
    out = cli.edit("in.mp4", "", dry_run=True, on_progress=lambda _l: None)
    assert out.endswith("_remy.mp4")  # vertical + empty caption is allowed


def test_render_single_adds_shortest_with_music(monkeypatch):
    captured = {}
    monkeypatch.setattr(R, "_run",
                        lambda cmd, out: captured.update(cmd=cmd))
    src = dict(SRC, audio=False)  # music-only: nothing else bounds the loop
    R._render_single("in.mp4", [(0, 5, 1.0)], None, src, None,
                     "out.mp4", 18, "ultrafast", "/tmp/m.wav",
                     False, None, "")
    assert "-shortest" in captured["cmd"]
    captured.clear()
    R._render_single("in.mp4", [(0, 5, 1.0)], None, SRC, None,
                     "out.mp4", 18, "ultrafast", None, False, None, "")
    assert "-shortest" not in captured["cmd"]  # no music, no loop to bound
