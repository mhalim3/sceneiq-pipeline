"""Final card polish: headline that fits and agrees with the detail, plus a mid-length version.

The writer picks the shortVersion from its own alternatives and the repair step does not
re-trim, so cards could reach review with a headline over the cap or one that describes a
different claim than the full fact. One call per card (a) checks the headline, rewriting it if it
is over the cap, mismatched or unclear, and (b) writes the "long version": the full fact cut to
at most cfg.long_cap characters. Both use only what is already in the full fact.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from .. import config
from ..gemini import GeminiClient

log = logging.getLogger("sceneiq")

_PROMPT = """You are the final editor of a movie pause-screen card for the film "{film}".

HEADLINE (short): {short}
FULL FACT: {long}

1. short_matches_long: true only if the headline states the SAME main claim as the first sentence of the full fact. False if the headline is about a different claim, detail, or name than the full fact leads with.
2. standalone: true only if the headline is clear and interesting to someone who has not seen the film (no unexplained in-film devices like "his inner thought" or "this reboot").
3. fixed_short: if the headline is longer than {cap} characters, or short_matches_long is false, or standalone is false, write a headline of at most {cap} characters that states the full fact's main claim, names the film or person it is about, and leads with the most surprising specific. Otherwise return an empty string.
4. long_version: the full fact cut down to AT MOST {long_cap} characters (count them). It must stand on its own: name the people, restate the headline's claim, then keep the single most interesting specific (a quote, number, reason or second party). Complete sentences, plain words, no ellipsis. Use ONLY facts already in the full fact.
Use only facts already in the full fact for everything you write."""

_SCHEMA = {
    "type": "object",
    "properties": {"short_matches_long": {"type": "boolean"},
                   "standalone": {"type": "boolean"},
                   "fixed_short": {"type": "string"},
                   "long_version": {"type": "string"}},
    "required": ["short_matches_long", "standalone", "fixed_short", "long_version"],
}


_SHORTEN_PROMPT = """Shorten this text to AT MOST {target} characters (it is {n} now; the hard limit is {cap}). Keep the people's names, the main claim and the single most interesting specific. Complete sentences, plain words, no ellipsis. Use only what is in the text.

TEXT: {text}"""
_SHORTEN_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}


def shorten_to_cap(client: GeminiClient, cfg: config.PipelineConfig, text: str,
                   cap: int | None = None) -> str:
    """Models miscount characters, so over-long text is re-shortened with feedback on its
    actual length, aiming well under the cap. Returns the shortest attempt (may still be over)."""
    cap = cap or cfg.long_cap
    best = text
    for target in (cap - 8, cap - 18, cap - 30):
        if len(best) <= cap:
            break
        try:
            d = client.structured(
                cfg.quality_model,
                _SHORTEN_PROMPT.format(target=target, n=len(best), cap=cap, text=best),
                _SHORTEN_SCHEMA, temperature=0.2)
        except Exception:
            continue
        t = (d.get("text") or "").strip()
        if t and len(t) < len(best):
            best = t
    return best


def _polish_one(client: GeminiClient, cfg: config.PipelineConfig, film: str, rec) -> None:
    card = rec.card
    if card is None:
        return
    best_long = ""
    headline_done = False
    for attempt in range(3):
        try:
            d = client.structured(
                cfg.quality_model,
                _PROMPT.format(film=film, short=card.short_version, long=card.long_description,
                               cap=cfg.short_cap, long_cap=cfg.long_cap),
                _SCHEMA, temperature=0.0 if attempt == 0 else 0.3)
        except Exception:
            continue
        if not headline_done:
            fixed = (d.get("fixed_short") or "").strip()
            needs = (len(card.short_version) > cfg.short_cap
                     or not d.get("short_matches_long", True) or not d.get("standalone", True))
            if not needs:
                headline_done = True
            elif fixed and len(fixed) <= cfg.short_cap:
                old = card.short_version
                card.short_version = fixed
                headline_done = True
                if rec.validation is not None:
                    rec.validation.flags.append(f"polish: headline rewritten (was: {old})")
                log.info("  ✎ headline: %s -> %s", old[:60], fixed[:60])
        lv = (d.get("long_version") or "").strip()
        if lv and (not best_long or len(lv) < len(best_long)):
            best_long = lv
        if best_long and len(best_long) <= cfg.long_cap and headline_done:
            break
    if best_long and len(best_long) > cfg.long_cap:
        best_long = shorten_to_cap(client, cfg, best_long)
    if len(card.short_version) > cfg.short_cap:
        card.short_version = shorten_to_cap(client, cfg, card.short_version, cap=cfg.short_cap)
    card.long_version = best_long
    if rec.validation is not None:
        if len(card.short_version) > cfg.short_cap:
            rec.validation.flags.append("polish: headline still over the cap")
        if not best_long or len(best_long) > cfg.long_cap:
            rec.validation.flags.append(
                f"polish: long version is {len(best_long)} chars (cap {cfg.long_cap})")


def polish_cards(client: GeminiClient, cfg: config.PipelineConfig, film_info: dict,
                 records: list) -> None:
    film = film_info.get("title", "")
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(records)))) as pool:
        list(pool.map(lambda r: _polish_one(client, cfg, film, r), records))
