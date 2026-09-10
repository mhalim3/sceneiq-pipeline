"""Stage 3: Card assembly — writes ONLY from fetched source bodies.

The model receives the actual text of each source (page extract or video
transcript) and must copy a 6-20 word verbatim anchor from the cited body for
every beat. Style rules adapted from the scene-sense prototype's card system
prompt. Abstention is a first-class output.
"""

from __future__ import annotations

from .. import config
from ..gemini import GeminiClient
from ..models import EvidencePacket, FactBeat, SceneFactCard

_BODY_EXCERPT_CHARS = 9000

_CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "abstain": {"type": "boolean"},
        "abstain_reason": {"type": "string"},
        "card": {
            "type": "object",
            "properties": {
                "shortVersion": {"type": "string"},
                "longDescription": {"type": "string"},
                "factCategory": {"type": "string", "enum": config.FACT_CATEGORIES},
                "factBeats": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "sourceIndex": {"type": "integer"},
                            "verbatimAnchor": {"type": "string"},
                        },
                        "required": ["text", "sourceIndex", "verbatimAnchor"],
                    },
                },
                "followUps": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["shortVersion", "longDescription", "factCategory", "factBeats", "followUps"],
        },
    },
    "required": ["abstain"],
}

_ASSEMBLY_PROMPT = """You write "Scene Fact" pause cards for SceneIQ, Tubi's CTV pause \
screen. Use ONLY the source bodies below. Never invent facts, names, numbers, or quotes.

A Scene Fact is a sourced insider detail about something the viewer can physically see \
or hear in the paused frame. It rewards curiosity about the film's WORLD, not the \
industry that made it.

FILM: {film_title} ({film_year})
SCENE (~{timecode}): {scene_description}
ANCHOR ELEMENT: {anchor_element}

SOURCES (cite beats ONLY from these, by index; each shows its fetched body text):
{source_blocks}

STYLE — match the best film-magazine writing:
- factBeats: prefer 4 to 5 single-sentence beats (minimum 3) that tell a micro-story in \
order. Each beat must be traceable to its cited source body, and each includes \
verbatimAnchor: a 6-20 word span COPIED VERBATIM from that source's body text supporting \
the beat. Beats are the verification layer — write them first, carefully.
- Each beat states ONLY what the text around its verbatim anchor says. Copy names, \
numbers, and reasons exactly — if the source says Stanford refused, write Stanford, \
never substitute a different university, person, or motive. Do not merge facts from \
two sources into one beat.
- longDescription: 2 to 4 sentences that merge the beats into one flowing paragraph. \
It must contain NO claim that is not in the beats — it is a rewrite of them, not an \
expansion. Viewer register, past tense for behind-the-scenes.
- shortVersion: ONE punchy on-screen statement of the fact itself. Aim for 50-60 \
characters, HARD MAXIMUM 80. Like "Cooper shucked every oyster himself in the opening \
scene." — a concrete fact statement, not a teaser, not clickbait. G-rated. It must be \
fully supported by the beats.
- followUps: 2 to 3 natural viewer next-questions, <= 10 words each (internal, for \
value scoring).
- Use CHARACTER names for what's on screen, REAL names for BTS figures (directors, \
costume designers, named crew).
- Don't re-describe the scene the viewer is watching; deliver only NEW information.
- Present tense for what's on screen; past tense for behind-the-scenes.
- Preserve hedges ("reportedly") — never upgrade a claim beyond its source.

HARD RULES:
- The card must point at the anchor element visible/audible in THIS scene.
- No plot information from later in the film than this scene.
- No casting drama, feuds, career-arc trivia, or negative claims about talent/partners.
- factCategory: one of actor, music, location, set_design, filming, historical, costume/prop.
{abstain_rule}"""

_ABSTAIN_STRICT = """- ABSTAIN (abstain: true, with a reason) if no source body supports 3 \
specific, scene-tied, non-obvious beats. Abstaining is the correct output for weak \
evidence — never pad."""

_ABSTAIN_RELAXED = """- ABSTAIN (abstain: true, with a reason) only if the source bodies \
genuinely cannot support 3 specific beats tied to this scene. If the bodies contain at \
least one specific, non-obvious detail about the anchor element, BUILD THE CARD — \
downstream validation will gate it. Do not pad beats with claims the bodies don't make."""


def _source_blocks(packet: EvidencePacket) -> str:
    blocks = []
    for i, s in enumerate(packet.sources):
        body = getattr(s, "_body", "")[:_BODY_EXCERPT_CHARS]
        blocks.append(
            f"[{i}] tier={s.tier} type={s.modality} | {s.title or s.domain}\n"
            f"    url: {s.url}\n"
            f"    body: {body}"
        )
    return "\n\n".join(blocks) or "(none)"


def assemble_card(
    client: GeminiClient, film_info: dict, packet: EvidencePacket, cfg: config.PipelineConfig
) -> SceneFactCard | None:
    if not packet.sources:
        return None
    data = client.structured(
        cfg.fast_model,
        _ASSEMBLY_PROMPT.format(
            film_title=film_info.get("title", ""),
            film_year=film_info.get("year", ""),
            timecode=packet.anchor.approx_timecode,
            scene_description=packet.anchor.scene_description,
            anchor_element=packet.anchor.anchor_element,
            source_blocks=_source_blocks(packet),
            abstain_rule=_ABSTAIN_RELAXED if cfg.evidence_mode == "relaxed" else _ABSTAIN_STRICT,
        ),
        _CARD_SCHEMA,
        temperature=cfg.assembly_temperature,
    )
    if data.get("abstain") or not data.get("card"):
        return None
    c = data["card"]
    beats = [
        FactBeat(
            text=b["text"],
            source_url=packet.sources[b["sourceIndex"]].url,
            source_index=b["sourceIndex"],
            verbatim_anchor=(b.get("verbatimAnchor") or "").strip(),
        )
        for b in c["factBeats"]
        if 0 <= b.get("sourceIndex", -1) < len(packet.sources)
    ]
    return SceneFactCard(
        short_version=(c["shortVersion"] or "").strip(),
        long_description=(c["longDescription"] or "").strip(),
        fact_category=c["factCategory"],
        fact_beats=beats,
        follow_ups=c["followUps"],
        scene_description=packet.anchor.scene_description,
        anchor_element=packet.anchor.anchor_element,
        runtime_fraction=packet.anchor.runtime_fraction,
        approx_timecode=packet.anchor.approx_timecode,
        sources=list(packet.sources),
    )
