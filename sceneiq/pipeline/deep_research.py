"""Deep research: work like a person who is trying to find the best sources and use them fully.

1. Go to the trusted outlets by name and look for the film there (not just what search ranks high).
2. Read the references Wikipedia cites and follow the links inside strong articles.
3. The moment an article is strong, mine it in full, in more than one pass, so little is left unread.
4. Research each mined fact like any other (which finds more sources), then read the new strong
   articles those facts lead to. Repeat until a round turns up nothing new.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

from .. import config
from . import authority, corroborate, youtube_sources
from .mine_articles import _about_film, mine_article

log = logging.getLogger("sceneiq")

_PASS_FOCUS = [
    "",
    "SECOND PASS: the first pass already took the headline facts. Now look for the smaller specifics it "
    "missed: numbers and money, what a cast or crew member said in their own words, objects and props, "
    "locations, near-miss castings, accidents, improvised lines, and anything a fan would not already know.",
    "THIRD PASS: look only for facts that connect to something a viewer can SEE or DO: a thing visible on "
    "screen, an easter egg, a real place or product, or something they can look up or buy.",
]


def _is_strong(s, cfg, film: str) -> bool:
    body = getattr(s, "_body", "") or ""
    if s.modality == "video":      # a transcript from a rated A/B channel (found by title match)
        return s.tier in ("A", "B") and getattr(s, "body_kind", "") == "transcript" and len(body) >= 1200
    return (s.modality == "text" and s.tier in ("A", "B") and len(body) >= cfg.deep_min_chars
            and _about_film(body, s.url, film) >= cfg.deep_min_title_mentions)


def _add(corpus: dict, pages, cfg, film: str) -> list:
    new = []
    for s in pages:
        if s.url not in corpus and _is_strong(s, cfg, film):
            corpus[s.url] = s
            new.append(s)
    return new


def _record_pages(records) -> list:
    out = []
    for r in records:
        packet = getattr(r, "evidence", None)
        if packet:
            out += [s for s in packet.sources]
    return out


def _mine_passes(client, cfg, film_info, src, have: list[str]) -> list:
    got: list = []
    kept = list(have)
    for i in range(max(1, cfg.deep_mine_passes)):
        focus = _PASS_FOCUS[min(i, len(_PASS_FOCUS) - 1)]
        part = mine_article(client, cfg, film_info, src, kept, focus=focus)
        kept += [a.anchor_element for a in part]
        got += part
        if i > 0 and not part:
            break
    return got


def deep_research(client, cfg: config.PipelineConfig, film_info: dict, cache, records: list,
                  explored: list[str], emitted_text, process) -> list:
    """Returns the new CardRecords. `process(anchors)` researches anchors and returns their records;
    `emitted_text()` lists the text of cards approved so far (for the "already have" list)."""
    film = str(film_info.get("title", ""))
    corpus: dict = {}
    _add(corpus, _record_pages(records), cfg, film)
    seen_urls = {s.url for s in _record_pages(records)}

    # 1-2. Go to the outlets by name; read Wikipedia's references.
    found = []
    try:
        found += authority.authority_sweep(client, cfg, film_info, cache, seen_urls)
    except Exception as e:
        log.warning("  authority sweep failed (%s); continuing", e)
    try:
        found += authority.wikipedia_references(client, cfg, film_info, cache, seen_urls | {s.url for s in found})
    except Exception as e:
        log.warning("  wikipedia references failed (%s); continuing", e)
    if cfg.youtube_sources:
        try:
            found += youtube_sources.discover(client, cfg, film_info, cache, seen_urls | {s.url for s in found})
        except Exception as e:
            log.warning("  youtube discovery failed (%s); continuing", e)
    corroborate.register(found)
    seen_urls |= {s.url for s in found}
    added = _add(corpus, found, cfg, film)
    log.info("Deep research: %d strong article(s) in hand (%d new from outlets and Wikipedia's references)",
             len(corpus), len(added))

    mined_urls: set = set()
    new_records: list = []
    for rnd in range(1, cfg.deep_rounds + 1):
        def rank(s):
            return (-_about_film(getattr(s, "_body", ""), s.url, film), -len(getattr(s, "_body", "")))
        todo = sorted((s for s in corpus.values() if s.url not in mined_urls), key=rank)[: cfg.deep_articles_per_round]
        if not todo:
            log.info("Deep research round %d: no unread strong articles — done", rnd)
            break
        mined_urls |= {s.url for s in todo}
        log.info("Deep research round %d: mining %d article(s) in %d pass(es) ...", rnd, len(todo),
                 cfg.deep_mine_passes)
        have = explored + list(emitted_text())
        with ThreadPoolExecutor(max_workers=min(len(todo), 6)) as pool:
            parts = list(pool.map(lambda s: _mine_passes(client, cfg, film_info, s, have), todo))
        anchors: list = []
        for part in parts:          # articles overlap: drop repeats across them
            for a in part:
                if not any(SequenceMatcher(None, a.anchor_element.lower(), b.anchor_element.lower()).ratio() > 0.6
                           for b in anchors):
                    anchors.append(a)
        if not anchors:
            log.info("  round %d: nothing new mined", rnd)
        else:
            explored.extend(a.anchor_element for a in anchors)
            log.info("  round %d: researching %d mined fact(s) ...", rnd, len(anchors))
            recs = process(anchors)
            new_records += recs
            # The research on those facts may have turned up more strong articles.
            nf = _add(corpus, [s for s in _record_pages(recs) if s.url not in seen_urls], cfg, film)
            seen_urls |= {s.url for s in _record_pages(recs)}
            log.info("  round %d: %d new strong article(s) from that research", rnd, len(nf))
        # Follow the links inside the articles just mined.
        try:
            lk = authority.linked_articles(client, cfg, film_info, cache, todo, seen_urls)
        except Exception as e:
            log.warning("  link following failed (%s); continuing", e)
            lk = []
        seen_urls |= {s.url for s in lk}
        nl = _add(corpus, lk, cfg, film)
        if nl:
            log.info("  round %d: %d new strong article(s) from links", rnd, len(nl))
    return new_records
