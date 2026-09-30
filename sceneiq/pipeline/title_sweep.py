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
# the same articles.
_SWEEP_QUERIES_LATER = [
    "{title} {year} director interview making of stories",
    "{title} {year} soundtrack hidden details easter eggs",
    "{title} {year} filming locations you can visit",
    "{title} {year} props costumes how they made",
]

_SWEEP_PROMPT = """Run a Google search for: {query}

Report the MOST INTERESTING facts about the film {title} ({year}) that the results \
attest — the facts fan articles and trivia lists lead with, the ones a viewer would \
retell at dinner. On-set stories, things that were secretly real (or secretly fake), \
actor transformations, hidden connections, crafty production solves, myths the film \
created or busted. Trivia sites and listicles are fine as LEADS here — every fact \
gets independently verified against qualified sources later. Do NOT invent any URL — \
only describe what the search returned. Skip plot summary and casting gossip."""

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
                    "scene_hint": {"type": "string"},
                },
                "required": ["fact_summary", "category_guess", "search_queries", "scene_hint"],
            },
        },
    },
    "required": ["facts"],
}

_STRUCTURE_PROMPT = """From these research notes about {title} ({year}), extract up to \
{max_facts} distinct, specific candidate facts worth a pause-screen card.

Rules:
- Facts must connect to THIS film. Director filmography ("the director also made \
Deadpool 2"), franchise context (how characters/actors connect across the series, \
spin-off firsts), and notable cast facts (returns, introductions, real-life \
connections) ARE eligible — casual viewers love them. NEVER propose: plot summary \
of THIS film, casting drama (who turned down or almost got a role, who demanded a \
casting), on-set feuds, or generic industry practice.
- MOST facts should be title-level context (Phase 3: title-level is the primary \
card class; scene-tied facts enhance where available).
- RANK BY INTERESTINGNESS. Lead with the facts a viewer would retell: on-set \
stories, secretly-real/secretly-fake reveals, actor transformations and real \
skills, hidden connections to other films/places/franchise entries, director and \
cast crossovers, surprise cameos and post-credits details, crafty production \
solves, myths the film created or busted. Business facts (box office, budgets, \
marketing spend, release scheduling) are LOW value — propose at most ONE, and only \
if genuinely surprising.
- Every fact must pass the "wait, really?" test: a concrete number, a first, a \
hidden connection (this farm was also Smallville), a record, or a surprising \
production solve. Skip facts a viewer would shrug at.
- For each: a one-line fact_summary, the closest category ("general" when it isn't \
tied to any on-screen element), 2-3 runnable search_queries (include the film title \
in at least one), and scene_hint — what would be on screen when this fact is most \
relevant, or "any" for general facts.

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
                                    "description": "-1 when no scene fits"},
                },
                "required": ["fact_index", "scene_index"],
            },
        },
    },
    "required": ["assignments"],
}

_ANCHOR_PROMPT = """Match each candidate fact to the ONE scene where a paused viewer \
would find it most relevant, using the real scene data below. Use scene_index -1 \
when no scene clearly fits (the fact becomes a general card shown at any time). \
Only assign a scene when its data (summary, cast, setting) actually supports the \
fact's subject being on screen.

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

    queries = _SWEEP_QUERIES if round_n <= 1 else _SWEEP_QUERIES_LATER

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

    # Anchor each fact to a real scene when Moments data supports it.
    assignments = {i: -1 for i in range(len(facts))}
    if moments is not None:
        scenes = moments.sampled_scenes(max_scenes=40)
        scene_blob = "\n\n".join(s.as_context(max_chars=400) for s in scenes)
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
                if 0 <= a.get("fact_index", -1) < len(facts):
                    assignments[a["fact_index"]] = a.get("scene_index", -1)
        except Exception:
            pass  # anchoring is best-effort; facts fall back to general

    anchors = []
    for i, f in enumerate(facts):
        scene = moments.scene(assignments[i]) if (moments and assignments[i] >= 0) else None
        if scene is not None:
            anchors.append(Anchor(
                scene_description=scene.as_context(),
                anchor_element=f["fact_summary"],
                runtime_fraction=scene.runtime_fraction,
                end_fraction=scene.end_fraction,
                approx_timecode=scene.start_time,
                category_guess=f["category_guess"] if f["category_guess"] != "general"
                else "filming",
                search_hint=f["fact_summary"],
                search_queries=list(f.get("search_queries") or [])[:3],
                scope="scene",
                origin="title_sweep",
            ))
        else:
            anchors.append(Anchor(
                scene_description="GENERAL CARD — shown at any point during the film, "
                                  "not tied to a specific scene.",
                anchor_element=f["fact_summary"],
                runtime_fraction=0.0,
                end_fraction=0.0,
                approx_timecode="",
                category_guess="general",
                search_hint=f["fact_summary"],
                search_queries=list(f.get("search_queries") or [])[:3],
                scope="general",
                origin="title_sweep",
            ))
    n_scene = sum(1 for a in anchors if a.scope == "scene")
    log.info("  title sweep round %d: %d facts (%d scene-anchored, %d general)",
             round_n, len(anchors), n_scene, len(anchors) - n_scene)
    return anchors
