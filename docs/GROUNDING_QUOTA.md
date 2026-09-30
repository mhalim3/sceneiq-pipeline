# Google Search Grounding — Quota & Budget

## The cap

SceneIQ's fact sourcing uses **Gemini with Google Search grounding**. On the
shared **`tubi-gemini-sandbox`** GCP project, grounded search is capped at
**5,000 grounded requests per day, project-wide** (shared across everyone using
the sandbox). It resets at **midnight PT**.

- **Ungrounded** calls (schema/structured output, dedup, validation, the
  consistency check) do **not** count against this cap and keep working when
  it's exhausted.
- A 429 on a grounded call means the shared daily pool is spent — not a
  transient blip. The client raises `GroundingQuotaExhausted` and fails fast
  (no retries), because retrying before the PT reset only wastes calls.

Check usage (read access on the project required):

- Quotas: console → IAM & Admin → Quotas, service
  `generativelanguage.googleapis.com`, search **`search_request`**.
- Traffic: Metrics Explorer → **API > Request count**, filter service to
  `generativelanguage.googleapis.com`, group by `response_code` to see
  200s vs 429s.

## Per-title grounding footprint

In title-level-only mode, grounded requests come from two stages:

| Stage | Grounded calls | Notes |
|-------|----------------|-------|
| Title sweep (`title_sweep.py`) | 4 per round × `max_sweep_rounds` | discovers candidate facts |
| Per-fact research (`research.py`) | `queries_per_anchor` × #facts | sources & verifies each fact |

**Right-sized defaults** (be a good sandbox citizen):

- `queries_per_anchor = 1` (was 3) — the sweep already grounded to find the
  fact; one targeted research query returns 4–8 candidate URLs to fetch.
- `max_sweep_rounds = 2` (was 3) — wide round 1 plus one top-up.

That puts a clean title at **~16–20 grounded requests** (was ~50–60), so a full
40-title pass is **~700–800**, comfortably under 5,000 with no reruns.

## Per-run budget guard

`GeminiClient` enforces a hard per-run cap so one run can never drain the shared
pool:

```
SCENEIQ_GROUNDING_BUDGET=800   # default; 0 disables
```

When reached, `grounded()` raises `GroundingBudgetExhausted` before making the
call. Each run reports `runStats.grounded_requests`.

## When SceneIQ needs more

Grounding is core to SceneIQ (sourced, verifiable facts can't come from the
scene catalog alone — that gives cast/genre/description, not "the armadillo cake
came from the writer's sister's wedding"). If SceneIQ needs grounding at
production volume, that's a **quota-increase request** the SceneIQ team files,
and at that scale it should run in **its own GCP project**, not the shared
sandbox.
