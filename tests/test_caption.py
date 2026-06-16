import os

import pytest

from remy import caption as C


def test_check_caption_flags_risky_terms():
    warnings = C.check_caption("Hacking WiFi with deauth attack")
    joined = " ".join(warnings)
    assert "hack" in joined
    assert "deauth" in joined
    assert "attack" in joined


def test_check_caption_clean_passes():
    assert C.check_caption("How I set up my new desk") == []


def test_check_caption_flags_overlong():
    warnings = C.check_caption("x" * (C.MAX_CAPTION_CHARS + 5))
    assert any("renders small" in w for w in warnings)


def test_balance_lines_two_balanced():
    a, b = C.balance_lines("How I set up my brand new desk")
    assert abs(len(a) - len(b)) <= 6


def test_balance_lines_short_single():
    assert C.balance_lines("hi there") == ["hi there"]


def test_split_runs_separates_emoji():
    runs = C.split_runs("hi ⚡")  # high-voltage emoji
    assert any(is_emoji for is_emoji, _ in runs)
    assert any(not is_emoji for is_emoji, _ in runs)


@pytest.mark.skipif(not os.path.exists(C.FONT_TEXT),
                    reason="DejaVu font not installed")
@pytest.mark.parametrize("style", sorted(C.STYLES))
def test_make_caption_styles(tmp_path, style):
    out = tmp_path / f"cap_{style}.png"
    w, h = C.make_caption("Styled caption", str(out), style=style)
    assert out.exists() and w > 0 and h > 0


@pytest.mark.skipif(not os.path.exists(C.FONT_TEXT),
                    reason="DejaVu font not installed")
def test_unknown_style_falls_back_to_default(tmp_path):
    out = tmp_path / "cap.png"
    w, h = C.make_caption("hi there", str(out), style="neon-zebra")
    assert out.exists() and w > 0 and h > 0


@pytest.mark.skipif(not os.path.exists(C.FONT_TEXT),
                    reason="DejaVu font not installed")
def test_make_caption_writes_png(tmp_path):
    out = tmp_path / "cap.png"
    w, h = C.make_caption("How I set up my brand new desk", str(out))
    assert out.exists()
    assert w > 0 and h > 0
    from PIL import Image
    assert Image.open(out).size == (w, h)


@pytest.mark.skipif(not os.path.exists(C.FONT_TEXT),
                    reason="DejaVu font not installed")
def test_make_hook_card_bigger_and_fits(tmp_path):
    text = "How I set this up"
    hook = tmp_path / "hook.png"
    hw, hh = C.make_hook_card(text, str(hook))
    assert hook.exists()
    from PIL import Image
    assert Image.open(hook).size == (hw, hh)
    # fits within the canvas-width safety bound
    assert hw <= C.HOOK_CARD_MAX_W
    # rendered larger than the default persistent caption
    cap = tmp_path / "cap.png"
    _cw, ch = C.make_caption(text, str(cap))
    assert hh > ch


@pytest.mark.skipif(not os.path.exists(C.FONT_TEXT),
                    reason="DejaVu font not installed")
def test_make_hook_card_long_text_downsizes_to_fit(tmp_path):
    out = tmp_path / "hook.png"
    w, _h = C.make_hook_card("How I set up my brand new desk today ⚡",
                             str(out))
    assert w <= C.HOOK_CARD_MAX_W
