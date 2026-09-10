"""Source-yield study aggregation (PRD "MVP source-yield experiment").

Reads every <title>.review.json under the output directory and reports, per
title and overall:
  - qualified candidates and approved cards per hour of runtime
  - approval rate by fact category
  - approval rate by source-evidence class (primary vs editorial share)
  - rejection reasons bucketed by failure mode
  - distribution of approved cards across runtime quartiles

Slices are reported per title, never blended away — the PRD forbids letting
well-documented titles mask long-tail coverage.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path


def _reason_bucket(reason: str) -> str:
    return reason.split(":", 1)[0].strip()


def _candidate_category(cand: dict) -> str:
    card = cand.get("card") or {}
    return card.get("factCategory") or cand.get("anchor", {}).get("category_guess", "unknown")


def _quartile(cand: dict) -> int:
    card = cand.get("card") or {}
    anchor = card.get("sceneAnchor") or {}
    if "runtimeQuartile" in anchor:
        return anchor["runtimeQuartile"]
    frac = cand.get("anchor", {}).get("runtime_fraction", 0.0)
    return min(4, int(frac * 4) + 1)


def _evidence_class(cand: dict) -> str:
    """Best evidence class among the candidate's sources."""
    classes = {s.get("evidence_class") for s in cand.get("sources", [])}
    if "primary_direct" in classes:
        return "primary_direct"
    if "reputable_editorial" in classes:
        return "reputable_editorial"
    if not cand.get("sources"):
        return "no_sources"
    return "unclassified"


def _third(cand: dict) -> int:
    card = cand.get("card") or {}
    anchor = card.get("sceneAnchor") or {}
    if "runtimeThird" in anchor:
        return anchor["runtimeThird"]
    frac = cand.get("anchor", {}).get("runtime_fraction", 0.0)
    return min(3, int(frac * 3) + 1)


def _title_stats(review: dict) -> dict:
    film = review.get("film", {})
    runtime_min = film.get("runtime_minutes") or 0
    hours = runtime_min / 60 if runtime_min else None
    candidates = review.get("candidates", [])
    emitted = [c for c in candidates if c.get("status") == "emitted"]

    by_category = defaultdict(lambda: {"candidates": 0, "approved": 0})
    by_evidence = defaultdict(lambda: {"candidates": 0, "approved": 0})
    reasons = Counter()
    evidence_share = Counter()
    quartiles = Counter()
    thirds = Counter()
    # Viewer value: HUMAN scores are the metric (PRD rubric, filled in the
    # review file); model curiosity composites are advisory triage only.
    human_vv: dict = defaultdict(list)
    advisory_curiosity: dict = defaultdict(list)

    for cand in candidates:
        cat = _candidate_category(cand)
        ec = _evidence_class(cand)
        by_category[cat]["candidates"] += 1
        by_evidence[ec]["candidates"] += 1
        hr = (cand.get("humanReview") or {}).get("scores") or {}
        if hr.get("viewer_value") is not None:
            human_vv[cat].append(hr["viewer_value"])
        if cand.get("status") == "emitted":
            by_category[cat]["approved"] += 1
            by_evidence[ec]["approved"] += 1
            quartiles[_quartile(cand)] += 1
            thirds[_third(cand)] += 1
            evidence_share[ec] += 1
            cur = (cand.get("validation") or {}).get("curiosity") or {}
            if cur.get("composite") is not None:
                advisory_curiosity[cat].append(cur["composite"])
        else:
            validation = cand.get("validation") or {}
            rs = validation.get("rejection_reasons") or []
            if rs:
                for r in rs:
                    reasons[_reason_bucket(r)] += 1
            else:
                reasons["abstained_or_error"] += 1

    def _rate(d):
        return {
            k: {**v, "approval_rate": round(v["approved"] / v["candidates"], 3) if v["candidates"] else 0.0}
            for k, v in d.items()
        }

    return {
        "title": f"{film.get('title', '?')} ({film.get('year', '?')})",
        "runtime_minutes": runtime_min,
        "candidates": len(candidates),
        "approved": len(emitted),
        "approval_rate": round(len(emitted) / len(candidates), 3) if candidates else 0.0,
        "candidates_per_hour": round(len(candidates) / hours, 2) if hours else None,
        "approved_per_hour": round(len(emitted) / hours, 2) if hours else None,
        "approval_by_category": _rate(by_category),
        "approval_by_evidence_class": _rate(by_evidence),
        "approved_evidence_share": dict(evidence_share),
        "approved_by_quartile": {q: quartiles.get(q, 0) for q in (1, 2, 3, 4)},
        "approved_by_third": {t: thirds.get(t, 0) for t in (1, 2, 3)},
        "human_viewer_value_by_category": {
            cat: {"scored": len(v), "mean": round(sum(v) / len(v), 2)}
            for cat, v in human_vv.items()
        },
        "human_viewer_value_pending": sum(
            1 for c in candidates
            if ((c.get("humanReview") or {}).get("scores") or {}).get("viewer_value") is None
            and c.get("status") == "emitted"
        ),
        "model_advisory_curiosity_by_category": {
            cat: {"cards": len(v), "mean_composite": round(sum(v) / len(v), 3)}
            for cat, v in advisory_curiosity.items()
        },
        "rejection_reasons": dict(reasons.most_common()),
    }


def aggregate(out_dir: str | Path) -> dict:
    out_dir = Path(out_dir)
    titles = []
    for path in sorted(out_dir.glob("*.review.json")):
        try:
            titles.append(_title_stats(json.loads(path.read_text())))
        except (json.JSONDecodeError, KeyError) as e:
            titles.append({"title": path.name, "error": str(e)})

    ok = [t for t in titles if "error" not in t]
    overall_reasons = Counter()
    overall_categories = defaultdict(lambda: {"candidates": 0, "approved": 0})
    for t in ok:
        overall_reasons.update(t["rejection_reasons"])
        for cat, n in t["approval_by_category"].items():
            overall_categories[cat]["candidates"] += n["candidates"]
            overall_categories[cat]["approved"] += n["approved"]

    total_cand = sum(t["candidates"] for t in ok)
    total_appr = sum(t["approved"] for t in ok)
    return {
        "titles": titles,
        "overall": {
            "titles": len(ok),
            "candidates": total_cand,
            "approved": total_appr,
            "approval_rate": round(total_appr / total_cand, 3) if total_cand else 0.0,
            "approval_by_category": dict(overall_categories),
            "rejection_reasons": dict(overall_reasons.most_common()),
        },
    }
