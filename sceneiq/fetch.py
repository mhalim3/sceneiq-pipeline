"""Source fetching and verbatim-matching utilities.

Ported/adapted from the scene-sense prototype's source_bank (rrao6/scene-sense):
fetch-then-write architecture support — page text extraction, YouTube caption
transcripts with segment timing, fuzzy verbatim matching, and transcript
timestamp attachment.
"""

from __future__ import annotations

import re
import threading
from difflib import SequenceMatcher
from urllib.parse import parse_qs, urlparse

import httpx

# Full browser UA: some outlets (fashionista.com et al.) 403 obvious bots.
_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    )
}


def normalize(text: str) -> str:
    t = re.sub(r"\s+", " ", text or "").strip().lower()
    t = re.sub(r"[“”\"'`.,;:!?()\[\]…—–-]+", "", t)
    return t


def best_substring_ratio(needle: str, haystack: str) -> float:
    """Best fuzzy-match ratio of `needle` against any window of `haystack`."""
    n = normalize(needle)
    h = normalize(haystack)
    if not n or not h:
        return 0.0
    if n in h:
        return 1.0
    win = len(n)
    if win < 16:
        return SequenceMatcher(None, n, h[: min(len(h), win * 4)]).ratio()
    best = 0.0
    step = max(1, win // 4)
    for i in range(0, max(1, len(h) - win + 1), step):
        chunk = h[i: i + win + 20]
        r = SequenceMatcher(None, n, chunk).ratio()
        if r > best:
            best = r
            if best >= 0.99:
                return best
    return best


def fetch_url_text(url: str, timeout_s: float) -> tuple[bool, str, str]:
    """GET a URL and return (ok, final_url, extracted_text)."""
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout_s, headers=_UA) as http:
            resp = http.get(url)
    except httpx.HTTPError:
        return False, url, ""
    final = str(resp.url)
    if resp.status_code >= 400:
        return False, final, ""
    ct = (resp.headers.get("content-type") or "").lower()
    if "html" in ct or "text" in ct:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            return True, final, soup.get_text(separator=" ", strip=True)
        except ImportError:
            body = re.sub(r"<[^>]+>", " ", resp.text)
            return True, final, re.sub(r"\s+", " ", body)
    return True, final, ""


def youtube_video_id(url: str) -> str | None:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if "youtu.be" in host:
        return p.path.lstrip("/") or None
    if "youtube.com" not in host:
        return None
    if p.path.startswith(("/shorts/", "/embed/")):
        parts = p.path.split("/")
        return parts[2] if len(parts) > 2 else None
    return (parse_qs(p.query).get("v") or [None])[0]


def _run_with_timeout(fn, timeout_s: float, default):
    """Run `fn` on a daemon thread with a hard deadline.

    youtube_transcript_api has no network timeout of its own — one stuck
    socket froze an entire pipeline run for 16 hours. The abandoned thread
    leaks briefly but the run finishes.
    """
    out: dict = {}

    def runner():
        try:
            out["v"] = fn()
        except Exception:
            pass

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(timeout_s)
    return out.get("v", default)


def fetch_youtube_transcript(url: str, timeout_s: float = 30.0) -> tuple[bool, str, list[dict]]:
    """Returns (ok, concatenated_text, segments[{text, start, duration}])."""
    vid = youtube_video_id(url)
    if not vid:
        return False, "", []
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return False, "", []

    def _fetch():
        api = YouTubeTranscriptApi()
        return api.fetch(vid, languages=["en", "en-US", "en-GB"])

    fetched = _run_with_timeout(_fetch, timeout_s, None)
    if fetched is None:
        return False, "", []
    segments = []
    for seg in fetched:
        if isinstance(seg, dict):
            segments.append({"text": seg.get("text", ""), "start": seg.get("start", 0.0),
                             "duration": seg.get("duration", 0.0)})
        else:
            segments.append({"text": getattr(seg, "text", ""), "start": getattr(seg, "start", 0.0),
                             "duration": getattr(seg, "duration", 0.0)})
    return True, " ".join(s["text"] for s in segments), segments


def _hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def attach_timestamp(quote_text: str, segments: list[dict]) -> str:
    """Locate the quote in transcript segments -> 'MM:SS–MM:SS' range, or ''."""
    if not segments or not normalize(quote_text):
        return ""
    window = 10
    best_ratio, best_start, best_end = 0.0, None, None
    for i in range(max(1, len(segments) - 1)):
        j = min(i + window, len(segments))
        chunk = " ".join(segments[k]["text"] for k in range(i, j))
        r = best_substring_ratio(quote_text, chunk)
        if r > best_ratio:
            best_ratio = r
            best_start = segments[i]["start"]
            best_end = segments[j - 1]["start"] + segments[j - 1].get("duration", 0)
            if r >= 0.99:
                break
    if best_ratio >= 0.75 and best_start is not None:
        return f"{_hms(best_start)}–{_hms(best_end)}"
    return ""


class FetchCache:
    """Run-level, thread-safe cache for URL fetches and transcripts.

    The same sources recur across anchors (one outlet's BTS article feeds
    many anchors); without this, every anchor re-fetches them. Values are
    identical to the underlying fetchers' returns, so cache hits are
    quality-neutral. Rare duplicate fetches under race are benign.
    """

    def __init__(self, timeout_s: float):
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._pages: dict[str, tuple[bool, str, str]] = {}
        self._transcripts: dict[str, tuple[bool, str, list]] = {}

    def page(self, url: str) -> tuple[bool, str, str]:
        with self._lock:
            if url in self._pages:
                return self._pages[url]
        result = fetch_url_text(url, self._timeout_s)
        with self._lock:
            self._pages[url] = result
        return result

    def transcript(self, url: str) -> tuple[bool, str, list]:
        with self._lock:
            if url in self._transcripts:
                return self._transcripts[url]
        result = fetch_youtube_transcript(url)
        with self._lock:
            self._transcripts[url] = result
        return result


def title_grounded(film_title: str, body: str, year: int | None = None) -> bool:
    """Off-topic filter: body must contain >= half the title tokens.

    Single-common-word titles ("Burnt") match far too much — a generic
    prop-design article containing the word 'burnt' passes the token test.
    For those, additionally require the release year or an explicit film
    framing ("film", "movie") near a title mention.
    """
    lower = body.lower()
    tokens = [t for t in re.split(r"\W+", film_title.lower()) if len(t) >= 4]
    if not tokens:
        return True
    hits = sum(1 for t in tokens if t in lower)
    if hits < max(1, len(tokens) // 2):
        return False
    if len(tokens) >= 2:
        return True
    # Single-token title: demand corroborating film context.
    if year and str(year) in lower:
        return True
    token = tokens[0]
    for m in re.finditer(re.escape(token), lower):
        window = lower[max(0, m.start() - 80): m.end() + 80]
        if "film" in window or "movie" in window or "(20" in window or "(19" in window:
            return True
    return False
