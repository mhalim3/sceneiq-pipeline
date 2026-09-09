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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="sceneiq",
        description="SceneIQ Scene Fact pipeline: movie prompt in, sourced scene facts out.",
    )
    p.add_argument("prompt", help='Movie prompt, e.g. "Legally Blonde (2001)"')
    p.add_argument("--max-anchors", type=int, default=12, help="candidate anchors to research")
    p.add_argument("--max-cards", type=int, default=10, help="cap on emitted cards")
    p.add_argument("--workers", type=int, default=4, help="parallel anchor workers")
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
        max_workers=args.workers,
        strict_verbatim=args.strict_verbatim,
        deep_model=args.deep_model,
        fast_model=args.fast_model,
    )

    try:
        result = orchestrator.run(args.prompt, cfg)
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
        f"in {stats['elapsed_seconds']}s",
        file=sys.stderr,
    )
    print(
        f"SceneIQ enablement: {'YES' if en['sceneiq_enabled'] else 'NO'} "
        f"(thirds: {en['cards_per_runtime_third']})\n"
        f"wrote {cards_path} and {review_path}\n",
        file=sys.stderr,
    )
    json.dump(report["sceneFacts"], sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
