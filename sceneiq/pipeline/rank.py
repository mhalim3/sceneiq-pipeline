"""Rank the kept cards against each other (best first) and score them 1-5.

Scoring each card alone saturates (almost everything gets a 4 or 5); ranking a title's cards
together separates the standout facts from the ordinary ones. Used to pick the top picks.
"""

from __future__ import annotations

from .. import config, editorial_guide
from ..gemini import GeminiClient

_PROMPT = """You are the editor choosing the best facts for a movie pause screen about "{film}". Below are {n} candidate cards. Rank them from MOST to LEAST interesting to a general viewer who has not seen behind-the-scenes material, then give each a score.

What makes a card great: a named person doing or saying something unexpected; a near-miss casting; a surprising production fact; the real-world origin of something in the film; a concrete detail the viewer will retell, and a headline that is interesting and clear on its own. What makes it weak: a plain credit, a statistic, a release or home-video detail, a filming location with no story, anything obvious from watching the film, a headline that only makes sense after reading the detail.

{exclusions}
If a card falls under one of those kinds, rank it last.

{favored}

Score: 5 = standout (use for at most the best quarter of the cards), 4 = good, 3 = fine but ordinary, 2 = weak, 1 = filler. Compare the cards against each other; do not give everything a 4 or 5.

CARDS:
{cards}

Return every card index exactly once in `ranking` (best first), and a score and one-sentence note per card."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "ranking": {"type": "array", "items": {"type": "integer"}},
        "scores": {"type": "array", "items": {
            "type": "object",
            "properties": {"index": {"type": "integer"}, "score": {"type": "integer"},
                           "why": {"type": "string"}},
            "required": ["index", "score", "why"]}},
    },
    "required": ["ranking", "scores"],
}


def rank_cards(client: GeminiClient, cfg: config.PipelineConfig, film_info: dict,
               records: list) -> None:
    """Sets rec.rank (1 = best), rec.rank_score and rec.rank_note on each record."""
    if not records:
        return
    cards = "\n".join(f"[{k}] {r.card.short_version} | {r.card.long_description}"
                      for k, r in enumerate(records))
    try:
        d = client.structured(
            cfg.quality_model,
            _PROMPT.format(film=film_info.get("title", ""), n=len(records), cards=cards,
                           exclusions=editorial_guide.exclusions_block(),
                           favored=editorial_guide.FAVORED),
            _SCHEMA, temperature=0.0)
    except Exception:
        return
    order = [k for k in d.get("ranking", []) if 0 <= k < len(records)]
    order += [k for k in range(len(records)) if k not in order]
    notes = {s["index"]: s for s in d.get("scores", [])}
    for pos, k in enumerate(order, 1):
        r = records[k]
        r.rank = pos
        r.rank_score = int(notes.get(k, {}).get("score") or 0)
        r.rank_note = notes.get(k, {}).get("why", "")
