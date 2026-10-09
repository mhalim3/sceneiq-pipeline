# SceneIQ source policy

Single source of truth: `sceneiq/source_policy.py`. Everything else reads it.

## What counts as authoritative
- PRIMARY: the person or record is the source itself. A named filmmaker, cast member, crew member or studio says it (a quoted interview, commentary track, press kit, or their own post), or an official record states it (permit databases, credits, music registries, court or government records).
- ESTABLISHED EDITORIAL: a publication with editorial standards reports it and names who said it or what document it comes from — trade press, major newspapers, established film magazines (see the outlet list).
- NOT AUTHORITATIVE (leads only, never support a card): listicles, trivia roundups and 'facts you didn't know' posts; aggregators; wikis, fan sites, forums and film databases (IMDb, TV Tropes, Fandom, Wikipedia); blogs and marketing sites (test-prep, tour, merchandise, auction listings); and any page that states a claim without naming where it came from.

## Rules
- A page that repeats another outlet's claim counts as that outlet, never as a second source.
- A specific number, quote, or 'first/only' claim must be backed by a PRIMARY source or an ESTABLISHED EDITORIAL source. A blog or unlisted site alone cannot carry it.
- A page from an unlisted domain can support a card only if it directly quotes a named participant making the claim (then it is PRIMARY). Otherwise it is a lead.
- When several pages state the same claim, cite the most authoritative one.

## Thresholds
- Unlisted domains count as support only as PRIMARY (they quote a named participant): `True`
- Relaxed mode: 1 primary OR 1 qualifying editorial. Strict mode: 1 primary OR 2 independent qualifying editorial.
- A card that misses the minimum is flagged low confidence in annotation mode, rejected otherwise.
- If every cited source is unlisted or C-tier, one targeted retry searches: Variety, Hollywood Reporter, Collider, Guardian, Business Insider, Vulture, Entertainment Weekly.

## Domain lists
- **A (domain authorities, plus any .gov):** `ascap.com`, `ascmag.com`, `bmi.com`, `criterion.com`, `filmla.com`, `nyc.gov`, `theasc.com`
- **B (established editorial):** `avclub.com`, `bbc.co.uk`, `bbc.com`, `businessinsider.com`, `collider.com`, `deadline.com`, `empireonline.com`, `ew.com`, `gq.com`, `harvardlawrecord.org`, `hollywoodreporter.com`, `indiewire.com`, `insider.com`, `latimes.com`, `newyorker.com`, `npr.org`, `nytimes.com`, `polygon.com`, `rollingstone.com`, `screenrant.com`, `slashfilm.com`, `slate.com`, `theguardian.com`, `theringer.com`, `thewrap.com`, `vanityfair.com`, `variety.com`, `vogue.com`, `vulture.com`, `wwd.com`
- **C (leads only, never support):** `afi.com`, `bfi.org.uk`, `boards.ie`, `buzzfeed.com`, `fandom.com`, `gamefaqs.gamespot.com`, `imdb.com`, `mentalfloss.com`, `moviechat.org`, `pinterest.com`, `quora.com`, `ranker.com`, `reddit.com`, `tcm.com`, `themoviedb.org`, `tmdb.org`, `tvtropes.org`, `wikia.com`, `wikimedia.org`, `wikipedia.org`
- **Never fetched:** `facebook.com`, `fandom.com`, `instagram.com`, `pinterest.com`, `reddit.com`, `tiktok.com`, `twitter.com`, `wikia.com`, `wikimedia.org`, `wikipedia.org`, `x.com`
- **Video (needs transcript):** `vimeo.com`, `youtu.be`, `youtube.com`

## Where it is used
tiers.py (domain tiers) · validate.py (judge prompt + emission rule) · assemble.py (writer prompt) · research.py (research prompt, retry query) · orchestrator.py (weak-source retry)
