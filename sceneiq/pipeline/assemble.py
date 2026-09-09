"""Stage 3: Card assembly.

Fast model in schema mode turns one evidence packet into one sceneFact card
per the PRD card contract — or abstains. Abstention is a first-class output:
the PRD prefers no card over a padded one.
"""

from __future__ import annotations

from .. import config
from ..gemini import GeminiClient
from ..models import EvidencePacket, FactBeat, SceneFactCard

_CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "abstain": {"type": "boolean"},
        "abstain_reason": {"type": "string"},
        "card": {
            "type": "object",
            "properties": {
                "proactivePrompt": {"type": "string"},
                "factCategory": {"type": "string", "enum": config.FACT_CATEGORIES},
                "factHeader": {"type": "string"},
                "factBeats": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "sourceIndex": {"type": "integer"},
                        },
                        "required": ["text", "sourceIndex"],
                    },
                },
                "followUps": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["proactivePrompt", "factCategory", "factHeader", "factBeats", "followUps"],
        },
    },
    "required": ["abstain"],
}

_ASSEMBLY_PROMPT = """You are the card-assembly stage of SceneIQ, Tubi's pause-screen surface. \
Build ONE "Scene Fact" card from the evidence packet below, or abstain.

A Scene Fact is a sourced insider detail about something the viewer can physically see \
or hear in the paused frame. It rewards curiosity about the film's WORLD, not the \
industry that made it. It should feel like a well-read friend's aside — specific, \
verifiable, non-obvious.

FILM: {film_title} ({film_year})
SCENE (~{timecode}): {scene_description}
ANCHOR ELEMENT: {anchor_element}

EVIDENCE PACKET (grounded search findings):
{findings}

AVAILABLE SOURCES (cite beats ONLY from these, by sourceIndex number — never write URLs):
{source_list}

Card contract:
- proactivePrompt: short engagement hook shown on pause (e.g. "Ever wonder where this \
was really filmed?"). Curious, not clickbait. G-rated.
- factCategory: one of actor, music, location, set_design, filming, historical, costume/prop.
- factHeader: the headline — the payoff in one line.
- factBeats: 3 to 5 bullets. EVERY beat states only what the evidence packet supports, \
and cites the single most relevant source by its sourceIndex from the list above.
- followUps: 2 to 3 short viewer prompts that extend curiosity about this element.

Hard rules:
- Do not state anything the evidence packet does not support. No invented names, \
numbers, dates, or causal claims. Preserve hedges ("reportedly").
- The card must point at the anchor element visible/audible in THIS scene.
- No plot information from later in the film than this scene.
- No casting drama, feuds, career-arc trivia, or negative claims about talent/partners.
- G-rated content only.
{abstain_rule}"""

_ABSTAIN_STRICT = """- ABSTAIN (abstain: true, with a reason) if the evidence says NO QUALIFIED EVIDENCE \
FOUND, is too thin for 3 supported beats, is generic/obvious, or cannot be tied to \
this scene. Abstaining is the correct output for weak evidence — never pad."""

_ABSTAIN_RELAXED = """- ABSTAIN (abstain: true, with a reason) only if the evidence says NO QUALIFIED \
EVIDENCE FOUND or genuinely cannot support 3 specific beats tied to this scene. If \
the evidence contains at least one specific, sourced, non-obvious detail about the \
anchor element, BUILD THE CARD — downstream validation will gate it. Do not pad \
beats with claims the evidence doesn't make."""


def assemble_card(
    client: GeminiClient, film_info: dict, packet: EvidencePacket, cfg: config.PipelineConfig
) -> SceneFactCard | None:
    if "NO QUALIFIED EVIDENCE FOUND" in packet.findings.upper() and len(packet.findings) < 400:
        return None
    source_list = "\n".join(
        f"[{i}] {s.title or s.domain or 'untitled source'}"
        for i, s in enumerate(packet.sources)
    ) or "(none)"
    data = client.structured(
        cfg.fast_model,
        _ASSEMBLY_PROMPT.format(
            film_title=film_info.get("title", ""),
            film_year=film_info.get("year", ""),
            timecode=packet.anchor.approx_timecode,
            scene_description=packet.anchor.scene_description,
            anchor_element=packet.anchor.anchor_element,
            findings=packet.findings,
            source_list=source_list,
            abstain_rule=_ABSTAIN_RELAXED if cfg.evidence_mode == "relaxed" else _ABSTAIN_STRICT,
        ),
        _CARD_SCHEMA,
        temperature=cfg.assembly_temperature,
    )
    if data.get("abstain") or not data.get("card"):
        return None
    c = data["card"]
    # Map index citations to URLs in code — models mangle long URLs when
    # asked to copy them. Beats citing an index outside the source list drop;
    # if fewer than 3 survive, the contract check downstream rejects.
    beats = [
        FactBeat(
            text=b["text"],
            source_url=packet.sources[b["sourceIndex"]].url,
            source_index=b["sourceIndex"],
        )
        for b in c["factBeats"]
        if 0 <= b.get("sourceIndex", -1) < len(packet.sources)
    ]
    return SceneFactCard(
        proactive_prompt=c["proactivePrompt"],
        fact_category=c["factCategory"],
        fact_header=c["factHeader"],
        fact_beats=beats,
        follow_ups=c["followUps"],
        scene_description=packet.anchor.scene_description,
        anchor_element=packet.anchor.anchor_element,
        runtime_fraction=packet.anchor.runtime_fraction,
        approx_timecode=packet.anchor.approx_timecode,
        sources=list(packet.sources),
    )
