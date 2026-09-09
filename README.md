# SceneIQ Scene Fact Pipeline

Orchestration pipeline for the SceneIQ GenAI model layer (per the SceneIQ GenAI
Model PRD). You enter a movie prompt; the pipeline returns validated, sourced
**Scene Fact** cards — insider details about things physically visible or
audible on screen.

**Tubi Moments stand-in:** scene anchoring normally comes from Tubi Moments VLM
output. For now, Stage 1 uses Gemini grounded search to reconstruct the film's
scene structure. When Moments is wired in, only `pipeline/anchors.py` changes —
the rest of the pipeline consumes `Anchor` objects and doesn't care where they
came from.

## Setup

```bash
cd sceneiq-pipeline
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export GEMINI_API_KEY=your-key
```

## Usage

```bash
python -m sceneiq "Legally Blonde (2001)"
```

Options:

| Flag | Default | Meaning |
|---|---|---|
| `--max-anchors` | 12 | candidate anchors researched per title |
| `--max-cards` | 10 | cap on emitted cards |
| `--workers` | 4 | parallel anchor workers |
| `--strict-verbatim` | off | hard-reject when named entities aren't found verbatim in fetched source bodies (default: flag for review) |
| `--deep-model` / `--fast-model` | gemini-2.5-pro / flash | also settable via `SCENEIQ_DEEP_MODEL` / `SCENEIQ_FAST_MODEL` |
| `-o, --out-dir` | `data/outputs` | where JSON lands |

Outputs:

- **stdout** — the `sceneFacts` array (card contract JSON)
- `data/outputs/<title>.cards.json` — cards + title-enablement decision + run stats
- `data/outputs/<title>.review.json` — every candidate's full journey (anchor,
  evidence packet, sources with tiers/evidence classes, per-check validation
  results, rejection reasons) for the HITL review surface

## Pipeline architecture

```
prompt
  │
  ▼
[1] Anchor discovery        deep model + grounded search
     scene structure, 12 candidate anchors across runtime thirds,
     7-category taxonomy, hard-reject framing baked into the prompt
  │                              (per-anchor, parallel ─────────────┐)
  ▼
[2] Evidence retrieval      deep model + grounded search
     tiered source priority: primary/direct > reputable editorial;
     wikis are leads only. "NO QUALIFIED EVIDENCE FOUND" is a valid answer.
  │
  ▼
[3] Card assembly           fast model, schema mode
     PRD card contract: proactivePrompt, factCategory, factHeader,
     factBeats (3-5, each with sourceUrl), followUps (2-3).
     Abstention is a first-class output.
  │
  ▼
[4] Validation layer        code + fast model judge, temperature 0
     contract → URL provenance (redirect unwrap) → domain tier (A/B/C) →
     claim entailment per beat → scene anchoring → spoiler boundary →
     G-rated/partner safety → non-obviousness → taxonomy →
     verbatim entity check → emission policy
     (1 primary/direct source OR 2 independent reputable editorial sources;
      C-tier never supports; same-domain sources aren't independent)
  │
  ▼
[5] Finalizer               code
     dedup, runtime ordering, title coverage policy
     (≥6 approved cards, ≥1 per runtime third, else sceneiq_enabled: false)
```

Failing any hard gate rejects the card — no partial credit, matching the
Stage 1 "Clearance" model in the PRD. Every rejection carries a machine-
readable reason so failure modes can be aggregated for the source-yield study.

## Where PRD requirements live in code

| PRD requirement | Where |
|---|---|
| 7-category taxonomy | `config.FACT_CATEGORIES`, enforced in schemas + judge |
| Card contract | `models.SceneFactCard.to_contract_dict()` |
| Source tiers A/B/C | `config.*_TIER_DOMAINS`, `tiers.classify_domain` |
| Evidence classes (primary/editorial/discovery) | judge in `pipeline/validate.py` |
| Emission rules (1 primary or 2 independent editorial) | `validate.py` step 6 |
| Independence requirement | `tiers.independent` (registered-domain collapse) |
| Spoiler boundary (timecode-relative) | judge check 4, uses anchor runtime fraction |
| No AI-invented content | entailment per beat + abstention in assembly |
| G-rated / partner safety | judge check 5 |
| Title coverage policy (≥6 cards, per-third) | `orchestrator.run` finalizer |
| Deep/fast model split, schema mode, tenacity retries | `gemini.py` |
| Review surface JSON | `<title>.review.json` |

## Known gaps vs. the PRD (deliberate for this cut)

- **Scene anchoring is search-derived**, not VLM-verified — the anchor check is
  an LLM judgment until Tubi Moments is integrated.
- **Verbatim matching** is best-effort against fetched HTML; paywalls and video
  sources flag rather than verify (PRD's own ASR caveat). Use
  `--strict-verbatim` to hard-gate it.
- **Video citations** (`sourceTimestamp`/`sourceModality`) not yet in the contract.
- Cross-domain **syndication detection** relies on the judge; no dedicated
  anti-circular tracer yet.
- Easter Egg pipeline not included (stretch goal; slots in as a sibling of
  `pipeline/assemble.py` behind the same validator).
