"""Tier a YouTube video by the channel that posted it.

A video is only as reliable as its uploader. The watch page gives the channel name,
handle and verified badge. The channel is rated once (cached in auto_channels.json):
the verified official channel of an A/B outlet takes that outlet's tier; a verified
studio, network or the film's own talent is B; fan, recap and "hidden details"
channels are C. Unverified outlet channels are at most B.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path

import httpx

from . import tiers

log = logging.getLogger("sceneiq")
_PATH = Path(__file__).with_name("auto_channels.json")
_lock = threading.Lock()
_cache: dict | None = None

_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["outlet", "studio_network", "talent_or_crew", "fan_or_creator", "other"]},
        "outlet_domain": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["kind", "outlet_domain", "reason"],
}

_PROMPT = """A YouTube video is being considered as a source for a fact about a movie. Classify the CHANNEL that posted it.

Channel name: {name}
Channel handle: {handle}
Verified badge: {verified}
Video title: {title}

kind:
- outlet: the official channel of a news, entertainment, trade or culture publication (GQ, Variety, IGN, Vanity Fair). Put its main website domain in outlet_domain (for example gq.com); otherwise "".
- studio_network: the official channel of a studio, network, streamer or distributor (Warner Bros., Tubi, Netflix).
- talent_or_crew: the official channel of an actor, director, writer or other person who worked on a film.
- fan_or_creator: a fan, recap, ranking, "hidden details", reaction or independent creator channel.
- other: anything else.
Only call a channel an outlet, studio_network or talent_or_crew if you are confident it is the real official channel (the verified badge supports this). Otherwise fan_or_creator. One-sentence reason."""

_UA = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US"}


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_PATH.read_text())
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save(cache: dict) -> None:
    """Merge with what other processes wrote since we loaded, then replace the file atomically."""
    try:
        on_disk = json.loads(_PATH.read_text())
    except (OSError, ValueError):
        on_disk = {}
    on_disk.update(cache)
    cache.update(on_disk)
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(sorted(on_disk.items())), indent=1, ensure_ascii=False))
    tmp.replace(_PATH)


def video_id(url: str) -> str:
    m = re.search(r"(?:v=|shorts/|youtu\.be/)([\w-]{11})", url)
    return m.group(1) if m else ""


def channel_info(url: str) -> dict:
    """{channel_id, name, handle, verified, title} from the watch page ({} on failure)."""
    vid = video_id(url)
    if not vid:
        return {}
    try:
        r = httpx.get(f"https://www.youtube.com/watch?v={vid}", headers=_UA, cookies={"CONSENT": "YES+1"},
                      follow_redirects=True, timeout=20)
        h = r.text
        cid = re.search(r'"channelId":"([\w-]+)"', h)
        name = re.search(r'"ownerChannelName":"([^"]+)"', h)
        handle = re.search(r'"canonicalBaseUrl":"/(@[^"]+)"', h)
        title = re.search(r'<meta name="title" content="([^"]*)"', h)
        if not cid or not name:
            return {}
        return {"channel_id": cid.group(1), "name": json.loads(f'"{name.group(1)}"'),
                "handle": handle.group(1) if handle else "",
                "verified": "BADGE_STYLE_TYPE_VERIFIED" in h,
                "title": title.group(1) if title else ""}
    except Exception as e:
        log.warning("  channel lookup failed for %s: %s", vid, e)
        return {}


def _tier(kind: str, verified: bool, outlet_domain: str) -> str:
    if kind == "outlet" and outlet_domain:
        t = tiers.classify_domain("https://" + outlet_domain)
        if t in ("A", "B"):
            return t if verified else "B"
    if kind in ("outlet", "studio_network", "talent_or_crew") and verified:
        return "B"
    return "C"


def rate_video(client, cfg, url: str) -> dict:
    """{tier, channel, verified, kind, reason} for the channel that posted `url` ({} if unknown)."""
    info = channel_info(url)
    if not info:
        return {}
    with _lock:
        cache = _load()
        e = cache.get(info["channel_id"])
        if e is None:
            try:
                d = client.structured(cfg.interest_model, _PROMPT.format(
                    name=info["name"], handle=info["handle"], verified="yes" if info["verified"] else "no",
                    title=info["title"][:150]), _SCHEMA, temperature=0.0)
            except Exception as ex:
                log.warning("  channel rating failed for %s: %s", info["name"], ex)
                return {"tier": "unknown", "channel": info["name"], "verified": info["verified"], "kind": "", "reason": ""}
            e = {"name": info["name"], "handle": info["handle"], "verified": info["verified"],
                 "kind": d.get("kind", "other"), "outlet_domain": d.get("outlet_domain", ""),
                 "reason": d.get("reason", "")}
            e["tier"] = _tier(e["kind"], e["verified"], e["outlet_domain"])
            cache[info["channel_id"]] = e
            _save(cache)
            log.info("  rated channel %s (%s%s) -> %s", e["name"], e["kind"], ", verified" if e["verified"] else "", e["tier"])
    return {"tier": e["tier"], "channel": e["name"], "verified": e["verified"], "kind": e["kind"], "reason": e["reason"]}
