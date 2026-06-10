"""Caption rendering (Pillow) and TikTok-eligibility checks."""

from PIL import Image, ImageDraw, ImageFont

FONT_TEXT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"
FONT_EMOJI = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"

PURPLE: tuple[int, int, int, int] = (147, 88, 235, 255)
BOX_FILL: tuple[int, int, int, int] = (252, 250, 255, 238)

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

MAX_CAPTION_CHARS = 48


def check_caption(text: str) -> list[str]:
    """Warn about terms likely to get the post flagged. Returns warnings."""
    low = text.lower()
    warnings: list[str] = []
    for term, alt in RISKY_TERMS.items():
        if term in low:
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


def make_caption(
    text: str, out_path: str, font_size: int = 54
) -> tuple[int, int]:
    """Stacked rounded white boxes with purple bold-italic text + emoji.

    Returns (width, height) of the saved PNG.
    """
    font = ImageFont.truetype(FONT_TEXT, font_size)
    lines = balance_lines(text)
    pad_x, pad_y, gap, radius = 30, 16, 12, 20
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

    canvas_w = max(w for _, w in rendered) + 2 * pad_x + 8
    box_h = line_h + 2 * pad_y
    canvas_h = len(rendered) * box_h + (len(rendered) - 1) * gap + 8
    img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    y = 2
    for parts, width in rendered:
        bx = (canvas_w - width - 2 * pad_x) // 2
        # soft shadow then box
        d.rounded_rectangle([bx + 3, y + 4, bx + width + 2 * pad_x + 3,
                             y + box_h + 4], radius, fill=(0, 0, 0, 70))
        d.rounded_rectangle([bx, y, bx + width + 2 * pad_x, y + box_h],
                            radius, fill=BOX_FILL)
        x = bx + pad_x
        for part in parts:
            if part[0] == "txt":
                d.text((x, y + pad_y), part[1], font=font, fill=PURPLE)
                x += part[2]
            else:
                tile = part[1]
                img.paste(tile,
                          (x + 4, y + pad_y + (line_h - tile.height) // 2),
                          tile)
                x += tile.width + 10
        y += box_h + gap

    img.save(out_path)
    return canvas_w, canvas_h
