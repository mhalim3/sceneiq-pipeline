"""Thin wrapper over the google-genai SDK.

Two call shapes, matching the PRD stack:
  - grounded():   generation with Google Search grounding enabled, returns
                  narrative text + the grounding sources.
  - structured(): schema-mode JSON output (search tools and response_schema
                  can't be combined in one call, so structuring is a second,
                  fast-model pass).

Tenacity retries transient errors; schema violations reject after
MAX_RETRIES per the PRD.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field

import httpx
from google import genai
from google.genai import types, errors
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
)

from . import config


class SchemaViolation(Exception):
    """Model failed to produce schema-conformant JSON after retries."""


class GroundingBudgetExhausted(Exception):
    """The per-run grounded-request budget was reached. This is a self-imposed
    cap so the pipeline can never silently drain the shared project-wide
    Google Search grounding quota (tubi-gemini-sandbox: 5,000 grounded
    requests/day, shared across the whole project)."""


class GroundingQuotaExhausted(Exception):
    """The project-wide daily grounding quota returned 429. It resets at
    midnight PT; retrying within this run cannot help, so we fail fast."""


@dataclass
class GroundedResult:
    text: str
    sources: list = field(default_factory=list)   # [{"url":..., "title":...}]


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, errors.APIError):
        return exc.code in (429, 500, 502, 503, 504)
    # Raw transport failures (DNS blips, read timeouts) surface as httpx
    # exceptions, not APIError — they're exactly what retries are for.
    return isinstance(exc, (ConnectionError, TimeoutError, httpx.TransportError))


# Transient HTTP failures (429-not-grounding, 500/502/503/504, transport blips)
# get their own, more patient retry budget — independent of MAX_RETRIES, which
# governs schema-violation rejections. gemini-2.5-flash returns bursts of 503
# "model is overloaded" during demand spikes; a shallow 3-try/20s-cap policy
# gives up mid-spike and drops otherwise-good facts. Six attempts with backoff
# to ~45s rides out the typical spike without wedging a worker for too long.
_TRANSIENT_RETRY_ATTEMPTS = int(os.environ.get("SCENEIQ_TRANSIENT_RETRIES", "6"))

_retry = retry(
    retry=retry_if_exception(_is_transient),
    stop=stop_after_attempt(_TRANSIENT_RETRY_ATTEMPTS),
    wait=wait_exponential(multiplier=1, min=2, max=45),
    reraise=True,
)


class GeminiClient:
    def __init__(self, api_key: str | None = None):
        key = api_key or os.environ.get(config.API_KEY_ENV)
        if not key:
            raise RuntimeError(
                f"{config.API_KEY_ENV} is not set. Export it before running:\n"
                f"  export {config.API_KEY_ENV}=your-key"
            )
        # Hard request deadline (ms): the SDK's default is no timeout, and a
        # single hung call stalls the whole worker pool.
        self._client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(timeout=180_000),
        )
        # Self-imposed cap on grounded (Google Search) requests per run. The
        # grounding quota is a shared, project-wide daily pool (5,000/day on
        # tubi-gemini-sandbox); this counter ensures one pipeline run can never
        # drain it. 0 disables the cap. Thread-safe (research/sweep fan out).
        self._grounded_budget = int(os.environ.get("SCENEIQ_GROUNDING_BUDGET", "800"))
        self._grounded_calls = 0
        self._grounded_lock = threading.Lock()

    @property
    def grounded_calls(self) -> int:
        return self._grounded_calls

    def grounded(self, model: str, prompt: str, temperature: float = 0.3) -> GroundedResult:
        # Reserve one unit of budget BEFORE the (retried) call, so retries
        # don't double-count and concurrent workers can't overshoot the cap.
        with self._grounded_lock:
            if self._grounded_budget and self._grounded_calls >= self._grounded_budget:
                raise GroundingBudgetExhausted(
                    f"per-run grounding budget of {self._grounded_budget} reached "
                    f"(SCENEIQ_GROUNDING_BUDGET). Shared project cap is 5,000/day."
                )
            self._grounded_calls += 1
        return self._grounded_call(model, prompt, temperature)

    @_retry
    def _grounded_call(self, model: str, prompt: str, temperature: float) -> GroundedResult:
        try:
            resp = self._client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    temperature=temperature,
                ),
            )
        except errors.APIError as e:
            # A 429 on a grounded call is the shared project-wide daily
            # grounding cap (5,000/day), not a transient blip — it resets at
            # midnight PT, so retrying within this run only wastes calls.
            # Fail fast with a clear, non-retryable error.
            if e.code == 429:
                raise GroundingQuotaExhausted(
                    "429 on Google Search grounding: the shared project daily "
                    "cap (5,000 grounded requests/day on tubi-gemini-sandbox) is "
                    "exhausted. It resets at midnight PT. Ungrounded calls still "
                    "work. See docs/GROUNDING_QUOTA.md."
                ) from e
            raise
        sources = []
        try:
            gm = resp.candidates[0].grounding_metadata
            for chunk in gm.grounding_chunks or []:
                web = getattr(chunk, "web", None)
                if web and web.uri:
                    sources.append({"url": web.uri, "title": web.title or ""})
        except (AttributeError, IndexError, TypeError):
            pass
        return GroundedResult(text=resp.text or "", sources=sources)

    def structured(self, model: str, prompt: str, schema: dict, temperature: float = 0.2):
        """Schema-mode JSON generation. Retries schema violations up to
        MAX_RETRIES, then raises SchemaViolation (final rejection per PRD)."""
        last_err: Exception | None = None
        for _ in range(config.MAX_RETRIES):
            try:
                raw = self._structured_call(model, prompt, schema, temperature)
                return json.loads(raw)
            except json.JSONDecodeError as e:
                last_err = e
                continue
        raise SchemaViolation(f"schema violation after {config.MAX_RETRIES} attempts: {last_err}")

    @_retry
    def _structured_call(self, model, prompt, schema, temperature) -> str:
        resp = self._client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=temperature,
            ),
        )
        return resp.text or ""
