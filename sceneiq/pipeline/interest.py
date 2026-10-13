"""Interest judge: how interesting is this card to a general viewer, 1-5?

Calibrated against the reviewer's (Lia's) ratings of real cards (data/eval/ratings.json):
the Pro model with this rubric tracks her ratings at Spearman ~0.74 on 29 cards, versus
~0.25 for a generic "casual fan" prompt. It separates her 1-3 rated cards (scores 1-3)
from her 4-5 rated cards (scores 3-5). Used as a selection gate: a card scoring at or
below cfg.min_interest - 1 is rejected as not interesting enough.
"""

from __future__ import annotations

from .. import config
from ..gemini import GeminiClient

_PROMPT = """You are an editor rating trivia cards for a movie pause screen. Rate how INTERESTING the card is to a general viewer, 1-5, as a viewer who has not seen behind-the-scenes material.

Film: {film}
Card headline: {short}
Card detail: {long}

Scoring guide:
5 = a surprising, concrete story a viewer would retell: a named person doing or saying something unexpected, a near-miss casting, a startling production fact, or a real-world origin of something in the film. The headline itself has the hook.
4 = clearly interesting, but the headline buries or blurs the best detail.
3 = mildly interesting; true but an expected, generic or unmemorable fact.
2 = boring: describes a design or creative intention, a plain statistic, or something already obvious from watching the film.
1 = trivial: a detail that merely identifies what is on screen, or says nothing a viewer would care about.
Judge the fact's appeal, not the writing polish."""

_SCHEMA = {
    "type": "object",
    "properties": {"score": {"type": "integer", "minimum": 1, "maximum": 5},
                   "why": {"type": "string"}},
    "required": ["score", "why"],
}


def interest_score(client: GeminiClient, film_title: str, short: str, long: str,
                   cfg: config.PipelineConfig) -> tuple[int | None, str]:
    """Returns (score 1-5, reason); (None, '') if the judge call fails."""
    for _ in range(2):
        try:
            d = client.structured(
                cfg.interest_model,
                _PROMPT.format(film=film_title, short=short, long=long),
                _SCHEMA, temperature=0.0)
            return int(d["score"]), d.get("why", "")
        except Exception:
            continue
    return None, ""


_BATCH_PROMPT = """You are an editor choosing which trivia facts about the film "{film}" are worth turning into a movie pause-screen card. Rate each candidate fact's INTERESTING-ness to a general viewer, 1-5, using this guide:

{guide}

CANDIDATE FACTS:
{facts}

Return one score per fact, in the same order (use the fact's number as "id")."""

_BATCH_SCHEMA = {
    "type": "object",
    "properties": {"scores": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "score": {"type": "integer", "minimum": 1, "maximum": 5}},
        "required": ["id", "score"]}}},
    "required": ["scores"],
}


def score_facts(client: GeminiClient, film_title: str, facts: list[str],
                cfg: config.PipelineConfig) -> list[int | None]:
    """Score raw candidate facts (before any card is written). Batched, in chunks of 25."""
    from .. import editorial_guide
    out: list[int | None] = [None] * len(facts)
    for start in range(0, len(facts), 25):
        chunk = facts[start:start + 25]
        listing = "\n".join(f"{i}. {f}" for i, f in enumerate(chunk))
        for _ in range(2):
            try:
                d = client.structured(
                    cfg.interest_model,
                    _BATCH_PROMPT.format(film=film_title, guide=editorial_guide.interest_block(),
                                         facts=listing),
                    _BATCH_SCHEMA, temperature=0.0)
                for s in d["scores"]:
                    if 0 <= s["id"] < len(chunk):
                        out[start + s["id"]] = int(s["score"])
                break
            except Exception:
                continue
    return out
