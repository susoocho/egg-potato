"""Hook captions burned into the top of each edited Short.

Style follows big couple channels: one short English line near the top, white
bold text with a black outline, on screen the whole video. The text sits inside
the Shorts safe zone (below the top bar, away from the right-hand buttons).

Caption text: whatever follows "__" in the file name (`그네__POV: he did it again.mp4`,
or several separated by " | " for the variants), otherwise lines picked from
POOL so each variant of one original gets a different line.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

POOL = [
    "POV: he thinks he's funny...",
    "When she says she's not hungry...",
    "Every couple has done this...",
    "It wasn't my fault...",
    "Wait for the end...",
    "He didn't see that coming...",
    "Tell me you're in love without telling me...",
    "Couples who do this >>>",
    "Rate his reaction 1-10",
    "POV: your boyfriend is a professional...",
    "Things only couples understand...",
    "She was NOT ready...",
    "He tried his best...",
    "Why is he like this...",
    "Our relationship in one video",
    "When your partner copies everything you do...",
    "Couple challenge: did we pass?",
    "Watch his face...",
    "Me vs my boyfriend",
    "Me vs my girlfriend",
    "This is why I married him...",
    "POV: date night at home",
    "Do this with your partner",
    "Try this with your boyfriend",
    "Try this with your girlfriend",
    "Who won this one?",
    "He thought I wouldn't notice...",
    "We tried the viral trend...",
    "Comment who's more dramatic",
    "Every girlfriend ever...",
    "Every boyfriend ever...",
    "POV: you're dating your best friend",
    "Is this normal for couples?",
    "He really said that...",
    "I can't believe he did this...",
    "Plot twist at the end...",
    "Nobody: / Us:",
    "Couple goals or nah?",
    "When the trend gets too competitive...",
    "Tag someone who would do this",
    "How it started vs how it's going",
    "He's never doing this again...",
    "She always wins...",
    "Why did we think this was a good idea...",
    "Things we do when nobody's watching",
    "This trend but make it couple",
    "POV: your partner has no rhythm",
    "Guess who practiced more...",
    "The way he looked at me...",
    "Only real couples can do this",
]

FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

WIDTH, HEIGHT = 1080, 1920
TOP = 300          # just below the top bar (dead zone ends ~290px)
MAX_W = 860        # stays clear of the right-hand buttons
FONT_SIZE = 70
STROKE = 7

_EMOJI = re.compile("[\U0001F000-\U0001FFFF☀-➿️‍]")


def from_name(name: str) -> list[str]:
    """Caption lines written in the file name after "__", split on " | "."""
    stem = Path(name).stem
    if "__" not in stem:
        return []
    text = stem.split("__", 1)[1]
    return [t.strip() for t in text.split("|") if t.strip()]


def pick(name: str, count: int, pool: list[str] = POOL) -> list[str]:
    """`count` captions for one original: from the file name, else from the pool.

    Pool picks are spread by a hash of the original name, so the same file always
    gets the same lines and different originals start at different places."""
    own = from_name(name)
    if own:
        return [own[i % len(own)] for i in range(count)]
    base = Path(name).stem.split("__", 1)[0]
    start = int(hashlib.sha1(base.encode()).hexdigest(), 16) % len(pool)
    step = 7 if len(pool) % 7 else 1  # walk the pool in big jumps so lines differ in mood
    return [pool[(start + i * step) % len(pool)] for i in range(count)]


def _font(size: int):
    from PIL import ImageFont

    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    lines: list[str] = []
    for para in text.split(" / "):  # " / " forces a line break
        words, cur = para.split(), ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if draw.textlength(trial, font=font) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
    return lines


def _balanced(draw, text: str, font, max_w: int) -> list[str]:
    """Same number of lines as a greedy wrap, but evenly filled (no lone last word)."""
    lines = _wrap(draw, text, font, max_w)
    lo, hi = max_w // 3, max_w
    while lo < hi:
        mid = (lo + hi) // 2
        if len(_wrap(draw, text, font, mid)) <= len(lines):
            hi = mid
        else:
            lo = mid + 1
    return _wrap(draw, text, font, lo)


def make_png(text: str, path: Path) -> Path:
    """Full-frame transparent PNG with the caption at the top, ready to overlay."""
    from PIL import Image, ImageDraw

    text = _EMOJI.sub("", text).strip()
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    size = FONT_SIZE
    font = _font(size)
    lines = _balanced(draw, text, font, MAX_W)
    while len(lines) > 3 and size > 48:  # long text: shrink a little rather than run long
        size -= 4
        font = _font(size)
        lines = _balanced(draw, text, font, MAX_W)
    y = TOP
    for line in lines:
        w = draw.textlength(line, font=font)
        draw.text(((WIDTH - w) / 2, y), line, font=font, fill="white",
                  stroke_width=STROKE, stroke_fill="black")
        y += int(size * 1.25)
    img.save(path)
    return path
