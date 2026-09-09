"""Stage 0 (optional): discovery leads from the Wikipedia API.

PRD source policy: Wikipedia is a discovery/lead source (C tier). It may
suggest what to look into but can never support a card. So this stage feeds
the anchor-discovery prompt with production/trivia context to widen the
candidate funnel; every resulting claim must still find A/B evidence in
stage 2 to survive validation.

IMDb is deliberately NOT wired here: IMDb prohibits scraping and has no free
official API; the PRD notes commercial use requires IMDb Essential Metadata
via AWS Data Exchange (a licensing decision, not a code change). TMDb's free
API is the interim alternative if a second lead source is wanted.
"""

from __future__ import annotations

import logging
import re

import httpx

log = logging.getLogger("sceneiq")

_API = "https://en.wikipedia.org/w/api.php"
_UA = {"User-Agent": "SceneIQ-pipeline/0.1 (mhalim@tubi.tv)"}

# Sections most likely to contain anchor-worthy production detail.
_SECTIONS = ("production", "filming", "music", "soundtrack", "development",
             "casting", "design", "locations")
_MAX_CHARS = 7000


def _search_page_title(http: httpx.Client, query: str) -> str | None:
    r = http.get(_API, params={
        "action": "query", "list": "search", "srsearch": f"{query} film",
        "srlimit": 1, "format": "json",
    })
    hits = r.json().get("query", {}).get("search", [])
    return hits[0]["title"] if hits else None


def _page_plaintext(http: httpx.Client, title: str) -> str:
    r = http.get(_API, params={
        "action": "query", "prop": "extracts", "explaintext": 1,
        "titles": title, "format": "json", "redirects": 1,
    })
    pages = r.json().get("query", {}).get("pages", {})
    for page in pages.values():
        return page.get("extract", "")
    return ""


def _relevant_sections(text: str) -> str:
    """Keep the intro plus production-adjacent sections, trimmed."""
    # Wikipedia plaintext extracts mark headings as "== Heading ==".
    parts = re.split(r"\n(?=== )", text)
    keep = [parts[0][:1500]] if parts else []
    for part in parts[1:]:
        heading = part.split("\n", 1)[0].lower()
        if any(k in heading for k in _SECTIONS):
            keep.append(part)
    return "\n".join(keep)[:_MAX_CHARS]


def wikipedia_leads(title_prompt: str, timeout_s: float = 10.0) -> str:
    """Best-effort. Returns '' on any failure — leads are never load-bearing."""
    try:
        with httpx.Client(timeout=timeout_s, headers=_UA) as http:
            page = _search_page_title(http, title_prompt)
            if not page:
                return ""
            text = _relevant_sections(_page_plaintext(http, page))
            if text:
                log.info("  wikipedia leads: %s (%d chars)", page, len(text))
            return text
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.info("  wikipedia leads unavailable: %s", e)
        return ""
