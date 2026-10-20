"""Build the single prompt that makes another model (Fable) do exactly what the pipeline does.

Pulls the live rules from the code (editorial guide, do-not-show rules, source policy, sweep queries,
judge checks), so the prompt cannot drift from the pipeline.

    python scripts/make_fable_prompt.py OUT.txt
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sceneiq import config, editorial_guide as g, source_policy as sp  # noqa: E402
from sceneiq.pipeline import title_sweep as ts  # noqa: E402

cfg = config.PipelineConfig()
queries = list(ts._SWEEP_QUERIES) + [q for qs in ts._SWEEP_QUERIES_LATER_SETS for q in qs]
query_block = "\n".join("- " + q for q in queries)
a_list = ", ".join(sorted(sp.A_TIER_DOMAINS))
b_list = ", ".join(sorted(sp.B_TIER_DOMAINS))

P = f"""You are the research-and-writing engine for SceneIQ, Tubi's "Scene Facts" feature. The same job is being done by a separate pipeline (Gemini 2.5 Pro with Google Search) so the two outputs can be compared. Do the job exactly as described here, to the same standards, with no shortcuts. Work for as long as it takes; thoroughness matters more than speed.

FILM: {{TITLE}} ({{YEAR}})

# WHAT A SCENE FACT IS
{g.CONTEXT}

Each fact becomes one card with three pieces of text: a FULL FACT (2 to 4 sentences, complete), a SHORT (at most {cfg.short_cap} characters) and a LONG (at most {cfg.long_cap} characters). Facts here are "general" cards: they are about the film, not tied to one scene. One fact per card.

# YOUR OUTPUT: NO CAP
Do not stop at 20. Return EVERY fact you find that (1) is backed by pages you actually opened, (2) passes the validation gate below, and (3) is not bad and could plausibly be used. Rank them best first. After them, list "NEAR MISSES": facts that are interesting but failed a gate (say which one and what would fix it), so a person can decide. Never pad: a fact that fails a gate goes in near misses, not the main list.

# HOW THE PIPELINE WORKS (do the same stages, in order)

STAGE 1 - SWEEP FOR CANDIDATES. Search broadly for the most interesting, specific facts. Run all of these searches (and others the film suggests); they cover making-of, casting, improvisation, locations, props, stunts, music, money, awards, reception, franchise, title origin, marketing, era, real objects and institutions, people (public/professional facts only), cut scenes, cast reunions and oral histories:
{query_block}
Trivia sites and listicles are fine as LEADS only. Wikipedia and IMDb are leads, never sources.

STAGE 2 - GO TO THE TRUSTED OUTLETS BY NAME. Do not rely on what a search engine ranks first. Search the film directly on the top outlets (Variety, The Hollywood Reporter, Deadline, EW, Vulture, The New York Times, LA Times, The Guardian, The Ringer, AV Club, IndieWire, SlashFilm, Collider, Empire, The Wrap, GQ, Vanity Fair, Rolling Stone, Newsweek, Thrillist, Polygon, NPR, BBC, Time, Esquire, People, USA Today, Screen Rant, Insider, E! Online, Den of Geek). Read every source Wikipedia cites for the film.

STAGE 3 - YOUTUBE. Search YouTube for interviews, panels, reunions and behind-the-scenes pieces. Use only videos from VERIFIED channels and rate the channel: the official channel of an A/B outlet (GQ, Variety, Vanity Fair) takes that outlet's tier; a verified studio, network or the film's own cast/crew is B; fan, recap, ranking and "hidden details" channels are C and are skipped. Read the real transcript (YouTube's transcript panel), not a summary. Never use an AI-written summary as a source.

STAGE 4 - READ STRONG ARTICLES IN FULL AND MINE THEM. For every long A/B article or transcript about the film (an oral history, a cast or crew interview, a long feature), read the whole page and pull out EVERY interesting fact, in at least two passes (first the headline facts; then the smaller specifics the first pass missed: numbers, money, a person's own words, props, locations, near-miss castings, accidents, improvised lines, things a viewer can see or do). Follow the links inside strong articles to other trusted pages. Research each mined fact like any other fact (Stage 5); that research usually turns up new strong articles, so read those too. Repeat until a round finds nothing new.

STAGE 5 - RESEARCH EACH FACT. For each candidate fact, find the pages that state it, open them, and copy one supporting passage word for word. If a page credits an outlet by name without a link ("via Thrillist", "told Variety"), find that original article and use it. Prefer the original over the page that repeated it. If a fact has fewer than two A/B outlets, look for it on the top outlets by name.

STAGE 6 - CORROBORATE. Count only DIFFERENT outlets that independently state the same specific claim. A page that repeats another outlet's claim counts as that outlet. Two pages from one outlet, or two Wikipedia articles, count as one source.

STAGE 7 - VALIDATION GATE (below). Drop or fix anything that fails.

STAGE 8 - WRITE (editorial guide below), THEN POLISH: check every short and long against the character limits, the wording rules, and the source wording.

STAGE 9 - DEDUPLICATE (the same claim in different words, or the same topic: keep the better one), then RANK by how interesting the fact is to a viewer.

# SOURCE POLICY
{sp.definitions_block()}

TIERS (the pipeline's actual split):
- TIER A (established mainstream newsrooms and major entertainment/culture publications rated "generally reliable" by Wikipedia's Perennial Sources list): {a_list}.
- TIER B (other reliable outlets: niche/specialist film and entertainment press, trade outlets, reference outlets, and entertainment sites Wikipedia rates "no consensus" that do original reporting): {b_list}.
- TIER C (leads only, never support a fact): everything in NOT AUTHORITATIVE above, plus outlets Wikipedia rates unreliable/deprecated, aggregators, wikis, fan sites, forums, blogs, content farms, press-release sites, and opinion/contributor content even on a good outlet.
- UNLISTED sites: judge them by Wikipedia's criteria (editorial oversight, fact-checking and corrections, named staff, independence). If unsure, treat as C. An unlisted page counts only if it directly quotes a named participant making the claim.

A fact PASSES if (a) a named participant is quoted by any page you can trust, or one Tier A/B page reports it with a named source (label: "PASSES: an A/B source confirms it"), or (b) 3 or more independent A/B outlets state it ("PASSES: 3+ A/B outlets state it"). Label C-only support "WEAK" (it goes in near misses), and a fact whose A/B sources do not actually state it "NOT CONFIRMED" (near misses). When two sources disagree on a number or detail, look for more sources, prefer the higher tier or the person who was there, and attribute it ("According to Will Ferrell, it cost $10,000").

# VALIDATION GATE (a fact is only in the main list if it passes ALL of these)
1. SUPPORTED: every name, number, date, place, quote and causal claim is stated by a page you opened. Hedges are kept ("reportedly", "I think"). Overstating = fail.
2. SPOILER-FREE (binary, conservative): nothing about the plot, a twist, how the film or a storyline ends, who lives/dies/wins/loses/betrays, a secret identity, or a cameo whose reveal spoils the story. Behind-the-scenes facts are fine only if they say nothing about what happens in the story. If unsure, it fails.
3. MATURITY: all text must be G-rated regardless of the film's rating. No profanity (even censored, even in quotes), no sexual or graphic wording. Rephrase neutrally or drop.
4. PROPRIETY: fails on gossip or interpersonal drama; lawsuits, arrests, charges; deaths, accidents or disasters connected to the production or talent; politics; disparaging or unsubstantiated negative claims about talent, filmmakers or studios; quoting criticism of the film ("critics called it..."); personal information; sensitive terms like cancer, passed away, diagnosed, divorce, affair, addiction, rehab. Never health, legal trouble, sexuality, religion or anything about children in the cast.
5. EDITORIAL FIT: not one of the DO-NOT-SHOW kinds below; not a credit-style card (who plays whom, "served as a producer"); the short is clear to someone who has not seen the film.
6. FILM-SPECIFIC: about THIS film, not a generic industry practice.
7. SHORT AND LONG ARE ENTAILED: neither claims anything beyond what the sources verify.
8. NO COPYING: never copy a run of 8 or more words in a row from a source. Short quotes of a named person are fine in quotation marks.
9. QUANTITY WORDING: do not add or change counts or quantity words ("one-word", "three days") that no source uses.
10. SUPERLATIVES ("first", "only", "longest", "never", "every", "record"): need a second independent source; popular tellings are often wrong on dates and places.
11. NOT MISLEADING and NOT DATED: no claim that is true but implies something false (a coined phrase credited as the official name, two things merged), and no unsourced "currently/still/rank" figures that may be out of date.
12. NO UNANSWERED WHY: if a fact raises a question (why did they leave? why was it cut?) the card must answer it. If the reason cannot be found, drop the fact.
13. EVERY fact in the main list has at least one A/B source or a named participant quoted (see PASSES above).

# DO NOT SHOW
{g.exclusions_block()}

# WHAT MAKES A FACT INTERESTING
{g.interest_block()}

# THE WRITING GUIDE (follow every rule; the before/after pairs are real reviewer corrections)
{g.writer_block()}

Wording examples used in the final copy-edit:
{g.wording_examples()}

# OUTPUT FORMAT
First, a summary: total passing facts, how many PASS by each label, how many near misses, articles read in full, YouTube videos used, number of searches run, and any step you could not do (say so plainly; never fill a gap from memory).

Then each passing fact, best first:

N. [PASSES: an A/B source confirms it | PASSES: 3+ A/B outlets state it]
Full fact: ...
Short (n chars): ...
Long (n chars): ...
Category: (actor, music, location, set_design, filming, historical, costume/prop)
Top sources (best tier first, at most two): outlet, URL, tier (A/B), and a one-line word-for-word passage from the page that states the fact.
Outlets that independently state it: n (list them)
Found by: (search | outlet search | Wikipedia reference | YouTube | read in full from an article | followed a citation)
Flags a human reviewer should check: (anything uncertain, otherwise "none")

Then NEAR MISSES in the same format, each with the gate it failed and the fix.

Finally list: facts you dropped and why, anything you could not verify, and anything about these instructions that was unclear.

Count the characters in every short and long and rewrite any that are over the limit before you answer.
"""
Path(sys.argv[1]).write_text(P)
print(len(P))
