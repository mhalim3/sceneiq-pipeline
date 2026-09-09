"""Stage 4: Shared validation layer.

Order of checks (cheap and deterministic first, LLM judge last):
  1. contract        — field counts, category enum (code)
  2. url_provenance  — every beat URL resolves; redirects unwrapped (code+http)
  3. domain_tier     — A/B/C per registered domain (code)
  4. judge           — claim entailment, evidence class per source, scene
                       anchoring, spoiler boundary, safety, non-obviousness
                       (fast model, temperature 0)
  5. verbatim        — named entities appear in fetched source bodies
                       (code+http; flag by default, reject in strict mode)
  6. emission        — 1 primary/direct source OR 2 independent reputable
                       editorial sources; C never supports (code)

Failing any hard gate rejects the card. No partial credit.
"""

from __future__ import annotations

import html
import re

import httpx

from .. import config
from ..gemini import GeminiClient
from ..models import EvidencePacket, SceneFactCard, SourceRef, ValidationResult
from ..tiers import classify_domain, independent, registered_domain

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh) SceneIQ-validator/0.1"}

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
                    "notes": {"type": "string"},
                },
                "required": ["index", "entailed"],
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
        "scene_anchored": {"type": "boolean"},
        "spoiler_safe": {"type": "boolean"},
        "safety_pass": {"type": "boolean"},
        "non_obvious": {"type": "boolean"},
        "category_correct": {"type": "boolean"},
        "named_entities": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": [
        "beats",
        "sources",
        "scene_anchored",
        "spoiler_safe",
        "safety_pass",
        "non_obvious",
        "category_correct",
        "named_entities",
    ],
}

_JUDGE_PROMPT = """You are the validation judge for SceneIQ, Tubi's pause-screen surface. \
Judge this candidate Scene Fact card strictly. A wrong card is worse than no card. \
When uncertain on any check, fail that check.

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
1. beats — for each beat (by index), is every material claim (names, places, numbers, \
quotes, causal claims) directly supported by the evidence packet? Hedges must be \
preserved, not stripped. Overstated = not entailed.
2. sources — classify each cited source's evidence class FOR THE CLAIMS IT SUPPORTS: \
"primary_direct" (the filmmakers/talent/studio or a domain authority directly attest, \
e.g. commentary, on-record interview where the participant makes the claim, permit \
records, ASC/ASCAP), "reputable_editorial" (reported coverage by an established \
publication), or "discovery" (wiki/fan/db/listicle — leads only).
3. scene_anchored — does the card point at something a viewer paused at THIS scene \
can see or hear?
4. spoiler_safe — true only if the card reveals NOTHING about plot after runtime \
fraction {fraction}. Foreshadowing payoffs count as spoilers.
5. safety_pass — G-rated wording; no disparaging, demeaning, or unsubstantiated \
negative claims about talent, filmmakers, studios, or partners; no PII.
6. non_obvious — would a casual viewer NOT already know this from watching?
7. category_correct — does factCategory fit?
8. named_entities — list every proper name, title, and specific number a fact-checker \
must find verbatim in the sources."""


def _resolve_sources(card: SceneFactCard, cfg: config.PipelineConfig) -> None:
    """Unwrap grounding redirect URLs, mark resolution, re-tier by final domain.

    Beats keep pointing at their source via URL; we remap them to final URLs.
    """
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
                if cfg.fetch_source_bodies and src.resolved and "text" in (
                    resp.headers.get("content-type", "")
                ):
                    body = re.sub(r"<[^>]+>", " ", resp.text)
                    src._body = html.unescape(re.sub(r"\s+", " ", body)).lower()  # type: ignore[attr-defined]
            except httpx.HTTPError:
                src.resolved = False
    for beat in card.fact_beats:
        if 0 <= beat.source_index < len(card.sources):
            beat.source_url = card.sources[beat.source_index].url
        else:
            beat.source_url = url_map.get(beat.source_url, beat.source_url)


def _verbatim_check(card: SceneFactCard, entities: list[str]) -> tuple[list[str], list[str]]:
    bodies = [getattr(s, "_body", "") for s in card.sources if getattr(s, "_body", "")]
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

    # 2. URL provenance.
    _resolve_sources(card, cfg)
    cited_urls = {b.source_url for b in card.fact_beats}
    cited = [s for s in card.sources if s.url in cited_urls]
    if not cited:
        # Beat URLs the model wrote that aren't in the grounding set at all.
        checks["url_provenance"] = False
        result.rejection_reasons.append("url_provenance: cited URLs not in grounding sources")
        return result
    unresolved = [s.url for s in cited if not s.resolved]
    checks["url_provenance"] = not unresolved
    if unresolved:
        result.rejection_reasons.append(f"url_provenance: unresolved {unresolved}")
        return result

    # 3. Domain tier (recorded; enforcement happens in emission step).
    checks["domain_tier"] = True

    # 4. LLM judge.
    beats_txt = "\n".join(f"  [{i}] {b.text} (source: {b.source_url})" for i, b in enumerate(card.fact_beats))
    src_txt = "\n".join(f"- {s.url} [domain tier {s.tier}]" for s in cited)
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

    unsupported = [b["index"] for b in judge["beats"] if not b["entailed"]]
    checks["claim_entailment"] = not unsupported
    checks["scene_anchoring"] = judge["scene_anchored"]
    checks["spoiler_boundary"] = judge["spoiler_safe"]
    checks["safety"] = judge["safety_pass"]
    checks["non_obvious"] = judge["non_obvious"]
    checks["taxonomy"] = judge["category_correct"]
    if unsupported:
        result.rejection_reasons.append(f"claim_entailment: beats {unsupported} not supported")
    if not judge["scene_anchored"]:
        result.rejection_reasons.append("scene_anchoring: not tied to visible/audible element")
    if not judge["spoiler_safe"]:
        result.rejection_reasons.append("spoiler_boundary: reveals later plot")
    if not judge["safety_pass"]:
        result.rejection_reasons.append("safety: maturity/partner-safety failure")
    if not judge["non_obvious"]:
        result.rejection_reasons.append("value: obvious/generic content")
    if not judge["category_correct"]:
        result.rejection_reasons.append("taxonomy: category mismatch")

    # Attach judge evidence classes to sources.
    by_url = {s["url"]: s["evidence_class"] for s in judge.get("sources", [])}
    for s in cited:
        s.evidence_class = by_url.get(s.url, s.evidence_class)

    # 5. Verbatim entity check.
    hits, misses = _verbatim_check(card, judge.get("named_entities", []))
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

    # 6. Emission rules. C-tier never supports. One primary/direct source, or
    # two independent reputable-editorial sources on A/B domains.
    supporting = [s for s in cited if s.tier != "C"]
    primaries = [s for s in supporting if s.evidence_class == "primary_direct"]
    editorial = independent(
        [s for s in supporting if s.evidence_class == "reputable_editorial" and s.tier in ("A", "B")]
    )
    emission_ok = bool(primaries) or len(editorial) >= 2
    checks["emission_policy"] = emission_ok
    if not emission_ok:
        result.rejection_reasons.append(
            "emission_policy: needs 1 primary/direct source or 2 independent "
            f"reputable editorial sources (got {len(primaries)} primary, "
            f"{len(editorial)} independent editorial)"
        )

    result.passed = not result.rejection_reasons
    return result
