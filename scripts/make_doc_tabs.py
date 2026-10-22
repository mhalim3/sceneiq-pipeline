"""Build Google Docs batchUpdate requests (one tab per title) from the merged Fable+Gemini lists.

    python scripts/make_doc_tabs.py OUTDIR

For each title writes OUTDIR/<slug>.json: {"title", "text_request", "style_requests"}; tabId is the
placeholder TABID. Indexes are UTF-16 code units, as the Docs API requires.
"""
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import merge_fable_gemini as m
from sceneiq.tiers import classify_domain

SETS = [("data/outputs_v16", "/tmp/fable_doc.md"), ("data/outputs_v17", "/tmp/fable_doc2.md")]
u16 = lambda s: len(s.encode("utf-16-le")) // 2


def sources(w):
    out = []
    if w["model"] == "Fable":
        for p in re.split(r"\s*(?=\d\)\s)", w["sources"]):
            mm = re.search(r"<(https?://[^>]+)>", p)
            if not mm:
                continue
            url = mm.group(1)
            q = (re.search(r"—\s*(.*)$", p) or [None, ""])[1].strip()
            q = re.split(r"\s;\s", q)[0].strip()
            out.append((url, classify_domain(url), q))
    else:
        for t, url, psg in w["srcs"]:
            out.append((url, t, psg))
    return out


def tidy(s, n):
    s = re.sub(r"\s+", " ", (s or "").replace("&ldquo;", "“").replace("&rdquo;", "”")).strip()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + " …"


def build(title, year, ranked):
    text, heads, links = [], [], []
    pos = 1

    def add(line, kind=None, link=None):
        nonlocal pos
        if kind:
            heads.append((pos, pos + u16(line), kind))
        if link:
            a, b, url = link
            links.append((pos + a, pos + b, url))
        text.append(line + "\n")
        pos += u16(line) + 1

    add(f"{title} ({year}) — Fable + Gemini merged, top {len(ranked)}", "HEADING_1")
    add("Ranked best first. Pick 30. Each fact shows which model's wording was used and the other model's short line when both found it. Source tiers use our outlet lists.")
    for n, (w, others, why) in enumerate(ranked, 1):
        oth = [o for o in others if o["model"] != w["model"]]
        weak = w["model"] == "Fable" and not any(classify_domain(u) in ("A", "B") for u, _, _ in sources(w))
        lab = "CHECK SOURCES (none is A/B in our lists)" if weak else w["label"].replace("PASSES: ", "PASSES: ")
        tag = f"found by both, {w['model']} wording used" if oth else f"{w['model']} only"
        if w["model"] == "Gemini" and w.get("mined"):
            tag += ", from article mining"
        add(f"{n}. {lab} | {tag}", "HEADING_3")
        add("Full fact: " + w["full"])
        add("Short: " + m.clean_len(w["short"]))
        add("Long: " + m.clean_len(w["long"]))
        for url, tier, q in sources(w)[:3]:
            host = re.sub(r"^www\.", "", url.split("/")[2])
            tl = "UNRATED" if tier == "unknown" else f"TIER {tier}"
            lead = "Source: "
            line = f"{lead}{host} | {tl}" + (f" | “{tidy(q, 170)}”" if q else "")
            add(line, link=(len(lead), len(lead) + len(host), url))
        if oth:
            add(f"Other version ({oth[0]['model']}): " + m.clean_len(oth[0]["short"]))
        fl = (w.get("flags") or "").strip()
        if fl and fl.lower() not in ("none", "none."):
            add("Why review: " + tidy(fl, 260))
        add("")
    body = "".join(text)
    reqs = [{"updateParagraphStyle": {"range": {"startIndex": a, "endIndex": b, "tabId": "TABID"},
                                      "paragraphStyle": {"namedStyleType": k}, "fields": "namedStyleType"}}
            for a, b, k in heads]
    reqs += [{"updateTextStyle": {"range": {"startIndex": a, "endIndex": b, "tabId": "TABID"},
                                  "textStyle": {"link": {"url": url}}, "fields": "link"}} for a, b, url in links]
    return body, reqs


out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
for gdir, fmd in SETS:
    fab, gem = m.parse_fable(fmd), m.gemini_pool(gdir)
    years = {}
    import glob
    for f in glob.glob(f"{gdir}/*.review.json"):
        d = json.load(open(f)); years[d["film"]["title"]] = d["film"].get("year")
    for title, rk, _ex in json.load(open(f"{gdir}/merged40.json")):
        byid = {it["id"]: it for it in fab[title] + gem[title]}
        ranked = [(byid[w], [byid[o] for o in oo if o in byid], why) for w, oo, why in rk if w in byid]
        body, reqs = build(title, years[title], ranked)
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        json.dump({"title": f"{title} ({years[title]})", "text": body, "style_requests": reqs},
                  open(out / f"{slug}.json", "w"))
        print(slug, len(ranked), "facts |", len(body), "chars |", len(reqs), "style requests")
