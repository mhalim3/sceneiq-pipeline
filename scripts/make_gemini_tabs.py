"""Build Google Docs tab payloads (one tab per title) from Gemini-only runs.

    python scripts/make_gemini_tabs.py OUTDIR [TOP]

Uses the latest run of each title that used the current prompts. Writes OUTDIR/<slug>.json:
{"title", "text", "style_requests"} with the placeholder TABID. Indexes are UTF-16 code units.
"""
import json, re, sys, glob
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import merge_fable_gemini as m

RUNS = [  # (slug, run dir, model)
    ("matilda", "data/outputs_v16", "Gemini 2.5 Pro"), ("once-upon-a-time-in-hollywood", "data/outputs_v16", "Gemini 2.5 Pro"),
    ("diary-of-a-mad-black-woman", "data/outputs_v16", "Gemini 2.5 Pro"), ("the-secret-life-of-pets", "data/outputs_v16", "Gemini 2.5 Pro"),
    ("john-wick-chapter-4", "data/outputs_v17", "Gemini 2.5 Pro"), ("what-men-want", "data/outputs_v17", "Gemini 2.5 Pro"),
    ("bee-movie", "data/outputs_v17", "Gemini 2.5 Pro"), ("the-karate-kid", "data/outputs_v17", "Gemini 2.5 Pro"),
    ("the-fifth-element", "data/outputs_v17", "Gemini 2.5 Pro"), ("step-brothers", "data/outputs_v18", "Gemini 3.1 Pro"),
    ("the-secret-life-of-pets-2", "data/outputs_v19", "Gemini 3.1 Pro"), ("u-s-marshals", "data/outputs_v19", "Gemini 3.1 Pro"),
    ("atl", "data/outputs_v19", "Gemini 3.1 Pro"), ("2012", "data/outputs_v19", "Gemini 3.1 Pro"),
    ("jingle-all-the-way", "data/outputs_v20", "Gemini 3.1 Pro"), ("norbit", "data/outputs_v20", "Gemini 3.1 Pro"),
    ("colombiana", "data/outputs_v20", "Gemini 3.1 Pro"), ("guess-who", "data/outputs_v20", "Gemini 3.1 Pro"),
    ("goodfellas", "data/outputs_v20", "Gemini 3.1 Pro"), ("john-wick", "data/outputs_v20", "Gemini 3.1 Pro"),
    ("sidelined-3", "data/outputs_v20", "Gemini 3.1 Pro"),
    ("john-wick-chapter-2", "data/outputs_v21", "Gemini 3.1 Pro"), ("the-maze-runner", "data/outputs_v21", "Gemini 3.1 Pro"),
    ("bringing-down-the-house", "data/outputs_v21", "Gemini 3.1 Pro"), ("pearl-harbor", "data/outputs_v21", "Gemini 3.1 Pro"),
    ("the-shawshank-redemption", "data/outputs_v21", "Gemini 3.1 Pro"),
]
u16 = lambda s: len(s.encode("utf-16-le")) // 2
tidy = lambda s, n: (lambda t: t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + " …")(re.sub(r"\s+", " ", s or "").strip())


def build(title, year, model, items):
    text, heads, links, pos = [], [], [], 1

    def add(line, kind=None, link=None):
        nonlocal pos
        if kind: heads.append((pos, pos + u16(line), kind))
        if link:
            a, b, url = link; links.append((pos + a, pos + b, url))
        text.append(line + "\n"); pos += u16(line) + 1
    add(f"{title} ({year}): Gemini facts" if year else f"{title}: Gemini facts", "HEADING_1")
    add(f"Model: {model}. Current prompts. {len(items)} approved facts, ranked best first by the pipeline. Every fact passed the pipeline's source checks; tiers use our outlet lists.")
    for n, it in enumerate(items, 1):
        add(f"{n}. {it['label']}" + (" | from article mining" if it.get("mined") else ""), "HEADING_3")
        add("Full fact: " + it["full"]); add("Short: " + m.clean_len(it["short"])); add("Long: " + m.clean_len(it["long"]))
        for t, url, psg in it["srcs"][:2]:
            host = re.sub(r"^www\.", "", url.split("/")[2]); tl = "UNRATED" if t == "unknown" else f"TIER {t}"
            lead = "Source: "
            add(f"{lead}{host} | {tl}" + (f" | “{tidy(psg, 170)}”" if psg else ""), link=(len(lead), len(lead) + len(host), url))
        fl = (it.get("flags") or "").strip()
        if fl: add("Why review: " + tidy(fl, 260))
        add("")
    reqs = [{"updateParagraphStyle": {"range": {"startIndex": a, "endIndex": b, "tabId": "TABID"}, "paragraphStyle": {"namedStyleType": k}, "fields": "namedStyleType"}} for a, b, k in heads]
    reqs += [{"updateTextStyle": {"range": {"startIndex": a, "endIndex": b, "tabId": "TABID"}, "textStyle": {"link": {"url": url}}, "fields": "link"}} for a, b, url in links]
    return "".join(text), reqs


out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
top = int(sys.argv[2]) if len(sys.argv) > 2 else 40
for slug, gdir, model in RUNS:
    rf = Path(gdir) / f"{slug}.review.json"
    if not rf.exists(): continue
    d = json.load(open(rf)); title, year = d["film"]["title"], d["film"].get("year")
    if d["film"].get("anchor_source") == "title_only" and slug == "sidelined-3":
        year = None  # the pipeline guessed this year (title-only mode); leave it out
    items = m.gemini_pool(gdir).get(title, [])[:top]
    if not items: continue
    body, reqs = build(title, year, model, items)
    json.dump({"title": f"{title} ({year})" if year else title, "text": body, "style_requests": reqs}, open(out / f"{slug}.json", "w"))
    print(slug, len(items), "facts |", len(body), "chars")
