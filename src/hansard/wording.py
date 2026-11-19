"""Titles as the site shows them. Older records write some titles in capitals ("WINNIE EWING", "ORDERS OF THE DAY —
CONSOLIDATED FUND BILL"); a page's title and heading give them in the ordinary way, keeping acronyms. Only how a
title is shown changes: addresses, and the stored record, keep the words as Parliament wrote them."""

from __future__ import annotations

import re

# kept in capitals however a title is written (words with no vowel, such as NHS or BBC, are kept too)
ACRONYMS = {"UK", "US", "USA", "EU", "EEA", "UN", "UNESCO", "UNICEF", "NATO", "OECD", "WTO", "NHS", "BBC", "ITV",
            "HMRC", "DWP", "DVLA", "GP", "GPS", "HIV", "AIDS", "IVF", "VAT", "ISA", "PFI", "LGBT", "LGBTQ", "MP",
            "MPS", "MSP", "MSPS", "MLA", "MLAS", "SNP", "DUP", "SDLP", "UKIP", "UUP", "HS2", "G7", "G8", "G20", "COP",
            "ID", "IRA", "OBE", "MBE", "CBE", "UEFA", "FIFA", "RAF", "RNLI", "RSPCA", "NSPCC", "UNHCR", "COVID",
            "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII"}
# short words that stay lower case inside a title
SMALL = {"a", "an", "and", "as", "at", "by", "etc", "for", "from", "in", "into", "of", "on", "or", "the", "to",
         "upon", "with"}
NOT_ACRONYMS = {"MR", "MRS", "MS", "DR", "ST", "SS"}  # no vowel, but words: Mr, St
PLURALS = {"MPS": "MPs", "MSPS": "MSPs", "MLAS": "MLAs", "GPS": "GPs", "MEPS": "MEPs"}


def _word(word: str, first: bool) -> str:
    core = re.sub(r"[^A-Za-z0-9]", "", word).upper()
    if core in PLURALS:
        return word.upper().replace(core, PLURALS[core])
    if core in ACRONYMS or (len(core) >= 2 and core.isalpha() and not re.search(r"[AEIOUY]", core)
                            and core not in NOT_ACRONYMS):
        return word.upper()
    lower = word.lower()
    if not first and core.lower() in SMALL and lower.strip(".,;:") == core.lower():
        return lower
    # each part of a hyphenated word, and the first letter after an opening bracket or quotation mark
    cased = re.sub(r"(^|[-(\"'‘“/])([a-z])", lambda m: m.group(1) + m.group(2).upper(), lower)
    return re.sub(r"(['’])S\b", r"\1s", cased)  # People's, not People'S


def title_case(text: str) -> str:
    """A title written in capitals, as a title: 'CONSOLIDATED FUND (NO. 2) BILL' -> 'Consolidated Fund (No. 2) Bill',
    'CUTS TO THE NHS IN DEVON' -> 'Cuts to the NHS in Devon'. A title not in capitals (nearly all its letters: 'FUND
    (No. 2) BILL' is) is returned as it is."""
    letters = [c for c in text if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) < 0.85 * len(letters):
        return text
    words = text.split()
    return " ".join(_word(w, i == 0 or words[i - 1].endswith((":", "—", "–"))) for i, w in enumerate(words))


def shorten(text: str, limit: int) -> str:
    """``text`` cut at a word boundary to at most ``limit`` characters, with an ellipsis if anything was cut."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    if text[limit - 1].isalnum():  # the cut is inside a word: back to the word before it
        cut = cut.rsplit(" ", 1)[0]
    cut = cut.rstrip(" ,;:—–-")
    return cut + "…"
