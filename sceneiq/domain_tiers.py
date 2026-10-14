"""Rate unlisted source domains with Wikipedia's reliability criteria.

Wikipedia's Perennial Sources list is the fixed starting point (wikipedia_rsp.json,
plus the local A/B/C lists in source_policy.py). A domain on neither is rated once,
when the pipeline first meets it, by a model applying the same criteria Wikipedia
editors use. The rating is cached in auto_tiers.json with a one-line reason, so a
domain gets the same tier every run. A model never hands out tier A: auto-rated
domains are B or C only. Edit auto_tiers.json (or add the domain to a list in
source_policy.py, which always wins) to override one.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

log = logging.getLogger("sceneiq")

_PATH = Path(__file__).with_name("auto_tiers.json")
_lock = threading.Lock()
_cache: dict | None = None

_SCHEMA = {
    "type": "object",
    "properties": {
        "tier": {"type": "string", "enum": ["B", "C"]},
        "outlet_type": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["tier", "outlet_type", "reason"],
}

_PROMPT = """Rate this website as a source of facts about movies, using the criteria Wikipedia editors use to judge whether a source is reliable.

Domain: {domain}
Example page title: {title}
Example page text (start): {sample}

Criteria (Wikipedia's reliable-sources guidelines):
- Reliable: an established outlet with editorial oversight, a reputation for fact-checking and issuing corrections, named professional staff, and independence from the subject. Includes news organizations, entertainment and trade press, established film or culture publications, reference works, and the official site of the organization or person the fact is about (a studio, a guild, an animal-welfare monitor, a film commission).
- Unreliable: user-generated or wiki content (fandom wikis, forums, social media, Q&A sites), personal blogs and free-hosted blog platforms, content farms and listicle/SEO aggregators, press-release or sponsored-content sites, rumor and gossip sites, merchandise/ticketing/location-rental or other commercial pages, and any site with no visible editorial process.

Answer B if it clearly meets the reliable criteria. Answer C if it is unreliable OR you are not sure. Never answer A.
Return the tier, a short outlet_type (for example "film news site", "personal blog", "local newspaper"), and a one-sentence reason."""


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_PATH.read_text())
        except (OSError, ValueError):
            _cache = {}
    return _cache


def lookup(domain: str) -> str:
    """Cached auto-rating ('B' | 'C') or '' when the domain has not been rated."""
    return _load().get(domain, {}).get("tier", "")


def reason(domain: str) -> str:
    e = _load().get(domain)
    return f"auto-rated {e['tier']} ({e.get('outlet_type', '')}): {e.get('reason', '')}" if e else ""


def resolve(client, cfg, domain: str, title: str = "", sample: str = "") -> str:
    """Rate `domain` if it is new; return its tier ('' if the call failed)."""
    with _lock:
        cache = _load()
        if domain in cache:
            return cache[domain]["tier"]
        try:
            d = client.structured(
                cfg.interest_model,
                _PROMPT.format(domain=domain, title=(title or "")[:150],
                               sample=" ".join((sample or "").split())[:600]),
                _SCHEMA, temperature=0.0)
            tier = "B" if d.get("tier") == "B" else "C"
            cache[domain] = {"tier": tier, "outlet_type": d.get("outlet_type", ""),
                             "reason": d.get("reason", "")}
            _PATH.write_text(json.dumps(dict(sorted(cache.items())), indent=1, ensure_ascii=False))
            log.info("  auto-rated %s -> %s (%s)", domain, tier, d.get("outlet_type", ""))
            return tier
        except Exception as e:  # leave unknown; the judge still decides per page
            log.warning("  auto-rating failed for %s: %s", domain, e)
            return ""
