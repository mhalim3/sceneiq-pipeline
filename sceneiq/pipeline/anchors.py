"""Stage 1: Scene anchor discovery.

In production this reads Tubi Moments VLM output. For now the deep model with
grounded search reconstructs the film's scene structure and proposes concrete
visible/audible anchors, spread across runtime thirds (the PRD calls out the
opening-act bias of sampling only early scenes — so distribution is an
explicit instruction here, not an afterthought).
"""

from __future__ import annotations

from .. import config
from ..gemini import GeminiClient
from ..models import Anchor

_DISCOVERY_PROMPT = """You are the anchor-discovery stage of SceneIQ, Tubi's pause-screen \
contextual intelligence pipeline.

Film request from operator: "{title_prompt}"

First, identify the exact film (title, year). Then, using search, reconstruct its \
scene structure and propose {max_anchors} candidate SCENE ANCHORS for "Scene Fact" cards.

A scene anchor is a concrete element a paused viewer can SEE or HEAR at a specific \
moment: a location, garment, prop, set, song, person on screen, action, or technique.

Rules:
- Anchors must be physically observable in the frame (or audible) at that moment.
- Be SPECIFIC. "Sophie de Rakoff's pink-everything decision" — not "costume design". \
"The bend-and-snap origin at a Los Angeles bar" — not "the bend and snap". Prefer \
specific named-person stories, quantified production facts, or surprising creative \
decisions. Fewer, deeper anchors beat more, shallower ones.
- Spread anchors across the WHOLE runtime: roughly a third in the first third of the \
film, a third in the middle, a third in the final act. Do not cluster in the opening act.
- Cover diverse categories where the film supports it — do not cluster in one.
- Prefer anchors where insider knowledge likely exists (real filming locations, \
licensed songs, notable costumes/props, filming techniques, historical grounding, \
actors visible in the scene).
- Categories (choose closest): actor, music, location, set_design, filming, \
historical, costume/prop.
- EXCLUDE: casting drama, actors who almost got roles, deleted scenes, on-set feuds, \
career-arc trivia, anything not tied to what is on screen.

For each anchor give: the scene (what's on screen), the specific anchor element, an \
approximate timecode and runtime fraction (0.0-1.0), the closest category, a search \
hint describing what an insider fact would look like, and 2-4 search_queries a \
search engine could actually run — not "tell me about production". Include the film \
title in at least one query per anchor.

Also state the film's approximate runtime in minutes.
{avoid_block}{leads_block}"""

_AVOID_BLOCK = """
ALREADY-EXPLORED ANCHORS (from earlier passes). Do NOT propose these or close \
variants — propose anchors about DIFFERENT scenes, elements, and categories:

{explored}
"""

_LEADS_BLOCK = """
DISCOVERY LEADS (Wikipedia — C-tier, lead-generation ONLY). These excerpts may \
suggest anchors worth proposing, but nothing here counts as evidence; every claim \
must be independently verified against qualified sources later in the pipeline:

{leads}"""

_ANCHOR_SCHEMA = {
    "type": "object",
    "properties": {
        "film_title": {"type": "string"},
        "film_year": {"type": "integer"},
        "runtime_minutes": {"type": "integer"},
        "anchors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scene_description": {"type": "string"},
                    "anchor_element": {"type": "string"},
                    "runtime_fraction": {"type": "number"},
                    "approx_timecode": {"type": "string"},
                    "category_guess": {"type": "string", "enum": config.FACT_CATEGORIES},
                    "search_hint": {"type": "string"},
                    "search_queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 4,
                    },
                },
                "required": [
                    "scene_description",
                    "anchor_element",
                    "runtime_fraction",
                    "approx_timecode",
                    "category_guess",
                    "search_hint",
                    "search_queries",
                ],
            },
        },
    },
    "required": ["film_title", "film_year", "runtime_minutes", "anchors"],
}

_STRUCTURE_PROMPT = """Convert this anchor-discovery narrative into the JSON schema. \
Keep runtime fractions between 0.0 and 1.0. Drop any anchor that is not a physically \
observable element.

NARRATIVE:
{narrative}"""


_MOMENTS_PROMPT = """You are the anchor-discovery stage of SceneIQ, Tubi's pause-screen \
contextual intelligence pipeline.

Film: {film_title} (runtime {runtime_min} min)

Below is REAL scene-by-scene VLM data for this title (sampled across the whole \
runtime). Propose {max_anchors} candidate SCENE ANCHORS for "Scene Fact" cards, \
each attached to one of these scenes by its scene index.

A scene anchor is a concrete element a paused viewer can SEE or HEAR in that \
scene: a location, garment, prop, set element, song, person on screen, action, \
or technique. Every anchor element MUST appear in the chosen scene's data \
(summary, setting, objects, actions, songs, cast, or dialogue).

Rules:
- Be SPECIFIC. "Sophie de Rakoff's pink-everything decision" — not "costume design". \
Prefer specific named-person stories, quantified production facts, or surprising \
creative decisions.
- Spread anchors across the WHOLE runtime and across diverse categories.
- Categories (choose closest): actor, music, location, set_design, filming, \
historical, costume/prop.
- EXCLUDE: casting drama, actors who almost got roles, deleted scenes, on-set \
feuds, career-arc trivia, anything not tied to what is on screen.

For each anchor give: the scene_index it attaches to, the specific anchor element \
(as it appears in that scene's data), the closest category, a search hint \
describing what an insider fact would look like, and 2-4 search_queries a search \
engine could actually run. Include the film title in at least one query per anchor.
{avoid_block}
SCENES:
{scene_blob}"""

_MOMENTS_ANCHOR_SCHEMA = {
    "type": "object",
    "properties": {
        "film_year": {"type": "integer"},
        "anchors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scene_index": {"type": "integer"},
                    "anchor_element": {"type": "string"},
                    "category_guess": {"type": "string", "enum": config.FACT_CATEGORIES},
                    "search_hint": {"type": "string"},
                    "search_queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 4,
                    },
                },
                "required": [
                    "scene_index",
                    "anchor_element",
                    "category_guess",
                    "search_hint",
                    "search_queries",
                ],
            },
        },
    },
    "required": ["film_year", "anchors"],
}


def discover_anchors_from_moments(
    client: GeminiClient,
    moments,
    cfg: config.PipelineConfig,
    explored: list[str] | None = None,
) -> tuple[dict, list[Anchor]]:
    """Anchor discovery over real Tubi Moments scene data. Timecodes and
    runtime fractions are assigned in CODE from the VLM record — the model
    only chooses which scene an anchor belongs to."""
    scenes = moments.sampled_scenes(max_scenes=40)
    scene_blob = "\n\n".join(s.as_context() for s in scenes)
    avoid = ""
    if explored:
        avoid = _AVOID_BLOCK.format(explored="\n".join(f"- {e}" for e in explored))
    data = client.structured(
        cfg.deep_model,
        _MOMENTS_PROMPT.format(
            film_title=moments.title,
            runtime_min=moments.runtime_minutes,
            max_anchors=cfg.max_anchors,
            avoid_block=avoid,
            scene_blob=scene_blob,
        ),
        _MOMENTS_ANCHOR_SCHEMA,
        temperature=cfg.discovery_temperature,
    )
    film_info = {
        "title": moments.title,
        "year": data.get("film_year"),
        "runtime_minutes": moments.runtime_minutes,
        "duration_sec": moments.duration_sec,
        "input_prompt": moments.title,
        "anchor_source": "tubi_moments",
    }
    anchors = []
    for a in data.get("anchors", []):
        scene = moments.scene(int(a["scene_index"]))
        if scene is None:
            continue  # model invented a scene index — discard
        anchors.append(
            Anchor(
                scene_description=scene.as_context(),
                anchor_element=a["anchor_element"],
                runtime_fraction=scene.runtime_fraction,
                end_fraction=scene.end_fraction,
                approx_timecode=scene.start_time,
                category_guess=a["category_guess"],
                search_hint=a["search_hint"],
                search_queries=list(a.get("search_queries") or [])[:4],
            )
        )
    return film_info, anchors[: cfg.max_anchors]


def discover_anchors(
    client: GeminiClient,
    title_prompt: str,
    cfg: config.PipelineConfig,
    leads: str = "",
    explored: list[str] | None = None,
) -> tuple[dict, list[Anchor]]:
    """Returns (film_info, anchors). `explored` lists anchor elements from
    earlier passes that this pass must avoid."""
    avoid = ""
    if explored:
        avoid = _AVOID_BLOCK.format(explored="\n".join(f"- {e}" for e in explored))
    grounded = client.grounded(
        cfg.deep_model,
        _DISCOVERY_PROMPT.format(
            title_prompt=title_prompt,
            max_anchors=cfg.max_anchors,
            avoid_block=avoid,
            leads_block=_LEADS_BLOCK.format(leads=leads) if leads else "",
        ),
        temperature=cfg.discovery_temperature,
    )
    data = client.structured(
        cfg.fast_model,
        _STRUCTURE_PROMPT.format(narrative=grounded.text),
        _ANCHOR_SCHEMA,
        temperature=0.1,
    )
    film_info = {
        "title": data.get("film_title", ""),
        "year": data.get("film_year"),
        "runtime_minutes": data.get("runtime_minutes"),
        "input_prompt": title_prompt,
    }
    anchors = [
        Anchor(
            scene_description=a["scene_description"],
            anchor_element=a["anchor_element"],
            runtime_fraction=max(0.0, min(1.0, float(a["runtime_fraction"]))),
            approx_timecode=a["approx_timecode"],
            category_guess=a["category_guess"],
            search_hint=a["search_hint"],
            search_queries=list(a.get("search_queries") or [])[:4],
        )
        for a in data.get("anchors", [])
    ][: cfg.max_anchors]
    return film_info, anchors
