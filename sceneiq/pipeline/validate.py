"""Stage 4: Shared validation layer.

Sources arrive from research already fetched (bodies + transcripts), so this
stage does no HTTP. Order of checks:

  1. contract          — field counts, category enum (code)
  2. source_binding    — every beat cites a fetched source AND its 6-20 word
                         verbatim anchor fuzzy-matches that source's body at
                         >= cfg.verbatim_anchor_min_ratio (code)
  3. judge             — claim entailment + supporting passage per beat,
                         evidence class per source, PRD 6-dimension rubric,
                         spoiler boundary (fast model, temperature 0)
  4. verbatim/x-modal  — named entities in bodies; video-sourced entities
                         also need a text source (code)
  5. emission          — C never supports; strict: 1 primary OR 2 independent
                         A/B editorial; relaxed: 1 primary OR 1 editorial
  6. disposition       — PRD rubric rule (strict) / floors of 1 (relaxed)

Relaxed mode drops bad beats (binding or entailment failures) instead of
rejecting, provided >= 3 beats survive. Safety and spoiler gates are identical
in both modes. Video timestamps attach from transcript segments per beat.
"""

from __future__ import annotations

import re

from .. import config, source_policy
from ..fetch import attach_timestamp, best_substring_ratio
from ..gemini import GeminiClient
from ..models import EvidencePacket, SceneFactCard, ValidationResult
from ..tiers import independent


# --- Deterministic check: names and numbers in the viewer-facing text must appear
# in the verified beats (catches misspelled names, swapped numbers, invented details).
_COMMON_CAPS = {
    "the", "a", "an", "this", "that", "these", "those", "his", "her", "their", "its",
    "he", "she", "they", "it", "in", "on", "at", "for", "with", "and", "but", "or",
    "after", "before", "when", "while", "during", "unlike", "despite", "although",
    "because", "one", "two", "three", "film", "movie", "director", "actor", "actress",
    "star", "lead", "producer", "composer", "designer", "writer", "screenwriter",
    "editor", "costume", "production", "cinematographer", "villain", "hero", "scene",
    "oscar", "academy", "award", "awards", "english", "french", "italian", "american",
    "british", "roman", "greek", "german", "spanish", "chinese", "japanese",
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "january", "february", "march", "april", "june", "july", "august",
    "september", "october", "november", "december",
}


def _norm_word(w: str) -> str:
    w = w.lower().replace("\u2019", "'")
    return w[:-2] if w.endswith("'s") else w.strip("'")


def unsupported_terms(text: str, support: str, allow: set[str]) -> list[str]:
    """Proper-noun-looking words and digit numbers in `text` that `support` lacks."""
    sup = support.lower().replace("\u2019", "'")
    sup_words = {_norm_word(w) for w in re.findall(r"[A-Za-z][A-Za-z'\u2019\-]*", sup)}
    sup_nums = {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*\.?\d*", sup)}
    missing: list[str] = []
    # Words after the first in a sentence (sentence-initial capitals are ambiguous).
    for sent in re.split(r"(?<=[.!?])\s+", text):
        toks = re.findall(r"[A-Za-z][A-Za-z'\u2019\-]*|\d[\d,]*\.?\d*", sent)
        for i, tok in enumerate(toks):
            if tok[0].isdigit():
                n = tok.replace(",", "").rstrip(".")
                if n and n not in sup_nums and n not in missing:
                    missing.append(n)
                continue
            if i == 0 or not tok[0].isupper():
                continue
            w = _norm_word(tok)
            if (len(w) < 3 or w in _COMMON_CAPS or w in allow or w in sup_words
                    or tok in missing):
                continue
            missing.append(tok)
    return missing

_RUBRIC_DIMS = [
    "factual_accuracy",
    "scene_grounding",
    "primitive_conformance",
    "viewer_value",
    "clarity",
    "spoiler_safety",
]
_SCORE = {"type": "integer", "minimum": 0, "maximum": 2}
# Must match assembly's _BODY_EXCERPT_CHARS: the judge must see the same
# evidence the writer saw, or true beats past the window read as hallucinated.
_JUDGE_EXCERPT_CHARS = 9000

_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "entailed": {"type": "boolean"},
                    "supporting_passage": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["index", "entailed", "supporting_passage"],
            },
        },
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "evidence_class": {
                        "type": "string",
                        "enum": ["primary_direct", "reputable_editorial", "discovery"],
                    },
                    "notes": {"type": "string"},
                },
                "required": ["url", "evidence_class"],
            },
        },
        "rubric": {
            "type": "object",
            "properties": {d: _SCORE for d in _RUBRIC_DIMS},
            "required": list(_RUBRIC_DIMS),
        },
        "spoiler_free": {"type": "boolean"},
        "maturity_pass": {"type": "boolean"},
        "propriety_pass": {"type": "boolean"},
        "category_correct": {"type": "boolean"},
        "summary_entailed": {"type": "boolean"},
        "film_specific": {"type": "boolean"},
        "matches_assigned_fact": {"type": "boolean"},
        "named_entities": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": [
        "beats",
        "sources",
        "rubric",
        "spoiler_free",
        "maturity_pass",
        "propriety_pass",
        "category_correct",
        "summary_entailed",
        "film_specific",
        "matches_assigned_fact",
        "named_entities",
    ],
}

_JUDGE_PROMPT = """You are the validation judge for SceneIQ, Tubi's pause-screen surface. \
Judge this candidate Scene Fact card strictly against the FETCHED SOURCE BODIES below. \
A wrong card is worse than no card. When uncertain on any check, score low / fail.

FILM: {film_title} ({film_year})
CARD ANCHOR SCENE (~{timecode}, runtime fraction {fraction}): {scene_description}
ANCHOR ELEMENT: {anchor_element}
{scope_note}

CANDIDATE CARD:
factCategory: {category}
shortVersion (on-screen line): {short_version}
longDescription (expanded fact): {long_description}
factBeats (internal verification units):
{beats}
followUps: {follow_ups}

FETCHED SOURCE BODIES cited by the card:
{source_blocks}

Evaluate:

1. beats — for each beat (by index): is every material claim (names, places, numbers, \
quotes, causal claims) directly supported by the cited source body? Hedges must be \
preserved, not stripped. Overstated = not entailed. For each beat, copy into \
supporting_passage the exact passage from the source body that supports it (empty \
string if none does — which means not entailed).

2. sources — classify each source's evidence class FOR THE CLAIMS IT SUPPORTS, using \
the SOURCE POLICY below: "primary_direct" (PRIMARY), "reputable_editorial" \
(ESTABLISHED EDITORIAL — only an outlet that fits that description, not a blog or \
marketing site), or "discovery" (NOT AUTHORITATIVE — a lead, never support).

SOURCE POLICY:
{source_policy}

3. rubric — score each dimension 0 (fail), 1 (partial), or 2 (meets bar):
- factual_accuracy: 2 = all material claims accurate and entailed by the bodies; \
1 = core claim true but a material detail weakly supported or overstated; 0 = any \
unsupported, contradicted, or misleading claim.
- scene_grounding: 2 = clearly points to a recognizable element visible/audible in \
THIS scene and rewards pausing there; 1 = connection indirect; 0 = not tied to the scene.
- primitive_conformance: 2 = cleanly fits the Scene Fact definition (insider detail \
about the film's world explaining something on screen); 1 = loose fit; 0 = does not \
satisfy the definition. Director filmography, franchise connections, notable cast \
facts, and casting/development history (actors considered, roles that changed, \
actors who dropped out, how the project developed) ARE conforming. Automatic 0 \
for: ANY financial information (budget, box office, profitability, opening \
weekend, financial comparisons); plot summary of THIS film; bare ratings or review \
scores without an interesting reception contrast; basic encyclopedic facts \
(release year, director name, studio, plain cast list); on-set feuds; industry \
gossip.
- viewer_value: 2 = specific, surprising, likely to prompt exploration; 1 = mildly \
interesting; 0 = generic or obvious.
- clarity: 2 = concise, tells the viewer what to notice; 1 = verbose or imprecise; \
0 = confusing.
- spoiler_safety: 2 = fully safe at the anchor timecode; 1 = borderline wording; \
0 = reveals later plot, outcomes, or significance.

4. spoiler_free — BINARY, no partial credit, deliberately conservative. Treat ANYTHING that \
could SOUND like a spoiler as a spoiler. Return false if the card mentions, hints at, or \
lets a viewer infer ANY of: a plot development or outcome; a twist or reveal; how the film \
or any storyline ends; who lives, dies, wins, loses, betrays or is revealed; a villain's or \
character's secret identity or hidden role; a surprise cameo or secret role; or anything \
that happens later in the film than the scene. This applies to scene cards and GENERAL \
cards alike. Behind-the-scenes facts (casting, locations, effects, preparation, how a \
moment was filmed) are fine ONLY when they say nothing about what happens in the story. \
If you are unsure, false.

5. maturity_pass — {maturity_rule}

6. propriety_pass — FAILS on any of: gossip or interpersonal drama; legal matters \
(lawsuits, arrests, charges, settlements); tragedies (deaths, accidents, or disasters \
connected to the production or its talent); politically charged content; disparaging \
or unsubstantiated negative claims about talent, filmmakers, studios, or partners; \
quoting or paraphrasing criticism of the title ("critics called it..."); PII. \
SceneIQ is an enrichment surface, never a venue for controversy.

7. category_correct — does factCategory fit?

8. named_entities — list every proper name, title, and specific number a fact-checker \
must find in the sources.

9. summary_entailed — true only if EVERY claim, name, and number in shortVersion and \
longDescription is stated by one of the entailed beats (which are themselves \
supported by the source bodies). The long is MEANT to carry extra specifics beyond \
the short — that is fine, and is not a failure — as long as each of those specifics \
appears in an entailed beat. Fail only for a claim, name, number, or implication \
that no entailed beat supports, or when the short and long describe different facts.

10. film_specific — true if the card's claims connect concretely to THIS film \
({film_title}): its production, cast, locations, music, making-of, its director's \
other work, or its franchise context. A card stating generic industry practice (how \
newspaper props are usually made, how films typically license songs) is NOT \
film-specific even when the prop or song appears in this film's scene.

11. matches_assigned_fact — the ANCHOR ELEMENT above is the fact this card was ASSIGNED to \
tell. true if the card is about that same fact (a narrower or reworded version is fine). \
false ONLY if the card tells a DIFFERENT fact from the sources instead."""


def _maturity_rule(cfg: config.PipelineConfig, film_info: dict) -> str:
    if cfg.maturity_standard == "title_rating":
        rating = film_info.get("content_rating") or "the title's own rating"
        return (
            f"all user-facing text must be appropriate for a {rating}-rated title "
            f"(this title is rated {rating}). Content must never exceed the title's "
            "own rating, and must not restate or amplify mature material beyond what "
            "the film itself presents."
        )
    return (
        "all user-facing text must be G-RATED regardless of the title's rating. "
        "Quoted profanity FAILS even when censored or bleeped (\"You b----!\" is "
        "not G-rated); a card whose payoff is an expletive line fails. Do not "
        "restate or amplify mature material even when it appears in the title, "
        "scene, or source."
    )


def _judge_source_blocks(cited: list) -> str:
    blocks = []
    for s in cited:
        body = getattr(s, "_body", "")[:_JUDGE_EXCERPT_CHARS]
        blocks.append(f"--- {s.url} [tier {s.tier}, {s.modality}]\n{body}")
    return "\n\n".join(blocks)


def _entity_hits(entities: list[str], bodies: list[str]) -> tuple[list[str], list[str]]:
    hits, misses = [], []
    for ent in entities:
        needle = ent.lower().strip()
        if not needle:
            continue
        (hits if any(needle in b.lower() for b in bodies) else misses).append(ent)
    return hits, misses


def validate_card(
    client: GeminiClient,
    film_info: dict,
    card: SceneFactCard,
    packet: EvidencePacket,
    cfg: config.PipelineConfig,
) -> ValidationResult:
    result = ValidationResult(passed=False)
    checks = result.checks
    relaxed = cfg.evidence_mode == "relaxed"
    src_by_url = {s.url: s for s in card.sources}

    # 1. Contract. shortVersion length is NOT a gate — an over-length short is a
    # copy-editing fix, never a reason to drop a good fact. Sufficiency and fact
    # quality matter more, so the cap is reported as a flag and tightened later.
    contract_ok = (
        cfg.min_beats <= len(card.fact_beats) <= 5
        and 2 <= len(card.follow_ups) <= 3
        and card.fact_category in (
            config.FACT_CATEGORIES + (["general"] if card.scope == "general" else [])
        )
        and bool(card.short_version and card.long_description)
        and all(b.source_url in src_by_url for b in card.fact_beats)
    )
    checks["contract"] = contract_ok
    if not contract_ok:
        result.rejection_reasons.append("contract: field counts/category/sources out of spec")
        return result
    if len(card.short_version) > 80:
        result.flags.append(
            f"shortVersion {len(card.short_version)} chars (OVER 80 cap — trim later)"
        )
    elif len(card.short_version) > 60:
        result.flags.append(
            f"shortVersion {len(card.short_version)} chars (target 50-60, cap 80)"
        )
    if len(card.long_description) > 280:
        result.flags.append(
            f"longDescription {len(card.long_description)} chars (max ~280)"
        )

    # 2. Source binding: per-beat verbatim anchor must fuzzy-match its cited
    # source's fetched body. Also attach video timestamps while we're here.
    binding_bad: set[int] = set()
    for i, b in enumerate(card.fact_beats):
        src = src_by_url[b.source_url]
        body = getattr(src, "_body", "")
        if not b.verbatim_anchor or not body:
            binding_bad.add(i)
            continue
        ratio = best_substring_ratio(b.verbatim_anchor, body)
        if ratio < cfg.verbatim_anchor_min_ratio:
            binding_bad.add(i)
            continue
        if src.modality == "video":
            b.source_timestamp = attach_timestamp(
                b.verbatim_anchor, getattr(src, "_segments", [])
            )
    checks["source_binding"] = not binding_bad

    # 3. LLM judge over the cited source bodies.
    cited = [src_by_url[u] for u in {b.source_url for b in card.fact_beats}]
    beats_txt = "\n".join(
        f"  [{i}] {b.text} (source: {b.source_url}; anchor: \"{b.verbatim_anchor}\")"
        for i, b in enumerate(card.fact_beats)
    )
    judge = client.structured(
        cfg.fast_model,
        _JUDGE_PROMPT.format(
            film_title=film_info.get("title", ""),
            film_year=film_info.get("year", ""),
            timecode=card.approx_timecode,
            fraction=round(card.runtime_fraction, 2),
            scene_description=card.scene_description,
            anchor_element=card.anchor_element,
            scope_note=(
                "" if card.scope == "scene" else
                "NOTE: this is a GENERAL card, not tied to a scene — the player may "
                "show it at ANY point in the film. Score scene_grounding 2 when the "
                "fact suits any-time display (a fact about the film as a whole). It "
                "must be spoiler-free for the ENTIRE runtime."
            ),
            short_version=card.short_version,
            category=card.fact_category,
            maturity_rule=_maturity_rule(cfg, film_info),
            long_description=card.long_description,
            beats=beats_txt,
            follow_ups=card.follow_ups,
            source_blocks=_judge_source_blocks(cited),
            source_policy=source_policy.definitions_block(),
        ),
        _JUDGE_SCHEMA,
        temperature=cfg.judge_temperature,
    )
    result.judge_notes = judge.get("notes", "")
    rubric = {d: int(judge["rubric"].get(d, 0)) for d in _RUBRIC_DIMS}
    result.rubric = rubric

    for jb in judge["beats"]:
        i = jb["index"]
        if 0 <= i < len(card.fact_beats):
            card.fact_beats[i].supporting_passage = jb.get("supporting_passage", "")

    # Spoilers: binary hard gate, identical in BOTH modes and both scopes.
    checks["spoiler_free"] = bool(judge["spoiler_free"]) and rubric["spoiler_safety"] >= 1
    checks["maturity"] = judge["maturity_pass"]
    checks["propriety"] = judge["propriety_pass"]
    if not checks["spoiler_free"]:
        result.rejection_reasons.append(
            "spoiler: card reveals plot, outcomes, or a surprise (cameo/secret role)"
        )
    if not judge["maturity_pass"]:
        result.rejection_reasons.append("maturity: content exceeds the maturity standard")
    if not judge["propriety_pass"]:
        result.rejection_reasons.append(
            "propriety: gossip/legal/tragedy/political/disparagement content")
    card.spoiler_boundary_fraction = 0.0  # binary policy: spoilers never emit

    checks["taxonomy"] = judge["category_correct"]
    if not judge["category_correct"]:
        if relaxed:
            result.flags.append("taxonomy: category mismatch")
        else:
            result.rejection_reasons.append("taxonomy: category mismatch")

    # The viewer-facing text may not exceed the verified beats — hard in
    # both modes, since the summaries ARE the display surface now.
    checks["summary_entailment"] = judge["summary_entailed"]
    if not judge["summary_entailed"]:
        result.rejection_reasons.append(
            "summary_entailment: shortVersion/longDescription claim beyond the beats"
        )

    # The card must tell the fact it was assigned, not a different one found in the sources.
    checks["matches_assigned_fact"] = judge["matches_assigned_fact"]
    if not judge["matches_assigned_fact"]:
        result.rejection_reasons.append(
            "card_mismatch: card is about a different fact than the one it was assigned")

    # Generic industry facts are not Scene Facts — hard in both modes.
    checks["film_specific"] = judge["film_specific"]
    if not judge["film_specific"]:
        result.rejection_reasons.append(
            "film_specific: claims are generic industry practice, not about this film"
        )

    # Beat survival: binding failures + entailment failures. Strict rejects;
    # relaxed drops bad beats if >= 3 remain.
    unsupported = {b["index"] for b in judge["beats"] if not b["entailed"]}
    bad_beats = unsupported | binding_bad
    checks["claim_entailment"] = not unsupported
    if bad_beats:
        if relaxed:
            kept = [b for i, b in enumerate(card.fact_beats) if i not in bad_beats]
            if len(kept) >= cfg.min_beats:
                card.fact_beats = kept
                result.flags.append(
                    f"relaxed: dropped beats {sorted(bad_beats)} "
                    "(binding or entailment failure) — review that shortVersion/"
                    "longDescription don't restate the dropped claims"
                )
            else:
                result.rejection_reasons.append(
                    f"claim_entailment: only {len(kept)} bound+supported beats remain (need {cfg.min_beats})"
                )
        else:
            if binding_bad:
                result.rejection_reasons.append(
                    f"source_binding: beats {sorted(binding_bad)} verbatim anchor "
                    f"not found in source body (min ratio {cfg.verbatim_anchor_min_ratio})"
                )
            if unsupported:
                result.rejection_reasons.append(
                    f"claim_entailment: beats {sorted(unsupported)} not supported"
                )

    # Deterministic terms check on the final (post beat-drop) beats: every name and
    # digit number in the short/long must appear in a beat (or its verbatim anchor).
    support = " ".join(f"{b.text} {b.verbatim_anchor}" for b in card.fact_beats)
    allow = {_norm_word(w) for w in re.findall(
        r"[A-Za-z][A-Za-z'\u2019\-]*", f"{film_info.get('title', '')}")}
    missing_terms = unsupported_terms(
        f"{card.short_version} {card.long_description}", support, allow)
    checks["summary_terms"] = not missing_terms
    if missing_terms:
        result.rejection_reasons.append(
            f"summary_entailment: names/numbers not in the verified beats {missing_terms}")

    # Re-derive cited sources from surviving beats; attach evidence classes.
    cited = [src_by_url[u] for u in {b.source_url for b in card.fact_beats}]
    by_url = {s["url"]: s["evidence_class"] for s in judge.get("sources", [])}
    for s in cited:
        s.evidence_class = by_url.get(s.url, s.evidence_class)

    # 4. Named-entity verbatim + cross-modal corroboration. (Video sources
    # always carry transcripts — research drops caption-less videos.)
    entities = judge.get("named_entities", [])
    all_bodies = [getattr(s, "_body", "") for s in cited if getattr(s, "_body", "")]
    text_bodies = [getattr(s, "_body", "") for s in cited
                   if getattr(s, "_body", "") and s.modality == "text"]
    hits, misses = _entity_hits(entities, all_bodies)
    for s in cited:
        s.verbatim_hits, s.verbatim_misses = hits, misses
    if misses:
        if cfg.strict_verbatim:
            checks["verbatim"] = False
            result.rejection_reasons.append(f"verbatim: entities not found in bodies {misses}")
        else:
            checks["verbatim"] = "flagged"
            result.flags.append(f"verbatim: could not confirm {misses} in fetched bodies")
    else:
        checks["verbatim"] = True

    video_supported = any(s.modality == "video" and s.evidence_class == "primary_direct"
                          for s in cited)
    text_supported = any(s.modality == "text" and s.tier != "C"
                         and s.evidence_class in ("primary_direct", "reputable_editorial")
                         for s in cited)
    cross_modal_ok = True
    if cfg.require_cross_modal and video_supported and not text_supported and entities:
        _, text_misses = _entity_hits(entities, text_bodies)
        cross_modal_ok = not text_misses
        if text_misses:
            if relaxed:
                checks["cross_modal"] = "flagged"
                result.flags.append(
                    f"cross_modal: video-sourced entities lack a text source {text_misses}"
                )
            else:
                checks["cross_modal"] = False
                result.rejection_reasons.append(
                    f"cross_modal: video-sourced entities lack a text source {text_misses}"
                )
        else:
            checks["cross_modal"] = True
    else:
        checks["cross_modal"] = True

    # 5. Emission rules. C-tier never supports in either mode.
    supporting = [s for s in cited if s.tier != "C"]
    primaries = [s for s in supporting if s.evidence_class == "primary_direct"]
    editorial_strict = independent(
        [s for s in supporting if s.evidence_class == "reputable_editorial" and s.tier in ("A", "B")]
    )
    # Source policy: an unlisted-domain page counts only as PRIMARY (it quotes a
    # named participant); "reputable editorial" support must come from A/B domains.
    editorial_relaxed = independent([
        s for s in supporting if s.evidence_class == "reputable_editorial"
        and (s.tier in ("A", "B") or not source_policy.UNKNOWN_DOMAIN_NEEDS_PRIMARY)
    ])
    strict_emission = bool(primaries) or len(editorial_strict) >= source_policy.STRICT_MIN_EDITORIAL
    relaxed_emission = bool(primaries) or len(editorial_relaxed) >= source_policy.RELAXED_MIN_EDITORIAL
    emission_ok = relaxed_emission if relaxed else strict_emission
    checks["emission_policy"] = emission_ok
    if not emission_ok:
        emission_msg = (
            "emission_policy: needs 1 primary/direct source or "
            + ("1 reputable editorial source" if relaxed else
               "2 independent reputable editorial sources")
            + f" (got {len(primaries)} primary, {len(editorial_relaxed)} editorial)"
        )
        # Annotation mode surfaces thin-sourced cards for review instead of
        # dropping them: the beats are still bound+entailed against SOME
        # fetched body, just not an A/B-tier one. Flagged and marked low
        # confidence below. Correctness/safety gates already ran and stay hard.
        if cfg.annotation_mode:
            result.flags.append(emission_msg + " — surfaced as low confidence (annotation mode)")
        else:
            result.rejection_reasons.append(emission_msg)

    # 6. Card disposition.
    non_gating = ["primitive_conformance", "viewer_value", "clarity", "spoiler_safety"]
    avg = sum(rubric[d] for d in non_gating) / len(non_gating)
    strict_rubric_ok = (
        rubric["factual_accuracy"] == 2
        and rubric["scene_grounding"] == 2
        and all(rubric[d] >= 1 for d in non_gating)
        and avg >= 1.5
    )
    # Beats were dropped in relaxed mode: the judge scored the PRE-drop card,
    # so a factual_accuracy driven down by now-removed beats is stale. The
    # surviving beats are all bound + entailed by construction.
    beats_were_dropped = relaxed and bad_beats and not result.rejection_reasons
    if relaxed:
        factual_floor_dims = ("scene_grounding",) if beats_were_dropped else (
            "factual_accuracy", "scene_grounding")
        if beats_were_dropped and rubric["factual_accuracy"] < 1:
            result.flags.append(
                "rubric: factual_accuracy scored pre-drop; surviving beats are entailed"
            )
        for dim in factual_floor_dims:
            if rubric[dim] < 1:
                result.rejection_reasons.append(f"rubric: {dim} {rubric[dim]}/2 (must be >=1)")
        # Conformance is a floor even in relaxed mode: plot summaries and
        # excluded content classes are not Scene Facts at any evidence bar.
        if rubric["primitive_conformance"] < 1:
            result.rejection_reasons.append(
                f"rubric: primitive_conformance {rubric['primitive_conformance']}/2 (must be >=1)"
            )
        low = [d for d in ("viewer_value", "clarity") if rubric[d] < 1]
        if low:
            result.flags.append(f"rubric low (relaxed): {low}")
        checks["rubric_disposition"] = (
            rubric["factual_accuracy"] >= 1 and rubric["scene_grounding"] >= 1
            and rubric["primitive_conformance"] >= 1
        )
    else:
        if rubric["factual_accuracy"] < 2:
            result.rejection_reasons.append(
                f"rubric: factual_accuracy {rubric['factual_accuracy']}/2 (must be 2)"
            )
        if rubric["scene_grounding"] < 2:
            result.rejection_reasons.append(
                f"rubric: scene_grounding {rubric['scene_grounding']}/2 (must be 2)"
            )
        low = [d for d in non_gating if rubric[d] < 1]
        if low:
            result.rejection_reasons.append(f"rubric: {low} scored 0")
        elif avg < 1.5:
            result.rejection_reasons.append(f"rubric: non-gating average {avg:.2f} < 1.5")
        checks["rubric_disposition"] = strict_rubric_ok

    # Strict shadow verdict — recorded in every mode.
    checks["would_pass_strict"] = bool(
        strict_emission
        and strict_rubric_ok
        and not unsupported
        and not binding_bad
        and checks["spoiler_free"]
        and checks["maturity"]
        and checks["propriety"]
        and judge["category_correct"]
        and judge["summary_entailed"]
        and judge["film_specific"]
        and cross_modal_ok
    )

    # Confidence label for annotation triage. Only meaningful for cards that
    # actually emit (no rejection reasons); rejected cards keep "".
    has_quality_source = bool(primaries) or len(editorial_relaxed) >= 1
    conf_reasons: list[str] = []
    if not has_quality_source:
        conf_reasons.append("no primary or reputable-editorial source (thin sourcing)")
    if misses:
        conf_reasons.append(f"unconfirmed entities in sources: {misses}")
    if rubric["viewer_value"] < 1:
        conf_reasons.append("low viewer-value score")
    if rubric["clarity"] < 1:
        conf_reasons.append("low clarity score")
    if bad_beats and relaxed:
        conf_reasons.append(f"dropped {len(bad_beats)} unsupported beat(s)")
    if checks.get("would_pass_strict"):
        result.confidence = "high"
        conf_reasons = []  # strict-pass: no caveats worth surfacing
    elif has_quality_source and rubric["viewer_value"] >= 1 and rubric["clarity"] >= 1 and not misses:
        result.confidence = "medium"
    else:
        result.confidence = "low"
    result.confidence_reasons = conf_reasons

    result.passed = not result.rejection_reasons
    return result
