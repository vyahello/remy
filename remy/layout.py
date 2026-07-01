"""Canvas layout: video rect + saliency-aware caption placement."""

import numpy as np
from PIL import Image

from .types import Layout, SourceInfo

OUT_W, OUT_H = 1080, 1920
VIDEO_BOX_H = 1700      # max video height inside the canvas
TOP_PAD = 30

# TikTok UI safe zone: the Following/For-You tab bar sits in the top ~6%,
# so 8% clears it while letting a tall two-line caption ride high on the
# black bar above the content (instead of dipping onto it). Bottom ~22%
# is the description / music ticker / username — captions live between.
# The caption lives strictly BETWEEN the two UI zones: never in the top ~8%
# (tab bar) and never in the bottom ~22% — that lower band is where the
# creator's OWN TikTok description + hashtags render, so a caption there
# double-stacks with them. Placement seeks a *static* region inside this
# window (a still header up top, or a dead desk/keyboard lower down) and
# leans toward the top, where a caption reads cleanest.
SAFE_TOP, SAFE_BOTTOM = 0.08, 0.78


def hook_card_y() -> int:
    """Top-of-frame y for the animated hook card, inside the safe zone."""
    return int(SAFE_TOP * OUT_H) + 20


def auto_caption_y(
    sal: np.ndarray, lay: Layout, cap_w: int, cap_h: int
) -> int:
    """Pick the caption y whose band covers the least salient content."""
    gs = 8  # output-space grid step
    gw, gh = OUT_W // gs, OUT_H // gs
    canvas = np.zeros((gh, gw), np.float32)
    img = Image.fromarray((sal * 255).astype(np.uint8))
    img = img.resize((max(1, lay["vw"] // gs), max(1, lay["vh"] // gs)))
    arr = np.asarray(img, np.float32) / 255
    x0, y0 = lay["vx"] // gs, lay["vy"] // gs
    canvas[y0:y0 + arr.shape[0], x0:x0 + arr.shape[1]] = \
        arr[: gh - y0, : gw - x0]

    cx0 = ((OUT_W - cap_w) // 2) // gs
    cx1 = ((OUT_W + cap_w) // 2) // gs
    y_lo = int(SAFE_TOP * OUT_H) + 10
    # Search the safe zone for the calmest band. Saliency is motion-led (see
    # analysis.saliency_map), so a STILL region reads as calm even when it's
    # bright — the caption may sit on a static header or a title bar, but
    # never over the part of the screen that's actively changing (typing,
    # scrolling, a device redrawing). The mild top bias then leans an
    # otherwise-tied choice toward the top, where a caption reads cleanest;
    # a busy top decisively pushes it down onto a calmer lower band instead.
    y_hi = int(SAFE_BOTTOM * OUT_H) - cap_h
    best_y, best_score = y_lo, float("inf")
    for y in range(y_lo, max(y_lo + 1, y_hi), 16):
        band = canvas[y // gs:(y + cap_h) // gs, cx0:cx1]
        # Blend the band's MEAN with its PEAK: a band that merely clips the
        # edge of content (sparse digits in a results table, the tail of a
        # word) has a low mean but a high peak, so peak rejects it in favour
        # of a band that's empty everywhere. Without this the caption settles
        # over sparse-but-real content because the black between glyphs
        # averages the mean down.
        score = 0.5 * float(band.mean()) + 0.5 * float(band.max())
        score += 0.05 * (y - y_lo) / max(1, y_hi - y_lo)
        if score < best_score:
            best_score, best_y = score, y
    return best_y


def _video_rect(src: SourceInfo, box_h: int) -> tuple[int, int]:
    """Even (w, h) of the source scaled to fit OUT_W × box_h."""
    scale = min(OUT_W / src["w"], box_h / src["h"])
    return int(src["w"] * scale / 2) * 2, int(src["h"] * scale / 2) * 2


# When auto placement parks the caption below the video, the video shrinks
# just enough to open a clean gap inside the safe zone. Only do that while
# the video still fills most of the width — a tall near-9:16 clip would
# pillarbox into a stamp, so it keeps the on-video overlay instead.
PARK_MIN_VW_FRAC = 0.85
CAP_GAP_PAD = 24  # breathing room around a parked caption


def compute_layout(
    src: SourceInfo,
    cap_size: tuple[int, int],
    pos: str,
    sal: np.ndarray | None = None,
) -> Layout:
    """Video rect + caption position for pos in auto|top|bottom.

    "auto" decides between two strategies by geometry:
    - **park** (wide-in-vertical content — terminals, code, slides): the
      video is pinned to the top and shrunk just enough to open a clean band
      below it, where the caption sits on empty canvas. Such content fills
      the frame top-to-bottom over its run, so an overlay would eventually
      cover the very text the viewer needs — parking never does.
    - **overlay** (tall near-9:16 footage that can't shrink without
      pillarboxing — phone clips): the caption rides the calmest region of
      the video (saliency), dodging the bright/active area.
    """
    cap_w, cap_h = cap_size
    safe_bottom = int(SAFE_BOTTOM * OUT_H)
    # geometry of a top-pinned video that leaves a safe-zone caption gap
    park_box_h = min(VIDEO_BOX_H, safe_bottom - TOP_PAD - cap_h - CAP_GAP_PAD)
    pvw, pvh = _video_rect(src, max(2, park_box_h))

    if pos == "auto":
        assert sal is not None, "auto caption placement needs a saliency map"
        if pvw >= PARK_MIN_VW_FRAC * OUT_W:
            pos = "bottom"  # park below the video — off the content for good
        else:  # overlay on the calmest band of a near-full-frame video
            vw, vh = _video_rect(src, OUT_H - 2 * TOP_PAD)
            lay: Layout = {"vw": vw, "vh": vh, "vx": (OUT_W - vw) // 2,
                           "vy": (OUT_H - vh) // 2,
                           "cap_x": (OUT_W - cap_w) // 2,
                           "cap_y": int(SAFE_TOP * OUT_H) + 10}
            lay["cap_y"] = auto_caption_y(sal, lay, cap_w, cap_h)
            return lay

    if pos == "bottom":
        vy = TOP_PAD
        cap_y = vy + pvh + (safe_bottom - vy - pvh - cap_h) // 2
        return {"vw": pvw, "vh": pvh, "vx": (OUT_W - pvw) // 2, "vy": vy,
                "cap_x": (OUT_W - cap_w) // 2, "cap_y": cap_y}

    # "top" (and the no-caption centered export): video centered, caption
    # pinned to the top of the safe zone
    vw, vh = _video_rect(src, OUT_H - 2 * TOP_PAD)
    return {"vw": vw, "vh": vh, "vx": (OUT_W - vw) // 2,
            "vy": (OUT_H - vh) // 2, "cap_x": (OUT_W - cap_w) // 2,
            "cap_y": int(SAFE_TOP * OUT_H) + 10}
