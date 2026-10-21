"""Facts from YouTube videos of a film's cast and crew, found with Gemini's search and read by Gemini.

    python scripts/youtube_facts.py "Step Brothers" 2008 [max_videos]

1. Gemini Google Search finds videos; redirect links are followed to real YouTube URLs.
2. Only verified A/B channels are kept.
3. Gemini watches each video and extracts facts (speaker, quote, timestamp); each is checked against
   YouTube's captions when they can be fetched, otherwise re-checked by a second Gemini pass.
Writes data/youtube_facts/<film>.json and .html.
"""
import html, json, logging, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sceneiq import channel_tiers, config
from sceneiq.gemini import GeminiClient
from sceneiq.pipeline import youtube_grounded as yg

logging.basicConfig(level=logging.INFO, format="%(message)s")
film, year = sys.argv[1], sys.argv[2]
max_videos = int(sys.argv[3]) if len(sys.argv) > 3 else 8
cfg, client = config.PipelineConfig(), GeminiClient()

ids = yg.find_video_ids(client, cfg, film, year)
with ThreadPoolExecutor(max_workers=6) as pool:
    infos = list(pool.map(yg.video_facts_info, ids))
kept = []
for i in infos:
    if not i["title"]:
        continue
    r = channel_tiers.rate_video(client, cfg, i["url"])
    i["tier"], i["kind"] = r.get("tier", "unknown"), r.get("kind", "")
    ok = i["tier"] in ("A", "B") and 60 <= i["seconds"] <= 45 * 60
    print(("KEEP " if ok else "skip ") + f'{i["tier"]:>7} {i["seconds"]:>5}s {i["channel"][:28]:28} {i["title"][:60]}')
    if ok:
        kept.append(i)
kept = sorted(kept, key=lambda i: (i["tier"] != "A", i["seconds"]))[:max_videos]
print(f"reading {len(kept)} video(s)...")
with ThreadPoolExecutor(max_workers=3) as pool:
    per = list(pool.map(lambda i: yg.extract_and_check(client, cfg, film, year, i), kept))
facts = yg.dedupe([f for fs in per for f in fs])
order = {"quote found in YouTube captions": 0, "re-checked by a second Gemini pass": 1, "unverified": 2}
facts.sort(key=lambda f: order.get(f["status"], 3))
out = Path("data/youtube_facts"); out.mkdir(parents=True, exist_ok=True)
slug = film.lower().replace(" ", "-")
json.dump({"film": film, "year": year, "videos": kept, "facts": facts}, open(out / f"{slug}.json", "w"), indent=1)
E = lambda s: html.escape(str(s or ""), quote=False)
body = [f"<h1>{E(film)} ({year}): facts from cast and crew YouTube videos</h1>",
        f"<p><i>Found with Gemini search, {len(kept)} verified-channel video(s) watched by Gemini. Each fact has the speaker, "
        "the words heard, a link to the exact moment, and how it was checked.</i></p><h3>Videos used</h3><ul>"
        + "".join(f'<li><a href="{i["url"]}">{E(i["title"])}</a> — {E(i["channel"])} (tier {i["tier"]})</li>' for i in kept)
        + "</ul>"]
for n, f in enumerate(facts, 1):
    v = f["video"]
    body.append(f'<h3>{n}. {E(f["status"])}</h3><p><b>Fact:</b> {E(f["fact"])}</p><p><b>Speaker:</b> {E(f["speaker"])} '
                f'| <b>Heard:</b> “{E(f["quote"])}”</p><p><b>Source:</b> <a href="{f["link"]}">{E(v["title"])}</a> '
                f'at {E(f["timestamp"])} — {E(v["channel"])} (tier {v["tier"]})</p>')
(out / f"{slug}.html").write_text("\n".join(body))
from collections import Counter
print(len(facts), "facts", dict(Counter(f["status"] for f in facts)))
