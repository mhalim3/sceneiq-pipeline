"""Find YouTube videos with Gemini's Google Search, then read them properly.

Gemini's search tool can surface YouTube videos but answers from snippets and cites Google redirect
links. This uses it only to DISCOVER videos: each redirect is followed to the real watch URL, the
channel is rated (verified A/B only), and Gemini then watches each video and extracts facts with
the speaker, a quote and a timestamp, which are checked against YouTube's captions when available.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

import httpx
from google.genai import types

from .. import channel_tiers, config
from ..fetch import best_substring_ratio, fetch_youtube_transcript, normalize

log = logging.getLogger("sceneiq")
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
_ID = re.compile(r"(?:youtube\.com/(?:watch\?v=|shorts/|embed/)|youtu\.be/)([\w-]{11})")

_DISCOVERY = [
    "Find YouTube videos in which the cast, director, writer or crew of the film {film} talk about making it: "
    "interviews, panel and press-junket clips, talk-show appearances, behind-the-scenes and making-of pieces. "
    "Prefer videos from official or publication channels.",
    "Find YouTube videos of {film} cast or crew reunions, anniversary interviews, commentary or Q&A sessions, "
    "and talk-show stories about filming it (Conan, Fallon, Kimmel, Rich Eisen, Pete Holmes, podcasts on video).",
]


def find_video_ids(client, cfg, film: str, year, extra_queries: list[str] | None = None) -> list[str]:
    """Video ids from Gemini's search results (redirect links followed to the real URL)."""
    prompts = [p.format(film=f"{film} ({year})") for p in _DISCOVERY] + list(extra_queries or [])
    redirects: list[str] = []
    for p in prompts:
        try:
            res = client.grounded(cfg.research_model, p + " Search YouTube.", temperature=0.2)
        except Exception as e:
            log.warning("  grounded video search failed: %s", e)
            continue
        redirects += [s["url"] for s in res.sources if s.get("url")]

    def _follow(u):
        m = _ID.search(u)
        if m:
            return m.group(1)
        try:
            r = httpx.get(u, headers=_UA, follow_redirects=True, timeout=20)
            m = _ID.search(str(r.url))
            return m.group(1) if m else None
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = [i for i in pool.map(_follow, list(dict.fromkeys(redirects))) if i]
    out = list(dict.fromkeys(ids))
    log.info("  grounded search found %d YouTube video(s)", len(out))
    return out


def video_facts_info(video_id: str) -> dict:
    """{title, channel, verified, seconds} from the watch page."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    info = channel_tiers.channel_info(url)
    secs = 0
    try:
        h = httpx.get(url, headers=_UA, cookies={"CONSENT": "YES+1"}, follow_redirects=True, timeout=20).text
        m = re.search(r'"lengthSeconds":"(\d+)"', h)
        secs = int(m.group(1)) if m else 0
    except Exception:
        pass
    return {"url": url, "id": video_id, "title": info.get("title", ""), "channel": info.get("name", ""),
            "verified": info.get("verified", False), "seconds": secs}


_SCHEMA = {"type": "object", "properties": {
    "relevant": {"type": "boolean"},
    "facts": {"type": "array", "items": {"type": "object", "properties": {
        "fact": {"type": "string"}, "speaker": {"type": "string"}, "quote": {"type": "string"},
        "timestamp": {"type": "string"}, "category": {"type": "string"}},
        "required": ["fact", "speaker", "quote", "timestamp", "category"]}}},
    "required": ["relevant", "facts"]}

_PROMPT = """Watch this video. It should feature people who made the film "{film}" ({year}): cast, director, writer or crew, talking about the film or its making.

If it is not mainly about that film or its making, return relevant=false and no facts.

Otherwise list up to {n} interesting, specific facts about the film that the speakers state: how it was made, casting stories, improvisation, props, locations, injuries, near-misses, numbers. For each:
- fact: one or two plain sentences that stand alone (name the speaker and the characters/actors).
- speaker: who says it.
- quote: the words the speaker says that support it, copied exactly as spoken (at most 40 words).
- timestamp: where it starts, as mm:ss (or h:mm:ss).
- category: one word.
Only facts actually said in the video. No plot summary, no facts about the speakers' other work, no opinions about quality."""

_CHECK = """Watch this video again. For each numbered claim below, say whether the person really says it at about the given time. Mark supported=true only if the speaker says what the claim says (the same specific detail). If the quote is slightly wrong, give the exact words in "heard".

{claims}"""
_CHECK_SCHEMA = {"type": "object", "properties": {"results": {"type": "array", "items": {
    "type": "object", "properties": {"id": {"type": "integer"}, "supported": {"type": "boolean"},
                                     "heard": {"type": "string"}}, "required": ["id", "supported", "heard"]}}},
    "required": ["results"]}


def _watch(client, model, url, prompt, schema):
    import json
    r = client._client.models.generate_content(
        model=model,
        contents=types.Content(parts=[types.Part(file_data=types.FileData(file_uri=url)), types.Part(text=prompt)]),
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema,
                                           temperature=0.1))
    return json.loads(r.text or "{}")


def _secs(ts: str) -> int:
    p = [int(x) for x in re.findall(r"\d+", ts or "")][:3]
    s = 0
    for x in p:
        s = s * 60 + x
    return s


def extract_and_check(client, cfg, film: str, year, info: dict, max_facts: int = 12) -> list[dict]:
    """Facts from one video, each marked verified (matches YouTube captions), rechecked, or unverified."""
    try:
        d = _watch(client, cfg.quality_model, info["url"], _PROMPT.format(film=film, year=year, n=max_facts), _SCHEMA)
    except Exception as e:
        log.warning("  could not read %s: %s", info["id"], type(e).__name__)
        return []
    if not d.get("relevant") or not d.get("facts"):
        return []
    facts = d["facts"]
    ok, text, segs = fetch_youtube_transcript(info["url"])
    for f in facts:
        f["status"] = "unverified"
    if ok and text:
        nt = normalize(text)
        for f in facts:
            q = f.get("quote", "")
            if q and (normalize(q) in nt or best_substring_ratio(q, text) >= 0.85):
                f["status"] = "quote found in YouTube captions"
    todo = [i for i, f in enumerate(facts) if f["status"] == "unverified"]
    if todo:
        claims = "\n".join(f'{i}. [{facts[i]["timestamp"]}] {facts[i]["speaker"]}: {facts[i]["fact"]}' for i in todo)
        try:
            chk = _watch(client, cfg.quality_model, info["url"], _CHECK.format(claims=claims), _CHECK_SCHEMA)
            res = {r["id"]: r for r in chk.get("results", [])}
            for i in todo:
                r = res.get(i)
                if r and r["supported"]:
                    facts[i]["status"] = "re-checked by a second Gemini pass"
                    if r.get("heard"):
                        facts[i]["quote"] = r["heard"]
                elif r:
                    facts[i]["status"] = "FAILED re-check"
        except Exception as e:
            log.warning("  re-check failed for %s: %s", info["id"], type(e).__name__)
    for f in facts:
        f["video"] = info
        f["link"] = f"{info['url']}&t={_secs(f.get('timestamp', ''))}s"
    return [f for f in facts if f["status"] != "FAILED re-check"]


def dedupe(facts: list[dict], thresh: float = 0.6) -> list[dict]:
    out: list[dict] = []
    for f in facts:
        if not any(SequenceMatcher(None, f["fact"].lower(), g["fact"].lower()).ratio() > thresh for g in out):
            out.append(f)
    return out
