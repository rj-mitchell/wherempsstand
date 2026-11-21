"""Share cards (1200 x 630 PNG) for saved briefings and topic briefings: the link preview that shows the checking.

Each card is drawn once by the Where They Stand family's shared drawing (wts-core's ``adapters.cards.briefing_card``,
with Pillow and no AI) in the site's cloth and typefaces, and kept on disk: the site's band, "Briefing written by AI,
checked against Hansard", a title, the check strip's three counts in words and the day it was prepared.

A card's title is never the reader's question (docs/DECISIONS.md, 36): a saved briefing's card carries its neutral
title (``briefings.briefing_title``, "Briefing from Hansard, 17 November 2026"), since a question can be worded as an
allegation and the card carries the site's name. A topic's card carries the topic's own title, which the owner
chose. The counts are the strip's (``wts_core.checks.check_counts``) on the answer as it is shown, after the display
rule for quotations (``rag/checks.py``)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from wts_core.adapters.cards import CardStyle, briefing_card, card_count_lines
from wts_core.checks import CheckCounts, check_counts

from hansard.config import STATIC_DIR

FONTS = STATIC_DIR / "fonts"
STYLE = CardStyle(
    site_name="Where MPs Stand", domain="wherempsstand.com", cloth=(29, 69, 53), record="Hansard",
    serif=FONTS / "SourceSerif4-Semibold.ttf", sans=FONTS / "SourceSans3-Regular.ttf",
    sans_bold=FONTS / "SourceSans3-Semibold.ttf", icon=STATIC_DIR / "brand" / "icon-192.png")

# change it when what a card shows changes, so cards already drawn are drawn again (v3: the family's card, with the
# check strip's counts, 21 November 2026)
CARD_VERSION = "v3"


def card_paths(cache_dir: Path, bid: str) -> tuple[Path, ...]:
    """Where a briefing's card is kept, then every earlier version's file: a card from before neutral headlines
    (it showed the question) and the cards drawn before this version."""
    return (cache_dir / f"{bid}.{CARD_VERSION}.png", cache_dir / f"{bid}.png", cache_dir / f"{bid}.v2.png")


def topic_card_path(cache_dir: Path, slug: str, bid: str) -> Path:
    """Where a topic's card is kept: one for each version of its briefing, so a new week's briefing has its own."""
    return cache_dir / f"topic-{slug}-{bid}.{CARD_VERSION}.png"


def counts_of(answer: dict[str, Any] | None) -> CheckCounts:
    """The strip's counts for an answer as it is shown (an answer with none gives zeros)."""
    return check_counts(answer or {})  # type: ignore[arg-type]  # an answer is a dict of the same keys


def card_alt(title: str, counts: CheckCounts) -> str:
    """What a card shows, in words, for its ``og:image:alt``."""
    said = ", ".join(f"{number} {words}" for number, words, _ in card_count_lines(counts))
    return f"Where MPs Stand: {title}. Briefing written by AI, checked against Hansard: {said}."


def _drawn(path: Path, title: str, counts: CheckCounts, prepared: str) -> Path:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(briefing_card(STYLE, title=title, counts=counts, prepared=prepared))
        tmp.replace(path)
    return path


def card_png(cache_dir: Path, bid: str, title: str, prepared: str, answer: dict[str, Any] | None) -> Path:
    """A saved briefing's card: drawn once, then the saved file is served. Cards of earlier versions are
    deleted."""
    path, *old = card_paths(cache_dir, bid)
    for p in old:
        p.unlink(missing_ok=True)
    return _drawn(path, title, counts_of(answer), prepared)


def topic_card_png(cache_dir: Path, slug: str, bid: str, title: str, prepared: str,
                   answer: dict[str, Any] | None) -> Path:
    """A topic's card, for its current briefing: drawn once, and the cards of the topic's earlier briefings are
    deleted."""
    path = topic_card_path(cache_dir, slug, bid)
    earlier = re.compile(rf"topic-{re.escape(slug)}-[a-z0-9]{{10}}\.[\w.]+\.png")
    if cache_dir.is_dir():
        for p in cache_dir.glob(f"topic-{slug}-*.png"):
            if p != path and earlier.fullmatch(p.name):
                p.unlink(missing_ok=True)
    return _drawn(path, title, counts_of(answer), prepared)
