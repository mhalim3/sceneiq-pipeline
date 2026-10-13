"""Domain-tier classification (first pass of the PRD source-tier policy).

Tier by registered domain is a proxy; the LLM judge later assigns the
evidence class per claim. C-tier can never support emission.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from . import config, source_policy

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


@lru_cache(maxsize=1)
def _rsp() -> dict:
    """Wikipedia's Perennial Sources list: domain -> gr | nc | gu | d | b."""
    path = Path(__file__).with_name("wikipedia_rsp.json")
    try:
        return json.loads(path.read_text())["domains"]
    except (OSError, ValueError, KeyError):
        return {}


def rsp_status(url: str) -> str:
    """Wikipedia perennial-sources status for the URL's domain ('' if unlisted)."""
    return _rsp().get(registered_domain(url), "")


def classify_domain(url: str) -> str:
    """Return 'A' | 'B' | 'C' | 'unknown' for a URL.

    Tiers start from Wikipedia's Perennial Sources list ("generally reliable"
    is its top group) and add our own split, see source_policy.py:
      A = generally reliable AND film/entertainment is the outlet's core beat;
      B = generally reliable general-interest outlets, plus entertainment or
          reference outlets Wikipedia rates "no consensus";
      C = other "no consensus", and generally unreliable / deprecated / blacklisted
          (lead only, never support).
    Video platforms are always left to the judge (a verified channel can be
    primary). For domains Wikipedia does not rate, the local lists in
    source_policy.py (domain authorities, trade outlets, lead-only sites) and
    any .gov decide. Everything else is 'unknown' and the judge decides.
    """
    dom = registered_domain(url)
    if not dom:
        return "unknown"
    if dom in config.VIDEO_DOMAINS:
        return "unknown"
    rsp = _rsp().get(dom, "")
    if rsp == "gr":
        return "A" if dom in source_policy.GR_FILM_EXPERT_A else "B"
    if rsp == "mixed":
        return "B"
    if rsp == "nc":
        return "B" if dom in source_policy.NC_ENTERTAINMENT_OR_REFERENCE_B else "C"
    if rsp in ("gu", "d", "b"):
        return "C"
    if dom in config.A_TIER_DOMAINS or dom.endswith(".gov"):
        return "A"
    if dom in config.B_TIER_DOMAINS:
        return "B"
    if dom in config.C_TIER_DOMAINS:
        return "C"
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
