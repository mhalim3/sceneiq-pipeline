"""Stage 4b: Curiosity judge — rates the card from a VIEWER's point of view.

Adopted from the scene-sense prototype. The judge sees ONLY what a viewer
would see: hook, header, beats, follow-ups, and the scene they paused on.
No source URLs, no validation state — so good sourcing can't halo a boring
card. This implements the PRD's Stage 2 value selection.

Gating: verdict "reject" kills the card in both evidence modes (the PRD's
value minimum wins even under sufficiency pressure). A composite below
cfg.curiosity_min_composite kills in strict and flags in relaxed.
"""

from __future__ import annotations

from .. import config
from ..gemini import GeminiClient
from ..models import SceneFactCard

_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "interestingness": {"type": "integer", "minimum": 1, "maximum": 5},
        "obviousness": {"type": "integer", "minimum": 1, "maximum": 5},
        "mainstream_appeal": {"type": "integer", "minimum": 1, "maximum": 5},
        "follow_up_pull": {"type": "integer", "minimum": 1, "maximum": 5},
        "verdict": {"type": "string", "enum": ["approve", "approve_with_edit", "reject"]},
        "reasoning": {"type": "string"},
        "suggested_edit": {"type": "string"},
    },
    "required": ["interestingness", "obviousness", "mainstream_appeal",
                 "follow_up_pull", "verdict", "reasoning"],
}

_JUDGE_PROMPT = """You are a TV viewer. A movie is playing; you've paused, and a small \
card has appeared on screen. Rate it AS A VIEWER — not as a fact-checker. You see no \
sources, no scores. Only the card.

THE SCENE YOU PAUSED ON: {scene_description}

THE CARD:
Hook: {prompt}
Header: {header}
Beats:
{beats}
Follow-ups: {follow_ups}

RATE 1-5:
- interestingness: does this make me lean in? Novel + specific = 5. Generic = 1.
- obviousness: is this ALREADY OBVIOUS from what I just watched? Card tells me the dog \
is a Chihuahua while a Chihuahua is on screen = 5 (bad). Needs outside info = 1 (good).
- mainstream_appeal: will a general audience care? A famous person, place, or a \
surprising real-world fact = 5. Inside-baseball only a specialist cares about = 1. \
(An unfamous person can still be a 4-5 if the STORY is universally interesting.)
- follow_up_pull: do I want to tap MORE? Real-world hook (is it still open? where is \
it? what else did they make?) = 5. Dead-end fact = 1.

VERDICTS:
- approve: strong on all 4.
- approve_with_edit: mostly good, one specific fixable issue — name the fix in \
suggested_edit.
- reject: obvious from the scene, generic praise, or no curiosity hook. Be firm.

EDITORIAL RULES:
- If the card's payoff is literally visible on screen or in dialogue that just \
played: REJECT.
- If the beats amount to "the design was deliberate/carefully considered" with no \
concrete surprising detail: REJECT.
- If the card invites a clear, specific follow-up: lean APPROVE."""


def judge_curiosity(
    client: GeminiClient, card: SceneFactCard, cfg: config.PipelineConfig
) -> dict:
    beats = "\n".join(f"  - {b.text}" for b in card.fact_beats)
    resp = client.structured(
        cfg.fast_model,
        _JUDGE_PROMPT.format(
            scene_description=card.scene_description,
            prompt=card.proactive_prompt,
            header=card.fact_header,
            beats=beats,
            follow_ups=" / ".join(card.follow_ups),
        ),
        _JUDGE_SCHEMA,
        temperature=0.2,
    )
    composite = round(
        (int(resp["interestingness"]) / 5) * 0.30
        + ((6 - int(resp["obviousness"])) / 5) * 0.25
        + (int(resp["mainstream_appeal"]) / 5) * 0.25
        + (int(resp["follow_up_pull"]) / 5) * 0.20,
        3,
    )
    return {
        "interestingness": int(resp["interestingness"]),
        "obviousness": int(resp["obviousness"]),
        "mainstream_appeal": int(resp["mainstream_appeal"]),
        "follow_up_pull": int(resp["follow_up_pull"]),
        "composite": composite,
        "verdict": resp["verdict"],
        "reasoning": resp.get("reasoning", ""),
        "suggested_edit": resp.get("suggested_edit", ""),
    }
