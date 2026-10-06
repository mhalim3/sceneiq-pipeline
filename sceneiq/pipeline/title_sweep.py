"""Stage 1b: Title-level fact sweep (search-first, then anchor).

The anchor-first flow can only research what discovery anchored — famous
title-level facts (the Michelin fork myth, Cooper's real French, box office
records) are unreachable if no matching anchor was proposed. This stage works
the other way, per the PRD cameo-detector pattern: broad grounded searches for
the title's most-documented facts, then each fact is anchored to a Moments
scene when one supports it (scope "scene") or kept as a GENERAL card (scope
"general") that the player may show at any time after its spoiler floor.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from .. import config
from ..gemini import GeminiClient
from ..models import Anchor

log = logging.getLogger("sceneiq")

_SWEEP_QUERIES = [
    "{title} {year} most interesting trivia fun facts",
    "{title} {year} behind the scenes secrets on-set stories",
    "{title} {year} what was real practical effects myths",
    "{title} {year} post-credits scenes cameos easter eggs director",
]

# Later rounds search different angles — repeating the same queries returns
# the same articles. One distinct set per round (round 2 -> index 0, ...); the
# last set repeats if more rounds run.
_SWEEP_QUERIES_LATER_SETS = [
    [
        "{title} {year} director interview making of stories",
        "{title} {year} soundtrack hidden details easter eggs",
        "{title} {year} filming locations you can visit",
        "{title} {year} props costumes how they made",
        "{title} {year} based on book true story real people how the movie differs",
    ],
    [
        "{title} {year} casting who else was considered turned down the role",
        "{title} {year} actors improvised ad-libbed lines scenes",
        "{title} {year} actor training preparation transformation injuries",
        "{title} {year} real story true events vs movie differences",
        "{title} {year} cinematography sound design set building production designer interview",
    ],
    [
        "{title} {year} stunts visual effects how it was done",
        "{title} {year} deleted scenes alternate ending cut from film",
        "{title} {year} cameos references nods to other movies",
        "{title} {year} things you missed hidden details",
        "{title} {year} early draft deleted scenes alternate ending changed lines oral history anniversary",
    ],
    [
        "{title} {year} screenplay origin development history writer",
        "{title} {year} cast reunion later interview looks back",
        "{title} {year} music score composer song choices",
        "{title} {year} cultural impact legacy memes quotes",
        "{title} {year} cast interview told Vulture Variety EW on set story",
    ],
]

_SWEEP_PROMPT = """Run a Google search for: {query}

Read what the results say and report the most interesting, SPECIFIC facts about the \
film {title} ({year}) that they attest. If a result points to a promising lead (a \
named interview, featurette, oral history, commentary), run one or two follow-up \
searches to pull the detail.

Breadth matters. Don't stop at the three facts every article leads with — include the \
lesser-known ones too. Look for: on-set stories; things secretly real or secretly \
fake; casting (who else was considered, who turned it down); actor preparation, \
transformations, improvised lines, stunts they did themselves; real people, true \
events and source material, and how the film differs; hidden details, references, \
cameos and callbacks; crafty production solves (sets, locations, props, costumes, VFX, \
sound, music); how the film got made (early drafts, alternate endings, cut scenes, \
title changes); and cultural impact.

Give 8-15 facts. One line each, in this form:
FACT: <the specific claim, with names and numbers exactly as the source gives them> | \
WHEN: <the scene or moment in the film it relates to, or "any"> | SAID BY: <the named \
filmmaker/cast/crew member if quoted, otherwise "reported"> | SOURCE: <outlet or site>

Rules:
- Trivia sites and listicles are fine as LEADS — every fact is verified against \
qualified sources later.
- If sources disagree (different numbers, different versions), report both and say so.
- Do NOT invent any URL or fact — only report what the results say.
- Skip plot summary, budget/box-office figures, awards lists, feuds, lawsuits and \
tragedies."""

_FACTS_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact_summary": {"type": "string"},
                    "category_guess": {
                        "type": "string",
                        "enum": config.FACT_CATEGORIES + ["general"],
                    },
                    "search_queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 3,
                    },
                    "scene_dependency": {
                        "type": "string",
                        "enum": ["agnostic", "specific"],
                    },
                    "scene_hint": {"type": "string"},
                },
                "required": ["fact_summary", "category_guess", "search_queries",
                             "scene_dependency", "scene_hint"],
            },
        },
    },
    "required": ["facts"],
}

_STRUCTURE_PROMPT = """From these research notes about {title} ({year}), extract up to \
{max_facts} distinct, specific candidate facts worth a pause-screen card.

Rules (the title-level fact ruleset):
- Facts must be directly connected to THIS film and understandable on their own \
(a paused viewer who does not know character names must still get it).
- CLASSIFY each fact by scene_dependency:
  - "agnostic" — makes sense shown at ANY point in the movie, not tied to any \
particular moment (e.g. how the production was financed, an actor's preparation, a \
title change, a filming-location fact). These are the backbone; keep finding them.
  - "specific" — only lands when the viewer is at a PARTICULAR moment: it is about \
a specific scene, shot, line, song cue, location, prop, stunt, or effect that is on \
screen at one identifiable point (e.g. "the diner shootout used no CGI", "the song \
playing here was written the night before"). For every "specific" fact, scene_hint \
must name that moment precisely enough to window it — what is on screen, said, or \
heard when the fact applies. Actively look for these; aim to surface several per \
title alongside the agnostic ones.
- ELIGIBLE classes — aim for a balanced mix, never forcing a category that has no \
interesting fact: production (locations, schedule, delays, reshoots, title changes, \
set construction, creative decisions); actors (preparation, training, reactions, \
working experiences, previous roles, cast relationships); main characters \
(inspirations, casting decisions, actor connections, unusual traits); casting and \
development history (actors considered, roles that changed, actors who dropped \
out, how the project developed); director/writer/producer comments; title-related \
locations, objects, organizations, creatures, or concepts; the central setting \
(how it was built, recreated, filmed); technical work (CGI, motion capture, \
stunts, practical effects, camera tech, makeup, costumes, production design); \
cultural impact.
- BANNED: any financial information (budget, box office, profitability, opening \
weekend, financial comparisons); plot summaries of this film; ANYTHING that could sound like a spoiler (plot outcomes, twists, who lives or dies, a villain's or character's secret identity, surprise cameos or secret roles, endings, later events) — if in doubt leave it out; bare ratings or \
review scores (reception only when the CONTRAST is interesting); awards unless \
notable or surprising; basic encyclopedic facts (release year, director name, \
studio, cast list alone); trivial goofs and continuity errors; on-set feuds and gossip.
- Reject trivial observations that merely identify what is visible ("the lead is holding a gun", "this is a car chase"). KEEP specific scene facts that reveal non-obvious production, historical, musical, technical, or cultural information about something visible. Reject: "the lead wears a white shirt". Keep: "the lead's white shirt was chosen to echo a famous earlier film". Reject: "this is a car chase". Keep: "the cars in this chase were filmed practically because the director banned digital vehicles".
- Every fact must state something that HAPPENED or IS TRUE and can be checked — an \
event, a number, a decision, a reversal, a named person, place or object. A general \
characterization of someone's style or approach ("the director lets the character \
become the story", "the film has a gritty tone") is NOT a fact; skip it unless the \
notes give a concrete story behind it.
- Every fact must be interesting, surprising, unusual, memorable, or informative — \
something a viewer would retell. Skip facts a viewer would shrug at, and reject \
vague-trait praise with no concrete specific (e.g., "an actor showed her wit", \
"gave a great performance").
- Each fact_summary must be understandable ON ITS OWN. Identify characters by ROLE plus \
character name, and the actor where it helps ("the lead character Jordan Belfort \
(Leonardo DiCaprio)") — never a bare "the lead" or a bare first name.
- The notes may tag each fact with WHEN (the moment in the film it relates to) and SAID BY (who is quoted). Use WHEN to write the scene_hint for a "specific" fact, and keep the SAID BY attribution in the fact_summary when it is a named filmmaker or cast member.
- For each: a one-line fact_summary, the closest category ("general" when it isn't \
tied to any on-screen element), 2-3 runnable search_queries (include the film title \
in at least one), scene_dependency ("agnostic" or "specific" per the rule above), and \
scene_hint — for a "specific" fact, the precise on-screen moment it belongs to; for an \
"agnostic" fact, "any".

{avoid_block}NOTES:
{notes}"""

_AVOID_BLOCK = """ALREADY-FOUND FACTS from earlier rounds. Do NOT repropose these or \
close variants — every fact this round must be a DIFFERENT story:

{found}

"""

_ANCHOR_SCHEMA = {
    "type": "object",
    "properties": {
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact_index": {"type": "integer"},
                    "scene_index": {"type": "integer",
                                    "description": "first scene the fact fits; "
                                                   "-1 when no scene fits"},
                    "anchor_evidence": {
                        "type": "string",
                        "description": "the exact detail from the scene data showing the "
                                       "fact's subject is visible or audible there; "
                                       "empty if there is none",
                    },
                    "scene_index_end": {
                        "type": "integer",
                        "description": "last scene of the span when the fact applies "
                                       "across several consecutive scenes; equal to "
                                       "scene_index for a single-moment fact; -1 if "
                                       "scene_index is -1",
                    },
                },
                "required": ["fact_index", "scene_index", "scene_index_end", "anchor_evidence"],
            },
        },
    },
    "required": ["assignments"],
}

_ANCHOR_PROMPT = """Match each candidate fact to the scene(s) where a paused viewer \
would find it most relevant, using the real scene data below.

- A HYPERSPECIFIC fact — about one line, one shot, one prop, one stunt, one song cue \
on screen at a single identifiable point — gets ONE scene: set scene_index to that \
scene and scene_index_end equal to it.
- A fact that applies across a STRETCH of the film — a recurring location, a running \
visual motif, a character trait or costume present through several scenes, an effect \
used across a sequence — gets a SPAN: set scene_index to the first scene it fits and \
scene_index_end to the last. Keep the span tight — only the consecutive scenes the \
fact genuinely covers, not the whole film.
- Use scene_index -1 (and scene_index_end -1) when no scene clearly fits; the fact \
becomes a general card shown at any time.

Only assign a scene (or span) when the scene data (summary, cast, setting, objects, actions) \
CONFIRMS the fact's subject is visible or audible there — a thematic relationship is not \
enough. For every assignment, put in anchor_evidence the specific detail from the scene \
data that shows it (e.g. \"the antique piano is visible in the background\"). If you cannot \
quote such a detail, use scene_index -1.

FACTS:
{facts}

SCENES:
{scene_blob}"""


def title_sweep(
    client: GeminiClient,
    film_info: dict,
    cfg: config.PipelineConfig,
    moments=None,
    avoid: list | None = None,
    round_n: int = 1,
) -> list[Anchor]:
    title = film_info.get("title", "")
    year = film_info.get("year", "")

    queries = (_SWEEP_QUERIES if round_n <= 1 else
               _SWEEP_QUERIES_LATER_SETS[min(round_n - 2, len(_SWEEP_QUERIES_LATER_SETS) - 1)])

    def _run(q):
        return client.grounded(
            cfg.research_model,
            _SWEEP_PROMPT.format(query=q.format(title=title, year=year),
                                 title=title, year=year),
            temperature=0.2,
        ).text

    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        notes = list(pool.map(_run, queries))

    data = client.structured(
        cfg.fast_model,
        _STRUCTURE_PROMPT.format(title=title, year=year,
                                 max_facts=cfg.sweep_facts_max,
                                 avoid_block=(_AVOID_BLOCK.format(
                                     found="\n".join(f"- {a}" for a in avoid))
                                     if avoid else ""),
                                 notes="\n\n---\n\n".join(notes)),
        _FACTS_SCHEMA,
        temperature=0.2,
    )
    facts = data.get("facts", [])[: cfg.sweep_facts_max]
    if not facts:
        return []

    # Anchor each fact to a real scene (or span of scenes) when Moments data
    # supports it. assignments[i] is a (start, end) scene-index pair; (-1, -1)
    # means no scene fit and the fact falls back to a general card.
    assignments = {i: (-1, -1) for i in range(len(facts))}
    if moments is not None:
        # Show (nearly) every scene: sampling 40 hid ~45% of a 73-scene film, so
        # facts about those scenes could never be anchored.
        scenes = moments.sampled_scenes(max_scenes=90)
        scene_blob = "\n\n".join(s.as_context(max_chars=350) for s in scenes)
        facts_blob = "\n".join(
            f"[{i}] {f['fact_summary']} (relevant on screen: {f['scene_hint']})"
            for i, f in enumerate(facts)
        )
        try:
            resp = client.structured(
                cfg.fast_model,
                _ANCHOR_PROMPT.format(facts=facts_blob, scene_blob=scene_blob),
                _ANCHOR_SCHEMA,
                temperature=0.0,
            )
            for a in resp.get("assignments", []):
                fi = a.get("fact_index", -1)
                if not 0 <= fi < len(facts):
                    continue
                start = a.get("scene_index", -1)
                end = a.get("scene_index_end", start)
                # No quoted on-screen evidence -> not anchored (general card).
                if start >= 0 and not (a.get("anchor_evidence") or "").strip():
                    start = -1
                if start < 0:
                    assignments[fi] = (-1, -1)
                    continue
                # Normalize: end defaults to start and never precedes it.
                if end < start:
                    end = start
                assignments[fi] = (start, end)
        except Exception:
            pass  # anchoring is best-effort; facts fall back to general

    anchors = []
    for i, f in enumerate(facts):
        dependency = "specific" if f.get("scene_dependency") == "specific" else "agnostic"
        hint = (f.get("scene_hint") or "").strip()
        if hint.lower() in ("", "any", "n/a", "none"):
            hint = ""
        start_i, end_i = assignments[i]
        scene = moments.scene(start_i) if (moments and start_i >= 0) else None
        end_scene = (moments.scene(end_i) if (scene and end_i > start_i) else scene) or scene
        # Guard against a runaway span: a window covering more than
        # max_scene_span_fraction of the runtime is not a pause-window, it is a
        # title-level fact. Demote it to a general card (shown anywhere) rather
        # than pin a whole-sequence fact to one arbitrary scene window.
        if (scene is not None
                and end_scene.end_fraction - scene.runtime_fraction
                > cfg.max_scene_span_fraction):
            scene = None
        if scene is not None:
            # Moments gave real timing: schedule as a scene card. When the fact
            # spans several scenes, the window runs from the first scene's start
            # to the last scene's end; a single-scene fact uses just that scene.
            spans = end_i > start_i
            anchors.append(Anchor(
                scene_description=(
                    f"{scene.as_context()}\n\n(fact spans through: "
                    f"{end_scene.as_context(max_chars=200)})" if spans
                    else scene.as_context()
                ),
                anchor_element=f["fact_summary"],
                runtime_fraction=scene.runtime_fraction,
                end_fraction=end_scene.end_fraction,
                approx_timecode=scene.start_time,
                category_guess=f["category_guess"] if f["category_guess"] != "general"
                else "filming",
                search_hint=f["fact_summary"],
                search_queries=list(f.get("search_queries") or [])[:3],
                scope="scene",
                scene_dependency="specific",
                scene_hint=hint or scene.as_context(max_chars=160),
                origin="title_sweep",
            ))
        else:
            # No Moments timing available: the card is scheduled as GENERAL
            # (showable across the runtime), but its editorial classification
            # is preserved. A "specific" fact carries its scene_hint so the
            # review can group it and a human can window it later.
            desc = ("GENERAL CARD — shown at any point during the film, "
                    "not tied to a specific scene.")
            if dependency == "specific" and hint:
                desc = ("SCENE-SPECIFIC fact (no Moments timing yet) — belongs at: "
                        f"{hint}")
            anchors.append(Anchor(
                scene_description=desc,
                anchor_element=f["fact_summary"],
                runtime_fraction=0.0,
                end_fraction=0.0,
                approx_timecode="",
                category_guess=f["category_guess"] if dependency == "specific"
                and f["category_guess"] != "general" else "general",
                search_hint=f["fact_summary"],
                search_queries=list(f.get("search_queries") or [])[:3],
                scope="general",
                scene_dependency=dependency,
                scene_hint=hint,
                origin="title_sweep",
            ))
    n_specific = sum(1 for a in anchors if a.scene_dependency == "specific")
    n_scene = sum(1 for a in anchors if a.scope == "scene")
    log.info("  title sweep round %d: %d facts (%d scene-specific, %d agnostic; "
             "%d scheduled as scene cards)",
             round_n, len(anchors), n_specific, len(anchors) - n_specific, n_scene)
    return anchors
