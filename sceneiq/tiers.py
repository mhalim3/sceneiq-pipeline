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
      A = established mainstream newsrooms and major entertainment/culture
          publications that Wikipedia rates generally reliable, plus film-permit records;
      B = other generally reliable outlets (niche, specialist, foreign-language),
          entertainment or reference outlets Wikipedia rates "no consensus",
          and trade outlets Wikipedia does not rate;
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
    if dom in source_policy.PATH_TIERS:
        prefix, sec_tier, rest_tier = source_policy.PATH_TIERS[dom]
        return sec_tier if (urlparse(url).path or "").startswith(prefix) else rest_tier
    if rsp in ("gr", "mixed") and dom in source_policy.ESTABLISHED_A:
        return "A"
    if rsp == "gr":
        return "B"
    if rsp == "mixed":
        return "B"
    if rsp == "nc":
        return "B" if dom in source_policy.NC_ENTERTAINMENT_OR_REFERENCE_B else "C"
    if rsp in ("gu", "d", "b"):
        return "C"
    if dom in config.A_TIER_DOMAINS:
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


def tier_reason(url: str) -> str:
    """Plain-language reason for the domain's tier (for reports)."""
    dom = registered_domain(url)
    if dom in config.VIDEO_DOMAINS:
        return "video platform, the judge decides"
    rsp = _rsp().get(dom, "")
    if dom in source_policy.PATH_TIERS:
        prefix = source_policy.PATH_TIERS[dom][0]
        if (urlparse(url).path or "").startswith(prefix):
            return "Wikipedia: contributor section rated generally unreliable"
        return "Wikipedia: staff content rated reliable or no consensus"
    if rsp in ("gr", "mixed") and dom in source_policy.ESTABLISHED_A:
        return "Wikipedia: generally reliable + established mainstream or major entertainment outlet"
    if rsp == "gr":
        return "Wikipedia: generally reliable, niche or specialist outlet"
    if rsp == "mixed":
        return "Wikipedia: entries disagree (reliable vs no consensus)"
    if rsp == "nc":
        if dom in source_policy.NC_ENTERTAINMENT_OR_REFERENCE_B:
            return "Wikipedia: no consensus, entertainment/reference outlet"
        return "Wikipedia: no consensus, not an entertainment/reference outlet"
    if rsp in ("gu", "d", "b"):
        return {"gu": "Wikipedia: generally unreliable", "d": "Wikipedia: deprecated",
                "b": "Wikipedia: blacklisted"}[rsp]
    if dom in config.A_TIER_DOMAINS:
        return "official record / domain authority (our list)"
    if dom in config.B_TIER_DOMAINS:
        return "trade outlet Wikipedia does not rate (our list)"
    if dom in config.C_TIER_DOMAINS:
        return "lead-only site (our list)"
    return "not on Wikipedia's list or ours; judge decides"
