import numpy as np

from tokcut import layout as L

SRC = {"w": 1038, "h": 1616, "duration": 90, "fps": 60, "audio": True}


def test_layout_fits_canvas():
    lay = L.compute_layout(SRC, (700, 200), "top")
    assert lay["vw"] <= L.OUT_W
    assert lay["vy"] + lay["vh"] <= L.OUT_H
    assert lay["vx"] >= 0


def test_top_caption_inside_safe_zone():
    cap_h = 200
    lay = L.compute_layout(SRC, (700, cap_h), "top")
    assert lay["cap_y"] >= int(L.SAFE_TOP * L.OUT_H)
    assert lay["cap_y"] + cap_h <= int(L.SAFE_BOTTOM * L.OUT_H) + 1


def test_hook_card_y_top_of_safe_zone():
    y = L.hook_card_y()
    assert int(L.SAFE_TOP * L.OUT_H) <= y < int(L.SAFE_BOTTOM * L.OUT_H)
    # sits in the upper portion of the frame for first-second visibility
    assert y < L.OUT_H // 3


def test_auto_caption_inside_safe_zone():
    cap_h = 200
    sal = np.zeros((40, 30), np.float32)
    sal[5:15, :] = 1.0  # busy near the top -> caption should avoid it
    lay = L.compute_layout(SRC, (700, cap_h), "auto", sal)
    assert lay["cap_y"] >= int(L.SAFE_TOP * L.OUT_H)
    assert lay["cap_y"] + cap_h <= int(L.SAFE_BOTTOM * L.OUT_H)


def test_auto_avoids_salient_band():
    cap_h = 180
    # make the TOP of the video extremely salient; caption should sit lower
    sal = np.zeros((100, 60), np.float32)
    sal[:30, :] = 1.0
    lay = L.compute_layout(SRC, (700, cap_h), "auto", sal)
    # caption band should not start in the very top portion of the video
    assert lay["cap_y"] > lay["vy"] + lay["vh"] * 0.15
