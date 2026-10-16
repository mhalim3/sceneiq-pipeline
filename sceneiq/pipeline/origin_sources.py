"""Find the original article an A/B page cites by name but does not link.

"Czapsky remembered (via Thrillist)", "McKay told Coming Soon": the outlet is named in
the text but there is no link, so link-following never reaches it. This step asks which
outlets the fetched pages credit for the fact, searches for each one by name, and
fetches the matching pages so they can corroborate the fact (and be the cited source).
"""

from __future__ import annotations

import html as _html
import logging
import os
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import httpx

from .. import config
from ..models import Anchor, SourceRef
from ..tiers import registered_domain
from .research import _fetch_candidate

log = logging.getLogger("sceneiq")

# Credited \"outlets\" that are not articles to look up (formats, social platforms, video sites).
_NOT_ARTICLES = {"youtube.com", "youtu.be", "facebook.com", "instagram.com", "twitter.com", "x.com",
                 "tiktok.com", "reddit.com", "wikipedia.org", "imdb.com", "amazon.com"}
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


_search_lock = threading.Lock()
_last_search = 0.0
_blocked_until = 0.0


def _api_search(domain: str, query: str, n: int) -> list[str] | None:
    """Real Google results through Serper (SERPER_API_KEY) or Brave (BRAVE_API_KEY).
    Returns None when no key is configured, so the caller can fall back."""
    q = f"site:{domain} {query}"
    try:
        if os.environ.get("SERPER_API_KEY"):
            r = httpx.post("https://google.serper.dev/search", json={"q": q, "num": 10},
                           headers={"X-API-KEY": os.environ["SERPER_API_KEY"]}, timeout=20)
            r.raise_for_status()
            links = [x.get("link", "") for x in r.json().get("organic", [])]
        elif os.environ.get("BRAVE_API_KEY"):
            r = httpx.get("https://api.search.brave.com/res/v1/web/search", params={"q": q, "count": 10},
                          headers={"X-Subscription-Token": os.environ["BRAVE_API_KEY"],
                                   "Accept": "application/json"}, timeout=20)
            r.raise_for_status()
            links = [x.get("url", "") for x in r.json().get("web", {}).get("results", [])]
        else:
            return None
    except Exception as e:
        log.warning("  search API failed for %s: %s", domain, e)
        return []
    return [u for u in dict.fromkeys(links) if u.startswith("http") and registered_domain(u) == domain][:n]


def site_search(domain: str, query: str, n: int = 3) -> list[str]:
    """Top results for `query` on one site, via DuckDuckGo's HTML endpoint (the grounded
    search tool will not reliably return a named outlet's page, plain site: search does).

    DuckDuckGo throttles scripted use, so calls are spaced out and, once it answers with
    its bot check, this stops asking for 10 minutes. A real search API key would remove
    this limit."""
    global _last_search, _blocked_until
    api = _api_search(domain, query, n)
    if api is not None:
        return api
    with _search_lock:
        if time.time() < _blocked_until:
            return []
        time.sleep(max(0.0, 3.0 - (time.time() - _last_search)))
        _last_search = time.time()
        try:
            r = httpx.get("https://html.duckduckgo.com/html/", params={"q": f"site:{domain} {query}"},
                          headers=_UA, follow_redirects=True, timeout=20)
        except Exception as e:
            log.warning("  site search failed for %s: %s", domain, e)
            return []
        if r.status_code != 200 or "result__a" not in r.text:
            _blocked_until = time.time() + 600
            log.warning("  site search is being throttled (HTTP %s); skipping named-origin lookups for 10 min",
                        r.status_code)
            return []
    out = []
    for m in re.finditer(r'class="result__a" href="([^"]+)"', r.text):
        href = _html.unescape(m.group(1))
        q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        url = (q.get("uddg") or [href])[0]
        if url.startswith("http") and registered_domain(url) == domain and url not in out:
            out.append(url)
        if len(out) >= n:
            break
    return out


_STOP = set("the a an of to and in is was as for by with his her that this it on at from he she be who were are".split())

_SCHEMA = {
    "type": "object",
    "properties": {"outlets": {"type": "array", "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "domain": {"type": "string"},
                       "credited_for": {"type": "string"}},
        "required": ["name", "domain", "credited_for"]}}},
    "required": ["outlets"],
}

_PROMPT = """Fact about the film "{film}":
{fact}

Below are passages from web pages that discuss this fact. List every PUBLICATION, website, magazine, newspaper, podcast or TV outlet that the passages credit as where the information (a quote, interview or report) ORIGINALLY came from, for example "via Thrillist", "told Uproxx", "reported by Variety", "the DVD documentary". Do NOT list the page's own site, and do not list people. For each give the outlet name, its most likely website domain (thrillist.com), and a few words on what it is credited for. If none is credited, return an empty list.

{passages}"""


def _passages(sources: list[SourceRef], fact: str, per: int = 900) -> str:
    fw = {w for w in re.findall(r"[a-z0-9']+", fact.lower()) if len(w) > 2 and w not in _STOP}
    out = []
    for s in sources:
        body = getattr(s, "_body", "") or ""
        if not body or s.modality != "text":
            continue
        sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+", body) if len(x.strip()) > 25]
        scored = sorted(range(len(sents)),
                        key=lambda i: -len(fw & set(re.findall(r"[a-z0-9']+", sents[i].lower()))))[:5]
        text = " ".join(sents[i] for i in sorted(scored))[:per]
        out.append(f"[{s.domain}] {text}")
    return "\n\n".join(out[:8])


def find_named_origins(client, film_info: dict, anchor: Anchor, fact: str, sources: list[SourceRef],
                       cfg: config.PipelineConfig, cache) -> list[SourceRef]:
    """New pages (not already in `sources`) from outlets the existing pages credit by name."""
    film = str(film_info.get("title", ""))
    passages = _passages(sources, fact)
    if not passages:
        return []
    try:
        d = client.structured(cfg.quality_model, _PROMPT.format(film=film, fact=fact, passages=passages),
                              _SCHEMA, temperature=0.0)
    except Exception as e:
        log.warning("  named-origin lookup failed: %s", e)
        return []
    have = {s.url for s in sources}
    have_dom = {s.domain for s in sources}
    outlets = [o for o in d.get("outlets", []) if o.get("domain") and o["domain"].lower() not in have_dom]
    outlets = outlets[: cfg.origin_max_outlets]
    if not outlets:
        return []
    title = f"{film} {film_info.get('year', '')}".strip()

    def _one(o):
        dom = registered_domain("https://" + o["domain"].lower().replace("https://", "").replace("http://", ""))
        if dom in _NOT_ARTICLES or "." not in dom:
            return o["name"], []
        words = " ".join(w for w in re.findall(r"[A-Za-z0-9']+", fact) if w.lower() not in _STOP)[:90]
        urls = site_search(dom, f"{film} {words}") or site_search(dom, f"{film} {film_info.get('year', '')}")
        found = []
        for u in urls:
            if u in have:
                continue
            src = _fetch_candidate({"url": u, "title": ""}, film, film_info.get("year"), cfg, cache, client=client)
            if src is not None and src.domain == dom and src.modality == "text":
                found.append(src)
        return o["name"], found

    out: list[SourceRef] = []
    with ThreadPoolExecutor(max_workers=max(1, len(outlets))) as pool:
        for name, found in pool.map(_one, outlets):
            for src in found[:2]:
                if src.url not in have:
                    have.add(src.url)
                    out.append(src)
            log.info("  named origin %r: %d page(s) found", name, len(found))
    return out
