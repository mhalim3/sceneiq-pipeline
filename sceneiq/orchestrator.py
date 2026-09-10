"""Pipeline orchestrator: prompt in -> scene facts out.

Stage 1 runs once; stages 2-4 run per anchor in a thread pool (no barrier
between anchors — each candidate flows research -> assembly -> validation
independently). The finalizer dedups, enforces the title coverage policy,
and writes the emit + review payloads.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher

from . import config
from .fetch import FetchCache
from .gemini import GeminiClient, SchemaViolation
from .models import Anchor, CardRecord
from .moments import TitleMoments, load_moments
from .pipeline.anchors import discover_anchors, discover_anchors_from_moments
from .pipeline.assemble import assemble_card
from .pipeline.curiosity import judge_curiosity
from .pipeline.leads import wikipedia_leads
from .pipeline.research import research_anchor
from .pipeline.validate import validate_card

log = logging.getLogger("sceneiq")


def _process_anchor(
    client: GeminiClient, film_info: dict, anchor: Anchor, cfg: config.PipelineConfig,
    cache: FetchCache,
) -> CardRecord:
    record = CardRecord(card=None, anchor=anchor, evidence=None, validation=None)
    try:
        packet = research_anchor(client, film_info, anchor, cfg, cache=cache)
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

        # Viewer-value triage: curiosity judge runs on cards that survived
        # the factual gates. Advisory by default — scores land in the review
        # record to prioritize human review; viewer value is scored manually
        # per the PRD rubric. cfg.curiosity_gating restores model gating.
        if result.passed and cfg.use_curiosity_judge:
            cur = judge_curiosity(client, card, cfg)
            result.curiosity = cur
            if cfg.curiosity_gating:
                if cur["verdict"] == "reject":
                    result.passed = False
                    result.rejection_reasons.append(f"curiosity: {cur['reasoning']}")
                elif cur["composite"] < cfg.curiosity_min_composite:
                    if cfg.evidence_mode == "strict":
                        result.passed = False
                        result.rejection_reasons.append(
                            f"curiosity: composite {cur['composite']} < {cfg.curiosity_min_composite}"
                        )
                    else:
                        result.flags.append(
                            f"curiosity: composite {cur['composite']} below bar (relaxed)"
                        )
            else:
                if cur["verdict"] != "approve":
                    result.flags.append(
                        f"curiosity (advisory): {cur['verdict']} — {cur['reasoning']}"
                    )
            if cur.get("suggested_edit"):
                result.flags.append(f"curiosity edit suggestion: {cur['suggested_edit']}")

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


_STOPWORDS = {
    "the", "a", "an", "is", "was", "were", "in", "of", "that", "this", "to",
    "for", "on", "at", "by", "with", "as", "its", "his", "her", "their",
}


def _semantic_key(r: CardRecord) -> str:
    """Scene-agnostic dedup key (scene-sense approach): normalized header +
    beat text with stopwords removed, hashed. Catches the same fact anchored
    to two different scenes."""
    text = (r.card.short_version + " " + " ".join(b.text for b in r.card.fact_beats)).lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", text) if t and t not in _STOPWORDS]
    return hashlib.sha256(" ".join(sorted(set(tokens))).encode()).hexdigest()[:16]


def _dedup(records: list[CardRecord]) -> list[CardRecord]:
    """Semantic-key collision first, fuzzy header similarity second."""
    kept: list[CardRecord] = []
    seen_keys: set[str] = set()
    for r in records:
        key = _semantic_key(r)
        dup = key in seen_keys or any(
            SequenceMatcher(
                None, r.card.short_version.lower(), k.card.short_version.lower()
            ).ratio() > 0.75
            for k in kept
        )
        if dup:
            r.status = "rejected"
            if r.validation:
                r.validation.rejection_reasons.append("dedup: near-duplicate of an emitted card")
        else:
            seen_keys.add(key)
            kept.append(r)
    return kept


def run(
    title_prompt: str,
    cfg: config.PipelineConfig | None = None,
    api_key: str | None = None,
    moments_path: str | None = None,
) -> dict:
    cfg = cfg or config.PipelineConfig()
    client = GeminiClient(api_key=api_key)
    t0 = time.time()

    moments: TitleMoments | None = None
    if moments_path:
        moments = load_moments(moments_path)
        log.info("Tubi Moments loaded: %s — %d scenes, %d min",
                 moments.title, len(moments.scenes), moments.runtime_minutes)

    leads = ""
    if cfg.use_wikipedia_leads:
        log.info("Stage 0/4: fetching Wikipedia discovery leads (C-tier, leads only) ...")
        leads = wikipedia_leads(moments.title if moments else title_prompt,
                                timeout_s=cfg.http_timeout_s)

    # Discovery for pass N+1 only needs pass N's anchor LIST (the avoid-list),
    # not its card results — so each pass's anchors are submitted to a shared
    # worker pool immediately and the next discovery runs while they process.
    records: list[CardRecord] = []
    explored: list[str] = []
    film_info: dict = {}
    anchors_proposed = 0
    cache = FetchCache(cfg.http_timeout_s)
    with ThreadPoolExecutor(max_workers=cfg.max_workers) as pool:
        futures = []
        for p in range(max(1, cfg.passes)):
            pass_label = f"pass {p + 1}/{cfg.passes}" if cfg.passes > 1 else ""
            log.info("Stage 1/4: discovering scene anchors for %r %s...", title_prompt, pass_label)
            if moments:
                fi, anchors = discover_anchors_from_moments(client, moments, cfg, explored=explored)
            else:
                fi, anchors = discover_anchors(client, title_prompt, cfg, leads=leads, explored=explored)
            film_info = film_info or fi
            # Drop near-duplicates of anchors already explored in earlier passes.
            anchors = [
                a for a in anchors
                if not any(
                    SequenceMatcher(None, a.anchor_element.lower(), e.lower()).ratio() > 0.8
                    for e in explored
                )
            ]
            explored.extend(a.anchor_element for a in anchors)
            anchors_proposed += len(anchors)
            log.info("  film: %s (%s), %s new anchors -> research/assembly/validation (%d workers)",
                     film_info["title"], film_info["year"], len(anchors), cfg.max_workers)
            futures += [
                pool.submit(_process_anchor, client, film_info, a, cfg, cache)
                for a in anchors
            ]
        for f in as_completed(futures):
            records.append(f.result())

    emitted = [r for r in records if r.status == "emitted"]
    emitted = _dedup(emitted)
    emitted.sort(key=lambda r: r.card.runtime_fraction)
    emitted = emitted[: cfg.max_cards]

    # Title sufficiency requirements (PRD, MVP, movies):
    #   1. >= 1 approved card per 10 minutes of runtime, rounded up
    #   2. >= 6 approved cards for a feature-length title
    #   3. >= 1 card in each runtime quartile (titles > 40 min)
    #   4. <= 40% of approved cards in any single quartile
    #   5. >= 2 distinct fact categories
    runtime_min = film_info.get("runtime_minutes") or 0
    quartiles = {1: 0, 2: 0, 3: 0, 4: 0}
    for r in emitted:
        quartiles[r.card.runtime_quartile] += 1
    categories = {r.card.fact_category for r in emitted}
    density_target = math.ceil(runtime_min / cfg.minutes_per_card) if runtime_min else cfg.min_cards_to_enable
    rules = {
        "cards_per_10_min": len(emitted) >= density_target,
        "min_six_cards": len(emitted) >= cfg.min_cards_to_enable,
        "card_in_each_quartile": runtime_min <= cfg.quartile_min_runtime
        or all(v > 0 for v in quartiles.values()),
        "max_40pct_single_quartile": bool(emitted)
        and max(quartiles.values()) / len(emitted) <= cfg.max_quartile_share,
        "min_two_categories": len(categories) >= cfg.min_categories,
    }
    enabled = all(rules.values())

    scene_facts = []
    for r in emitted:
        d = r.card.to_contract_dict()
        d["wouldPassStrict"] = bool(
            r.validation and r.validation.checks.get("would_pass_strict")
        )
        scene_facts.append(d)

    report = {
        "film": film_info,
        "sceneFacts": scene_facts,
        "titleEnablement": {
            "sceneiq_enabled": enabled,
            "approved_cards": len(emitted),
            "density_target": density_target,
            "cards_per_runtime_quartile": quartiles,
            "distinct_categories": sorted(categories),
            "rules": rules,
        },
        "runStats": {
            "evidence_mode": cfg.evidence_mode,
            "passes": cfg.passes,
            "anchors_proposed": anchors_proposed,
            "cards_emitted": len(emitted),
            "would_pass_strict": sum(
                1 for r in emitted
                if r.validation and r.validation.checks.get("would_pass_strict")
            ),
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
