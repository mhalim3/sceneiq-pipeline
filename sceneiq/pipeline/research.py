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

from .. import config
from ..fetch import fetch_url_text, fetch_youtube_transcript, title_grounded
from ..gemini import GeminiClient
from ..models import Anchor, EvidencePacket, SourceRef
from ..tiers import classify_domain, registered_domain

log = logging.getLogger("sceneiq")

_QUERY_PROMPT = """Run a Google search for: {query}

After searching, describe the top 4-8 most authoritative results for insider \
production details about the film {film_title} ({film_year}). Do NOT invent any \
URL — only describe what the search returned. Prefer primary/direct sources \
(filmmaker and talent interviews, commentary, production notes, trade press) \
over fan content."""

_TIER_ORDER = {"A": 0, "B": 1, "unknown": 2, "C": 3}


def _fallback_queries(film_info: dict, anchor: Anchor) -> list[str]:
    title = f"{film_info.get('title', '')} {film_info.get('year', '')}".strip()
    return [
        f"{title} {anchor.anchor_element}",
        f"{title} {anchor.category_guess} behind the scenes {anchor.anchor_element}",
    ]


def research_anchor(
    client: GeminiClient, film_info: dict, anchor: Anchor, cfg: config.PipelineConfig
) -> EvidencePacket:
    queries = (anchor.search_queries or _fallback_queries(film_info, anchor))[: cfg.queries_per_anchor]

    # Pass 1: discovery — collect grounding citation URLs across queries.
    seen: set[str] = set()
    candidates: list[dict] = []
    notes: list[str] = []
    for q in queries:
        grounded = client.grounded(
            cfg.deep_model,
            _QUERY_PROMPT.format(
                query=q,
                film_title=film_info.get("title", ""),
                film_year=film_info.get("year", ""),
            ),
            temperature=0.1,
        )
        notes.append(f"[query] {q}\n{grounded.text}")
        for s in grounded.sources:
            url = (s.get("url") or "").strip()
            if url and url not in seen:
                seen.add(url)
                candidates.append(s)

    # Pass 2: fetch bodies, gate, tier.
    sources: list[SourceRef] = []
    for cand in candidates:
        ok, final_url, body = fetch_url_text(cand["url"], cfg.http_timeout_s)
        if not ok:
            continue
        domain = registered_domain(final_url)
        if domain in config.SKIP_FETCH_DOMAINS:
            continue
        src = SourceRef(
            url=final_url,
            title=cand.get("title", ""),
            domain=domain,
            tier=classify_domain(final_url),
            resolved=True,
        )
        if domain in config.VIDEO_DOMAINS:
            t_ok, transcript, segments = fetch_youtube_transcript(final_url)
            if not t_ok or not transcript:
                continue  # video without transcript is unverifiable — never enters the bank
            src.modality = "video"
            src.body_kind = "transcript"
            src._body = transcript                      # type: ignore[attr-defined]
            src._segments = segments                    # type: ignore[attr-defined]
        else:
            if not body:
                continue
            src.modality = "text"
            src.body_kind = "html"
            src._body = body                            # type: ignore[attr-defined]
            src._segments = []                          # type: ignore[attr-defined]
        if not title_grounded(str(film_info.get("title", "")), src._body):  # type: ignore[attr-defined]
            continue
        sources.append(src)

    sources.sort(key=lambda s: (_TIER_ORDER.get(s.tier, 4), -len(getattr(s, "_body", ""))))
    sources = sources[: cfg.sources_per_anchor]
    log.info("  sources for %r: %d fetched (%s)", anchor.anchor_element[:40],
             len(sources), ",".join(s.tier for s in sources) or "none")
    return EvidencePacket(anchor=anchor, findings="\n\n".join(notes), sources=sources)
