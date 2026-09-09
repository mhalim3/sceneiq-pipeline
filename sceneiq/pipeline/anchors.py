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
- Spread anchors across the WHOLE runtime: roughly a third in the first third of the \
film, a third in the middle, a third in the final act. Do not cluster in the opening act.
- Prefer anchors where insider knowledge likely exists (real filming locations, \
licensed songs, notable costumes/props, filming techniques, historical grounding, \
actors visible in the scene).
- Categories (choose closest): actor, music, location, set_design, filming, \
historical, costume/prop.
- EXCLUDE: casting drama, actors who almost got roles, deleted scenes, on-set feuds, \
career-arc trivia, anything not tied to what is on screen.

For each anchor give: the scene (what's on screen), the specific anchor element, an \
approximate timecode and runtime fraction (0.0-1.0), the closest category, and a \
search hint describing what an insider fact about it would look like.

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
                },
                "required": [
                    "scene_description",
                    "anchor_element",
                    "runtime_fraction",
                    "approx_timecode",
                    "category_guess",
                    "search_hint",
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
        )
        for a in data.get("anchors", [])
    ][: cfg.max_anchors]
    return film_info, anchors
