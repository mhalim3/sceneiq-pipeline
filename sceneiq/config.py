"""Pipeline configuration.

Everything here maps to a PRD decision. Model names, thresholds, and tier
tables are env-overridable so prompt/model A/B tests (Phase 4) don't require
code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


# --- GenAI stack (PRD: "Gemini via the google-genai SDK. Grounded search
# enabled at generation time. Deep model for topic clustering, fast model for
# validation.") ---
DEEP_MODEL = os.environ.get("SCENEIQ_DEEP_MODEL", "gemini-2.5-pro")
FAST_MODEL = os.environ.get("SCENEIQ_FAST_MODEL", "gemini-2.5-flash")

API_KEY_ENV = "GEMINI_API_KEY"

# PRD: "Retry via tenacity on transient errors, with final rejection on
# schema violation after 3 retries."
MAX_RETRIES = 3

# The 7-category Scene Fact taxonomy (PRD "Category taxonomy").
FACT_CATEGORIES = [
    "actor",
    "music",
    "location",
    "set_design",
    "filming",
    "historical",
    "costume/prop",
]


@dataclass
class PipelineConfig:
    # How many candidate anchors to discover per title. PRD topic clustering
    # proposes 6-12; we over-generate slightly since validation rejects hard.
    max_anchors: int = 12
    # Cap on emitted (approved) cards.
    max_cards: int = 10
    # PRD "Title sufficiency requirements" (MVP, movies only):
    #   - >= 1 approved card per `minutes_per_card` of runtime, rounded up
    #   - >= `min_cards_to_enable` approved cards for a feature-length title
    #   - >= 1 card per runtime quartile for titles > `quartile_min_runtime` min
    #   - <= `max_quartile_share` of cards in any single quartile
    #   - >= `min_categories` distinct fact categories
    min_cards_to_enable: int = 6
    minutes_per_card: int = 10
    quartile_min_runtime: int = 40
    max_quartile_share: float = 0.40
    min_categories: int = 2
    # Parallelism for per-anchor research/assembly/validation.
    max_workers: int = 4
    # Strict mode hard-rejects cards whose named entities can't be verbatim-
    # matched in a fetched source body. Default flags them instead, because
    # page fetches are flaky and ASR/paywall noise causes false rejects
    # (PRD "Handling video and audio sources" makes the same tradeoff call).
    strict_verbatim: bool = False
    deep_model: str = DEEP_MODEL
    fast_model: str = FAST_MODEL
    # Generation temperatures.
    discovery_temperature: float = 0.4
    assembly_temperature: float = 0.2
    judge_temperature: float = 0.0
    # HTTP validation.
    http_timeout_s: float = 10.0
    fetch_source_bodies: bool = True
    # PRD "Handling video and audio sources": a video may be the primary
    # source for a claim, but any named person or number must also appear in
    # a text source (cross-modal corroboration).
    require_cross_modal: bool = True
    # Wikipedia API discovery leads (C-tier: seed anchors, never support cards).
    use_wikipedia_leads: bool = True
    extra: dict = field(default_factory=dict)


# --- Source tiers (PRD "Source Tiers and Validation") -----------------------
# Tiers are assigned by registered domain as a first pass; the LLM judge then
# classifies the *evidence class* of each source against the specific claim
# (primary_direct / reputable_editorial / discovery), because per the PRD
# "Source classification is based on the nature and independence of the
# evidence, not solely on the publishing platform or brand." A GQ video where
# the actor breaks down their own scene is primary evidence even though GQ's
# domain sits in the editorial table.

# A tier: domain authorities that are primary within their category.
A_TIER_DOMAINS = {
    "ascmag.com",            # American Cinematographer (filming technique)
    "theasc.com",
    "criterion.com",         # Criterion supplements
    "ascap.com",             # music cue sheets / Songview
    "bmi.com",
    "filmla.com",            # film permit records
    "nyc.gov",               # NYC Open Data permits
}

# B tier: reputable editorial / trade press. Can carry a card with one
# independent corroborating A/B source, or alone when the article contains a
# directly attributable primary statement (judge decides).
B_TIER_DOMAINS = {
    "variety.com",
    "hollywoodreporter.com",
    "theringer.com",
    "vulture.com",
    "vogue.com",
    "wwd.com",
    "nytimes.com",
    "vanityfair.com",
    "gq.com",
    "collider.com",
    "insider.com",
    "businessinsider.com",
    "ew.com",
    "rollingstone.com",
    "indiewire.com",
    "latimes.com",
    "theguardian.com",
    "newyorker.com",
    "empireonline.com",
    "avclub.com",
    "slate.com",
    "npr.org",
    "bbc.com",
    "bbc.co.uk",
    "deadline.com",
    "thewrap.com",
    "slashfilm.com",
    "screenrant.com",        # borderline; judge downgrades listicle content
    "polygon.com",
    "harvardlawrecord.org",
}

# Video platforms: tier stays judge-decided (an official studio or GQ channel
# clip can carry primary evidence), but modality becomes "video", which
# triggers transcript fetching and the cross-modal corroboration rule.
VIDEO_DOMAINS = {
    "youtube.com",
    "youtu.be",
    "vimeo.com",
}

# C tier: discovery / lead sources only. Can never support emission.
C_TIER_DOMAINS = {
    "wikipedia.org",
    "wikimedia.org",
    "imdb.com",
    "themoviedb.org",
    "tmdb.org",
    "fandom.com",
    "wikia.com",
    "reddit.com",
    "tvtropes.org",
    "afi.com",
    "bfi.org.uk",
    "tcm.com",
    "pinterest.com",
    "quora.com",
    "buzzfeed.com",
    "ranker.com",
    "mentalfloss.com",
}
