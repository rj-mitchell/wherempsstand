"""The checks every briefing passes through before a reader sees it, with this site's own words and rules.

The checks themselves are the Where They Stand sites' shared ones (wts-core, ``wts_core.checks``): every citation
is looked up in its source, a sentence with no citation is marked as a point to check, a quotation is looked for
word for word, American spellings in the site's own words are made British, and judgemental words in the site's own
voice are highlighted (never hidden, and never inside a quotation). What is this site's own is passed in here:

* the words a "no records" answer opens with (``rag/prompts.py`` tells the model to use them);
* the judgemental words: the family's common list, as this site has always had it;
* what counts as a note about the evidence, which is not a claim to check. This site has the wider of the family's
  two forms, as it always has: a sentence saying something "was supplied" or "were retrieved" counts as one (so
  "Gas was supplied to two million homes" is not marked). Narrowing it is a change of its own, to be made with an
  evaluation run before and after (wts-core docs/MIGRATION.md 1.4); tests/test_rag.py holds it where it is.
"""

from __future__ import annotations

import re
from typing import Any

from wts_core.checks import (
    COMMON_HONORIFICS,
    COMMON_LOADED_WORDS,
    EVIDENCE_NOTES_WIDE,
    LoadedWords,
    Verifier,
    check_briefing,
)
from wts_core.events import Answer

from hansard.rag.prompts import NO_RECORDS

LOADED = re.compile(rf"\b({COMMON_LOADED_WORDS})\b", re.IGNORECASE)
LOADED_WORDS = LoadedWords(LOADED)

VERIFIER = Verifier(no_records=NO_RECORDS, evidence_notes=EVIDENCE_NOTES_WIDE, honorifics=COMMON_HONORIFICS)


def checked(blocks: list[dict[str, Any]], sources: list[Any]) -> Answer:
    """The model's finished blocks as the briefing a reader is sent: every check, in the family's one order
    (citations, openers about the records dropped, spelling, judgemental words, quotations)."""
    return check_briefing(blocks, sources, VERIFIER, LOADED_WORDS)


def shown_again(answer: dict[str, Any] | None) -> dict[str, Any] | None:
    """A saved briefing's answer as its address shows it today: its points to check and its judgemental-word marks
    judged again by the current rules, so a mark since found to be wrong is not shown. Nothing is ever newly
    flagged, and a briefing written under today's rules is left as it is."""
    return LOADED_WORDS.still_loaded(VERIFIER.recheck(answer))
