"""Stage 3: Card assembly — writes ONLY from fetched source bodies.

The model receives the actual text of each source (page extract or video
transcript) and must copy a 6-20 word verbatim anchor from the cited body for
every beat. Style rules adapted from the scene-sense prototype's card system
prompt. Abstention is a first-class output.
"""

from __future__ import annotations

from .. import config, source_policy, editorial_guide
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
                "shortAlternatives": {"type": "array", "items": {"type": "string"}},
                "longDescription": {"type": "string"},
                "primaryClaim": {"type": "string"},
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
            "required": ["shortVersion", "longDescription", "primaryClaim", "factCategory", "factBeats", "followUps"],
        },
    },
    "required": ["abstain"],
}

_ASSEMBLY_PROMPT = """You write "Scene Fact" pause cards for SceneIQ, Tubi's CTV pause \
screen. Use ONLY the source bodies below. Never invent facts, names, numbers, or quotes.

A Scene Fact is a sourced fact about the film (its making, cast, crew, music, locations, release, money, or reception), written as a short headline and a longer version. The fact you are given was chosen already; your job is to state it accurately and phrase it well.

FILM: {film_title} ({film_year})
SCENE (~{timecode}): {scene_description}
ASSIGNED FACT (the ONE fact this card must tell): {anchor_element}

SOURCES (cite beats ONLY from these, by index; each shows its fetched body text):
{source_blocks}

SOURCE POLICY (which sources count; when several state the same claim, cite the most authoritative):
{source_policy}

{editorial_guide}

STYLE — match the best film-magazine writing:
- factBeats: prefer 3 to 5 single-sentence beats (minimum {min_beats}) that tell a micro-story in \
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
fully understand it on its own. Aim for 50-70 characters and try to stay under 80, but \
NEVER sacrifice the clarity or the punch of the fact just to hit the length — a clear, \
complete, surprising short that runs a little long is far better than a cramped or \
vague one. (An over-length short is copy-edited down later; it is never dropped.) \
Lively, natural, and fun — never dry, robotic, or overly formal. Playful wording is \
welcome but must never hide the actual fact.
  - SELF-CONTAINED CHARACTERS: follow the NAME PEOPLE principle in the EDITORIAL GUIDE (role + character name + actor; a name you use must be stated by one of your factBeats or be named in the ASSIGNED FACT).
  - LEAD WITH THE PUNCHLINE: the very first clause must land the single most \
surprising, specific point of the fact — the reversal, the record, the one who \
objected, the thing that was secretly real. If the hook is that an actor REQUESTED a \
line be cut, that an element is an easter egg to a SPECIFIC other film, or that a crew \
member was a FORMER stunt coordinator, say THAT first — do not bury it behind setup or \
soften it into a category. Ask: what is the one detail a viewer would text a friend? \
Open on it.
  - NAME THE SPECIFIC, NOT THE CATEGORY: always name the concrete thing — the exact \
film referenced ("an easter egg nod to The Italian Job", not "references his past \
role"), the exact object (the Mini Cooper, the specific song, the specific move), the \
exact prior job ("a former stunt coordinator", not "brought real fighting experience"). \
A category label where a specific exists is a failed short.
  - FACTUAL PRECISION: copy the source's details exactly — a white tank top is not a \
t-shirt, Stanford is not "a university", six days is not "a short time". Never round \
away or generalize a concrete detail the source gives.
  - HOOK: every short must carry a concrete, specific point. BANNED: \
empty phrases ("was a wild one", "had an interesting experience", "was a big deal") \
AND vague praise with no specific ("showed her uninhibited wit", "gave a great \
performance") — unless the statement says exactly what happened and why it is \
surprising. Prefer concrete numbers, firsts, reversals, and specifics.
  - GOOD: "Kevin Bacon was almost unrecognizable as the film's flamboyant salon owner."
  - GOOD: "The Rock was CGI'd into the fast-car scenes because of real motion sickness."
  - GOOD: "The lead asked for his most famous line to be CUT from the final cut."
  - BAD: "Alfre Woodard showed her uninhibited wit." (no concrete fact, no hook)
  - BAD: "The lead's line references one of his earlier action roles." (names no \
specific film — which role? say it)
  - BAD: "Joe encourages Gina's piano prodigy daughter." (bare character names, not \
understandable alone, no hook)
  G-rated, no clickbait, fully supported by the beats.
  - shortAlternatives: ALSO give 2-3 different headlines for the SAME fact, each {short_cap} characters or fewer and each fully backed by the beats (same punchline, different wording or name forms). shortVersion should be the best headline that is {short_cap} characters or fewer; if you cannot fit the punchline and the needed names in {short_cap}, put your best attempt in shortVersion anyway.
- longDescription (this field holds the FULL FACT, the complete sourced statement): one to three sentences, about 280 characters at most. A separate 140-character "long" version is written from it LATER, so do not try to write that one here. Follow the LONG STANDS ALONE principle in the EDITORIAL GUIDE: restate the short's claim (naming people again), then add ONE new specific that sharpens the hook. EVERY specific in it must be stated by one of your factBeats. Do NOT mention scenes, moments or where in the film something happens — the card is shown without scene context, and the scene hint is only an internal note that may be wrong. No claim beyond the beats.
- primaryClaim: ONE plain sentence stating the core claim of the card (who did/was what, with the key number or name), stripped of hooks and extra detail. Used only to spot duplicate stories.
- followUps: 2 to 3 natural viewer next-questions, <= 10 words each (internal, for \
value scoring).
- A card stands alone with NO scene context: apply the NAME PEOPLE principle to the shortVersion AND the longDescription (full fact).
- Don't spend words describing what the viewer can already see; deliver only NEW \
information. You still must identify who or what the fact is about by role or name \
so the card reads clearly on its own — identify, don't narrate.
- Present tense for what's on screen; past tense for behind-the-scenes.
- Preserve hedges ("reportedly") — never upgrade a claim beyond its source.

HARD RULES:
- Write ONLY about the ASSIGNED FACT. The sources may contain many other facts — do not switch to a different one, even if it is more surprising. Other details may appear only as support for the assigned fact. If the sources confirm the CORE of the assigned fact but not an extra detail in it (an added title, number, quote, ranking or clause), write the card about the confirmed core and leave the unconfirmed detail OUT. ABSTAIN only if the core itself is not supported.
- {scene_rule}
- Every fact must be about {film_title} SPECIFICALLY — this production, this cast, \
this location, this song. Generic facts about filmmaking, prop design, or industry \
practice that do not concern this film are NOT Scene Facts; if that's all the \
sources offer, ABSTAIN.
- NO spoilers of any kind: if a line could SOUND like a spoiler, leave it out. Do not mention \
plot outcomes, twists, who lives/dies/wins/loses, a character's secret identity or hidden role, \
a hidden role whose reveal spoils a surprise in the story, endings, or anything that happens later in the film. A real person appearing as themselves is fine. Tell \
the behind-the-scenes fact without saying what it means for the story.
- Money facts are allowed when a source states the figures: budget, worldwide box office, how many times the budget a film earned, franchise records, and what a named lead actor was paid. Give the actual numbers, and put them in your beats. Do not guess or round beyond the source.
- Casting and development history ARE welcome (actors considered, roles that \
changed, actors who dropped out, how the project developed). No on-set feuds, \
gossip, or negative claims about talent/partners.
- If the assigned fact is one of the DO NOT SHOW kinds in the editorial guide (cameo, cast credit, mainly about another film, unflattering to a real person, off-topic personal trivia, plain reference data), ABSTAIN with a reason that starts "excluded:". The shortVersion must also pass the STANDALONE test: interesting and clear with no plot knowledge, and never longer than {short_cap} characters.
- factCategory: one of actor, music, location, set_design, filming, historical, costume/prop.
{abstain_rule}"""

_ABSTAIN_STRICT = """- ABSTAIN (abstain: true, with a reason) if no source body supports {min_beats} \
specific beats that support the assigned fact. Abstaining is the correct output for weak \
evidence — never pad."""

_ABSTAIN_RELAXED = """- ABSTAIN (abstain: true, with a reason) only if the source bodies \
genuinely cannot support {min_beats} specific beats tied to this scene. If the bodies contain at \
least one specific detail that supports the assigned fact, BUILD THE CARD — \
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
        packet.abstain_reason = "no sources fetched"
        return None
    data = client.structured(
        cfg.quality_model,
        _ASSEMBLY_PROMPT.format(
            film_title=film_info.get("title", ""),
            film_year=film_info.get("year", ""),
            timecode=packet.anchor.approx_timecode,
            scene_description=packet.anchor.scene_description,
            anchor_element=packet.anchor.anchor_element,
            source_blocks=_source_blocks(packet),
            source_policy=source_policy.definitions_block(),
            editorial_guide=editorial_guide.writer_block(),
            scene_rule=(
                "The assigned fact must relate to something visible/audible in THIS scene."
                if packet.anchor.scope == "scene" else
                "This is a GENERAL title card, shown at any point during the film: the "
                "fact must be about the film as a whole (production, records, myths it "
                "created, reception). No scene tie is required. factCategory may be "
                "'general' when none of the 7 fits."
            ),
            min_beats=cfg.min_beats,
            short_cap=cfg.short_cap,
            abstain_rule=(_ABSTAIN_RELAXED if cfg.evidence_mode == "relaxed" else _ABSTAIN_STRICT
                          ).format(min_beats=cfg.min_beats),
        ),
        _CARD_SCHEMA,
        temperature=cfg.assembly_temperature,
    )
    if data.get("abstain") or not data.get("card"):
        packet.abstain_reason = (data.get("abstain_reason") or "writer abstained (no reason given)").strip()
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
    # Headline cap: keep the writer's best headline when it fits; otherwise take the
    # longest (most informative) alternative that does. No rewrite pass: rewriting
    # after the fact is what introduced claims the beats did not support.
    short = (c["shortVersion"] or "").strip()
    if len(short) > cfg.short_cap:
        fits = [a.strip() for a in (c.get("shortAlternatives") or [])
                if a and a.strip() and len(a.strip()) <= cfg.short_cap]
        if fits:
            short = max(fits, key=len)
    return SceneFactCard(
        short_version=short,
        primary_claim=(c.get("primaryClaim") or "").strip(),
        long_description=(c["longDescription"] or "").strip(),
        fact_category=c["factCategory"],
        fact_beats=beats,
        follow_ups=c["followUps"],
        scope=packet.anchor.scope,
        scene_dependency=packet.anchor.scene_dependency,
        scene_hint=packet.anchor.scene_hint,
        scene_end_fraction=packet.anchor.end_fraction,
        scene_description=packet.anchor.scene_description,
        anchor_element=packet.anchor.anchor_element,
        runtime_fraction=packet.anchor.runtime_fraction,
        approx_timecode=packet.anchor.approx_timecode,
        sources=list(packet.sources),
    )


_REPAIR_SCHEMA = {
    "type": "object",
    "properties": {
        "abstain": {"type": "boolean"},
        "shortVersion": {"type": "string"},
        "longDescription": {"type": "string"},
    },
    "required": ["abstain"],
}

_REPAIR_PROMPT = """A pause-screen fact card for {film_title} ({film_year}) was rejected by \
review for a WORDING problem only. Rewrite its on-screen text so it passes.

ASSIGNED FACT (the card must stay about this): {assigned_fact}

VERIFIED BEATS (the ONLY facts you may use — each is already checked against sources):
{beats}

CURRENT shortVersion: {short_version}
CURRENT longDescription (the full fact): {long_description}

PROBLEMS TO FIX:
{problems}

Rules:
- Use ONLY what the beats state. Drop any name, number, or claim the beats do not say.
- Keep the fact's punchline first and name the concrete specific; the short must stand \
alone (identify people by role or real name + job, never a bare first name).
- The longDescription (the full fact) must elaborate the SAME fact and add a detail that IS in the beats \
(max 280 characters); do not just restate the short.
- Everything must be G-rated: no profanity (even bleeped or censored), no sexual, drug, or \
graphic-violence detail. Say the interesting thing without the mature wording. If the fact \
cannot be told G-rated without losing what makes it a fact, set abstain true.
- Aim for a short of 50-70 characters; clarity beats length."""


def repair_card(client: GeminiClient, film_info: dict, card: SceneFactCard,
                problems: list[str], cfg: config.PipelineConfig) -> bool:
    """Rewrite card.short_version / long_description from its verified beats.
    Returns False when the model abstains (fact can't be repaired)."""
    data = client.structured(
        cfg.quality_model,
        _REPAIR_PROMPT.format(
            film_title=film_info.get("title", ""),
            film_year=film_info.get("year", ""),
            assigned_fact=card.anchor_element,
            beats="\n".join(f"- {b.text}" for b in card.fact_beats),
            short_version=card.short_version,
            long_description=card.long_description,
            problems="\n".join(f"- {p}" for p in problems),
        ),
        _REPAIR_SCHEMA,
        temperature=cfg.assembly_temperature,
    )
    short = (data.get("shortVersion") or "").strip()
    long_ = (data.get("longDescription") or "").strip()
    if data.get("abstain") or not short or not long_:
        return False
    card.short_version, card.long_description = short, long_
    return True

