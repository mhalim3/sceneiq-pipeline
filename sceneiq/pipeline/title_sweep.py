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

from .. import config
from ..gemini import GeminiClient
from ..models import Anchor

log = logging.getLogger("sceneiq")

_SWEEP_QUERIES = [
    "{title} {year} film production behind the scenes facts",
    "{title} {year} movie myths inaccuracies what it got wrong",
    "{title} {year} box office records reception trivia cast preparation",
]

_SWEEP_PROMPT = """Run a Google search for: {query}

Report the most notable, well-documented facts about the film {title} ({year}) \
that the results attest — production stories, records, corrections of popular \
beliefs, real-world details. Include facts about the film as a whole (box office, \
reception records, myths the film created) as well as scene-tied details. \
Do NOT invent any URL — only describe what the search returned. Skip plot summary \
and casting gossip."""

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
- Facts must be about THIS film specifically. NEVER propose: plot summary or \
character story beats, casting stories (who turned down or almost got a role, who \
requested a casting), or generic industry practice — downstream validation rejects \
all of these unconditionally, so proposing them wastes a slot.
- Include both scene-tied facts AND general title facts. ELIGIBLE general classes: \
box office/reception records, title changes and production history, myths or false \
beliefs the film created or corrected (highest value — flag first), real-world \
impact (laws, trends, institutions the film affected), and production-wide details \
attested by the filmmakers.
- For each: a one-line fact_summary, the closest category ("general" when it isn't \
tied to any on-screen element), 2-3 runnable search_queries (include the film title \
in at least one), and scene_hint — what would be on screen when this fact is most \
relevant, or "any" for general facts.

NOTES:
{notes}"""

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
) -> list[Anchor]:
    title = film_info.get("title", "")
    year = film_info.get("year", "")

    notes = []
    for q in _SWEEP_QUERIES:
        grounded = client.grounded(
            cfg.research_model,
            _SWEEP_PROMPT.format(query=q.format(title=title, year=year),
                                 title=title, year=year),
            temperature=0.2,
        )
        notes.append(grounded.text)

    data = client.structured(
        cfg.fast_model,
        _STRUCTURE_PROMPT.format(title=title, year=year,
                                 max_facts=cfg.sweep_facts_max,
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
    log.info("  title sweep: %d facts (%d scene-anchored, %d general)",
             len(anchors), n_scene, len(anchors) - n_scene)
    return anchors
