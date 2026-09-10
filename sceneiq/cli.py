"""CLI: python -m sceneiq "Legally Blonde (2001)" """

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

from . import config, orchestrator


def _load_dotenv() -> None:
    """Load KEY=value lines from a .env next to the project (or cwd).

    Real env vars win over .env values.
    """
    for candidate in (Path(__file__).resolve().parent.parent / ".env", Path.cwd() / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "title"


def _report_main(argv: list[str]) -> int:
    from . import report as report_mod

    p = argparse.ArgumentParser(
        prog="sceneiq report",
        description="Aggregate review.json files into source-yield study metrics.",
    )
    p.add_argument("-o", "--out-dir", default="data/outputs")
    args = p.parse_args(argv)
    json.dump(report_mod.aggregate(args.out_dir), sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "report":
        return _report_main(argv[1:])

    p = argparse.ArgumentParser(
        prog="sceneiq",
        description="SceneIQ Scene Fact pipeline: movie prompt in, sourced scene facts out.",
    )
    p.add_argument("prompt", help='Movie prompt, e.g. "Legally Blonde (2001)" — or "report" to aggregate outputs')
    p.add_argument("--max-anchors", type=int, default=12, help="candidate anchors per pass")
    p.add_argument("--max-cards", type=int, default=10, help="cap on emitted cards")
    p.add_argument("--passes", type=int, default=1,
                   help="discovery passes; anchors dedupe across passes, approved cards merge")
    p.add_argument("--evidence", choices=["relaxed", "strict"], default="relaxed",
                   help="relaxed: 1 editorial source suffices, weak beats drop; "
                        "strict: PRD-exact gates (safety/spoiler identical in both)")
    p.add_argument("--workers", type=int, default=8, help="parallel anchor workers")
    p.add_argument("--moments", metavar="PATH",
                   help="Tubi Moments JSON for this title — real scene data replaces "
                        "model-reconstructed scene structure and timecodes")
    p.add_argument("--content-id", metavar="CONTENT_ID",
                   help="fetch Moments scene rows from Databricks by Tubi content_id (see sceneiq/databricks_moments.py "
                        "for required env vars); implies --moments on the fetched file")
    p.add_argument("--title-lookup", action="store_true",
                   help="resolve the prompt to a Tubi content_id via the Databricks content "
                        "table, then fetch its Moments rows (full chain: title -> content_id -> scenes)")
    p.add_argument("--strict-verbatim", action="store_true",
                   help="hard-reject cards whose named entities aren't verbatim in fetched source bodies")
    p.add_argument("--deep-model", default=config.DEEP_MODEL)
    p.add_argument("--fast-model", default=config.FAST_MODEL)
    p.add_argument("-o", "--out-dir", default="data/outputs", help="output directory")
    p.add_argument("-q", "--quiet", action="store_true")
    args = p.parse_args(argv)

    _load_dotenv()
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )
    for noisy in ("httpx", "google_genai", "google_genai.models"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    cfg = config.PipelineConfig(
        max_anchors=args.max_anchors,
        max_cards=args.max_cards,
        passes=args.passes,
        evidence_mode=args.evidence,
        max_workers=args.workers,
        strict_verbatim=args.strict_verbatim,
        deep_model=args.deep_model,
        fast_model=args.fast_model,
    )

    moments_path = args.moments
    try:
        content_id = args.content_id
        if args.title_lookup and not content_id:
            from .databricks_moments import resolve_content_id
            # Strip a trailing "(year)" for the name match.
            bare_title = re.sub(r"\s*\(\d{4}\)\s*$", "", args.prompt)
            matches = resolve_content_id(bare_title)
            movies = [m for m in matches if str(m.get("content_type", "")).upper() == "MOVIE"]
            candidates = movies or matches
            if not candidates:
                print(f"error: no content_id found for {bare_title!r}", file=sys.stderr)
                return 2
            if len(candidates) > 1:
                print(f"multiple matches for {bare_title!r} — rerun with --content-id:", file=sys.stderr)
                for m in candidates:
                    print(f"  {m.get('content_id')}: {m.get('content_name')} ({m.get('content_type')})",
                          file=sys.stderr)
                return 2
            content_id = str(candidates[0]["content_id"])
            print(f"resolved {bare_title!r} -> content_id {content_id}", file=sys.stderr)
        if content_id:
            from .databricks_moments import fetch_moments
            moments_path = str(fetch_moments(content_id))
        result = orchestrator.run(args.prompt, cfg, moments_path=moments_path)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    report, review = result["report"], result["review"]
    slug = _slug(report["film"].get("title") or args.prompt)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cards_path = out_dir / f"{slug}.cards.json"
    review_path = out_dir / f"{slug}.review.json"
    cards_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    review_path.write_text(json.dumps(review, indent=2, ensure_ascii=False))

    # Human-readable summary on stderr, machine-readable cards on stdout.
    en = report["titleEnablement"]
    stats = report["runStats"]
    print(
        f"\n{report['film']['title']} ({report['film']['year']}): "
        f"{en['approved_cards']} approved / {stats['anchors_proposed']} anchors "
        f"({stats['cards_rejected']} rejected, {stats['errors']} errors) "
        f"in {stats['elapsed_seconds']}s "
        f"[{stats['evidence_mode']} mode, {stats['passes']} pass(es); "
        f"{stats['would_pass_strict']}/{en['approved_cards']} would pass strict]",
        file=sys.stderr,
    )
    failed_rules = [k for k, v in en["rules"].items() if not v]
    print(
        f"SceneIQ enablement: {'YES' if en['sceneiq_enabled'] else 'NO'} "
        f"(quartiles: {en['cards_per_runtime_quartile']}, "
        f"categories: {en['distinct_categories']})"
        + (f"\n  failed rules: {', '.join(failed_rules)}" if failed_rules else "")
        + f"\nwrote {cards_path} and {review_path}\n",
        file=sys.stderr,
    )
    json.dump(report["sceneFacts"], sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
