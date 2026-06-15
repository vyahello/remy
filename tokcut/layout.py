"""Canvas layout: video rect + saliency-aware caption placement."""

import numpy as np
from PIL import Image

from .types import Layout, SourceInfo

OUT_W, OUT_H = 1080, 1920
VIDEO_BOX_H = 1700      # max video height inside the canvas
TOP_PAD = 30

# TikTok UI safe zone: top ~11% (Following/For You bar), bottom ~22%
# (description, music ticker, username) — captions must live between.
SAFE_TOP, SAFE_BOTTOM = 0.11, 0.78

# Auto placement keeps the caption in the top half of the frame so it
# stays clear of the main action (which sits lower in most clips).
CAP_MAX_Y = int(0.5 * OUT_H)


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
    # Keep the caption in the upper part of the frame: the main action
    # (typing, the demo) almost always lives in the lower two-thirds, so
    # cap the search at mid-frame and only dodge *downward* within that
    # top band when the very top is itself busy.
    y_hi = min(int(SAFE_BOTTOM * OUT_H), CAP_MAX_Y) - cap_h
    best_y, best_score = y_lo, float("inf")
    for y in range(y_lo, max(y_lo + 1, y_hi), 16):
        band = canvas[y // gs:(y + cap_h) // gs, cx0:cx1]
        score = float(band.mean())
        # strong bias toward the top — only move down to clear real content
        score += 0.30 * (y - y_lo) / max(1, y_hi - y_lo)
        if score < best_score:
            best_score, best_y = score, y
    return best_y


def compute_layout(
    src: SourceInfo,
    cap_size: tuple[int, int],
    pos: str,
    sal: np.ndarray | None = None,
) -> Layout:
    """Video rect + caption position for pos in auto|top|bottom."""
    cap_w, cap_h = cap_size
    if pos == "bottom":
        box_h = min(VIDEO_BOX_H, OUT_H - TOP_PAD - cap_h - 40)
    else:
        box_h = OUT_H - 2 * TOP_PAD
    scale = min(OUT_W / src["w"], box_h / src["h"])
    vw = int(src["w"] * scale / 2) * 2
    vh = int(src["h"] * scale / 2) * 2

    if pos == "bottom":
        vy = TOP_PAD
        cap_y = TOP_PAD + vh + (OUT_H - TOP_PAD - vh - cap_h) // 2
    else:
        vy = (OUT_H - vh) // 2
        cap_y = int(SAFE_TOP * OUT_H) + 10  # default for "top"

    lay: Layout = {"vw": vw, "vh": vh, "vx": (OUT_W - vw) // 2,
                   "cap_x": (OUT_W - cap_w) // 2, "vy": vy, "cap_y": cap_y}
    if pos == "auto":  # dodge the action
        assert sal is not None, "auto caption placement needs a saliency map"
        lay["cap_y"] = auto_caption_y(sal, lay, cap_w, cap_h)
    return lay
