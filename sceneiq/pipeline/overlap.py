"""Copying check: a card must be in new words, not a sentence lifted from a source.

Flags any run of `min_words` or more words in a row that the card shares with a cited source.
Text inside quotation marks is ignored (a named person's words may be quoted). Catches copied
wording only, not a close paraphrase.
"""

from __future__ import annotations

import re

_QUOTED = re.compile(r"[\"“”][^\"“”]{3,}[\"“”]")


def _words(t: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (t or "").lower())


def copied_runs(text: str, bodies: list[str], min_words: int = 8) -> list[str]:
    """Phrases (at least `min_words` long) that `text` shares word for word with any body."""
    # Cut quoted text out, leaving a break so runs cannot span it.
    parts = [p for p in _QUOTED.sub("\n", text or "").split("\n")]
    grams: set = set()
    for b in bodies:
        w = _words(b)
        grams.update(tuple(w[i:i + min_words]) for i in range(len(w) - min_words + 1))
    found: list[str] = []
    for p in parts:
        w = _words(p)
        i = 0
        while i <= len(w) - min_words:
            if tuple(w[i:i + min_words]) in grams:
                j = i + min_words
                while j < len(w) and tuple(w[j - min_words + 1:j + 1]) in grams:
                    j += 1
                found.append(" ".join(w[i:j]))
                i = j
            else:
                i += 1
    return found
