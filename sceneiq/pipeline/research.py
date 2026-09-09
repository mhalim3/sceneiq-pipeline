"""Stage 2: Tiered evidence retrieval per anchor.

Grounded search per anchor. The prompt encodes the PRD's evidence taxonomy so
the search favors primary/scene-scoped sources (commentary, filmmaker
interviews, trade press) and treats wikis as leads, not support.
"""

from __future__ import annotations

from .. import config
from ..gemini import GeminiClient
from ..models import Anchor, EvidencePacket, SourceRef
from ..tiers import classify_domain, registered_domain

_RESEARCH_PROMPT = """You are the evidence-retrieval stage of SceneIQ. Research ONE scene \
anchor for the film {film_title} ({film_year}) and report only what sources actually attest.

SCENE (~{timecode}): {scene_description}
ANCHOR ELEMENT: {anchor_element}
WHAT AN INSIDER FACT WOULD LOOK LIKE: {search_hint}

Search for verifiable insider details about this specific element. Priority order:
1. PRIMARY/DIRECT: director, cast, costume/production designer, or studio statements \
about it (interviews, DVD/Blu-ray commentary transcripts, official production notes, \
scene-breakdown videos like NYT Anatomy of a Scene / Vanity Fair Notes on a Scene / \
GQ Breakdown), domain authorities (American Cinematographer for technique, ASCAP/BMI \
for music, film permit records for locations).
2. REPUTABLE EDITORIAL: reported coverage in established publications (Variety, THR, \
Vulture, The Ringer, Vogue/WWD for costume, etc.).
3. Wikipedia/IMDb/fan wikis are LEADS ONLY — if they mention something, find who \
originally said it.

Report each distinct verifiable claim you find, with:
- the exact claim,
- who originally attests it (person + role, or publication),
- the source (publication/format),
- a short verbatim quote or close paraphrase of the supporting passage.

Only report claims a source actually makes. If you find nothing solid for this \
anchor, say exactly: NO QUALIFIED EVIDENCE FOUND. Do not invent or embellish."""


def research_anchor(
    client: GeminiClient, film_info: dict, anchor: Anchor, cfg: config.PipelineConfig
) -> EvidencePacket:
    grounded = client.grounded(
        cfg.deep_model,
        _RESEARCH_PROMPT.format(
            film_title=film_info.get("title", ""),
            film_year=film_info.get("year", ""),
            timecode=anchor.approx_timecode,
            scene_description=anchor.scene_description,
            anchor_element=anchor.anchor_element,
            search_hint=anchor.search_hint,
        ),
        temperature=cfg.assembly_temperature,
    )
    sources = []
    for s in grounded.sources:
        url = s["url"]
        sources.append(
            SourceRef(
                url=url,
                title=s.get("title", ""),
                domain=registered_domain(url),
                tier=classify_domain(url),
            )
        )
    return EvidencePacket(anchor=anchor, findings=grounded.text, sources=sources)
