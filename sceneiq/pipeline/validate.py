"""Stage 4: Shared validation layer.

Order of checks (cheap and deterministic first, LLM judge last):
  1. contract        — field counts, category enum (code)
  2. url_provenance  — every beat URL resolves; redirects unwrapped; video
                       sources get their caption transcript fetched as the
                       searchable body (code+http)
  3. domain_tier     — A/B/C per registered domain (code)
  4. judge           — claim entailment + supporting passage per beat,
                       evidence class per source, PRD 6-dimension rubric
                       (0/1/2), spoiler boundary as earliest-safe fraction
                       (fast model, temperature 0)
  5. verbatim        — named entities appear in fetched bodies; video-sourced
                       claims additionally need entities in a TEXT source
                       (PRD cross-modal corroboration)
  6. emission        — 1 primary/direct source OR 2 independent reputable
                       editorial sources; C never supports (code)
  7. disposition     — PRD card-disposition rule: rubric 2s on accuracy and
                       grounding, >=1 elsewhere, non-gating average >=1.5

Failing any hard gate rejects the card. No partial credit.

Evidence modes (cfg.evidence_mode):
  strict  — PRD-exact: any unsupported beat or unresolved URL rejects the
            card; emission needs 1 primary OR 2 independent A/B editorial;
            rubric disposition requires 2s on accuracy+grounding, >=1
            elsewhere, non-gating average >=1.5.
  relaxed — exploration mode for the source-yield phase: beats that are
            unsupported or cite unresolved URLs are DROPPED (card survives
            with >=3 remaining); emission accepts 1 reputable editorial
            source on any non-C domain; rubric floors are >=1; cross-modal
            and taxonomy downgrade to flags. Safety and spoiler gates are
            identical in both modes. Every relaxed pass also computes the
            strict verdict and records it as checks["would_pass_strict"].
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlparse

import httpx

from .. import config
from ..gemini import GeminiClient
from ..models import EvidencePacket, SceneFactCard, ValidationResult
from ..tiers import classify_domain, independent, registered_domain

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh) SceneIQ-validator/0.1"}

_RUBRIC_DIMS = [
    "factual_accuracy",      # gating: must be 2
    "scene_grounding",       # gating: must be 2
    "primitive_conformance", # non-gating: >= 1
    "viewer_value",          # non-gating: >= 1
    "clarity",               # non-gating: >= 1
    "spoiler_safety",        # non-gating floor >= 1; hard gate separately
]
_SCORE = {"type": "integer", "minimum": 0, "maximum": 2}

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
        "named_entities",
    ],
}

_JUDGE_PROMPT = """You are the validation judge for SceneIQ, Tubi's pause-screen surface. \
Judge this candidate Scene Fact card strictly. A wrong card is worse than no card. \
When uncertain on any check, score low / fail.

FILM: {film_title} ({film_year})
CARD ANCHOR SCENE (~{timecode}, runtime fraction {fraction}): {scene_description}
ANCHOR ELEMENT: {anchor_element}

CANDIDATE CARD:
proactivePrompt: {prompt}
factCategory: {category}
factHeader: {header}
factBeats:
{beats}
followUps: {follow_ups}

EVIDENCE PACKET the card was built from:
{findings}

SOURCES cited by the card:
{source_list}

Evaluate:

1. beats — for each beat (by index): is every material claim (names, places, numbers, \
quotes, causal claims) directly supported by the evidence packet? Hedges must be \
preserved, not stripped. Overstated = not entailed. For each beat, copy into \
supporting_passage the exact passage from the evidence packet that supports it \
(empty string if none does — which means not entailed).

2. sources — classify each cited source's evidence class FOR THE CLAIMS IT SUPPORTS: \
"primary_direct" (the filmmakers/talent/studio or a domain authority directly attest: \
commentary, an on-record interview where the participant makes the claim, permit \
records, ASC/ASCAP — an editorial article COUNTS as primary when it directly quotes \
the relevant participant making this claim), "reputable_editorial" (reported coverage \
by an established publication without a direct participant statement), or "discovery" \
(wiki/fan/db/listicle — leads only, never support).

3. rubric — score each dimension 0 (fail), 1 (partial), or 2 (meets bar), per the \
SceneIQ evaluation rubric:
- factual_accuracy: 2 = all material claims accurate and entailed by cited sources; \
1 = core claim true but a material detail weakly supported or overstated; 0 = any \
unsupported, contradicted, or misleading claim.
- scene_grounding: 2 = clearly points to a recognizable element visible/audible in \
THIS scene and rewards pausing there; 1 = connection indirect or hard to spot; 0 = \
not tied to the anchor scene.
- primitive_conformance: 2 = cleanly fits the Scene Fact definition (insider detail \
about the film's world explaining something on screen — NOT casting stories, career \
trivia, industry gossip); 1 = fits loosely with some generic/disallowed material; \
0 = does not satisfy the definition.
- viewer_value: 2 = specific, surprising, likely to prompt exploration; 1 = mildly \
interesting; 0 = generic or obvious.
- clarity: 2 = concise, natural, tells the viewer what to notice; 1 = understandable \
but verbose or imprecise; 0 = confusing or unfindable.
- spoiler_safety: 2 = fully safe at the anchor timecode; 1 = borderline wording; \
0 = reveals later plot, outcomes, or significance.

4. earliest_safe_fraction — the earliest runtime fraction (0.0-1.0) at which this \
card could be shown without spoiling anything. If safe at its anchor, use the anchor \
fraction {fraction} or lower.

5. safety_pass — G-rated wording; no disparaging, demeaning, or unsubstantiated \
negative claims about talent, filmmakers, studios, or partners; no PII; does not \
restate or amplify mature material.

6. category_correct — does factCategory fit?

7. named_entities — list every proper name, title, and specific number a fact-checker \
must find verbatim in the sources."""


def _youtube_video_id(url: str) -> str | None:
    p = urlparse(url)
    host = (p.hostname or "").removeprefix("www.")
    if host == "youtu.be":
        return p.path.lstrip("/") or None
    if host.endswith("youtube.com"):
        return (parse_qs(p.query).get("v") or [None])[0]
    return None


def _fetch_transcript(url: str) -> str:
    """PRD: transcripts are the searchable layer for video sources.

    Best effort via the YouTube caption API; '' when unavailable (no captions,
    library missing, or network refusal) — the cross-modal rule then governs.
    """
    vid = _youtube_video_id(url)
    if not vid:
        return ""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return ""
    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(vid)          # v1.x API
        snippets = getattr(fetched, "snippets", fetched)
        return " ".join(s.text for s in snippets).lower()
    except AttributeError:
        try:
            data = YouTubeTranscriptApi.get_transcript(vid)   # v0.x API
            return " ".join(d["text"] for d in data).lower()
        except Exception:
            return ""
    except Exception:
        return ""


def _resolve_sources(card: SceneFactCard, cfg: config.PipelineConfig) -> None:
    """Unwrap grounding redirect URLs, mark resolution, re-tier by final
    domain, set modality, and fetch searchable bodies (HTML text, or caption
    transcript for video sources)."""
    url_map: dict[str, str] = {}
    with httpx.Client(
        follow_redirects=True, timeout=cfg.http_timeout_s, headers=_UA, verify=True
    ) as http:
        for src in card.sources:
            try:
                resp = http.get(src.url)
                final = str(resp.url)
                src.resolved = resp.status_code < 400
                url_map[src.url] = final
                src.url = final
                src.domain = registered_domain(final)
                src.tier = classify_domain(final)
                src.modality = "video" if src.domain in config.VIDEO_DOMAINS else "text"
                if cfg.fetch_source_bodies and src.resolved:
                    if src.modality == "video":
                        transcript = _fetch_transcript(final)
                        if transcript:
                            src._body = transcript  # type: ignore[attr-defined]
                            src.body_kind = "transcript"
                    elif "text" in resp.headers.get("content-type", ""):
                        body = re.sub(r"<[^>]+>", " ", resp.text)
                        src._body = html.unescape(re.sub(r"\s+", " ", body)).lower()  # type: ignore[attr-defined]
                        src.body_kind = "html"
            except httpx.HTTPError:
                src.resolved = False
    for beat in card.fact_beats:
        if 0 <= beat.source_index < len(card.sources):
            beat.source_url = card.sources[beat.source_index].url
        else:
            beat.source_url = url_map.get(beat.source_url, beat.source_url)


def _entity_hits(entities: list[str], bodies: list[str]) -> tuple[list[str], list[str]]:
    hits, misses = [], []
    for ent in entities:
        needle = ent.lower().strip()
        if not needle:
            continue
        (hits if any(needle in b for b in bodies) else misses).append(ent)
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

    # 1. Contract.
    contract_ok = (
        3 <= len(card.fact_beats) <= 5
        and 2 <= len(card.follow_ups) <= 3
        and card.fact_category in config.FACT_CATEGORIES
        and bool(card.proactive_prompt and card.fact_header)
        and all(b.source_url for b in card.fact_beats)
    )
    checks["contract"] = contract_ok
    if not contract_ok:
        result.rejection_reasons.append("contract: field counts/category/URLs out of spec")
        return result

    relaxed = cfg.evidence_mode == "relaxed"

    # 2. URL provenance (+ modality, bodies, transcripts).
    _resolve_sources(card, cfg)
    cited_urls = {b.source_url for b in card.fact_beats}
    cited = [s for s in card.sources if s.url in cited_urls]
    if not cited:
        checks["url_provenance"] = False
        result.rejection_reasons.append("url_provenance: cited URLs not in grounding sources")
        return result
    unresolved = {s.url for s in cited if not s.resolved}
    checks["url_provenance"] = not unresolved
    if unresolved and not relaxed:
        result.rejection_reasons.append(f"url_provenance: unresolved {sorted(unresolved)}")
        return result

    # 3. Domain tier (recorded; enforcement happens in emission step).
    checks["domain_tier"] = True

    # 4. LLM judge.
    beats_txt = "\n".join(f"  [{i}] {b.text} (source: {b.source_url})" for i, b in enumerate(card.fact_beats))
    src_txt = "\n".join(f"- {s.url} [domain tier {s.tier}, {s.modality}]" for s in cited)
    judge = client.structured(
        cfg.fast_model,
        _JUDGE_PROMPT.format(
            film_title=film_info.get("title", ""),
            film_year=film_info.get("year", ""),
            timecode=card.approx_timecode,
            fraction=round(card.runtime_fraction, 2),
            scene_description=card.scene_description,
            anchor_element=card.anchor_element,
            prompt=card.proactive_prompt,
            category=card.fact_category,
            header=card.fact_header,
            beats=beats_txt,
            follow_ups=card.follow_ups,
            findings=packet.findings,
            source_list=src_txt,
        ),
        _JUDGE_SCHEMA,
        temperature=cfg.judge_temperature,
    )
    result.judge_notes = judge.get("notes", "")
    rubric = {d: int(judge["rubric"].get(d, 0)) for d in _RUBRIC_DIMS}
    result.rubric = rubric

    # Attach per-beat supporting passages (PRD review-record requirement).
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

    # Claim entailment. Strict: any unsupported beat rejects. Relaxed: beats
    # that are unsupported or cite a dead URL are dropped; the card survives
    # only if >= 3 supported beats remain (contract minimum).
    unsupported = {b["index"] for b in judge["beats"] if not b["entailed"]}
    dead_url_beats = {i for i, b in enumerate(card.fact_beats) if b.source_url in unresolved}
    bad_beats = unsupported | dead_url_beats
    checks["claim_entailment"] = not unsupported
    if bad_beats:
        if relaxed:
            kept = [b for i, b in enumerate(card.fact_beats) if i not in bad_beats]
            if len(kept) >= 3:
                card.fact_beats = kept
                result.flags.append(
                    f"relaxed: dropped beats {sorted(bad_beats)} (unsupported or dead URL)"
                )
            else:
                result.rejection_reasons.append(
                    f"claim_entailment: only {len(kept)} supported beats remain (need 3)"
                )
        else:
            result.rejection_reasons.append(
                f"claim_entailment: beats {sorted(unsupported)} not supported"
            )

    # Re-derive cited sources from the surviving beats.
    cited_urls = {b.source_url for b in card.fact_beats}
    cited = [s for s in card.sources if s.url in cited_urls]

    # Attach judge evidence classes to sources.
    by_url = {s["url"]: s["evidence_class"] for s in judge.get("sources", [])}
    for s in cited:
        s.evidence_class = by_url.get(s.url, s.evidence_class)

    # 5. Verbatim entity check + cross-modal corroboration.
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

    # PRD cross-modal rule: when support rests on video, every named entity
    # must also appear in a text source. Flag-only in relaxed mode.
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

    # 6. Emission rules. C-tier never supports in either mode.
    #    strict:  1 primary/direct OR 2 independent A/B reputable editorial
    #    relaxed: 1 primary/direct OR 1 reputable editorial on any non-C domain
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

    # 7. Card disposition. Strict = PRD rule; relaxed = floors of 1 on the
    #    gating dimensions, low value dims flagged not fatal.
    non_gating = ["primitive_conformance", "viewer_value", "clarity", "spoiler_safety"]
    avg = sum(rubric[d] for d in non_gating) / len(non_gating)
    strict_rubric_ok = (
        rubric["factual_accuracy"] == 2
        and rubric["scene_grounding"] == 2
        and all(rubric[d] >= 1 for d in non_gating)
        and avg >= 1.5
    )
    if relaxed:
        for dim in ("factual_accuracy", "scene_grounding"):
            if rubric[dim] < 1:
                result.rejection_reasons.append(f"rubric: {dim} {rubric[dim]}/2 (must be >=1)")
        low = [d for d in ("primitive_conformance", "viewer_value", "clarity") if rubric[d] < 1]
        if low:
            result.flags.append(f"rubric low (relaxed): {low}")
        checks["rubric_disposition"] = (
            rubric["factual_accuracy"] >= 1 and rubric["scene_grounding"] >= 1
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

    # Strict shadow verdict — recorded in every mode so relaxed output can be
    # re-gated later without re-running the pipeline.
    checks["would_pass_strict"] = bool(
        strict_emission
        and strict_rubric_ok
        and not unsupported
        and not unresolved
        and checks["spoiler_boundary"]
        and checks["safety"]
        and judge["category_correct"]
        and cross_modal_ok
    )

    result.passed = not result.rejection_reasons
    return result
