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
from .pipeline.anchors import (discover_anchors, discover_anchors_from_moments,
                               identify_film)
from .pipeline.assemble import assemble_card, repair_card
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
        _t = time.time()
        packet = research_anchor(client, film_info, anchor, cfg, cache=cache)
        record.timings["research"] = time.time() - _t
        record.evidence = packet
        _t = time.time()
        card = assemble_card(client, film_info, packet, cfg)
        record.timings["assemble"] = time.time() - _t
        if card is None:
            record.status = "rejected"
            record.validation = None
            log.info("  ✗ abstained: %s", anchor.anchor_element)
            return record
        record.card = card
        _t = time.time()
        result = validate_card(client, film_info, card, packet, cfg)
        record.timings["validate"] = time.time() - _t
        # One repair attempt when the ONLY problems are fixable wording
        # (summary claims beyond the beats, maturity wording). The rewritten
        # card goes back through the full validation, so every gate still applies.
        if (cfg.repair_rejected and not result.passed and result.rejection_reasons
                and all(r.startswith(("summary_entailment", "maturity"))
                        for r in result.rejection_reasons)):
            problems = list(result.rejection_reasons)
            try:
                if repair_card(client, film_info, card, problems, cfg):
                    repaired = validate_card(client, film_info, card, packet, cfg)
                    if repaired.passed:
                        repaired.flags.append(
                            "repaired: rewritten after review — " + "; ".join(problems))
                    result = repaired
                    record.validation = result
            except Exception as e:  # repair is best-effort; keep the original verdict
                log.warning("  repair failed on %s: %s", anchor.anchor_element, e)
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

Group cards that tell substantially the same story. Compare the CLAIM of each card first: \
two cards are duplicates when their primary claims would lead a viewer to tell the same \
underlying story, even if the wording, sources, or secondary details differ. \
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
        f"CLAIM: {r.card.primary_claim or r.card.short_version} | "
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
    except Exception:
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


_CONSISTENCY_SCHEMA = {
    "type": "object",
    "properties": {
        "contradictions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "indices": {"type": "array", "items": {"type": "integer"}},
                    "keep_index": {"type": "integer"},
                    "issue": {"type": "string"},
                },
                "required": ["indices", "keep_index", "issue"],
            },
        },
    },
    "required": ["contradictions"],
}

_CONSISTENCY_PROMPT = """These pause-screen fact cards for the film {film_title} will all \
be shown to the same viewer during one film. Find cards that CONTRADICT each other — \
mutually incompatible factual claims (different numbers for the same quantity, \
mutually exclusive statements about the same event).

NOT contradictions: cards covering different aspects of the same topic, one card \
being more specific than another, or complementary details. A card that CORRECTS a \
reported figure (e.g. "records show the budget was actually X") contradicts a card \
that flatly states the reported figure as fact.

CARDS:
{card_list}

For each contradiction group, choose keep_index — the card whose claim rests on the \
stronger evidence (prefer domain authorities and official records, then strict=True, \
then the more specific claim). Report the issue in one sentence. Return an empty \
list when the set is consistent."""


def _consistency_check(client: GeminiClient, cfg: config.PipelineConfig,
                       film_info: dict, records: list[CardRecord]) -> list[CardRecord]:
    """Reject cards whose claims contradict a better-sourced card in the set.

    Two individually-sourced cards can still disagree (e.g. one repeats the
    reported budget, another corrects it from official records) — nothing
    upstream compares cards to each other. Best-effort: a failed call never
    drops cards."""
    if len(records) < 2:
        return records
    card_list = "\n".join(
        f"[{i}] (strict={bool(r.validation and r.validation.checks.get('would_pass_strict'))}, "
        f"sources: {', '.join(sorted({b.source_url.split('/')[2] for b in r.card.fact_beats}))}) "
        f"SHORT: {r.card.short_version} | LONG: {r.card.long_description[:300]}"
        for i, r in enumerate(records)
    )
    try:
        data = client.structured(
            cfg.fast_model,
            _CONSISTENCY_PROMPT.format(
                film_title=film_info.get("title", ""), card_list=card_list
            ),
            _CONSISTENCY_SCHEMA,
            temperature=0.0,
        )
    except Exception:
        return records
    drop: dict[int, str] = {}
    keep_flag: dict[int, str] = {}
    for g in data.get("contradictions", []):
        indices = [i for i in g.get("indices", []) if 0 <= i < len(records)]
        keep = g.get("keep_index")
        if len(indices) < 2 or keep not in indices:
            continue
        issue = g.get("issue", "contradiction")
        keep_flag[keep] = issue
        for i in indices:
            if i != keep and i not in drop:
                drop[i] = issue
    kept = []
    for i, r in enumerate(records):
        if i in drop:
            r.status = "rejected"
            if r.validation:
                r.validation.rejection_reasons.append(f"consistency: {drop[i]}")
            log.info("  ✗ consistency: %s (%s)", r.card.short_version, drop[i])
        else:
            if i in keep_flag and r.validation:
                r.validation.flags.append(
                    f"consistency: kept over a contradicting card — verify ({keep_flag[i]})"
                )
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

    # Title-level-only mode: no scene cards — partition the runtime into
    # equal slots, one per general card, spread across the duration.
    # Floor-sorted so spoiler-gated cards land later in the film.
    if not scene_records and general_records:
        gens = sorted(general_records,
                      key=lambda r: r.card.spoiler_boundary_fraction)
        n = len(gens)
        seg = duration_s / n
        for i, r in enumerate(gens):
            start = i * seg
            end = (i + 1) * seg if i < n - 1 else duration_s
            floor = r.card.spoiler_boundary_fraction * duration_s
            start = max(start, floor)
            if start >= end:
                start, end = max(floor, duration_s - seg), duration_s
            r.card.display_windows = [[round(start, 1), round(end, 1)]]

    # Merge covered intervals (scene windows + any pre-assigned general
    # slots), find gaps.
    ivs = sorted(w for r in emitted for w in r.card.display_windows)
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
    unassigned = [r for r in general_records if not r.card.display_windows]
    for gap in gaps:
        pool = unassigned or general_records
        eligible = [r for r in pool
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
        title_only = cfg.title_level_only or not moments
        scene_passes = 0 if title_only else max(1, cfg.passes)
        if title_only:
            # Title-level-only: every card must be showable at any point in
            # the film. The sweep is the sole card source.
            log.info("Stage 1: title-level-only mode")
            film_info = identify_film(client, moments.title if moments else title_prompt, cfg)
            if moments:
                # Moments still supplies authoritative runtime and rating.
                film_info.update({
                    "runtime_minutes": moments.runtime_minutes,
                    "duration_sec": moments.duration_sec,
                    "content_rating": getattr(moments, "content_rating", ""),
                })
            log.info("  film: %s (%s), %s min",
                     film_info["title"], film_info["year"], film_info["runtime_minutes"])
        for p in range(scene_passes):
            pass_label = f"pass {p + 1}/{cfg.passes}" if cfg.passes > 1 else ""
            log.info("Stage 1/4: discovering scene anchors for %r %s...", title_prompt, pass_label)
            fi, anchors = discover_anchors_from_moments(client, moments, cfg, explored=explored)
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
        # Title-level fact sweep (both modes; the PRIMARY card source in
        # title-only mode): search-first facts, anchored to a Moments scene
        # when supported, otherwise GENERAL cards shown at any time.
        if cfg.use_title_sweep:
            log.info("Stage 1b: title-level fact sweep ...")
            try:
                # Pass Moments whenever we have it — even in title-level-only
                # mode. The sweep anchors scene-specific facts to a real scene
                # window (scope="scene", real timecode) and leaves agnostic
                # facts as GENERAL cards, so title-level coverage is preserved
                # while specific facts gain timing.
                swept = title_sweep(client, film_info, cfg, moments=moments)
            except Exception as e:  # sweep is additive — never kill the run
                log.warning("  title sweep failed (%s); continuing without it", e)
                swept = []
            sweep_anchors = [
                a for a in swept
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

        # Adaptive sweep: title-level facts are the primary card class, so
        # when yield is short of the fact target, spend more sweep rounds
        # (fresh query angles + avoid-list) rather than more scene passes.
        # Stops when the target is met, a round approves nothing new, or
        # max_sweep_rounds is exhausted.
        runtime_min_early = film_info.get("runtime_minutes") or 0
        target_early = max(cfg.density_min, min(
            cfg.density_max,
            math.ceil(runtime_min_early / cfg.minutes_per_card) if runtime_min_early else cfg.density_min,
        ))
        if cfg.annotation_mode:
            # Annotation wants MORE facts than the 12-15 density band.
            target_early = max(target_early, cfg.annotation_target_cards)

        def _current_approved() -> list:
            # Fully deduped view (string + semantic + topic-cluster), else
            # pre-dedup inflation stops adaptive rounds that are still
            # needed. Mutation is durable: a dupe stays rejected.
            em = [r for r in records if r.status == "emitted"]
            em = _dedup(em)
            if cfg.use_topic_dedup and len(em) > 1:
                em = _topic_dedup(client, cfg, film_info, em)
            return em

        approved_so_far = len(_current_approved())
        sweep_round = 1
        max_rounds = cfg.annotation_sweep_rounds if cfg.annotation_mode else cfg.max_sweep_rounds
        while (cfg.use_title_sweep
               and approved_so_far < target_early
               and sweep_round < max_rounds):
            sweep_round += 1
            log.info("Stage 1b: adaptive sweep round %d (%d/%d approved) ...",
                     sweep_round, approved_so_far, target_early)
            try:
                swept = title_sweep(client, film_info, cfg,
                                    moments=moments,
                                    avoid=explored, round_n=sweep_round)
            except Exception as e:
                log.warning("  sweep round %d failed (%s); stopping", sweep_round, e)
                break
            new_anchors = [
                a for a in swept
                if not any(
                    SequenceMatcher(None, a.anchor_element.lower(), e.lower()).ratio() > 0.8
                    for e in explored
                )
            ]
            if not new_anchors:
                log.info("  sweep round %d: dry (no new facts)", sweep_round)
                break
            explored.extend(a.anchor_element for a in new_anchors)
            anchors_proposed += len(new_anchors)
            round_futs = [
                pool.submit(_process_anchor, client, film_info, a, cfg, cache)
                for a in new_anchors
            ]
            for f in as_completed(round_futs):
                records.append(f.result())
            now_approved = len(_current_approved())
            if now_approved <= approved_so_far:
                log.info("  sweep round %d: nothing new approved — stopping", sweep_round)
                break
            approved_so_far = now_approved

    emitted = [r for r in records if r.status == "emitted"]
    emitted = _dedup(emitted)
    if cfg.use_topic_dedup and len(emitted) > 1:
        emitted = _topic_dedup(client, cfg, film_info, emitted)
    if cfg.use_consistency_check and len(emitted) > 1:
        emitted = _consistency_check(client, cfg, film_info, emitted)
    # Scene cards sort by position and respect max_cards; general cards are
    # kept in full (bounded by sweep_facts_max) — they're the coverage filler.
    scene_records = sorted(
        (r for r in emitted if r.card.scope == "scene"),
        key=lambda r: r.card.runtime_fraction,
    )[: max(cfg.max_cards, cfg.annotation_target_cards if cfg.annotation_mode else 0)]
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
    # Phase 3 fact sufficiency: ~1 per 10 min, clamped to 12-15 per title.
    raw_target = math.ceil(runtime_min / cfg.minutes_per_card) if runtime_min else cfg.density_min
    density_target = max(cfg.density_min, min(cfg.density_max, raw_target))
    rules = {
        "fact_target_12_15": len(emitted) >= density_target,
        "min_six_cards": len(emitted) >= cfg.min_cards_to_enable,
        # Quartile distribution applies to scene cards only; in title-level-
        # only mode (no Moments coverage) the rules are N/A and pass — the
        # full-time-coverage rule guarantees the timeline is served.
        "card_in_each_quartile": not scene_records
        or runtime_min <= cfg.quartile_min_runtime
        or all(v > 0 for v in quartiles.values()),
        "max_40pct_single_quartile": not scene_records
        or max(quartiles.values()) / len(scene_records) <= cfg.max_quartile_share,
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
        # Annotation triage: surface the confidence label + why, and the
        # non-fatal flags, so reviewers see which cards may be weaker.
        d["confidence"] = r.validation.confidence if r.validation else ""
        d["confidenceReasons"] = r.validation.confidence_reasons if r.validation else []
        d["reviewFlags"] = r.validation.flags if r.validation else []
        scene_facts.append(d)

    confidence_counts = {"high": 0, "medium": 0, "low": 0}
    for d in scene_facts:
        confidence_counts[d.get("confidence") or "low"] = (
            confidence_counts.get(d.get("confidence") or "low", 0) + 1
        )
    dependency_counts = {
        "agnostic": sum(1 for d in scene_facts if d.get("sceneDependency") == "agnostic"),
        "specific": sum(1 for d in scene_facts if d.get("sceneDependency") == "specific"),
    }

    report = {
        "film": film_info,
        "sceneFacts": scene_facts,
        "titleEnablement": {
            "sceneiq_enabled": enabled,
            "approved_cards": len(emitted),
            # Annotation floor: did this title reach the 10-15 target? When
            # below, coverage was genuinely limited (sparse/obscure title).
            "annotation_floor": cfg.annotation_min_cards if cfg.annotation_mode else None,
            "below_annotation_floor": bool(
                cfg.annotation_mode and len(emitted) < cfg.annotation_min_cards
            ),
            "confidence_breakdown": confidence_counts,
            "scene_dependency_breakdown": dependency_counts,
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
            "grounded_requests": client.grounded_calls,
            "models": {"deep": cfg.deep_model, "fast": cfg.fast_model},
        },
    }
    log.info("grounded requests this title: %d (budget %s)",
             client.grounded_calls, cfg.extra.get("grounding_budget", "800"))
    review = {
        "film": film_info,
        "candidates": [r.to_dict() for r in records],
    }
    return {"report": report, "review": review}
