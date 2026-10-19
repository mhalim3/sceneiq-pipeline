"""Stage 2: Build a per-anchor source bank (fetch-then-write).

Architecture adopted from the scene-sense prototype's two-pass flow:

  Pass 1 (discovery):  run each of the anchor's explicit search queries through
                       Gemini + Google Search grounding. We never trust the LLM
                       to emit URLs — only the grounding citations count.
  Pass 2 (fetch):      resolve redirects, fetch every candidate URL's actual
                       text (BeautifulSoup for pages, caption transcript with
                       segment timing for video), apply the title-grounding
                       gate, tag domain tiers, keep the top N by tier.

Downstream, card assembly writes ONLY from these fetched bodies — grounding is
structural, not judged after the fact.
"""

from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from .. import config, source_policy
from .. import channel_tiers, domain_tiers
from ..fetch import FetchCache, _run_with_timeout, page_links, title_grounded
from ..gemini import GeminiClient
from ..models import Anchor, EvidencePacket, SourceRef
from ..tiers import classify_domain, registered_domain

log = logging.getLogger("sceneiq")

_QUERY_PROMPT = """Run a Google search for: {query}

After searching, describe the top 4-8 most authoritative results for insider \
production details about the film {film_title} ({film_year}). Do NOT invent any \
URL — only describe what the search returned. Prefer the most authoritative \
sources, in this order: PRIMARY (filmmaker/cast/crew/studio interviews, commentary, \
press kits, official records), then established trade press and major publications, \
over listicles, blogs and fan content."""

_TIER_ORDER = {"A": 0, "B": 1, "unknown": 2, "C": 3}


def _fallback_queries(film_info: dict, anchor: Anchor) -> list[str]:
    title = f"{film_info.get('title', '')} {film_info.get('year', '')}".strip()
    return [
        f"{title} {anchor.anchor_element}",
        f"{title} {anchor.category_guess} behind the scenes {anchor.anchor_element}",
    ]


def _run_query(client: GeminiClient, cfg: config.PipelineConfig, film_info: dict, q: str):
    grounded = client.grounded(
        cfg.research_model,
        _QUERY_PROMPT.format(
            query=q,
            film_title=film_info.get("title", ""),
            film_year=film_info.get("year", ""),
        ),
        temperature=0.1,
    )
    return q, grounded


_VIDEO_PROMPT = """Watch this video. If it is NOT about the film "{title}" ({year}) or does not discuss the topic below, reply with exactly: NONE

TOPIC: {fact}

Otherwise reply in this form:
VIDEO: <title and channel> | ABOUT: <what film or production it covers>
Then transcribe, as close to word for word as you can, ONLY the spoken passages that discuss the topic, each preceded by its [mm:ss] timestamp and the speaker if known. Do not summarize, do not paraphrase, and do not add anything that is not said."""


def _video_notes(client, cfg, url: str, film_title: str, film_year, fact: str) -> str | None:
    """Have Gemini watch the video and transcribe the passages about the fact."""
    from google.genai import types
    def _call():
        resp = client._client.models.generate_content(
            model=config.VIDEO_MODEL,
            contents=types.Content(parts=[
                types.Part(file_data=types.FileData(file_uri=url)),
                types.Part(text=_VIDEO_PROMPT.format(title=film_title, year=film_year, fact=fact)),
            ]))
        return resp.text or ""
    text = (_run_with_timeout(_call, 120.0, "") or "").strip()
    if not text or text.upper().startswith("NONE") or "VIDEO:" not in text:
        return None
    return text


def _video_topic(anchor: Anchor, film_title: str) -> str:
    """A neutral topic for the video note-taker. The claim itself is NOT passed in: a model
    handed the claim tends to echo it back as if the video said it."""
    qs = [q for q in (anchor.search_queries or []) if q]
    return qs[0] if qs else f"making of {film_title}"


def _fact_relevant(fact: str, film_title: str, body: str) -> bool:
    """Cheap check that a followed page actually discusses this fact's names/numbers."""
    skip = {t.lower() for t in re.split(r"\W+", film_title)}
    toks = {t for t in re.findall(r"[A-Z][a-z]{3,}|\d[\d,]*", fact) if t.lower() not in skip}
    if len(toks) < 2:
        return True
    low = body.lower()
    hits = sum(1 for t in toks if t.lower() in low)
    return hits >= max(2, (len(toks) + 1) // 2)


def _fetch_candidate(cand: dict, film_title: str, film_year, cfg: config.PipelineConfig,
                     cache: FetchCache, client=None, fact: str = "",
                     video_budget: dict | None = None) -> SourceRef | None:
    ok, final_url, body = cache.page(cand["url"])
    if not ok:
        return None
    domain = registered_domain(final_url)
    if domain in config.SKIP_FETCH_DOMAINS:
        return None
    tier = classify_domain(final_url)
    if tier == "unknown" and client is not None and domain not in config.VIDEO_DOMAINS and body:
        # Unlisted outlet: rate it once with Wikipedia's reliability criteria (cached).
        domain_tiers.resolve(client, cfg, domain, cand.get("title", ""), body)
        tier = classify_domain(final_url)
    src = SourceRef(
        url=final_url,
        title=cand.get("title", ""),
        domain=domain,
        tier=tier,
        resolved=True,
    )
    if domain in config.VIDEO_DOMAINS:
        if client is not None and "youtube" in domain:
            ch = channel_tiers.rate_video(client, cfg, final_url)
            if ch:
                src.channel, src.channel_verified = ch["channel"], ch["verified"]
                src.channel_kind = ch["kind"]
                if ch["tier"] in ("A", "B", "C"):
                    src.tier = ch["tier"]
        if video_budget is not None:
            with video_budget["lock"]:
                if video_budget["n"] <= 0:
                    return None
                video_budget["n"] -= 1
        t_ok, transcript, segments = cache.transcript(final_url)
        src.modality = "video"
        if t_ok and transcript:
            src.body_kind = "transcript"
            src._body = transcript                      # type: ignore[attr-defined]
            src._segments = segments                    # type: ignore[attr-defined]
        else:
            # Transcript unavailable (often blocked): let Gemini watch the video and
            # transcribe the relevant passages. Weaker evidence, so cards that rely on
            # it are flagged for human review.
            notes = (_video_notes(client, cfg, final_url, film_title, film_year, fact)
                     if (client is not None and cfg.use_video_sources and fact) else None)
            if not notes:
                return None  # unverifiable video never enters the bank
            src.body_kind = "gemini_video"
            src._body = notes                           # type: ignore[attr-defined]
            src._segments = []                          # type: ignore[attr-defined]
    else:
        if not body:
            return None
        src.modality = "text"
        src.body_kind = "html"
        src._body = body                                # type: ignore[attr-defined]
        src._segments = []                              # type: ignore[attr-defined]
    if not title_grounded(film_title, src._body, year=film_year):  # type: ignore[attr-defined]
        return None
    return src


def research_anchor(
    client: GeminiClient,
    film_info: dict,
    anchor: Anchor,
    cfg: config.PipelineConfig,
    cache: FetchCache | None = None,
) -> EvidencePacket:
    cache = cache or FetchCache(cfg.http_timeout_s)
    queries = (anchor.search_queries or _fallback_queries(film_info, anchor))[: cfg.queries_per_anchor]
    # Also search the fact itself: the generated queries are often too generic to
    # surface the article that actually states this specific claim.
    if anchor.search_queries:
        queries.append(f"{film_info.get('title', '')} {film_info.get('year', '')} {anchor.anchor_element}".strip())
    # And look for a video interview/featurette that talks about it.
    if cfg.use_video_sources:
        queries.append(f"{film_info.get('title', '')} {film_info.get('year', '')} "
                       f"{anchor.anchor_element[:120]} interview video YouTube")
    video_budget = {"n": cfg.max_video_sources, "lock": threading.Lock()}

    # Pass 1: discovery — queries are independent, run them concurrently.
    seen: set[str] = set()
    candidates: list[dict] = []
    notes: list[str] = []
    # Pages the candidate search cited for this fact go first.
    for sd in anchor.seed_sources or []:
        url = (sd.get("url") or "").strip()
        if url and url not in seen:
            seen.add(url)
            candidates.append(dict(sd))
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        for q, grounded in pool.map(
            lambda q: _run_query(client, cfg, film_info, q), queries
        ):
            notes.append(f"[query] {q}\n{grounded.text}")
            for s in grounded.sources:
                url = (s.get("url") or "").strip()
                if url and url not in seen:
                    seen.add(url)
                    candidates.append(s)

    # Pass 2: fetch bodies concurrently through the shared cache, gate, tier.
    film_title = str(film_info.get("title", ""))
    with ThreadPoolExecutor(max_workers=cfg.fetch_workers) as pool:
        fetched = pool.map(
            lambda c: _fetch_candidate(c, film_title, film_info.get("year"), cfg, cache,
                                       client=client, fact=_video_topic(anchor, film_title),
                                       video_budget=video_budget), candidates
        )
        sources = [s for s in fetched if s is not None]
    # Same final URL can arrive via two redirect links — keep first.
    uniq: dict[str, SourceRef] = {}
    for s in sources:
        uniq.setdefault(s.url, s)
    sources = list(uniq.values())

    # "Double click" on citations: articles often cite the primary outlet (Business
    # Insider quoting the NYT). Follow outbound links to A/B-tier pages that discuss
    # this fact and add them as sources.
    if cfg.follow_citations and sources:
        have = {s.url for s in sources}
        links: list[str] = []
        for s in sources:
            if s.modality != "text":
                continue
            n = 0
            for link in page_links(s.url):
                dom = registered_domain(link)
                if (dom != s.domain and classify_domain(link) in ("A", "B")
                        and len(link.split("//", 1)[-1].split("/", 1)[-1]) > 12
                        and link not in have and link not in links):
                    links.append(link)
                    n += 1
                    if n >= 3:
                        break
        links = links[:6]
        if links:
            with ThreadPoolExecutor(max_workers=cfg.fetch_workers) as pool:
                extra = list(pool.map(
                    lambda u: _fetch_candidate({"url": u, "title": ""}, film_title,
                                               film_info.get("year"), cfg, cache, client=client), links))
            added = [s for s in extra if s is not None and s.url not in have
                     and _fact_relevant(anchor.anchor_element, film_title,
                                        getattr(s, "_body", ""))]
            if added:
                log.info("  followed citations -> %d page(s) for %r", len(added),
                         anchor.anchor_element[:40])
                sources += added

    # Nothing usable fetched (dead links, paywalls, off-topic hits)? Retry once
    # with broader fallback queries before giving up on the fact.
    if not sources and anchor.search_queries:
        fb = [q for q in _fallback_queries(film_info, anchor) if q not in queries]
        with ThreadPoolExecutor(max_workers=max(1, len(fb))) as pool:
            retry_cands = []
            for q, grounded in pool.map(lambda q: _run_query(client, cfg, film_info, q), fb):
                notes.append(f"[fallback query] {q}\n{grounded.text}")
                for s in grounded.sources:
                    url = (s.get("url") or "").strip()
                    if url and url not in seen:
                        seen.add(url)
                        retry_cands.append(s)
        with ThreadPoolExecutor(max_workers=cfg.fetch_workers) as pool:
            sources = [s for s in pool.map(
                lambda c: _fetch_candidate(c, film_title, film_info.get("year"), cfg, cache,
                                           client=client), retry_cands) if s is not None]
        uniq = {}
        for s in sources:
            uniq.setdefault(s.url, s)
        sources = list(uniq.values())

    sources.sort(key=lambda s: (_TIER_ORDER.get(s.tier, 4), -len(getattr(s, "_body", ""))))
    sources = sources[: cfg.sources_per_anchor]
    log.info("  sources for %r: %d fetched (%s)", anchor.anchor_element[:40],
             len(sources), ",".join(s.tier for s in sources) or "none")
    return EvidencePacket(anchor=anchor, findings="\n\n".join(notes), sources=sources)


def research_stronger_sources(
    client: GeminiClient,
    film_info: dict,
    anchor: Anchor,
    cfg: config.PipelineConfig,
    cache: FetchCache,
    have_urls: set[str],
) -> list[SourceRef]:
    """One targeted search for A/B-tier pages that state this fact.

    Used when a card's only support is unknown/C-tier pages. Returns only NEW
    pages from A- or B-tier domains (fetched, on-topic); empty if none found.
    """
    title = f"{film_info.get('title', '')} {film_info.get('year', '')}".strip()
    q = f"{title} {anchor.anchor_element} {source_policy.retry_query_suffix()}"
    _, grounded = _run_query(client, cfg, film_info, q)
    film_title = str(film_info.get("title", ""))
    cands, seen = [], set(have_urls)
    for s in grounded.sources:
        url = (s.get("url") or "").strip()
        if url and url not in seen:
            seen.add(url)
            cands.append(s)
    with ThreadPoolExecutor(max_workers=cfg.fetch_workers) as pool:
        fetched = pool.map(
            lambda c: _fetch_candidate(c, film_title, film_info.get("year"), cfg, cache,
                                       client=client), cands)
        found = [s for s in fetched if s is not None and s.tier in ("A", "B")
                 and s.url not in have_urls]
    uniq: dict[str, SourceRef] = {}
    for s in found:
        uniq.setdefault(s.url, s)
    return list(uniq.values())

