"""SceneIQ source policy — the single source of truth for what counts as an
authoritative source.

Everything that decides "is this source good enough?" reads from here: the
domain tiers (tiers.py), the validation judge and writer/research prompts
(prompt blocks below), the emission rule (validate.py), and the stronger-source
retry (orchestrator.py). Change a rule here and all of them follow. Do not
hard-code outlet lists, thresholds or definitions anywhere else.
"""

from __future__ import annotations

# --- Definitions (plain language; also injected into the prompts) ------------

PRIMARY = (
    "PRIMARY: the person or record is the source itself. A named filmmaker, cast "
    "member, crew member or studio says it (a quoted interview, commentary track, "
    "press kit, or their own post), or an official record states it (permit "
    "databases, credits, music registries, court or government records)."
)

ESTABLISHED_EDITORIAL = (
    "ESTABLISHED EDITORIAL: a publication with editorial standards reports it and "
    "names who said it or what document it comes from — trade press, major "
    "newspapers, established film magazines (see the outlet list)."
)

NOT_AUTHORITATIVE = (
    "NOT AUTHORITATIVE (leads only, never support a card): listicles, trivia "
    "roundups and 'facts you didn't know' posts; aggregators; wikis, fan sites, "
    "forums and film databases (IMDb, TV Tropes, Fandom, Wikipedia); blogs and "
    "marketing sites (test-prep, tour, merchandise, auction listings); and any "
    "page that states a claim without naming where it came from."
)

RULES = (
    "- A page that repeats another outlet's claim counts as that outlet, never as a "
    "second source.\n"
    "- A specific number, quote, or 'first/only' claim must be backed by a PRIMARY "
    "source or an ESTABLISHED EDITORIAL source. A blog or unlisted site alone cannot "
    "carry it.\n"
    "- A page from an unlisted domain can support a card only if it directly quotes a "
    "named participant making the claim (then it is PRIMARY). Otherwise it is a lead.\n"
    "- When several pages state the same claim, cite the most authoritative one."
)

# --- Thresholds ----------------------------------------------------------------

# Unlisted-domain pages count as support only when the judge labels them
# primary_direct (a named participant is quoted). reputable_editorial counts
# only from A/B-tier domains.
UNKNOWN_DOMAIN_NEEDS_PRIMARY = True
# Cards needing at least: 1 primary OR 1 qualifying editorial (relaxed mode);
# 1 primary OR 2 independent qualifying editorial (strict mode).
RELAXED_MIN_EDITORIAL = 1
STRICT_MIN_EDITORIAL = 2

# Outlets named in the stronger-source retry query (subset of B tier).
RETRY_OUTLETS = [
    "Variety", "Hollywood Reporter", "Collider", "Guardian",
    "Business Insider", "Vulture", "Entertainment Weekly",
]

# --- Domain lists ----------------------------------------------------------------

# A tier: domain authorities, primary within their category (plus any .gov).
A_TIER_DOMAINS = {
    "ascap.com",
    "ascmag.com",
    "bmi.com",
    "criterion.com",
    "filmla.com",
    "nyc.gov",
    "theasc.com",
}

# B tier: established editorial / trade press.
B_TIER_DOMAINS = {
    "avclub.com",
    "bbc.co.uk",
    "bbc.com",
    "businessinsider.com",
    "collider.com",
    "deadline.com",
    "empireonline.com",
    "ew.com",
    "gq.com",
    "harvardlawrecord.org",
    "hollywoodreporter.com",
    "indiewire.com",
    "insider.com",
    "latimes.com",
    "newyorker.com",
    "npr.org",
    "nytimes.com",
    "polygon.com",
    "rollingstone.com",
    "screenrant.com",
    "slashfilm.com",
    "slate.com",
    "theguardian.com",
    "theringer.com",
    "thewrap.com",
    "vanityfair.com",
    "variety.com",
    "vogue.com",
    "vulture.com",
    "wwd.com",
}

# C tier: discovery / lead sources only. Can never support a card.
C_TIER_DOMAINS = {
    "afi.com",
    "bfi.org.uk",
    "boards.ie",
    "buzzfeed.com",
    "fandom.com",
    "gamefaqs.gamespot.com",
    "imdb.com",
    "mentalfloss.com",
    "moviechat.org",
    "pinterest.com",
    "quora.com",
    "ranker.com",
    "reddit.com",
    "tcm.com",
    "themoviedb.org",
    "tmdb.org",
    "tvtropes.org",
    "wikia.com",
    "wikimedia.org",
    "wikipedia.org",
}

# Never fetched as evidence (policy plus social/aggregator noise).
SKIP_FETCH_DOMAINS = {
    "facebook.com",
    "fandom.com",
    "instagram.com",
    "pinterest.com",
    "reddit.com",
    "tiktok.com",
    "twitter.com",
    "wikia.com",
    "wikimedia.org",
    "wikipedia.org",
    "x.com",
}

# Video platforms: need a fetched transcript; tier stays judge-decided.
VIDEO_DOMAINS = {
    "vimeo.com",
    "youtu.be",
    "youtube.com",
}


# --- Prompt blocks ---------------------------------------------------------------

def definitions_block() -> str:
    """The definitions + rules, as text for the judge, writer and research prompts."""
    return "\n".join([PRIMARY, ESTABLISHED_EDITORIAL, NOT_AUTHORITATIVE, "Rules:", RULES])


def outlet_list() -> str:
    """Human-readable list of A/B-tier outlets for the prompts."""
    return ", ".join(sorted(A_TIER_DOMAINS | B_TIER_DOMAINS))


def retry_query_suffix() -> str:
    return "interview " + " OR ".join(RETRY_OUTLETS)
