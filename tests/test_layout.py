import numpy as np

from remy import layout as L

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


def test_auto_clear_frame_sits_at_top():
    cap_h = 200
    sal = np.zeros((100, 60), np.float32)  # nothing busy anywhere
    lay = L.compute_layout(SRC, (700, cap_h), "auto", sal)
    # with a clear frame the caption pins to the top of the safe zone
    assert lay["cap_y"] <= int(L.SAFE_TOP * L.OUT_H) + 30


def test_auto_uniform_busy_frame_sits_at_top():
    cap_h = 200
    sal = np.ones((100, 60), np.float32)  # uniformly busy — tie-break decides
    lay = L.compute_layout(SRC, (700, cap_h), "auto", sal)
    assert lay["cap_y"] <= int(L.SAFE_TOP * L.OUT_H) + 30


def test_auto_parks_wide_short_below_video():
    # a wide-in-vertical recording (a terminal cropped short) pins the video
    # to the top and drops the caption onto clean canvas BELOW it — never
    # overlapping the content, whatever the saliency map says
    src = {"w": 952, "h": 1082, "duration": 40, "fps": 60, "audio": False}
    cap_h = 288
    sal = np.ones((100, 90), np.float32)  # content everywhere -> overlay risky
    lay = L.compute_layout(src, (760, cap_h), "auto", sal)
    assert lay["cap_y"] >= lay["vy"] + lay["vh"]          # below the video
    assert lay["cap_y"] + cap_h <= int(L.SAFE_BOTTOM * L.OUT_H)
    assert lay["vw"] >= 0.85 * L.OUT_W                    # stays wide


def test_bottom_keeps_caption_below_video_in_safe_zone():
    src = {"w": 952, "h": 1082, "duration": 40, "fps": 60, "audio": False}
    cap_h = 288
    lay = L.compute_layout(src, (760, cap_h), "bottom")
    assert lay["cap_y"] >= lay["vy"] + lay["vh"]
    assert lay["cap_y"] + cap_h <= int(L.SAFE_BOTTOM * L.OUT_H)


def test_auto_drops_onto_calm_bottom():
    # the IMG_2110 case: bright/active laptop screen fills the top, the dark
    # keyboard + hand is calm below — the caption must leave the top half and
    # settle on that calm lower band ("on my hand"), not over the screen text.
    cap_h = 200
    sal = np.zeros((100, 60), np.float32)
    sal[:60, :] = 1.0  # top 60% of the video is the glowing, busy screen
    lay = L.compute_layout(SRC, (700, cap_h), "auto", sal)
    assert lay["cap_y"] > L.OUT_H // 2
    assert lay["cap_y"] + cap_h <= int(L.SAFE_BOTTOM * L.OUT_H)
