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

from .. import config
from ..fetch import attach_timestamp, best_substring_ratio
from ..gemini import GeminiClient
from ..models import EvidencePacket, SceneFactCard, ValidationResult
from ..tiers import independent

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
        "earliest_safe_fraction": {"type": "number"},
        "safety_pass": {"type": "boolean"},
        "category_correct": {"type": "boolean"},
        "summary_entailed": {"type": "boolean"},
        "film_specific": {"type": "boolean"},
        "named_entities": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": [
        "beats",
        "sources",
        "rubric",
        "earliest_safe_fraction",
        "safety_pass",
        "category_correct",
        "summary_entailed",
        "film_specific",
        "named_entities",
    ],
}

_JUDGE_PROMPT = """You are the validation judge for SceneIQ, Tubi's pause-screen surface. \
Judge this candidate Scene Fact card strictly against the FETCHED SOURCE BODIES below. \
A wrong card is worse than no card. When uncertain on any check, score low / fail.

FILM: {film_title} ({film_year})
CARD ANCHOR SCENE (~{timecode}, runtime fraction {fraction}): {scene_description}
ANCHOR ELEMENT: {anchor_element}

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

2. sources — classify each source's evidence class FOR THE CLAIMS IT SUPPORTS: \
"primary_direct" (filmmakers/talent/studio or a domain authority directly attest — \
including an article or transcript that directly quotes the relevant participant \
making this claim), "reputable_editorial" (reported coverage by an established \
publication without a direct participant statement), or "discovery" (wiki/fan/db/\
listicle — leads only, never support).

3. rubric — score each dimension 0 (fail), 1 (partial), or 2 (meets bar):
- factual_accuracy: 2 = all material claims accurate and entailed by the bodies; \
1 = core claim true but a material detail weakly supported or overstated; 0 = any \
unsupported, contradicted, or misleading claim.
- scene_grounding: 2 = clearly points to a recognizable element visible/audible in \
THIS scene and rewards pausing there; 1 = connection indirect; 0 = not tied to the scene.
- primitive_conformance: 2 = cleanly fits the Scene Fact definition (insider detail \
about the film's world explaining something on screen); 1 = loose fit; 0 = does not \
satisfy the definition. Automatic 0 for: plot summary or character backstory (what \
happens in the story is not a Scene Fact), deleted scenes or alternate versions with \
no visible on-screen artifact, casting stories, career trivia, industry gossip.
- viewer_value: 2 = specific, surprising, likely to prompt exploration; 1 = mildly \
interesting; 0 = generic or obvious.
- clarity: 2 = concise, tells the viewer what to notice; 1 = verbose or imprecise; \
0 = confusing.
- spoiler_safety: 2 = fully safe at the anchor timecode; 1 = borderline wording; \
0 = reveals later plot, outcomes, or significance.

4. earliest_safe_fraction — the earliest runtime fraction (0.0-1.0) at which this \
card could be shown without spoiling anything. If safe at its anchor, use the anchor \
fraction {fraction} or lower.

5. safety_pass — G-rated wording; no disparaging or unsubstantiated negative claims \
about talent, filmmakers, studios, or partners; no PII; does not restate or amplify \
mature material. Quoted profanity FAILS even when censored or bleeped ("You b----!" \
is not G-rated); a card whose payoff is an expletive line fails.

6. category_correct — does factCategory fit?

7. named_entities — list every proper name, title, and specific number a fact-checker \
must find in the sources.

8. summary_entailed — true only if BOTH shortVersion and longDescription contain no \
claim, name, number, or implication beyond what the entailed beats state. They are \
rewrites of the beats, never expansions.

9. film_specific — true only if the card's claims are specifically about THIS film \
({film_title}): its production, cast, locations, music, or making-of. A card stating \
generic industry practice (how newspaper props are usually made, how films typically \
license songs) is NOT film-specific even when the prop or song appears in this \
film's scene — the sources must attest facts about {film_title} itself."""


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

    # 1. Contract. shortVersion: 50-60 target, 80 hard cap.
    contract_ok = (
        3 <= len(card.fact_beats) <= 5
        and 2 <= len(card.follow_ups) <= 3
        and card.fact_category in config.FACT_CATEGORIES
        and bool(card.short_version and card.long_description)
        and len(card.short_version) <= 80
        and all(b.source_url in src_by_url for b in card.fact_beats)
    )
    checks["contract"] = contract_ok
    if not contract_ok:
        if len(card.short_version or "") > 80:
            result.rejection_reasons.append(
                f"contract: shortVersion {len(card.short_version)} chars (hard cap 80)"
            )
        else:
            result.rejection_reasons.append("contract: field counts/category/sources out of spec")
        return result
    if len(card.short_version) > 60:
        result.flags.append(
            f"shortVersion {len(card.short_version)} chars (target 50-60, cap 80)"
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
            short_version=card.short_version,
            category=card.fact_category,
            long_description=card.long_description,
            beats=beats_txt,
            follow_ups=card.follow_ups,
            source_blocks=_judge_source_blocks(cited),
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

    # Spoiler boundary and safety: identical hard gates in BOTH modes.
    checks["spoiler_boundary"] = rubric["spoiler_safety"] >= 1 and (
        judge["earliest_safe_fraction"] <= card.runtime_fraction + 0.02
    )
    checks["safety"] = judge["safety_pass"]
    if not checks["spoiler_boundary"]:
        result.rejection_reasons.append(
            "spoiler_boundary: card only safe from fraction "
            f"{judge['earliest_safe_fraction']:.2f}, anchored at {card.runtime_fraction:.2f}"
        )
    if not judge["safety_pass"]:
        result.rejection_reasons.append("safety: maturity/partner-safety failure")
    card.spoiler_boundary_fraction = min(
        float(judge["earliest_safe_fraction"]), card.runtime_fraction
    ) if checks["spoiler_boundary"] else float(judge["earliest_safe_fraction"])

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
            if len(kept) >= 3:
                card.fact_beats = kept
                result.flags.append(
                    f"relaxed: dropped beats {sorted(bad_beats)} "
                    "(binding or entailment failure) — review that shortVersion/"
                    "longDescription don't restate the dropped claims"
                )
            else:
                result.rejection_reasons.append(
                    f"claim_entailment: only {len(kept)} bound+supported beats remain (need 3)"
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
    editorial_relaxed = independent(
        [s for s in supporting if s.evidence_class == "reputable_editorial"]
    )
    strict_emission = bool(primaries) or len(editorial_strict) >= 2
    relaxed_emission = bool(primaries) or len(editorial_relaxed) >= 1
    emission_ok = relaxed_emission if relaxed else strict_emission
    checks["emission_policy"] = emission_ok
    if not emission_ok:
        result.rejection_reasons.append(
            "emission_policy: needs 1 primary/direct source or "
            + ("1 reputable editorial source" if relaxed else
               "2 independent reputable editorial sources")
            + f" (got {len(primaries)} primary, {len(editorial_relaxed)} editorial)"
        )

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
        and checks["spoiler_boundary"]
        and checks["safety"]
        and judge["category_correct"]
        and judge["summary_entailed"]
        and judge["film_specific"]
        and cross_modal_ok
    )

    result.passed = not result.rejection_reasons
    return result
