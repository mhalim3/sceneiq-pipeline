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
# Research search calls are retrieval, not reasoning ("run this search,
# describe results, never invent URLs") — the grounding citations come from
# Google Search itself, and assembly reads fetched bodies, not the search
# narrative. Flash is several times faster here at equivalent retrieval.
RESEARCH_MODEL = os.environ.get("SCENEIQ_RESEARCH_MODEL", FAST_MODEL)

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
    # Parallelism for per-anchor research/assembly/validation. The work is
    # I/O-bound (API calls + HTTP), so this is limited by API rate tier.
    max_workers: int = 8
    # Discovery passes: each pass proposes anchors avoiding already-explored
    # elements; approved cards merge across passes (raises yield on titles
    # where single-pass anchor variance is the bottleneck).
    passes: int = 1
    # Evidence mode. "strict" = PRD emission policy + full rubric disposition.
    # "relaxed" = exploration mode for the source-yield phase: one reputable
    # editorial source suffices, unsupported beats drop instead of killing
    # the card, rubric floors are >=1. Safety and spoiler gates are IDENTICAL
    # in both modes — never loosened. Relaxed cards record wouldPassStrict.
    evidence_mode: str = "relaxed"
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
    deep_model_discovery: str = DEEP_MODEL   # anchor discovery keeps the deep model
    research_model: str = RESEARCH_MODEL     # per-anchor search calls
    # HTTP validation.
    http_timeout_s: float = 10.0
    # Concurrent URL fetches per anchor (shared run-level cache dedupes).
    fetch_workers: int = 6
    fetch_source_bodies: bool = True
    # PRD "Handling video and audio sources": a video may be the primary
    # source for a claim, but any named person or number must also appear in
    # a text source (cross-modal corroboration).
    require_cross_modal: bool = True
    # Wikipedia API discovery leads (C-tier: seed anchors, never support cards).
    use_wikipedia_leads: bool = True
    # Fetch-then-write source bank (scene-sense architecture): per anchor, run
    # up to `queries_per_anchor` explicit searches, fetch bodies, keep the top
    # `sources_per_anchor` by tier.
    queries_per_anchor: int = 3
    sources_per_anchor: int = 8
    # Per-beat verbatim anchor must fuzzy-match its source body at this ratio.
    verbatim_anchor_min_ratio: float = 0.8
    # Title-level fact sweep: broad searches for the title's most-documented
    # facts (myths the film created, records, production-wide details), each
    # anchored to a Moments scene when one supports it, else emitted as a
    # GENERAL card the player may show at any time after its spoiler floor.
    use_title_sweep: bool = True
    sweep_facts_max: int = 8
    # Scene cards display from their scene start through scene end plus this
    # padding (seconds). General cards fill the remaining timeline gaps.
    scene_card_pad_s: float = 60.0
    # Topic-cluster dedup: an LLM pass over the approved set that collapses
    # cards telling the same underlying story in different wordings (string
    # dedup can't catch "actors cooked for real" x4). Keeps the strongest
    # card per story.
    use_topic_dedup: bool = True
    # Viewer-POV curiosity judge. ADVISORY by default: scores and verdicts
    # are recorded in the review file to triage human review, but never
    # reject a card — viewer value is scored manually by reviewers per the
    # PRD human rubric. Set curiosity_gating=True to let the model gate
    # (pre-manual-review behavior).
    use_curiosity_judge: bool = True
    curiosity_gating: bool = False
    curiosity_min_composite: float = 0.5
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

# Never fetched as evidence sources: C-tier by policy (can never support a
# card) and social/aggregator noise. Wikipedia already feeds discovery leads.
SKIP_FETCH_DOMAINS = {
    "wikipedia.org",
    "wikimedia.org",
    "pinterest.com",
    "reddit.com",
    "fandom.com",
    "wikia.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "x.com",
    "twitter.com",
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
