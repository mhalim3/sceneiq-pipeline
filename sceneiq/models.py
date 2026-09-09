"""Data contracts for the pipeline.

`SceneFactCard` is the PRD card contract. Everything else is internal
plumbing that ends up in the review record (`<title>.review.json`).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class SourceRef:
    url: str                          # final URL after redirect resolution
    title: str = ""
    domain: str = ""                  # registered domain
    tier: str = "unknown"             # A | B | C | unknown
    evidence_class: str = "unknown"   # primary_direct | reputable_editorial | discovery
    modality: str = "text"            # text | video (PRD: sourceModality)
    resolved: bool = False            # URL fetched with a 2xx/3xx
    body_kind: str = ""               # html | transcript | "" (nothing fetched)
    verbatim_hits: list = field(default_factory=list)   # entities found in body
    verbatim_misses: list = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        d.pop("_body", None)
        return d


@dataclass
class Anchor:
    """A concrete visible/audible element in a scene worth researching."""

    scene_description: str            # what's on screen at this moment
    anchor_element: str               # the specific thing the fact points at
    runtime_fraction: float           # 0.0-1.0 position in the film
    approx_timecode: str              # "~00:22" style, best effort
    category_guess: str               # one of the 7 categories
    search_hint: str                  # what to look for in sources

    @property
    def runtime_third(self) -> int:
        if self.runtime_fraction < 1 / 3:
            return 1
        if self.runtime_fraction < 2 / 3:
            return 2
        return 3

    def to_dict(self):
        d = asdict(self)
        d["runtime_third"] = self.runtime_third
        return d


@dataclass
class EvidencePacket:
    anchor: Anchor
    findings: str                     # grounded-search narrative w/ citations
    sources: list = field(default_factory=list)   # list[SourceRef]

    def to_dict(self):
        return {
            "anchor": self.anchor.to_dict(),
            "findings": self.findings,
            "sources": [s.to_dict() for s in self.sources],
        }


@dataclass
class FactBeat:
    text: str
    source_url: str
    source_index: int = -1            # index into card.sources after validation
    supporting_passage: str = ""      # PRD: source passage per material claim

    def to_dict(self):
        return asdict(self)


@dataclass
class SceneFactCard:
    """PRD card contract for sceneFact."""

    proactive_prompt: str
    fact_category: str
    fact_header: str
    fact_beats: list                  # list[FactBeat], 3-5
    follow_ups: list                  # list[str], 2-3
    # anchoring metadata (would come from Tubi Moments in production)
    scene_description: str = ""
    anchor_element: str = ""
    runtime_fraction: float = 0.0
    approx_timecode: str = ""
    # PRD labeling guidance: earliest runtime fraction at which this card is
    # spoiler-safe to display. <= runtime_fraction when the card is safe at
    # its own anchor (set by the validation judge).
    spoiler_boundary_fraction: float = 0.0
    sources: list = field(default_factory=list)   # list[SourceRef]

    @property
    def runtime_third(self) -> int:
        if self.runtime_fraction < 1 / 3:
            return 1
        if self.runtime_fraction < 2 / 3:
            return 2
        return 3

    @property
    def runtime_quartile(self) -> int:
        if self.runtime_fraction < 0.25:
            return 1
        if self.runtime_fraction < 0.5:
            return 2
        if self.runtime_fraction < 0.75:
            return 3
        return 4

    def _source_by_url(self, url: str):
        for s in self.sources:
            if s.url == url:
                return s
        return None

    def to_contract_dict(self):
        """The viewer-facing card contract exactly as the PRD specifies."""
        beats = []
        for b in self.fact_beats:
            src = self._source_by_url(b.source_url)
            beats.append({
                "text": b.text,
                "sourceUrl": b.source_url,
                "sourceModality": src.modality if src else "text",
                "sourceTimestamp": None,   # video in-point; needs transcript alignment
            })
        return {
            "proactivePrompt": self.proactive_prompt,
            "factCategory": self.fact_category,
            "factHeader": self.fact_header,
            "factBeats": beats,
            "followUps": list(self.follow_ups),
            "sceneAnchor": {
                "sceneDescription": self.scene_description,
                "anchorElement": self.anchor_element,
                "approxTimecode": self.approx_timecode,
                "runtimeFraction": round(self.runtime_fraction, 3),
                "runtimeThird": self.runtime_third,
                "runtimeQuartile": self.runtime_quartile,
            },
            "spoilerBoundary": {
                "earliestSafeFraction": round(self.spoiler_boundary_fraction, 3),
            },
        }


@dataclass
class ValidationResult:
    passed: bool
    checks: dict = field(default_factory=dict)     # check name -> pass/fail/flag
    # PRD human-evaluation rubric, automated: dimension -> 0 | 1 | 2
    rubric: dict = field(default_factory=dict)
    rejection_reasons: list = field(default_factory=list)
    flags: list = field(default_factory=list)      # non-fatal warnings
    judge_notes: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class CardRecord:
    """One candidate's full journey through the pipeline, for the review surface."""

    card: Optional[SceneFactCard]
    anchor: Anchor
    evidence: Optional[EvidencePacket]
    validation: Optional[ValidationResult]
    status: str = "pending"           # emitted | rejected | error

    def to_dict(self):
        return {
            "status": self.status,
            "card": self.card.to_contract_dict() if self.card else None,
            "anchor": self.anchor.to_dict(),
            "sources": [s.to_dict() for s in (self.card.sources if self.card else [])],
            # PRD: "The review record must identify the evidence type and
            # source passage for each material claim."
            "claim_evidence": [
                {
                    "beat": b.text,
                    "sourceUrl": b.source_url,
                    "supporting_passage": b.supporting_passage,
                }
                for b in (self.card.fact_beats if self.card else [])
            ],
            "validation": self.validation.to_dict() if self.validation else None,
        }
