"""Caption rendering (Pillow) and TikTok-eligibility checks."""

import os
import re

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Caption face: a heavy UPRIGHT sans reads as cleaner and more "TikTok
# native" than the old bold-OBLIQUE DejaVu (which skewed meme-y). First
# installed wins; the last entry always exists on the dev/CI image, so the
# font-gated tests still have something to skip on. Override with REMY_FONT.
_FONT_CANDIDATES = [
    os.environ.get("REMY_FONT", ""),
    "/usr/share/fonts/truetype/open-sans/OpenSans-ExtraBold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
]
FONT_TEXT = next((p for p in _FONT_CANDIDATES if p and os.path.exists(p)),
                 _FONT_CANDIDATES[-1])
FONT_EMOJI = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"

RGBA = tuple[int, int, int, int]

# Caption style presets. Each is text colour + pill fill + a hairline
# accent keyline + a thin glyph stroke (keeps letters crisp where they meet
# the pill edge / busy footage behind a translucent fill). "purple" is the
# house default; the others are high-contrast alternates pickable via
# --style or a bot redo ("yellow caption").
Style = dict[str, RGBA]
STYLES: dict[str, Style] = {
    "purple": {"text": (124, 58, 237, 255), "fill": (255, 255, 255, 242),
               "accent": (124, 58, 237, 105), "stroke": (124, 58, 237, 60)},
    "yellow": {"text": (20, 18, 12, 255), "fill": (250, 209, 34, 246),
               "accent": (20, 18, 12, 70), "stroke": (20, 18, 12, 45)},
    "black": {"text": (255, 255, 255, 255), "fill": (16, 16, 20, 224),
              "accent": (255, 255, 255, 60), "stroke": (0, 0, 0, 150)},
}
DEFAULT_STYLE = "purple"

# TikTok OCRs on-screen text; these terms commonly trigger moderation or
# reduced reach. Keep captions descriptive rather than sensational. The
# list is conservative — edit it to fit your own content.
RISKY_TERMS: dict[str, str | None] = {
    "hack": "set up / build / make",
    "hacking": "building / tinkering",
    "hacker": "creator / builder",
    "attack": "try / test",
    "exploit": "feature / trick",
    "deauth": None,
    "crack": "open / solve",
    "bypass": "skip / work around",
    "spy": "watch / track",
    "payload": "file / script",
    "jam": None,
    "steal": None,
    "free wifi": None,
}

# Match each risky term only as a WHOLE word — plain substring matching
# false-flags legitimate tech wording (#hackathon, jamstack, Spyder,
# "wisecrack") and would silently strip good captions/hashtags.
_RISKY_RE = {term: re.compile(rf"\b{re.escape(term)}\b")
             for term in RISKY_TERMS}

# A caption is a single scannable line, not a paragraph — long ones read as
# amateur and (being two-line-tall) can't tuck into a clean strip of the
# frame. Keep it short enough to stay on one line at a confident size.
MAX_CAPTION_CHARS = 34

# Persistent-caption sizing. The face is rendered BIG (TikTok captions read
# large — a timid caption gets scrolled past) but auto-shrinks to fit a
# frame-safe width so a long line never clips or runs to the canvas edge.
# CAPTION_MAX_W leaves a margin each side of the 1080 frame; CAPTION_MIN_FONT
# is the floor below which text stops being glanceable on a phone.
CAPTION_FONT = 64
CAPTION_MIN_FONT = 38
CAPTION_MAX_W = 960
# Wrap to a second line only when a single line would shrink below this — a
# short/medium caption stays one clean, confident line; only a genuinely
# long one stacks (still big) rather than shrinking to a whisper.
ONE_LINE_MIN_FONT = 46

# Animated hook card: a bigger version of the caption for the cold open.
# make_caption fits it to HOOK_CARD_MAX_W (≈ 0.92 * 1080) on its own.
HOOK_CARD_FONT = 84
HOOK_CARD_MAX_W = 994


def check_caption(text: str) -> list[str]:
    """Warn about terms likely to get the post flagged. Returns warnings."""
    low = text.lower()
    warnings: list[str] = []
    for term, alt in RISKY_TERMS.items():
        if _RISKY_RE[term].search(low):
            hint = f' — try "{alt}"' if alt else ""
            warnings.append(f'risky term "{term}"{hint}')
    if len(text) > MAX_CAPTION_CHARS:
        warnings.append(f"caption is {len(text)} chars; "
                        f">{MAX_CAPTION_CHARS} renders small — shorten it")
    return warnings


def balance_lines(text: str) -> list[str]:
    """Split text into up to two visually balanced lines."""
    words = text.split()
    if len(words) < 3:
        return [text]
    best: list[str] = [text]
    best_diff = float("inf")
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        diff = abs(len(a) - len(b))
        if diff < best_diff:
            best, best_diff = [a, b], diff
    return best


def split_runs(text: str) -> list[list]:
    """Split a line into [is_emoji, chunk] runs."""
    runs: list[list] = []
    for ch in text:
        is_emoji = ord(ch) > 0x2600
        if runs and runs[-1][0] == is_emoji:
            runs[-1][1] += ch
        else:
            runs.append([is_emoji, ch])
    return runs


def _emoji_tile(ch: str, height: int) -> Image.Image | None:
    """Render one color-emoji glyph and scale it to the text line height."""
    f = ImageFont.truetype(FONT_EMOJI, 109)  # CBDT strike size
    img = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((10, 10), ch, font=f, embedded_color=True)
    box = img.getbbox()
    if not box:
        return None
    img = img.crop(box)
    scale = height / img.height
    return img.resize((max(1, int(img.width * scale)), height),
                      Image.Resampling.LANCZOS)


def _line_text_width(line: str, font: ImageFont.FreeTypeFont,
                     font_size: int, measurer: ImageDraw.ImageDraw) -> int:
    """Estimate a line's content width (emoji ≈ one em) for the fit loop."""
    width = 0
    for is_emoji, chunk in split_runs(line):
        if is_emoji:
            width += sum(font_size + 10 for _ in chunk.strip())
        else:
            width += int(measurer.textlength(chunk, font=font))
    return width


def _fit_font_size(lines: list[str], target: int, min_size: int,
                   pad_x: int, margin: int, max_w: int) -> int:
    """Largest size in [min_size, target] whose full PNG width fits max_w.

    The caption is rendered as big as it can be without the canvas reaching
    the frame edge — so it stays bold and glanceable, but a long or
    wide-glyph caption can never clip or overrun the frame. `max_w` budgets
    the whole PNG (pill + shadow margin), so the returned canvas is always
    ≤ max_w.
    """
    measurer = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    fixed = 2 * pad_x + 2 * margin
    size = target
    while size > min_size:
        font = ImageFont.truetype(FONT_TEXT, size)
        sw = max(2, round(size / 30))
        widest = max(_line_text_width(ln, font, size, measurer)
                     for ln in lines)
        if widest + fixed + 2 * sw <= max_w:
            break
        size -= 2
    return size


def make_caption(
    text: str, out_path: str, font_size: int = CAPTION_FONT,
    style: str = DEFAULT_STYLE, max_w: int = CAPTION_MAX_W,
) -> tuple[int, int]:
    """Stacked rounded pills with heavy upright text + color emoji.

    `style` picks a STYLES preset (text colour, pill fill, accent keyline
    and a glyph stroke). The face is rendered as large as `font_size` but
    auto-shrinks (down to CAPTION_MIN_FONT) so the pill never exceeds
    `max_w` — big and punchy, yet a long caption can't clip or run off the
    frame. A two-layer lift (a wide ambient glow + a tighter contact
    shadow) floats the block off busy footage without a hard halo, so the
    text stays legible over anything. Returns (width, height) of the saved
    PNG — the size includes a transparent margin for the shadow bleed, so
    layout keeps it centred.
    """
    st = STYLES.get(style, STYLES[DEFAULT_STYLE])
    text_color, box_fill = st["text"], st["fill"]
    accent, stroke = st["accent"], st["stroke"]
    pad_x, pad_y, gap, radius = 36, 20, 15, 28
    margin = 40                             # transparent room for the glow

    # Prefer ONE line. Only wrap to two when keeping it on a single line
    # would force the face below ONE_LINE_MIN_FONT — a short/medium caption
    # then reads as one clean, confident line instead of an amateur stack.
    one_font = _fit_font_size([text], font_size, CAPTION_MIN_FONT,
                              pad_x, margin, max_w)
    if one_font >= ONE_LINE_MIN_FONT:
        lines = [text]
    else:
        lines = balance_lines(text)
    font_size = _fit_font_size(lines, font_size, CAPTION_MIN_FONT,
                               pad_x, margin, max_w)
    font = ImageFont.truetype(FONT_TEXT, font_size)
    sw = max(2, round(font_size / 30))      # glyph outline width
    ascent, descent = font.getmetrics()
    line_h = ascent + descent

    measurer = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    rendered: list[tuple[list, int]] = []
    for line in lines:
        parts: list = []
        width = 0
        for is_emoji, chunk in split_runs(line):
            if is_emoji:
                for ch in chunk.strip():
                    tile = _emoji_tile(ch, font_size)
                    if tile:
                        parts.append(("img", tile))
                        width += tile.width + 10
            else:
                w = int(measurer.textlength(chunk, font=font))
                parts.append(("txt", chunk, w))
                width += w
        rendered.append((parts, width))

    box_h = line_h + 2 * pad_y
    block_w = max(w for _, w in rendered) + 2 * pad_x + 2 * sw
    block_h = len(rendered) * box_h + (len(rendered) - 1) * gap + 2 * sw
    canvas_w, canvas_h = block_w + 2 * margin, block_h + 2 * margin

    # lay out each line's pill rect once, reused by the shadow + main pass
    rows: list[tuple[list, int, int, int]] = []
    y = margin + sw
    for parts, width in rendered:
        bw = width + 2 * pad_x
        bx = (canvas_w - bw) // 2
        rows.append((parts, bx, y, bw))
        y += box_h + gap

    # two-layer lift: a wide ambient glow under everything, then a tighter
    # contact shadow dropped just beneath the pills. Together they read as a
    # soft float on any footage (bright screen or dark desk) — no hard halo.
    img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    for blur, dy, alpha in ((20, 0, 110), (7, 10, 150)):
        layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        for _parts, bx, y, bw in rows:
            ld.rounded_rectangle([bx, y + dy, bx + bw, y + box_h + dy],
                                 radius, fill=(0, 0, 0, alpha))
        img = Image.alpha_composite(img, layer.filter(
            ImageFilter.GaussianBlur(blur)))

    d = ImageDraw.Draw(img)
    for parts, bx, y, bw in rows:
        d.rounded_rectangle([bx, y, bx + bw, y + box_h], radius, fill=box_fill)
        if accent[3]:
            d.rounded_rectangle([bx, y, bx + bw, y + box_h], radius,
                                outline=accent, width=3)
        x = bx + pad_x
        for part in parts:
            if part[0] == "txt":
                d.text((x, y + pad_y), part[1], font=font, fill=text_color,
                       stroke_width=sw, stroke_fill=stroke)
                x += part[2]
            else:
                tile = part[1]
                img.paste(tile,
                          (x + 4, y + pad_y + (line_h - tile.height) // 2),
                          tile)
                x += tile.width + 10

    img.save(out_path)
    return canvas_w, canvas_h


def make_hook_card(
    text: str, out_path: str, style: str = DEFAULT_STYLE,
    max_w: int = HOOK_CARD_MAX_W,
) -> tuple[int, int]:
    """Render the cold-open hook card PNG — a bigger caption.

    Thin wrapper over make_caption: renders at the large hook size and lets
    make_caption's own fit loop shrink it to `max_w` if the line is long.
    Returns (width, height) of the saved PNG.
    """
    return make_caption(text, out_path, font_size=HOOK_CARD_FONT,
                        style=style, max_w=max_w)
