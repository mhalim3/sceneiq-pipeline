"""Pipeline orchestrator: prompt in -> scene facts out.

Stage 1 runs once; stages 2-4 run per anchor in a thread pool (no barrier
between anchors — each candidate flows research -> assembly -> validation
independently). The finalizer dedups, enforces the title coverage policy,
and writes the emit + review payloads.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher

from . import config
from .gemini import GeminiClient, SchemaViolation
from .models import Anchor, CardRecord
from .pipeline.anchors import discover_anchors
from .pipeline.assemble import assemble_card
from .pipeline.research import research_anchor
from .pipeline.validate import validate_card

log = logging.getLogger("sceneiq")


def _process_anchor(
    client: GeminiClient, film_info: dict, anchor: Anchor, cfg: config.PipelineConfig
) -> CardRecord:
    record = CardRecord(card=None, anchor=anchor, evidence=None, validation=None)
    try:
        packet = research_anchor(client, film_info, anchor, cfg)
        record.evidence = packet
        card = assemble_card(client, film_info, packet, cfg)
        if card is None:
            record.status = "rejected"
            record.validation = None
            log.info("  ✗ abstained: %s", anchor.anchor_element)
            return record
        record.card = card
        result = validate_card(client, film_info, card, packet, cfg)
        record.validation = result
        record.status = "emitted" if result.passed else "rejected"
        mark = "✓" if result.passed else "✗"
        log.info("  %s %s — %s", mark, anchor.anchor_element,
                 "approved" if result.passed else "; ".join(result.rejection_reasons))
    except SchemaViolation as e:
        record.status = "error"
        log.warning("  ! schema violation on %s: %s", anchor.anchor_element, e)
    except Exception as e:  # one bad anchor must not sink the title run
        record.status = "error"
        log.warning("  ! error on %s: %s", anchor.anchor_element, e)
    return record


def _dedup(records: list[CardRecord]) -> list[CardRecord]:
    """Drop emitted cards whose headers are near-duplicates (keep first)."""
    kept: list[CardRecord] = []
    for r in records:
        dup = any(
            SequenceMatcher(
                None, r.card.fact_header.lower(), k.card.fact_header.lower()
            ).ratio() > 0.75
            for k in kept
        )
        if dup:
            r.status = "rejected"
            if r.validation:
                r.validation.rejection_reasons.append("dedup: near-duplicate of an emitted card")
        else:
            kept.append(r)
    return kept


def run(title_prompt: str, cfg: config.PipelineConfig | None = None, api_key: str | None = None) -> dict:
    cfg = cfg or config.PipelineConfig()
    client = GeminiClient(api_key=api_key)
    t0 = time.time()

    log.info("Stage 1/4: discovering scene anchors for %r ...", title_prompt)
    film_info, anchors = discover_anchors(client, title_prompt, cfg)
    log.info("  film: %s (%s), %s anchors proposed",
             film_info["title"], film_info["year"], len(anchors))

    log.info("Stage 2-4: research -> assembly -> validation (%d workers) ...", cfg.max_workers)
    records: list[CardRecord] = []
    with ThreadPoolExecutor(max_workers=cfg.max_workers) as pool:
        futures = [pool.submit(_process_anchor, client, film_info, a, cfg) for a in anchors]
        for f in as_completed(futures):
            records.append(f.result())

    emitted = [r for r in records if r.status == "emitted"]
    emitted = _dedup(emitted)
    emitted.sort(key=lambda r: r.card.runtime_fraction)
    emitted = emitted[: cfg.max_cards]

    # Title coverage policy (PRD): >= min cards and >= 1 per runtime third.
    thirds = {1: 0, 2: 0, 3: 0}
    for r in emitted:
        thirds[r.card.runtime_third] += 1
    enabled = len(emitted) >= cfg.min_cards_to_enable and (
        not cfg.require_card_per_third or all(v > 0 for v in thirds.values())
    )

    report = {
        "film": film_info,
        "sceneFacts": [r.card.to_contract_dict() for r in emitted],
        "titleEnablement": {
            "sceneiq_enabled": enabled,
            "approved_cards": len(emitted),
            "min_required": cfg.min_cards_to_enable,
            "cards_per_runtime_third": thirds,
            "policy": "≥{} approved cards with ≥1 per runtime third".format(cfg.min_cards_to_enable),
        },
        "runStats": {
            "anchors_proposed": len(anchors),
            "cards_emitted": len(emitted),
            "cards_rejected": sum(1 for r in records if r.status == "rejected"),
            "errors": sum(1 for r in records if r.status == "error"),
            "elapsed_seconds": round(time.time() - t0, 1),
            "models": {"deep": cfg.deep_model, "fast": cfg.fast_model},
        },
    }
    review = {
        "film": film_info,
        "candidates": [r.to_dict() for r in records],
    }
    return {"report": report, "review": review}
