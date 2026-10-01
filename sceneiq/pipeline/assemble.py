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
                "factCategory": {"type": "string", "enum": config.FACT_CATEGORIES + ["general"]},
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
- shortVersion: ONE concise, direct statement that lands the surprising point \
IMMEDIATELY and reads as a STANDALONE HOOK. The card is shown with no scene context, \
so a viewer who has not seen the film and does not know any character's name must \
fully understand it on its own. Aim for 50-70 characters, HARD MAXIMUM 80 (stretch to \
~90 ONLY when a role descriptor is needed for a character to be understood). Lively, \
natural, and fun — never dry, robotic, or overly formal. Playful wording is welcome \
but must never hide the actual fact.
  - SELF-CONTAINED CHARACTERS: never reference a character by a bare proper name the \
viewer would have to already know. Name a character by their ROLE in the film — "the \
salon owner", "the film's main villain", "the lead", "the protagonist's mother" — \
optionally pairing the role with the name ("salon owner Jorge"). The actor's real \
name plus the role is ideal ("Kevin Bacon's flamboyant salon owner"). For a \
behind-the-scenes person, use their real name and job ("director Roland Emmerich").
  - HOOK: every short must carry a concrete, surprising, retellable point. BANNED: \
empty phrases ("was a wild one", "had an interesting experience", "was a big deal") \
AND vague praise with no specific ("showed her uninhibited wit", "gave a great \
performance") — unless the statement says exactly what happened and why it is \
surprising. Prefer concrete numbers, firsts, reversals, and specifics.
  - GOOD: "Kevin Bacon was almost unrecognizable as the film's flamboyant salon owner."
  - GOOD: "The Rock was CGI'd into the fast-car scenes because of real motion sickness."
  - BAD: "Alfre Woodard showed her uninhibited wit." (no concrete fact, no hook)
  - BAD: "Joe encourages Gina's piano prodigy daughter." (bare character names, not \
understandable alone, no hook)
  G-rated, no clickbait, fully supported by the beats.
- longDescription: one or two sentences that ADD CONTEXT and show why the fact is \
interesting, surprising, or unusual — not a verbatim echo of the short. Introduce any \
character by role on first mention here too ("Djimon Hounsou's character, the \
handyman Joe"), so the long also reads standalone. Maximum 280 characters. Example: short "Kevin Bacon finished his entire role in just six days." \
long "Bacon's time on set was practically a blink-and-you'll-miss-it appearance — \
he completed all of his work in only six days, an unusually short schedule for a \
recognizable supporting actor." No claim beyond the beats.
- followUps: 2 to 3 natural viewer next-questions, <= 10 words each (internal, for \
value scoring).
- A card stands alone with NO scene context, so identify everyone a viewer can't be \
assumed to know: name on-screen characters by their ROLE in the film (the lead, the \
villain, the salon owner), optionally with the character's name; use REAL names and \
jobs for behind-the-scenes figures (director, costume designer, named crew). Never \
reference a character by a bare first name the viewer would have to already know.
- Don't re-describe the scene the viewer is watching; deliver only NEW information.
- Present tense for what's on screen; past tense for behind-the-scenes.
- Preserve hedges ("reportedly") — never upgrade a claim beyond its source.

HARD RULES:
- {scene_rule}
- Every fact must be about {film_title} SPECIFICALLY — this production, this cast, \
this location, this song. Generic facts about filmmaking, prop design, or industry \
practice that do not concern this film are NOT Scene Facts; if that's all the \
sources offer, ABSTAIN.
- No plot information from later in the film than this scene.
- NO financial information: budget, box office, profitability, opening weekend, or \
financial comparisons — abstain if that is all the sources offer.
- Casting and development history ARE welcome (actors considered, roles that \
changed, actors who dropped out, how the project developed). No on-set feuds, \
gossip, or negative claims about talent/partners.
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
            scene_rule=(
                "The card must point at the anchor element visible/audible in THIS scene."
                if packet.anchor.scope == "scene" else
                "This is a GENERAL title card, shown at any point during the film: the "
                "fact must be about the film as a whole (production, records, myths it "
                "created, reception). No scene tie is required. factCategory may be "
                "'general' when none of the 7 fits."
            ),
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
        scope=packet.anchor.scope,
        scene_end_fraction=packet.anchor.end_fraction,
        scene_description=packet.anchor.scene_description,
        anchor_element=packet.anchor.anchor_element,
        runtime_fraction=packet.anchor.runtime_fraction,
        approx_timecode=packet.anchor.approx_timecode,
        sources=list(packet.sources),
    )
