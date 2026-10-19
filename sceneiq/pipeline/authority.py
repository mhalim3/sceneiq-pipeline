"""Find articles the way a researcher would, not the way a search engine ranks them.

Grounded search returns a few self-chosen, SEO-ranked pages. A person researching a film
instead goes to the outlets they trust and looks for the film there, and follows the
references Wikipedia and the articles themselves cite. This module does that:

- `outlet_urls`        a film's pages on one named outlet (Google News RSS site: search, no key)
- `authority_sweep`    the film on each of the top A/B outlets
- `wikipedia_references` the external sources Wikipedia cites for the film (Wikipedia stays lead-only)
- `linked_articles`    A/B pages that already-found articles link to
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor

import httpx

from .. import config
from ..fetch import page_links
from ..tiers import classify_domain, registered_domain
from .origin_sources import _NOT_ARTICLES, _news_search
from .research import _fetch_candidate

log = logging.getLogger("sceneiq")

# Outlets worth checking by name for any film, most reliable and most film-focused first.
AUTHORITY_OUTLETS = [
    "variety.com", "hollywoodreporter.com", "deadline.com", "ew.com", "vulture.com",
    "nytimes.com", "latimes.com", "theguardian.com", "theringer.com", "avclub.com",
    "indiewire.com", "slashfilm.com", "collider.com", "empireonline.com", "thewrap.com",
    "vanityfair.com", "gq.com", "rollingstone.com", "washingtonpost.com", "newsweek.com",
    "thrillist.com", "polygon.com", "npr.org", "bbc.com", "time.com", "esquire.com",
    "people.com", "usatoday.com", "screenrant.com", "insider.com", "eonline.com", "denofgeek.com",
]

_STOP = set("the a an of to and in is was as for by with his her that this it on at from he she be who "
            "film movie were are had has have their they them also into after before during while".split())
_UA = {"User-Agent": "SceneIQ-pipeline/0.1 (mhalim@tubi.tv)"}


def key_words(film: str, fact: str, n: int = 4) -> str:
    """The most specific words of a fact (no title words, no filler), for short news queries."""
    tt = {t.lower() for t in re.findall(r"[A-Za-z0-9']+", film)}
    words = [w for w in re.findall(r"[A-Za-z0-9']+", fact)
             if w.lower() not in _STOP and w.lower() not in tt and len(w) > 3]
    return " ".join(words[:n])


def outlet_urls(domain: str, queries: list[str], n: int = 3) -> list[str]:
    urls: list[str] = []
    for q in queries:
        for u in _news_search(domain, q, n):
            if u not in urls:
                urls.append(u)
    return urls[:n * 2]


def _fetch_many(urls: list[str], film: dict, cfg, cache, client, have: set,
                domains: set | None = None) -> list:
    """Fetch urls, keep A/B text pages."""
    urls = [u for u in dict.fromkeys(urls) if u not in have]
    with ThreadPoolExecutor(max_workers=cfg.fetch_workers) as pool:
        got = list(pool.map(lambda u: _fetch_candidate({"url": u, "title": ""}, str(film.get("title", "")),
                                                       film.get("year"), cfg, cache, client=client), urls))
    out = []
    for s in got:
        if s is None or s.tier not in ("A", "B") or s.modality != "text" or not getattr(s, "_body", ""):
            continue
        if domains is not None and s.domain not in domains:
            continue
        out.append(s)
    return out


def authority_sweep(client, cfg: config.PipelineConfig, film_info: dict, cache, have: set) -> list:
    """The film's own pages on the top outlets."""
    film = str(film_info.get("title", ""))
    year = film_info.get("year", "")
    qs = [f"{film} {year}", f"{film} interview", f"{film} behind the scenes"]
    outlets = AUTHORITY_OUTLETS[: cfg.authority_outlets]
    with ThreadPoolExecutor(max_workers=6) as pool:
        lists = list(pool.map(lambda d: outlet_urls(d, qs, cfg.authority_per_outlet), outlets))
    urls = [u for l in lists for u in l]
    log.info("  authority sweep: %d candidate page(s) from %d outlets", len(urls), len(outlets))
    return _fetch_many(urls, film_info, cfg, cache, client, have)


def wikipedia_references(client, cfg: config.PipelineConfig, film_info: dict, cache, have: set) -> list:
    """A/B pages cited in the film's Wikipedia article. Wikipedia itself is never a source."""
    film = str(film_info.get("title", ""))
    try:
        with httpx.Client(timeout=15, headers=_UA) as http:
            r = http.get("https://en.wikipedia.org/w/api.php", params={
                "action": "query", "list": "search", "srsearch": f"{film} {film_info.get('year', '')} film",
                "srlimit": 1, "format": "json"})
            hits = r.json().get("query", {}).get("search", [])
            if not hits:
                return []
            r = http.get("https://en.wikipedia.org/w/api.php", params={
                "action": "parse", "page": hits[0]["title"], "prop": "externallinks",
                "format": "json", "redirects": 1})
            links = r.json().get("parse", {}).get("externallinks", [])
    except Exception as e:
        log.info("  wikipedia references unavailable: %s", e)
        return []
    urls = []
    for u in links:
        dom = registered_domain(u)
        if dom in _NOT_ARTICLES or "wikipedia" in dom or "archive.org" in dom:
            continue
        # Known-good outlets only up front; unlisted ones are skipped to bound the fetches.
        if classify_domain(u) in ("A", "B"):
            urls.append(u)
    urls = urls[: cfg.wiki_ref_max]
    log.info("  wikipedia references: %d A/B link(s) to read", len(urls))
    return _fetch_many(urls, film_info, cfg, cache, client, have)


def linked_articles(client, cfg: config.PipelineConfig, film_info: dict, cache, pages: list, have: set) -> list:
    """A/B pages that the strong articles link to (a researcher follows the citations)."""
    urls = []
    for s in pages:
        for u in page_links(s.url):
            dom = registered_domain(u)
            if dom == s.domain or dom in _NOT_ARTICLES or classify_domain(u) not in ("A", "B"):
                continue
            if u.rstrip("/").count("/") < 3:            # a site front page, not an article
                continue
            urls.append(u)
    urls = list(dict.fromkeys(urls))[: cfg.link_follow_max]
    if urls:
        log.info("  following %d A/B link(s) from strong articles", len(urls))
    return _fetch_many(urls, film_info, cfg, cache, client, have)
