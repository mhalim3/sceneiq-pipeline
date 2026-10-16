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
    "marketing sites (test-prep, tour, merchandise, auction listings); OPINION AND "
    "CONTRIBUTOR CONTENT even on a reputable outlet (op-eds, columns, commentary, "
    "editorials, contributor, community and 'council' posts, reader-submitted pages), "
    "because it reflects the author's view and is not fact-checked like staff reporting; "
    "and any page that states a claim without naming where it came from."
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

# --- Our A/B split inside Wikipedia's verdicts ----------------------------------
# Wikipedia "generally reliable" is the top group. We split it by standing:
#   A = established, mainstream newsrooms and major entertainment/culture
#       publications (the outlets a viewer or a manager would call authoritative
#       for entertainment news), plus film-permit records.
#   B = generally reliable but niche or specialist (games, tech, politics, science,
#       fact-checkers, think tanks, foreign-language press), and every other
#       outlet we rely on that is not in A.
# Wikipedia "no consensus" is B or C, never A:
#   B = an entertainment/film outlet with an editorial staff and bylines, or a
#       records/reference authority.
#   C = everything else (general-interest, tabloid, lifestyle, gossip, user-edited).
# Sites with a "generally unreliable" entry for part of the site (contributor
# blogs) are C, except where listed in PATH_TIERS.
# Domains whose entries disagree between reliable and no consensus are B, unless
# listed in A.
ESTABLISHED_A = {
    # major entertainment and culture publications
    "avclub.com", "deadline.com", "deadlinehollywooddaily.com", "ew.com", "gq.com",
    "gq-magazine.co.uk", "hollywoodreporter.com", "newyorker.com", "people.com",
    "rollingstone.com", "thewrap.com", "vanityfair.com", "variety.com", "vogue.com",
    # mainstream newsrooms with established culture/entertainment coverage
    "abcnews.com", "ap.org", "apnews.com", "bbc.co.uk", "bbc.com", "cnn.com",
    "latimes.com", "nbcnews.com", "npr.org", "nytimes.com", "reuters.com", "theatlantic.com",
    "theguardian.com", "theguardian.co.uk", "guardian.co.uk", "time.com", "usatoday.com",
    "washingtonpost.com", "wsj.com",
}
# Sites with contributor sections Wikipedia rates unreliable: (path prefix, tier for
# that section, tier for the rest of the site).
PATH_TIERS = {
    "forbes.com": ("/sites/", "C", "B"),
}

# Opinion and contributor sections on ANY site are lead only (tier C): they carry the
# author's view and are not fact-checked like staff reporting, even at outlets such as
# the NYT, Guardian or Washington Post (their opinion desks do edit, but the content is
# opinion). Matched against the URL path; the judge prompt says the same in words.
import re as _re
OPINION_PATH_RE = _re.compile(
    r"/(opinion|opinions|op-ed|op-eds|oped|editorial-board|editorials?|commentary|"
    r"commentisfree|columnists?|columns?|perspectives?|viewpoints?|voices|"
    r"contributors?|community|councils?|guest-posts?|letters)(/|$)"
    r"|_b_\d+\.html",          # HuffPost's old contributor ("blog") post URLs
    _re.I,
)
# Sites whose contributor/community content cannot be told apart by path. The whole
# site is lead only unless the page is staff news (judged per page by the AI judge).
CONTRIBUTOR_HEAVY = {"buzzfeed.com", "huffpost.com", "huffingtonpost.com"}
NC_ENTERTAINMENT_OR_REFERENCE_B = {
    "screenrant.com", "mashable.com", "dexerto.com", "dexerto.es", "decider.com",
    "britannica.com", "guinnessworldrecords.com", "businessinsider.com",
}

# LOCAL ADDITIONS. Tiers come from Wikipedia's perennial-sources list
# (wikipedia_rsp.json: A = generally reliable, B = no consensus, C = unreliable /
# deprecated / blacklisted). The three sets below apply ONLY to domains Wikipedia
# does not rate; where Wikipedia has a verdict it wins.
#
# A: domain authorities, primary within their category (plus any .gov).
A_TIER_DOMAINS = {
    "ascap.com",
    "ascmag.com",
    "bmi.com",
    "criterion.com",
    "filmla.com",
    "nyc.gov",
    "theasc.com",
}

# B: established editorial / trade press that Wikipedia does not rate.
B_TIER_DOMAINS = {
    "thrillist.com",       # reviewer: the original source for the Matilda oral history
    "hellomagazine.com",   # reviewer: reliable celebrity press
    "eonline.com",
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

# C: discovery / lead sources only. Can never support a card.
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
