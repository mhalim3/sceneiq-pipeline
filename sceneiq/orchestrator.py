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
from .pipeline.title_sweep import title_sweep
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


_TOPIC_DEDUP_SCHEMA = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "indices": {"type": "array", "items": {"type": "integer"}},
                    "keep_index": {"type": "integer"},
                    "story": {"type": "string"},
                },
                "required": ["indices", "keep_index", "story"],
            },
        },
    },
    "required": ["groups"],
}

_TOPIC_DEDUP_PROMPT = """You are deduplicating pause-screen fact cards for the film \
{film_title}. Some cards below tell the SAME underlying story in different wordings \
(e.g. four variations of "the cast cooked for real in a fully functional kitchen").

CARDS:
{card_list}

Group cards that tell substantially the same story — the same core fact or the same \
production narrative. Two cards are the same story even when details differ slightly. \
They are DIFFERENT stories when each has a distinct headline fact a viewer would \
experience as new (e.g. "Cooper based his character on three chefs" vs "a Michelin \
chef scored the film's realism 9/10" are different even though both involve realism).

Report ONLY groups of 2 or more cards. For each group choose keep_index — prefer \
cards marked strict=True, then the card with the most specific, distinct details. \
Unique cards must not appear in any group."""


def _topic_dedup(client: GeminiClient, cfg: config.PipelineConfig,
                 film_info: dict, records: list[CardRecord]) -> list[CardRecord]:
    if len(records) < 2:
        return records
    card_list = "\n".join(
        f"[{i}] (strict={bool(r.validation and r.validation.checks.get('would_pass_strict'))}) "
        f"SHORT: {r.card.short_version} | LONG: {r.card.long_description[:300]}"
        for i, r in enumerate(records)
    )
    try:
        data = client.structured(
            cfg.fast_model,
            _TOPIC_DEDUP_PROMPT.format(
                film_title=film_info.get("title", ""), card_list=card_list
            ),
            _TOPIC_DEDUP_SCHEMA,
            temperature=0.0,
        )
    except SchemaViolation:
        return records  # dedup is best-effort; never lose cards to a bad call
    drop: dict[int, str] = {}
    for g in data.get("groups", []):
        indices = [i for i in g.get("indices", []) if 0 <= i < len(records)]
        keep = g.get("keep_index")
        if len(indices) < 2 or keep not in indices:
            continue
        for i in indices:
            if i != keep and i not in drop:
                drop[i] = g.get("story", "same story")
    kept = []
    for i, r in enumerate(records):
        if i in drop:
            r.status = "rejected"
            if r.validation:
                r.validation.rejection_reasons.append(
                    f"dedup: same story as a kept card ({drop[i]})"
                )
            log.info("  ✗ topic-dedup: %s (%s)", r.card.short_version, drop[i])
        else:
            kept.append(r)
    return kept


def _schedule(emitted: list[CardRecord], duration_s: float,
              cfg: config.PipelineConfig) -> dict:
    """Assign exact display windows to every card.

    Scene cards: [scene start, scene end + pad], floored at their spoiler
    boundary. General cards: fill every timeline gap they're spoiler-eligible
    for; leftover generals get [spoiler floor, end]. Returns the coverage
    report engineers need: covered fraction + any uncovered gaps.
    """
    if duration_s <= 0:
        return {"fraction": 0.0, "uncovered": []}
    scene_records = [r for r in emitted if r.card.scope == "scene"]
    general_records = [r for r in emitted if r.card.scope == "general"]

    for r in scene_records:
        c = r.card
        start = max(c.runtime_fraction, c.spoiler_boundary_fraction) * duration_s
        end = (c.scene_end_fraction or 0.0) * duration_s
        if end <= start:
            end = start + 120.0
        end = min(end + cfg.scene_card_pad_s, duration_s)
        c.display_windows = [[round(start, 1), round(end, 1)]]

    # Merge covered intervals, find gaps.
    ivs = sorted(w for r in scene_records for w in r.card.display_windows)
    merged: list[list[float]] = []
    for a, b in ivs:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    gaps, cursor = [], 0.0
    for a, b in merged:
        if a > cursor:
            gaps.append([cursor, a])
        cursor = max(cursor, b)
    if cursor < duration_s:
        gaps.append([cursor, duration_s])

    uncovered = []
    for r in general_records:
        r.card.display_windows = []
    for gap in gaps:
        eligible = [r for r in general_records
                    if r.card.spoiler_boundary_fraction * duration_s <= gap[0] + 1.0]
        if eligible:
            # Least-loaded general card takes the gap (spread the filler).
            pick = min(eligible, key=lambda r: len(r.card.display_windows))
            pick.card.display_windows.append([round(gap[0], 1), round(gap[1], 1)])
        else:
            uncovered.append([round(gap[0], 1), round(gap[1], 1)])
    # Generals that filled nothing are still showable across their safe span.
    for r in general_records:
        if not r.card.display_windows:
            floor = r.card.spoiler_boundary_fraction * duration_s
            r.card.display_windows = [[round(floor, 1), round(duration_s, 1)]]

    uncovered_len = sum(b - a for a, b in uncovered)
    return {
        "fraction": round(1.0 - uncovered_len / duration_s, 4),
        "uncovered": uncovered,
    }


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
            # Title-level fact sweep (once, after film identity is known):
            # search-first facts, anchored to a Moments scene when supported,
            # otherwise GENERAL cards the player may show at any time.
            if p == 0 and cfg.use_title_sweep:
                log.info("Stage 1b: title-level fact sweep ...")
                sweep_anchors = [
                    a for a in title_sweep(client, film_info, cfg, moments=moments)
                    if not any(
                        SequenceMatcher(None, a.anchor_element.lower(), e.lower()).ratio() > 0.8
                        for e in explored
                    )
                ]
                explored.extend(a.anchor_element for a in sweep_anchors)
                anchors_proposed += len(sweep_anchors)
                futures += [
                    pool.submit(_process_anchor, client, film_info, a, cfg, cache)
                    for a in sweep_anchors
                ]
        for f in as_completed(futures):
            records.append(f.result())

    emitted = [r for r in records if r.status == "emitted"]
    emitted = _dedup(emitted)
    if cfg.use_topic_dedup:
        emitted = _topic_dedup(client, cfg, film_info, emitted)
    # Scene cards sort by position and respect max_cards; general cards are
    # kept in full (bounded by sweep_facts_max) — they're the coverage filler.
    scene_records = sorted(
        (r for r in emitted if r.card.scope == "scene"),
        key=lambda r: r.card.runtime_fraction,
    )[: cfg.max_cards]
    general_records = [r for r in emitted if r.card.scope == "general"]
    emitted = scene_records + general_records

    # Display schedule: exact windows for the player, 100% coverage target.
    duration_s = float(film_info.get("duration_sec") or (film_info.get("runtime_minutes") or 0) * 60)
    coverage = _schedule(emitted, duration_s, cfg)

    # Title sufficiency requirements (PRD, MVP, movies). Position rules apply
    # to scene-scoped cards; density counts everything; full-time coverage is
    # the new engineering requirement.
    runtime_min = film_info.get("runtime_minutes") or 0
    quartiles = {1: 0, 2: 0, 3: 0, 4: 0}
    for r in scene_records:
        quartiles[r.card.runtime_quartile] += 1
    categories = {r.card.fact_category for r in emitted if r.card.fact_category != "general"}
    density_target = math.ceil(runtime_min / cfg.minutes_per_card) if runtime_min else cfg.min_cards_to_enable
    rules = {
        "cards_per_10_min": len(emitted) >= density_target,
        "min_six_cards": len(emitted) >= cfg.min_cards_to_enable,
        "card_in_each_quartile": runtime_min <= cfg.quartile_min_runtime
        or all(v > 0 for v in quartiles.values()),
        "max_40pct_single_quartile": bool(scene_records)
        and max(quartiles.values()) / len(scene_records) <= cfg.max_quartile_share,
        "min_two_categories": len(categories) >= cfg.min_categories,
        "full_time_coverage": coverage["fraction"] >= 0.999,
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
            "scene_cards": len(scene_records),
            "general_cards": len(general_records),
            "density_target": density_target,
            "cards_per_runtime_quartile": quartiles,
            "distinct_categories": sorted(categories),
            "timeCoverage": coverage,
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
