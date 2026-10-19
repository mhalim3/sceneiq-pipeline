"""Claude (Anthropic Messages API) behind the same two call shapes as the Gemini client.

Any model name that starts with "claude" is routed here by GeminiClient:
  - structured(): a forced tool call whose input schema is the JSON schema.
  - grounded():   Anthropic's server-side web search tool, results converted to the same
                  GroundedResult (text, sources, per-sentence citations) the pipeline reads.
Needs ANTHROPIC_API_KEY. Plain httpx, no extra SDK.
"""

from __future__ import annotations

import os
import threading

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

_URL = "https://api.anthropic.com/v1/messages"
_VERSION = "2023-06-01"
_MAX_TOKENS = int(os.environ.get("SCENEIQ_CLAUDE_MAX_TOKENS", "16000"))
_SEARCH_USES = int(os.environ.get("SCENEIQ_CLAUDE_SEARCHES", "6"))
_SLOTS = threading.BoundedSemaphore(int(os.environ.get("SCENEIQ_CLAUDE_CONCURRENCY", "8")))


def is_claude(model: str) -> bool:
    return (model or "").lower().startswith("claude")


def _transient(e: BaseException) -> bool:
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code in (429, 500, 502, 503, 504, 529)
    return isinstance(e, (ConnectionError, TimeoutError, httpx.TransportError))


_retry = retry(retry=retry_if_exception(_transient), stop=stop_after_attempt(6),
               wait=wait_exponential(multiplier=1, min=2, max=45), reraise=True)


def _key() -> str:
    k = os.environ.get("ANTHROPIC_API_KEY")
    if not k:
        raise RuntimeError("ANTHROPIC_API_KEY is not set (needed for claude-* models).")
    return k


@_retry
def _post(body: dict) -> dict:
    with _SLOTS:
        r = httpx.post(_URL, json=body, timeout=300,
                       headers={"x-api-key": _key(), "anthropic-version": _VERSION,
                                "content-type": "application/json"})
    if r.status_code >= 400:
        r.raise_for_status()
    return r.json()


def structured(model: str, prompt: str, schema: dict, temperature: float) -> dict:
    body = {"model": model, "max_tokens": _MAX_TOKENS, "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [{"name": "answer", "description": "Return the answer in this exact structure.",
                       "input_schema": schema}],
            "tool_choice": {"type": "tool", "name": "answer"}}
    d = _post(body)
    for b in d.get("content", []):
        if b.get("type") == "tool_use":
            return b.get("input") or {}
    return {}


def grounded(model: str, prompt: str, temperature: float):
    from .gemini import GroundedResult
    msgs = [{"role": "user", "content": prompt}]
    text, sources, chunk_urls, supports = "", [], [], []
    seen: dict = {}

    def chunk(url, title):
        if url not in seen:
            seen[url] = len(chunk_urls)
            chunk_urls.append({"url": url, "title": title or ""})
            sources.append({"url": url, "title": title or ""})
        return seen[url]

    for _ in range(4):                                   # the server tool may pause a long turn
        d = _post({"model": model, "max_tokens": _MAX_TOKENS, "temperature": temperature, "messages": msgs,
                   "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": _SEARCH_USES}]})
        for b in d.get("content", []):
            t = b.get("type")
            if t == "web_search_tool_result":
                for res in b.get("content") or []:
                    if isinstance(res, dict) and res.get("url"):
                        chunk(res["url"], res.get("title"))
            elif t == "text":
                text += b.get("text", "")
                idxs = [chunk(c["url"], c.get("title")) for c in b.get("citations") or [] if c.get("url")]
                if idxs:
                    supports.append({"end": len(text.encode("utf-8")), "chunks": sorted(set(idxs))})
        if d.get("stop_reason") != "pause_turn":
            break
        msgs = msgs + [{"role": "assistant", "content": d["content"]}]
    return GroundedResult(text=text, sources=sources, supports=supports, chunk_urls=chunk_urls)
