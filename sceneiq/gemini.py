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


_retry = retry(
    retry=retry_if_exception(_is_transient),
    stop=stop_after_attempt(config.MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=20),
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

    @_retry
    def grounded(self, model: str, prompt: str, temperature: float = 0.3) -> GroundedResult:
        resp = self._client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=temperature,
            ),
        )
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
