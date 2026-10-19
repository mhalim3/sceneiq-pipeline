"""Mine strong A/B articles for every interesting fact in them.

A long oral history or interview (The Ringer on Step Brothers, Newsweek on Matilda) can
hold a dozen good facts, but the pipeline normally takes one fact from a page. This reads
the full text of each strong article found for the title and turns each distinct,
interesting fact into a new candidate. Each fact carries a verbatim quote from the
article, which is checked against the page text, and the article as its seed source.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

from .. import config, editorial_guide
from ..models import Anchor

log = logging.getLogger("sceneiq")

_SCHEMA = {
    "type": "object",
    "properties": {"facts": {"type": "array", "items": {
        "type": "object",
        "properties": {"fact_summary": {"type": "string"}, "quote": {"type": "string"},
                       "category_guess": {"type": "string"},
                       "search_queries": {"type": "array", "items": {"type": "string"}}},
        "required": ["fact_summary", "quote", "category_guess", "search_queries"]}}},
    "required": ["facts"],
}

_PROMPT = """You are reading a full article about the film "{film}" ({year}) from {domain}. Pull out the facts in it that a viewer pausing the movie would find interesting.

{guide}

Rules:
- Up to {max_facts} DISTINCT facts, best first. One fact per item: who did or said what, with the names, numbers and the vivid specific (a quote, an object, a count).
- Each must be understandable on its own (name characters and actors, never "the lead").
- Only facts this article states. Copy ONE supporting passage word for word from the article into "quote".
- Skip anything on the "already have" list or that repeats it in different words.
- Skip kinds of fact listed under DO NOT SHOW.
{focus}- category_guess: one word (casting, filming, production, music, trivia, cast...). search_queries: 2 runnable searches that would find another source for it.

ALREADY HAVE:
{have}

ARTICLE:
{body}"""


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _about_film(body: str, url: str, film_title: str) -> int:
    """How strongly the page is about this film: title mentions, doubled if it is in the URL
    or the first 600 characters (a long page that merely mentions the film does not count)."""
    t = film_title.lower()
    n = body.lower().count(t)
    slug = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    head = body[:600].lower()
    if slug in url.lower() or t in head:
        n += 10
    return n


def strong_articles(records: list, cfg: config.PipelineConfig, film_title: str = "") -> list:
    """Unique long A/B text pages about this film found while researching `records`, best first."""
    seen: dict = {}
    for r in records:
        packet = getattr(r, "evidence", None)
        if not packet:
            continue
        for s in packet.sources:
            body = getattr(s, "_body", "") or ""
            if s.modality != "text" or s.tier not in ("A", "B") or len(body) < cfg.mine_min_chars:
                continue
            if film_title and _about_film(body, s.url, film_title) < cfg.mine_min_title_mentions:
                continue
            e = seen.setdefault(s.url, [s, 0])
            e[1] += 1
    ranked = sorted(seen.values(), key=lambda e: (-_about_film(getattr(e[0], "_body", ""), e[0].url, film_title),
                                                  -e[1], -len(getattr(e[0], "_body", ""))))
    return [e[0] for e in ranked[: cfg.mine_max_articles]]


def mine_article(client, cfg: config.PipelineConfig, film_info: dict, src, have: list[str],
                 focus: str = "") -> list[Anchor]:
    body = getattr(src, "_body", "")[:60000]
    film = str(film_info.get("title", ""))
    try:
        d = client.structured(
            cfg.quality_model,
            _PROMPT.format(film=film, year=film_info.get("year", ""), domain=src.domain,
                           guide=editorial_guide.interest_block() + "\n\n" + editorial_guide.exclusions_block(),
                           max_facts=cfg.mine_facts_per_article,
                           focus=(focus + "\n") if focus else "",
                           have="\n".join(f"- {h[:160]}" for h in have[-60:]) or "(nothing yet)",
                           body=body),
            _SCHEMA, temperature=0.2)
    except Exception as e:
        log.warning("  article mining failed for %s: %s", src.domain, e)
        return []
    nb = _norm(body)
    anchors, kept = [], list(have)
    for f in d.get("facts", []):
        fs, q = (f.get("fact_summary") or "").strip(), (f.get("quote") or "").strip()
        if not fs or not q or _norm(q) not in nb:          # the quote must really be in the article
            continue
        if any(SequenceMatcher(None, fs.lower(), h.lower()).ratio() > 0.6 for h in kept):
            continue
        kept.append(fs)
        anchors.append(Anchor(
            scene_description="GENERAL CARD — shown at any point during the film, not tied to a specific scene.",
            anchor_element=fs, runtime_fraction=0.0, approx_timecode="",
            category_guess=f.get("category_guess") or "general", search_hint=fs,
            search_queries=list(f.get("search_queries") or [])[:3], end_fraction=0.0,
            scope="general", scene_dependency="agnostic", scene_hint="", origin="article_mining",
            seed_sources=[{"url": src.url, "title": src.domain}]))
    log.info("  mined %d new fact(s) from %s", len(anchors), src.url[:80])
    return anchors


def mine_all(client, cfg, film_info, records: list, explored: list[str]) -> list[Anchor]:
    out: list[Anchor] = []
    have = list(explored)
    for src in strong_articles(records, cfg, str(film_info.get("title", ""))):
        got = mine_article(client, cfg, film_info, src, have)
        have += [a.anchor_element for a in got]
        out += got
    return out
