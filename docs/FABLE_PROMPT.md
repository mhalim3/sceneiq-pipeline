# SceneIQ in one prompt (for comparing a single model against the pipeline)

Replace {TITLE} and {YEAR}, then paste everything below the line. The model needs web search and the
ability to open pages. If it cannot open pages, it must say so instead of guessing.

---

You are building "Scene Facts" for the movie {TITLE} ({YEAR}). A viewer pauses the movie and a small card appears for a few seconds. The viewer is a general audience member on a TV across the room, not a film buff. A card succeeds if they think "huh, I didn't know that" and would repeat it to the person next to them.

Produce the 20 best facts about this film, ranked best first. Every fact must be backed by real pages you actually opened. Never invent a fact, a quote, a URL, or a source. If you cannot support a fact, leave it out.

## How to work (do these steps in order)

1. SEARCH BROADLY. Run many searches: interesting facts, behind the scenes, how it was filmed, casting stories, improvisation, props and costumes, music, locations, director and cast interviews, box office versus budget, deleted scenes, easter eggs, anniversary oral histories. Collect candidate facts.
2. READ THE STRONG ARTICLES IN FULL. When you find a long article from a trusted outlet (an oral history, a cast or crew interview, a long feature), open it and read the whole page. Pull EVERY interesting fact out of it, not just the one that matched your search.
3. FOLLOW THE CITATIONS. If a page says "told Thrillist", "via Variety", or "from a Coming Soon interview", find that original article yourself and use it. Prefer the original over the page that repeated it.
4. CORROBORATE. For each fact, look for other independent trusted outlets that state the same specific claim. Count only different outlets. A page that repeats another outlet's claim counts as that outlet, not a second source.
5. VERIFY. For each fact, copy one supporting passage word for word from a page you opened. If you cannot find the passage on the page, drop or reword the fact.
6. WRITE, THEN CHECK THE WORDING AGAINST THE SOURCE. Use only what the sources say. Do not turn a phrase into a count or add a motive or sequence the source does not give.
7. REMOVE DUPLICATES and anything in the "do not show" list below. Rank what is left.

## Source tiers

- TIER A: established mainstream newsrooms and major entertainment or culture publications that Wikipedia's Perennial Sources list rates "generally reliable" (for example The New York Times, The Guardian, Los Angeles Times, Variety, The Hollywood Reporter, Deadline, The Wrap, AV Club), plus official records.
- TIER B: other reliable outlets: niche or specialist film and entertainment press, trade outlets, reference outlets, and entertainment sites Wikipedia rates "no consensus" when they do original reporting (for example Screen Rant, Collider, SlashFilm, The Ringer, Newsweek, Thrillist).
- TIER C (leads only, never support a fact): outlets Wikipedia rates unreliable or deprecated, listicles and "facts you didn't know" roundups, aggregators, wikis, fan sites, forums, film databases (IMDb, TV Tropes, Fandom, Wikipedia), blogs, content farms, press-release sites, opinion pieces and contributor content even on a good outlet, and any page that states a claim without saying where it came from.
- UNLISTED sites: judge them by Wikipedia's criteria (editorial oversight, fact-checking and corrections, named staff, independence). If unsure, treat as C.
- YOUTUBE: a video is only as reliable as the channel. The verified official channel of a Tier A or B outlet (GQ, Variety, IGN) takes that tier. Verified studio or cast and crew channels count as B. Fan, recap and "hidden details" channels are C. Use the transcript, not a summary, and never use a summary written by an AI as a source.
- A page from an unlisted site can support a fact only if it directly quotes a named participant making the claim.

A fact PASSES if (a) one Tier A or B page confirms it with a named participant quoted or solid reporting, or (b) three or more independent Tier A or B outlets state it. A fact that only has Tier C or unlisted support is WEAK; include it only if nothing better exists and label it.

When two sources disagree on a number or detail, look for more sources, prefer the higher tier or the person who was there, and attribute it: "According to Will Ferrell, it cost $10,000."

## Do not show these kinds of fact

- Obvious cameos (a known person appears). A low-key cameo most viewers would miss is fine.
- Who plays whom, the rest of the cast, an actor's other roles. Casting STORIES are fine (who was almost cast, who turned it down, how someone won the role).
- Facts mainly about another film.
- Anything that makes a real person look bad (fired, late, conflicts, rudeness, failures, scandals), even if they tell it themselves.
- Private-life facts (diet, hobbies, favorite places) unless they tie into this film. Never rumor, gossip, politics or crimes.
- Plain reference data: rating, release date, runtime, home-video extras, track lists, who composed or directed with no story. Money facts only with a surprising comparison or a named source.
- Alcohol or drug framing. Rephrase neutrally.
- Spoilers, unless the fact is safe at the start.

## Facts viewers like most

An impressive money figure (box office against budget, an actor's striking pay); a memorable comment from the director or cast; easter eggs; how something was filmed; how an actor prepared; a lead actor's real life set against their character; a confirmed romance during filming. Prefer something that HAPPENED to or was DONE BY a named person (an accident, an improvisation, a near-miss, a regret) over a description of a design. Put the single most surprising specific in the first words.

## Writing rules for each fact

Write three versions of every fact:
- FULL FACT: 2 to 4 sentences, complete, with names and numbers.
- SHORT: 80 characters or fewer. It must stand alone with no knowledge of the plot.
- LONG: 140 characters or fewer. It restates the short's claim (naming the people again) and adds ONE new specific.

Rules (each with a before and after):
1. Do not name the movie in the short or long, and never write "In <title>," or use the title as a possessive when it is also a character's name.
   BEFORE: "In Matilda, the Chokey's terrifying nails were harmless rubber." AFTER: "The Chokey closet's terrifying nails were actually made of harmless rubber."
2. Drop "the actor" and "the director" for household names (Danny DeVito, Brad Pitt, Will Ferrell). Keep a short descriptor for people whose names are not universal (co-stars, crew).
3. When there is room, identify the role in parentheses.
   BEFORE: "Jon Hamm was a finalist for Derek." AFTER: "Jon Hamm was one of the finalists for the role of Derek (played by Adam Scott)."
4. Explain in-film terms in a few words ("the torture closet, called the 'Chokey'").
5. Attribute conflicting numbers to the person who knows ("According to Will Ferrell, the prop cost $10,000").
6. If a viewer can experience it in real life, say so plainly.
   BEFORE: "The Catalina Wine Mixer is now a three-day festival." AFTER: "You can buy tickets to the real Catalina Wine Mixer."
7. Lead with the juiciest detail, not the generic one.
   BEFORE: "Step Brothers shot 1.5 million feet of film due to improv." AFTER: "Step Brothers shot so much film that Kodak sent champagne."
   BEFORE: "The gardener had no idea." AFTER: "Pam Ferris called the gardener 'the most incredible bully to her clients.'"
8. Name the actual thing a fact refers to (the song, the scene, the line): "like the movie's 'Boats N Hoes'", not "before the project was scrapped".
9. Use the source's words. Do not invent counts or wording. BEFORE: "greenlit on a one-word pitch, 'bunk beds'" (it is two words, and no source said "one-word"). AFTER: "grew from a one-line 'bunk beds' joke".
10. Write easter eggs as full sentences with a subject: "During the Catalina Wine Mixer, the real Catalina Island is a visible easter egg."
11. Plain words, active verbs, the way you would tell a friend. No stiff phrasing.
12. Do not put two facts in the same scene or restate the same claim in different words. If two facts overlap, keep the better one.

## Output

First give a one-line summary: how many facts, how many PASS, how many WEAK.

Then, for each of the 20 facts, in rank order:

N. [PASSES: an A/B source confirms it | PASSES: 3+ A/B outlets state it | NOT CONFIRMED | WEAK: C-tier only]
Full fact: ...
Short (n chars): ...
Long (n chars): ...
Top sources (best tier first, at most two): outlet, URL, tier (A/B/C), and a one-line word-for-word passage from the page that states the fact.
Outlets that independently state it: n (list them)
Found by: (search | read in full from an article | followed a citation)

Count the characters in each short and long. If one is over its limit, rewrite it before you answer.

At the end, list: (a) facts you dropped and why, (b) articles you read in full, (c) anything you could not verify.
