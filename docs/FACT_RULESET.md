# Title-Level Fact Ruleset (v1 — stakeholder feedback, Sep 2026)

Authoritative content rules for title-level facts. The pipeline prompts
(title_sweep, assemble, validate) implement a distilled version of these;
this file is the source of truth for reviewers.

1. Facts must be directly connected to the movie.
2. Facts must be interesting, surprising, unusual, memorable, or informative.
3. Facts should be entertaining enough to hold the audience's attention.
4. Facts must be understandable on their own.
5. Facts should make sense at any point during the movie.
6. Facts may cover production, filming, casting, main characters, title-related elements, technical effects, or cultural impact.
7. Production facts may include filming locations, production length, delays, reshoots, title changes, set construction, and creative decisions.
8. Actor facts may include preparation, training, personal reactions, working experiences, previous roles, or relationships with other cast members.
9. Facts about the movie's main characters should be included when they are interesting.
10. Character facts may cover inspirations, casting decisions, actor connections to the role, preparation, major character development, or unusual character traits.
11. Director, writer, producer, and creator comments may be included.
12. Casting and development history may include actors who were considered, roles that changed, actors who dropped out, or how the project developed.
13. Title-related locations, objects, organizations, creatures, events, or concepts may be included.
14. Scene-specific facts may be included when they directly relate to the title or central concept.
15. Facts about the movie's central setting may include how it was built, recreated, filmed, or used.
16. Technical facts may include CGI, motion capture, stunt work, practical effects, camera technology, set extensions, makeup, costumes, and production design.
17. Do not include financial information such as budget, box office, profitability, opening weekend, or financial comparisons.
18. Audience and critical reception may only be included when the contrast is interesting or meaningful.
19. Do not include ratings or review scores by themselves unless they support an interesting reception contrast.
20. Awards and nominations should only be included when they are notable, surprising, or connected to an important achievement.
21. Do not include basic facts that are not especially interesting, such as only listing the release year, director, studio, or main cast.
22. Avoid simple plot summaries.
23. Avoid ordinary cast lists unless the casting fact is unusual or meaningful.
24. Avoid minor scene trivia unless it is especially interesting or directly connected to the title.
25. Avoid facts that depend on exact scene order.
26. Avoid vague or ambiguous wording.
27. The short fact must clearly explain the interesting point immediately.
28. The short fact should sound lively, natural, and fun—not dry, robotic, or overly formal.
29. Use playful wording when appropriate, but never let the wording hide the actual fact.
30. Avoid empty phrases such as "was a wild one," "had an interesting experience," or "was a big deal" unless the statement explains exactly why.
31. The long explanation should add context and make the fact feel more entertaining.
32. Every fact should be verifiable through reliable sources.
33. Facts should explain why the information is interesting, surprising, unusual, or important.
34. Use a balanced mix of production, actor, character, title-related, director, and technical facts.
35. Do not force a category into the list if it does not produce an interesting fact for that movie.

Format: shortVersion = concise, direct, attention-grabbing, explains the
specific point immediately. longDescription = one or two sentences adding
context and showing why it is interesting (no longer a verbatim echo of the
short). Style example:
  SHORT: Kevin Bacon finished his entire role in just six days.
  LONG: Bacon's time on set was practically a blink-and-you'll-miss-it
  appearance—he completed all of his work in only six days, an unusually
  short schedule for a recognizable supporting actor.

Scope note: title-level only for now — every card must be showable at any
point in the film. Scene-scoped generation remains in the codebase behind
config.title_level_only for Phase 3+.
