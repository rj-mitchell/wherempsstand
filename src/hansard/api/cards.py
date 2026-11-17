"""Link-preview cards (1200 x 630 PNG) for shared briefings, in the site's own typefaces and colours."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from hansard.config import STATIC_DIR

W, H = 1200, 630
PAPER = (255, 255, 255)
PANEL = (242, 244, 244)
INK = (28, 36, 39)
INK_SOFT = (79, 90, 94)
COMMONS = (29, 69, 53)
ON_CLOTH = (255, 255, 255)
FONTS = STATIC_DIR / "fonts"
LEFT, RIGHT = 96, 1120
BAND = 150


@lru_cache(maxsize=32)
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _fit_question(draw: ImageDraw.ImageDraw, question: str, width: int, max_lines: int) -> tuple[Any, list[str]]:
    for size in (64, 58, 52, 47, 43, 40):
        font = _font("SourceSerif4-Semibold.ttf", size)
        lines = _wrap(draw, question, font, width)
        if len(lines) <= max_lines:
            return font, lines
    lines = _wrap(draw, question, font, width)[:max_lines]
    while lines and draw.textlength(lines[-1] + "…", font=font) > width:
        lines[-1] = lines[-1].rsplit(" ", 1)[0]
    lines[-1] += "…"
    return font, lines


def evidence_line(evidence: dict[str, Any] | None) -> str:
    if not evidence or not evidence.get("records"):
        return ""
    parts = [f"{evidence['records']} contributions from {evidence['speakers']} speakers"]
    parties = sorted((evidence.get("parties") or {}).items(), key=lambda kv: -kv[1])
    if parties:
        parts.append("parties: " + ", ".join(f"{p} {n}" for p, n in parties[:4]))
    return "; ".join(parts)


def render_card(question: str, prepared: str, records_up_to: str, evidence: dict[str, Any] | None) -> Image.Image:
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, BAND), fill=COMMONS)  # the site's cloth, as the header band on every page

    icon = Image.open(STATIC_DIR / "brand" / "icon-192.png").convert("RGBA").resize((76, 76), Image.LANCZOS)
    img.paste(icon, (LEFT, 37), icon)
    d.text((LEFT + 100, 44), "Where MPs Stand", font=_font("SourceSerif4-Semibold.ttf", 38), fill=ON_CLOTH)
    d.text((LEFT + 100, 92), "A Where They Stand project", font=_font("SourceSans3-Regular.ttf", 23), fill=ON_CLOTH)

    d.text((LEFT, 186), "Briefing written by AI from Hansard", font=_font("SourceSans3-Semibold.ttf", 23), fill=INK_SOFT)
    font, lines = _fit_question(d, question, RIGHT - LEFT, 4)
    y = 228
    line_h = int(font.size * 1.18)
    for line in lines:
        d.text((LEFT, y), line, font=font, fill=INK)
        y += line_h

    ev = evidence_line(evidence)
    small = _font("SourceSans3-Regular.ttf", 23)
    if ev:
        while d.textlength(ev, font=small) > RIGHT - LEFT and " " in ev:
            ev = ev.rsplit(" ", 1)[0].rstrip(",;") + "…"
        d.text((LEFT, 500), ev, font=small, fill=INK_SOFT)
    d.rectangle((0, 548, W, H), fill=PANEL)
    stamp = f"Prepared {prepared}" + (f" from records up to {records_up_to}" if records_up_to else "")
    d.text((LEFT, 574), stamp, font=small, fill=INK_SOFT)
    url_font = _font("SourceSans3-Semibold.ttf", 23)
    d.text((RIGHT - d.textlength("wherempsstand.com", font=url_font), 574), "wherempsstand.com", font=url_font,
           fill=COMMONS)
    return img


def card_png(cache_dir: Path, bid: str, question: str, prepared: str, records_up_to: str,
             evidence: dict[str, Any] | None) -> Path:
    """Render once, then serve the saved file."""
    path = cache_dir / f"{bid}.png"
    if not path.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        render_card(question, prepared, records_up_to, evidence).save(tmp, "PNG", optimize=True)
        tmp.replace(path)
    return path
