"""Domain-tier classification (first pass of the PRD source-tier policy).

Tier by registered domain is a proxy; the LLM judge later assigns the
evidence class per claim. C-tier can never support emission.
"""

from __future__ import annotations

from urllib.parse import urlparse

from . import config

# Minimal multi-label public suffixes we care about for registered-domain
# extraction. Not a full PSL, but covers the tier tables.
_TWO_LABEL_SUFFIXES = {"co.uk", "org.uk", "ac.uk", "com.au", "co.jp", "co.nz"}


def registered_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_LABEL_SUFFIXES:
        return ".".join(parts[-3:])
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def classify_domain(url: str) -> str:
    """Return 'A' | 'B' | 'C' | 'unknown' for a URL."""
    dom = registered_domain(url)
    if not dom:
        return "unknown"
    if dom in config.A_TIER_DOMAINS:
        return "A"
    if dom in config.B_TIER_DOMAINS:
        return "B"
    if dom in config.C_TIER_DOMAINS:
        return "C"
    # Government records (film permits, city open data) are domain authorities
    # per the PRD's A-tier examples (NYC Open Data, FilmLA).
    if dom.endswith(".gov"):
        return "A"
    # Otherwise unknown -> judge decides, since an official studio/GQ channel
    # video can be primary evidence despite an unranked domain.
    return "unknown"


def independent(sources: list) -> list:
    """Collapse sources sharing a registered domain to one representative.

    PRD independence requirement: two sources are not independent when they
    derive from the same origin. Same-domain is the cheap, enforceable half
    of that rule; the judge handles cross-domain syndication.
    """
    seen: set = set()
    out = []
    for s in sources:
        dom = s.domain or registered_domain(s.url)
        if dom in seen:
            continue
        seen.add(dom)
        out.append(s)
    return out
