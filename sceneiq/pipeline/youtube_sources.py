"""Find YouTube videos worth reading, the way a researcher would.

Interviews, panels and behind-the-scenes pieces posted by a publication's own channel (GQ, Variety,
Vanity Fair), a studio or network (TBS, Sony Pictures) are real sources, but ordinary search rarely
returns them. This searches YouTube directly (no key), keeps videos from VERIFIED channels, rates
each channel (A/B/C, see channel_tiers.py), reads the transcript, and hands the A/B ones to the same
article-mining step as written articles. Fan, recap and "hidden details" channels are skipped.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import httpx

from .. import config
from .research import _fetch_candidate

log = logging.getLogger("sceneiq")

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
       "Accept-Language": "en-US,en;q=0.9"}
_OUTLETS = ["GQ", "Vanity Fair", "Variety", "The Hollywood Reporter", "Vulture", "Entertainment Weekly",
            "IGN", "Collider", "Rolling Stone", "Esquire", "People", "The Ringer", "Empire", "BuzzFeed Celeb",
            "Wired", "Vogue", "Late Night", "The Tonight Show", "Conan"]


def search(query: str) -> list[dict]:
    """[{id, title, channel, verified, seconds}] from YouTube's results page."""
    try:
        r = httpx.get("https://www.youtube.com/results", params={"search_query": query}, headers=_UA,
                      cookies={"CONSENT": "YES+1", "SOCS": "CAI"}, follow_redirects=True, timeout=20)
        m = re.search(r"ytInitialData\s*=\s*(\{.*?\});</script>", r.text, re.S)
        data = json.loads(m.group(1)) if m else {}
    except Exception as e:
        log.info("  youtube search failed: %s", e)
        return []
    out: list[dict] = []

    def walk(o):
        if isinstance(o, dict):
            v = o.get("videoRenderer")
            if v and v.get("videoId"):
                length = (v.get("lengthText") or {}).get("simpleText", "")
                parts = [int(x) for x in length.split(":") if x.isdigit()]
                secs = sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
                badges = [b.get("metadataBadgeRenderer", {}).get("style", "") for b in v.get("ownerBadges", [])]
                out.append({"id": v["videoId"],
                            "title": "".join(x.get("text", "") for x in v.get("title", {}).get("runs", [])),
                            "channel": ((v.get("ownerText") or {}).get("runs") or [{}])[0].get("text", ""),
                            "verified": any("VERIFIED" in b for b in badges), "seconds": secs})
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(data)
    return out


def discover(client, cfg: config.PipelineConfig, film_info: dict, cache, have: set) -> list:
    """Video SourceRefs (A/B channel, real transcript) about this film."""
    film = str(film_info.get("title", ""))
    year = film_info.get("year", "")
    base = f"{film} {year}"
    queries = [f"{base} interview", f"{base} behind the scenes", f"{base} cast reunion anniversary",
               f"{base} making of", f"{base} director commentary explained"]
    queries += [f"{o} {film} {year}" for o in _OUTLETS[: cfg.youtube_outlet_queries]]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(search, queries))
    cands: dict[str, dict] = {}
    for res in results:
        for v in res:
            if (v["verified"] and 60 <= v["seconds"] <= 75 * 60 and v["id"] not in cands
                    and film.lower() in v["title"].lower()):
                cands[v["id"]] = v
    urls = [f"https://www.youtube.com/watch?v={i}" for i in cands if f"https://www.youtube.com/watch?v={i}" not in have]
    urls = urls[: cfg.youtube_max_videos]
    log.info("  youtube: %d verified-channel video(s) about the film to read", len(urls))
    found = []
    for u in urls:            # one at a time: transcript requests are paced to avoid a block
        try:
            s = _fetch_candidate({"url": u, "title": cands[u.rsplit("=", 1)[1]]["title"]}, film,
                                 film_info.get("year"), cfg, cache, client=client)
        except Exception as e:
            log.info("  youtube video skipped (%s)", e)
            continue
        if s is not None and s.tier in ("A", "B") and getattr(s, "body_kind", "") == "transcript" \
                and len(getattr(s, "_body", "")) >= 1200:
            found.append(s)
            log.info("  youtube: %s [%s, tier %s]", cands[u.rsplit("=", 1)[1]]["title"][:60], s.channel, s.tier)
    return found
