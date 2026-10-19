"""Build the review HTML (top N ranked facts per title, best sources, labels) from review.json files.

Usage: python scripts/make_review_doc.py OUT.html DATA_DIR "Title 1" "Title 2" ...
"""
import collections
import glob
import html
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sceneiq.tiers import classify_domain  # noqa: E402

E_ = lambda s: html.escape(str(s or ""), quote=False)
EA = lambda s: html.escape(str(s or ""), quote=True)
TR = {"A": 0, "B": 1, "unknown": 2, "C": 3}
CR = {"primary_direct": 0, "reputable_editorial": 1}
TYPE = {"primary_direct": "named participant quoted", "reputable_editorial": "editorial reporting"}


def dom(u):
    h = urlparse(u).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def main(out, data_dir, titles, top=20):
    data = {}
    for f in glob.glob(f"{data_dir}/*.review.json"):
        d = json.load(open(f))
        data[d["film"]["title"]] = d
    body, rows, tot = [], [], collections.Counter()
    for name in titles:
        d = data[name]
        sel = sorted([r for r in d["candidates"] if r["status"] == "emitted" and r.get("rank")],
                     key=lambda r: r["rank"])[:top]
        body.append(f"<h1>{E_(name)} ({d['film'].get('year')})</h1>")
        cnt = collections.Counter()
        for n, r in enumerate(sel, 1):
            c = r["card"]
            cu = {y["url"] for y in c["sources"]}
            cited = [x for x in r["sources"] if x["url"] in cu]
            ev = {}
            for e in r.get("claim_evidence") or []:
                if (e.get("supporting_passage") or "").strip():
                    ev.setdefault(e["sourceUrl"], e["supporting_passage"].strip())
            cands = {}
            tier_of = lambda x: x.get("tier") if x.get("modality") == "video" and x.get("tier") in ("A", "B", "C") else classify_domain(x["url"])
            for x in cited:
                cands[x["url"]] = {"url": x["url"], "tier": tier_of(x), "cls": x.get("evidence_class"),
                                   "video": x.get("modality") == "video", "psg": ev.get(x["url"], "")}
            for co in r.get("corroboration") or []:
                e = cands.setdefault(co["url"], {"url": co["url"], "tier": classify_domain(co["url"]),
                                                 "cls": None, "video": False})
                e["psg"] = e.get("psg") or co.get("passage", "")
                e["corr"] = True
            cor = {dom(co["url"]) for co in (r.get("corroboration") or []) if classify_domain(co["url"]) in ("A", "B")}
            sup = any(v["tier"] in ("A", "B") and v["cls"] in CR for v in cands.values())
            corr_ab = any(v["tier"] in ("A", "B") and v.get("corr") for v in cands.values())
            tiers = [v["tier"] for v in cands.values()]
            if len(cor) >= 3:
                key, lab = "pass3", f"✅ PASSES: {len(cor)} A/B outlets state it"
            elif sup or corr_ab:
                key, lab = "pass1", "✅ PASSES: an A/B source confirms it"
            elif any(t in ("A", "B") for t in tiers):
                key, lab = "nc", "🔶 NOT CONFIRMED: A/B sources cite it, none confirmed it"
            elif tiers and all(t == "C" for t in tiers):
                key, lab = "weak", "⚠ WEAK: C-tier sources only"
            else:
                key, lab = "unr", "🔶 UNRATED: sources not rated A/B"
            cnt[key] += 1
            rank = lambda v: (TR.get(v["tier"], 2), 0 if v.get("corr") or v["cls"] in CR else 1,
                              CR.get(v["cls"], 2), 1 if v["video"] else 0)
            top2, seen = [], set()
            for v in sorted(cands.values(), key=rank):
                if dom(v["url"]) in seen:
                    continue
                seen.add(dom(v["url"]))
                top2.append(v)
                if len(top2) == 2:
                    break
            lines = []
            for v in top2:
                t = v["tier"]
                tt = "TIER " + t if t != "unknown" else "UNRATED"
                how = TYPE.get(v["cls"], "states the fact" if v.get("corr") else "cited") + (", video" if v["video"] else "")
                psg = v["psg"]
                if len(psg) > 170:
                    psg = psg[:170].rsplit(" ", 1)[0] + " …"
                lines.append(f'<a href="{EA(v["url"])}">{E_(dom(v["url"]))}</a> | {tt} | {how}'
                             + (f'<br><i>Passage:</i> &ldquo;{E_(psg)}&rdquo;' if psg else ""))
            why = [x for x in ((r.get("validation") or {}).get("review_reasons") or []) if "authoritative" not in x]
            tag = " | FROM ARTICLE MINING" if r["anchor"].get("origin") == "article_mining" else ""
            body.append(
                f"<h3>{n}. {lab}{tag}</h3><p><b>Full fact:</b> {E_(c['longDescription'])}</p>"
                f"<p><b>Short</b> <i>({len(c['shortVersion'])} chars)</i><b>:</b> {E_(c['shortVersion'])}</p>"
                f"<p><b>Long</b> <i>({len(c.get('longVersion') or '')} chars)</i><b>:</b> {E_(c.get('longVersion'))}</p>"
                f"<p><b>Top sources:</b><br>" + "<br>".join(lines) + "</p>"
                + (f"<p><b>Why review:</b> {E_('; '.join(why))}</p>" if why else ""))
        tot += cnt
        rows.append((name, d["film"].get("year"), len(sel), cnt))
    R = lambda c: f"<td>{c['pass3']}</td><td>{c['pass1']}</td><td>{c['nc']}</td><td>{c['unr']}</td><td>{c['weak']}</td>"
    tab = "".join(f"<tr><td>{E_(n)} ({y})</td><td>{t}</td>{R(c)}</tr>" for n, y, t, c in rows)
    head = (f"<h1>SceneIQ: {', '.join(E_(t) for t in titles)}, latest run</h1><p><i>Top {top} ranked facts per title from the "
            "latest run (article mining after the second search round, new wording rules, channel tiering). Facts marked "
            "FROM ARTICLE MINING were pulled from a strong A/B article read in full. Each fact shows the full fact, a short "
            "(80 characters or fewer), a long (140 or fewer), and its top one or two sources by tier.</i></p>"
            "<ul><li><b>✅ PASSES: 3+ A/B outlets state it</b></li><li><b>✅ PASSES: an A/B source confirms it</b></li>"
            "<li><b>🔶 NOT CONFIRMED:</b> A/B sources cite it but none confirmed it</li><li><b>🔶 UNRATED:</b> sources not rated A/B</li>"
            "<li><b>⚠ WEAK:</b> C-tier sources only</li></ul>"
            "<table border=\"1\" cellpadding=\"4\"><tr><th>Title</th><th>Facts</th><th>3+ A/B</th><th>A/B confirms</th>"
            f"<th>Not confirmed</th><th>Unrated</th><th>Weak</th></tr>{tab}<tr><td><b>Total</b></td><td><b>{sum(r[2] for r in rows)}</b></td>{R(tot)}</tr></table>")
    Path(out).write_text(head + "\n".join(body))
    print(len(head + "".join(body)), [(r[0], r[2], dict(r[3])) for r in rows])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
