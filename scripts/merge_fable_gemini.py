"""Merge the Fable top-30 doc with the Gemini pipeline output, per overlapping title.

    python scripts/merge_fable_gemini.py FABLE_DOC.md GEMINI_DIR OUT.html [TOP]

Each title: pool Fable's facts and Gemini's approved facts, group duplicates, keep the better version
of each, rank by interest, return the top N (default 30). Nothing is rewritten: the winning text is
used as written by the model that produced it.
"""
import glob
import html
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sceneiq import config, editorial_guide as g  # noqa: E402
from sceneiq.gemini import GeminiClient  # noqa: E402
from sceneiq.tiers import classify_domain  # noqa: E402

E_ = lambda s: html.escape(str(s or ""), quote=False)
EA = lambda s: html.escape(str(s or ""), quote=True)
UN = lambda s: re.sub(r"\\(.)", r"\1", s or "").strip()


def parse_fable(path):
    t = open(path).read()
    skip = ("Scene Facts", "Index", "Pipeline prompt")
    heads = [(m.start(), re.sub(r"\s*\(\d{4}\)\s*$", "", m.group(1)).strip(), None)
             for m in re.finditer(r"^# ([^#\n]+?)\s*$", t, re.M) if not m.group(1).startswith(skip)]
    out = {}
    for i, (pos, title, year) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(t)
        sec = t[pos:end]
        sec = re.split(r"^## (?:NEAR MISSES|Near misses)", sec, flags=re.M)[0]
        facts = []
        for m in re.finditer(r"^### (\d+)\\?\. (PASSES[^\n]*)\n(.*?)(?=^### \d+\\?\.|\Z)", sec, re.M | re.S):
            b = m.group(3)
            f = lambda lab: (re.search(r"\*\*" + lab + r"[^*]*:\*\*\s*(.*?)\n", b) or [None, ""])[1]
            out.setdefault(title, []).append({
                "id": f"F{m.group(1)}", "model": "Fable", "rank": int(m.group(1)), "label": UN(m.group(2)),
                "full": UN(f("Full fact")), "short": UN(re.sub(r"^\(\d+ chars\):\s*", "", f("Short"))) if False else UN(f("Short")),
                "long": UN(f("Long")), "sources": UN(f("Top sources")) or UN((re.search(r"\*\*Top sources:\*\*\s*\n+(.*?)\n\n", b, re.S) or [None, ""])[1]),
                "outlets": UN(f("Outlets that independently state it")), "flags": UN(f("Flags a human reviewer should check")),
                "found": UN(f("Found by"))})
        out[title] = out.get(title, [])
    return out


def clean_len(s):
    return re.sub(r"^\*?\*?\(?\d+ chars\)?\*?\*?:?\s*", "", s or "")


def gemini_pool(gdir):
    out = {}
    for fp in glob.glob(f"{gdir}/*.review.json"):
        d = json.load(open(fp))
        title = d["film"]["title"]
        em = sorted([r for r in d["candidates"] if r["status"] == "emitted" and r.get("rank")], key=lambda r: r["rank"])
        items = []
        for n, r in enumerate(em, 1):
            c = r["card"]
            cu = {y["url"] for y in c["sources"]}
            ev = {}
            for e in r.get("claim_evidence") or []:
                if (e.get("supporting_passage") or "").strip():
                    ev.setdefault(e["sourceUrl"], e["supporting_passage"].strip())
            srcs = []
            for x in r["sources"]:
                if x["url"] in cu:
                    t = x.get("tier") if x.get("modality") == "video" and x.get("tier") in ("A", "B", "C") else classify_domain(x["url"])
                    srcs.append((t, x["url"], ev.get(x["url"], "")))
            for co in r.get("corroboration") or []:
                if co["url"] not in cu:
                    srcs.append((classify_domain(co["url"]), co["url"], co.get("passage", "")))
            srcs.sort(key=lambda s: {"A": 0, "B": 1, "unknown": 2, "C": 3}.get(s[0], 2))
            ab = [s for s in srcs if s[0] in ("A", "B")]
            cor = {re.sub(r"^www\.", "", s[1].split("/")[2]) for s in ab}
            label = (f"PASSES: {len(cor)} A/B outlets state it" if len(cor) >= 3 else
                     "PASSES: an A/B source confirms it" if ab else "WEAK or unrated (no A/B source)")
            items.append({"id": f"G{n}", "model": "Gemini", "rank": n, "label": label, "full": c["longDescription"],
                          "short": c["shortVersion"], "long": c.get("longVersion") or "", "srcs": srcs[:2],
                          "flags": "; ".join(x for x in ((r.get("validation") or {}).get("review_reasons") or []) if "authoritative" not in x),
                          "mined": r["anchor"].get("origin") == "article_mining"})
        out[title] = items
    return out


_SCHEMA = {"type": "object", "properties": {
    "ranked": {"type": "array", "items": {"type": "object", "properties": {
        "winner": {"type": "string"}, "same_fact_as": {"type": "array", "items": {"type": "string"}},
        "why": {"type": "string"}}, "required": ["winner", "same_fact_as", "why"]}},
    "excluded": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "why": {"type": "string"}}, "required": ["id", "why"]}}},
    "required": ["ranked", "excluded"]}

_PROMPT = """You are the final editor merging two independent research runs on the film "{film}" ({year}) into one list. Run F is Fable, run G is Gemini. Both followed the same rules. Every item below already passed source checks.

TASK
1. Group items that tell the SAME fact (same people, same specific detail), within or across runs. Facts that merely share a topic are different facts.
2. For each group choose the single BEST version to keep (the winner), judged on: (a) accuracy to what the sources show and not overstated; (b) source strength (a named participant quoted or an A/B outlet beats C-tier/unrated/YouTube-only); (c) writing quality per the guide (short is only the hook and stands alone; long restates the claim and adds one new specific; plain words; no film title in short/long; no unexplained terms; numbers have a benchmark). Do not rewrite anything; pick the best existing version.
3. Exclude any group that breaks the rules (list it in "excluded" with the reason): do-not-show kinds, profanity even censored/quoted, real-world crimes/deaths/tragedies connected to the production or talent, gossip, claims that look wrong or overstated, plain reference data, or facts whose short is unclear without seeing the film.
4. Rank the remaining groups best first by how interesting the fact is to a general viewer who just paused the movie (interest guide below), using source strength and wording only as tie-breakers. Return the top {top}. If fewer than {top} good groups exist, return fewer; never pad.

{exclusions}

{interest}

WRITING RULES (examples)
{examples}

ITEMS (id | label | short | long | first part of the full fact | flags)
{items}

Return "ranked": for each kept group the winner id, the ids of the other versions of the same fact (same_fact_as, empty if none), and a one-line reason. Return "excluded" for removed groups."""


def merge_title(client, title, year, fab, gem, top):
    lines = []
    for it in fab + gem:
        lines.append(f'{it["id"]} | {it["label"]} | {it["short"]} | {it["long"]} | {it["full"][:420]} | {it.get("flags", "")[:160]}')
    d = client.structured(config.DEEP_MODEL, _PROMPT.format(
        film=title, year=year, top=top, exclusions=g.exclusions_block(), interest=g.interest_block(),
        examples=g.wording_examples(), items="\n".join(lines)), _SCHEMA, temperature=0.1)
    byid = {it["id"]: it for it in fab + gem}
    seen, ranked = set(), []
    for r in d["ranked"]:
        w = r["winner"].strip()
        if w in byid and w not in seen and not any(x in seen for x in r["same_fact_as"] if x in byid):
            seen.add(w)
            seen.update(x for x in r["same_fact_as"] if x in byid)
            ranked.append((byid[w], [byid[x] for x in r["same_fact_as"] if x in byid and x != w], r["why"]))
    return ranked[:top], d["excluded"]


def fable_has_ab(s):
    return any(classify_domain(u) in ("A", "B") for u in re.findall(r"<(https?://[^>]+)>", s or ""))


def src_html_fable(s):
    parts = re.split(r"\s*(?=\d\)\s)", s)
    out = []
    for p in parts:
        m = re.search(r"<(https?://[^>]+)>", p)
        if not m:
            continue
        url = m.group(1)
        tier = classify_domain(url)
        tier = "UNRATED" if tier == "unknown" else "TIER " + tier
        host = re.sub(r"^www\.", "", url.split("/")[2])
        q = (re.search(r"—\s*(.*)$", p) or [None, ""])[1].strip()
        q = re.split(r"\s;\s", q)[0].strip()      # drop the trailing "; Other outlet (...)" notes
        q = q[:200].rsplit(" ", 1)[0] + " …" if len(q) > 200 else q
        out.append(f'<a href="{EA(url)}">{E_(host)}</a> | {tier}' + (f"<br><i>Passage:</i> {E_(q)}" if q else ""))
    return "<br>".join(out)


def src_html_gemini(srcs):
    out = []
    for t, url, psg in srcs:
        host = re.sub(r"^www\.", "", url.split("/")[2])
        psg = psg[:170].rsplit(" ", 1)[0] + " …" if len(psg) > 170 else psg
        out.append(f'<a href="{EA(url)}">{E_(host)}</a> | {"TIER " + t if t != "unknown" else "UNRATED"}'
                   + (f"<br><i>Passage:</i> &ldquo;{E_(psg)}&rdquo;" if psg else ""))
    return "<br>".join(out)


def main(fable_md, gdir, out, top=30):
    fab, gem = parse_fable(fable_md), gemini_pool(gdir)
    titles = [t for t in fab if t in gem]
    print("overlap:", titles, {t: (len(fab[t]), len(gem[t])) for t in titles})
    client = GeminiClient()
    years = {t: json.load(open(f))["film"].get("year") for f in glob.glob(f"{gdir}/*.review.json")
             for t in [json.load(open(f))["film"]["title"]]}
    saved = out.replace(".html", ".json")
    if "--render-only" in sys.argv and Path(saved).exists():
        byid = {t: {it["id"]: it for it in fab[t] + gem[t]} for t in titles}
        sv = {x[0]: x for x in json.load(open(saved))}
        res = [([(byid[t][w], [byid[t][o] for o in oo if o in byid[t]], why) for w, oo, why in sv[t][1]], sv[t][2]) for t in titles]
    else:
        with ThreadPoolExecutor(max_workers=5) as pool:
            res = list(pool.map(lambda t: merge_title(client, t, years[t], fab[t], gem[t], top), titles))
    body, summ = [], []
    for t, (ranked, excl) in zip(titles, res):
        both = sum(1 for _, o, _ in ranked if o and ({w.get("model") for w in o} - {_}))
        n_f = sum(1 for w, o, _ in ranked if w["model"] == "Fable")
        n_g = sum(1 for w, o, _ in ranked if w["model"] == "Gemini")
        n_b = sum(1 for w, o, _ in ranked if any(x["model"] != w["model"] for x in o))
        summ.append((t, years[t], len(ranked), n_f, n_g, n_b, f"{len(fab[t])} / {len(gem[t])}"))
        body.append(f"<h1>{E_(t)} ({years[t]})</h1>")
        for n, (w, others, why) in enumerate(ranked, 1):
            oth = [o for o in others if o["model"] != w["model"]]
            tag = (f" | FOUND BY BOTH (using the {w['model']} version)" if oth else f" | {w['model'].upper()} ONLY")
            if w["model"] == "Gemini" and w.get("mined"):
                tag += " | FROM ARTICLE MINING"
            srcs = src_html_fable(w["sources"]) if w["model"] == "Fable" else src_html_gemini(w["srcs"])
            alt = ""
            if oth:
                o = oth[0]
                alt = f'<p><b>Other version ({o["model"]}):</b> {E_(o["short"])}</p>'
            fl = w.get("flags", "")
            lab = ("✅ " + w["label"]) if (w["model"] == "Gemini" or fable_has_ab(w["sources"])) else \
                  "🔶 Fable marks this as passing, but none of its listed sources is A/B in our outlet lists"
            body.append(f'<h3>{n}. {E_(lab)}{E_(tag)}</h3>'
                        f'<p><b>Full fact:</b> {E_(w["full"])}</p><p><b>Short:</b> {E_(clean_len(w["short"]))}</p>'
                        f'<p><b>Long:</b> {E_(clean_len(w["long"]))}</p><p><b>Top sources:</b><br>{srcs}</p>' + alt
                        + (f"<p><b>Why review:</b> {E_(fl)}</p>" if fl and fl.lower() not in ("none", "none.") else ""))
    rows = "".join(f"<tr><td>{E_(t)} ({y})</td><td>{n}</td><td>{f}</td><td>{gg}</td><td>{b}</td><td>{x}</td></tr>"
                   for t, y, n, f, gg, b, x in summ)
    head = (f"<h1>SceneIQ: Fable + Gemini merged, top {top} per title</h1><p><i>Both runs' facts were pooled per title, "
            "duplicates grouped, the better version of each fact kept (nothing rewritten), and the best "
            f"{top} ranked by how interesting they are to a viewer. 'Found by both' means the same fact appeared in the Fable and "
            "Gemini runs; the other model's short is shown for comparison. Every fact passed its run's source checks.</i></p>"
            '<table border="1" cellpadding="4"><tr><th>Title</th><th>Facts</th><th>Fable version used</th><th>Gemini version used</th>'
            f"<th>Found by both</th><th>Pool size (Fable / Gemini)</th></tr>{rows}</table>")
    Path(out).write_text(head + "\n".join(body))
    if "--render-only" not in sys.argv:
        json.dump([[t, [(w["id"], [o["id"] for o in oo], why) for w, oo, why in rk], ex] for t, (rk, ex) in zip(titles, res)],
                  open(saved, "w"), indent=1)
    print(summ)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0], args[1], args[2], int(args[3]) if len(args) > 3 else 30)
