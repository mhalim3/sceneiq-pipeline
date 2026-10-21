"""Find YouTube videos with Gemini and have Gemini transcribe them.

    python scripts/gemini_transcribe.py "Step Brothers (2008)" 3

Gemini (Google Search) proposes videos, each URL is checked against YouTube's oEmbed, and Gemini then
watches each video and writes a timestamped transcript to data/gemini_transcripts/<id>.txt.
These are model-written transcripts, not YouTube's caption files, so they live apart from data/transcripts.
"""
import json, re, sys, time
from pathlib import Path
import httpx
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from google.genai import types
from sceneiq.gemini import GeminiClient
from sceneiq import config

film, n = sys.argv[1], int(sys.argv[2])
c = GeminiClient()
res = c.grounded(config.DEEP_MODEL,
    f"Use Google Search to find {n+3} different YouTube videos (full youtube.com/watch?v= URLs) in which the cast, "
    f"director or crew of the film {film} talk about making it (interviews, panels, behind-the-scenes). Prefer videos "
    "from official or publication channels, 2 to 12 minutes long. List one URL per line with the title. Do not invent URLs.",
    temperature=0.2)
urls = []
for u in re.findall(r"https?://(?:www\.)?youtube\.com/watch\?v=[\w-]{11}", res.text) + \
        [s["url"] for s in res.sources if "youtube.com/watch" in s["url"]]:
    u = u.split("&")[0]
    if u not in urls:
        urls.append(u)
good = []
for u in urls:
    try:
        r = httpx.get("https://www.youtube.com/oembed", params={"url": u, "format": "json"}, timeout=15)
        if r.status_code == 200:
            j = r.json(); good.append((u, j["title"], j["author_name"]))
    except Exception:
        pass
if len(good) < n:     # Gemini's search often returns redirect links, not watch URLs: fall back to YouTube's own search
    from sceneiq.pipeline.youtube_sources import search
    title_key = film.split("(")[0].strip().lower()
    for q in (f"{film} interview cast", f"{film} behind the scenes"):
        for v in search(q):
            u = f"https://www.youtube.com/watch?v={v['id']}"
            if (v["verified"] and 90 <= v["seconds"] <= 15 * 60 and title_key in v["title"].lower()
                    and all(u != g[0] for g in good)):
                good.append((u, v["title"], v["channel"]))
print("verified videos:", len(good))
for g in good: print(" ", g)
PROMPT = ("Transcribe the spoken words in this video as exactly as you can. Put a [mm:ss] timestamp and the speaker "
          "(if known) at the start of each line. Do not summarize or add anything that is not said. "
          "If the video is not about the film " + film + " say NOT RELEVANT.")
out = []
for u, title, ch in good:
    if len(out) >= n: break
    vid = u.split("=")[1]
    t0 = time.time()
    try:
        r = c._client.models.generate_content(model=config.DEEP_MODEL, contents=types.Content(parts=[
            types.Part(file_data=types.FileData(file_uri=u)), types.Part(text=PROMPT)]))
        txt = (r.text or "").strip()
    except Exception as e:
        print("  failed", u, type(e).__name__); continue
    if not txt or txt.upper().startswith("NOT RELEVANT"):
        print("  skipped (not relevant / empty):", title); continue
    Path(f"data/gemini_transcripts/{vid}.txt").write_text(f"{title}\n{ch}\n{u}\n\n{txt}\n")
    out.append((vid, title, ch, u, len(txt), round(time.time() - t0)))
    print("  saved", vid, title[:60], len(txt), "chars")
json.dump(out, open("data/gemini_transcripts/index.json", "w"), indent=1)
