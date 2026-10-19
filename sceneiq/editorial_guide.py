"""SceneIQ editorial guide — WHAT good cards look like and WHY.

Single source of truth for the reviewer's (Lia's) editorial standards. Each principle
states the rule, the reason behind it (so the model can apply it to cases the examples
don't cover), and before/after examples taken from real reviewer rewrites.
Consumed by: the writer prompt (assemble.py), the interest judge (interest.py), and
fact selection. Rendered to docs/EDITORIAL_GUIDE.md for humans.

Example facts are from reviewed films; they show STYLE and judgment, not content to copy.
"""

from __future__ import annotations

CONTEXT = """\
WHO READS THESE: a person watching a movie on a TV across the room who has just pressed \
pause. A small card appears for a few seconds. They are a general viewer, not a film buff, \
and may not have seen this movie recently. They will read the headline (the "short") in \
about two seconds; many will never tap for the "long". A card succeeds if the viewer thinks \
"huh, I didn't know that" and would repeat it to the person next to them."""

# Each principle: (id, rule, why, [(before, after, note)])
PRINCIPLES = [
    ("prestige",
     "When a true fact can be told through a bigger, more widely-known name, place or "
     "person, tell it that way. Lead with the most famous element.",
     "People find a fact more interesting when it involves things they already know and "
     "care about. Harvard and Stanford are famous, high-prestige schools; USC and UCLA are "
     "not (to a general audience). A fact about Stanford refusing to be named is something "
     "everyone has an opinion on; a fact about a fictional 'CULA' stand-in for USC is not. "
     "The same holds for actors (Brad Pitt almost cast beats a lesser-known actor "
     "considered), studios, directors and locations. Familiarity makes the surprise land. "
     "This is about choosing which TRUE angle to lead with, never about adding claims.",
     [("USC and Stanford refused their names for Legally Blonde, so Elle Woods attended "
       "'CULA'.",
       "In the original novel, the lead character attended USC before Stanford Law. "
       "Stanford refused to let the film use its name, so the story moved to Harvard.",
       "lead with Stanford/Harvard, drop the obscure USC/CULA detail"),
      ("Brad Pitt was almost the lead, Jordan Belfort, with Ridley Scott directing.",
       "Brad Pitt was almost cast as the lead character, Jordan Belfort.",
       "the famous name carries the hook; the director detail is secondary")]),
    ("hook",
     "Put the single most surprising, specific detail in the first words of the short. "
     "Name the odd, concrete thing itself rather than describing it.",
     "The short is often the only thing read. If the memorable part (a funny store name, "
     "a startling admission, a number) is buried or replaced by a generic description "
     "('an intimates shop's stockroom'), the card loses the exact detail a viewer would "
     "repeat to a friend. Ask: what is the one detail someone would text a friend?",
     [("The lead's iconic pink bunny costume was created in an intimates shop's stockroom.",
       "Elle's iconic pink bunny costume was created at a store called 'Trashy Lingerie'.",
       "the store's name is the hook"),
      ("Christina Applegate turned down the lead role of Elle Woods, hesitant to play "
       "another stereotypical blonde.",
       "Christina Applegate says she regrets turning down the role of Elle Woods.",
       "a person's regret is a stronger, more human hook than her reason for passing"),
      ("The lead's red Bottega bag was originally her mother's in the prequel series Elle.",
       "The new prequel series 'Elle' shows her red bag was originally her mother's.",
       "lead with what is new and newsworthy (the new series), not the object")]),
    ("story_over_description",
     "Prefer something that HAPPENED to or was DONE BY a named person (an accident, an "
     "improvisation, a near-miss, a regret, an admission) over a description of a design "
     "choice or intention.",
     "Stories with a person and a turn of events are retellable. 'The designer contrasted "
     "two color palettes' is abstract: a viewer learns nothing surprising and has nothing "
     "to say about it. Reviewers rated design-intention cards 1-3 out of 5 and "
     "story cards 5 out of 5.",
     [("The production designer intentionally contrasted the lead's colorful sorority house "
       "with her muted Harvard dorm. (rated 1/5)",
       "(drop it, or find the concrete story behind it)", "design intention, nothing surprising"),
      ("Jonah Hill's prosthetic teeth broke when co-star Jon Bernthal accidentally punched him. "
       "(rated 5/5)", "(keep as is)", "a person, an accident, a vivid result")]),
    ("not_obvious",
     "Do not state what the viewer can already see or work out from the movie, unless the "
     "card adds a twist the viewer would not know.",
     "A card has to open a curiosity gap. 'Elle scored a 179 on her LSAT' is stated in the "
     "film itself, so it is not insider information, and the card was rated boring. A bare "
     "'the license plate reads 22BLONDE' only restates what is on screen. If you keep such "
     "a fact, the card must add something that is not visible (a nod, a reason, a "
     "'blink-and-you'll-miss-it' pointer).",
     [("The lead character, Elle Woods, scored a near-perfect 179 on her LSAT. (rated 2/5)",
       "(only keep with an outside-the-film detail, e.g. a source saying why 179)",
       "already stated in the movie"),
      ("The lead character's convertible sported a vanity license plate reading '22BLONDE'. "
       "(rated 1/5)",
       "Elle's Porsche plate reads '22BLONDE', a detail that's easy to miss.",
       "at minimum point out what is easy to miss; better, add a sourced reason")]),
    ("name_people",
     "On first mention, name the character AND (where it helps) the actor: 'the lead "
     "character, Elle Woods,' 'Jordan Belfort (Leonardo DiCaprio)'. Never a bare 'the "
     "lead', 'the protagonist' or a surname alone. If a person is not a household name, add "
     "a few words saying who they are.",
     "The card is read with no context. 'The lead' forces the viewer to decode who is meant, "
     "and a surname alone ('Davis') does not say whether it is a character or an actor. "
     "Names are also what make a fact feel real and shareable. Identifying an unfamiliar "
     "person ('Tommy Chong, of Cheech and Chong') lets the viewer feel the surprise "
     "instead of wondering who that is. Use the full name once, then short forms.",
     [("Davis based his character on former President George W. Bush.",
       "Matthew Davis based his character, Warner Huntington III, on former President "
       "George W. Bush.", "full actor and character names"),
      ("The film's memoir was inspired by actor Tommy Chong, the protagonist's prison "
       "cellmate.",
       "Jordan Belfort wrote his memoir because his cellmate Tommy Chong, of Cheech and "
       "Chong, encouraged him to.", "says who Chong is and who did what to whom")]),
    ("plain_words",
     "Write the way you would tell a friend. Simple, concrete, active verbs. No stiff "
     "or awkward phrasing, no passive constructions that hide who did what.",
     "The card is read quickly on a TV. Stilted wording ('came away with numerous red "
     "marks', 'was added by DiCaprio', 'was literally written as') makes the reader work "
     "and drains the fun. Plain verbs ('gave her tons of paper cuts', 'smother his feet in "
     "dog food', 'really did eat a live goldfish') carry the vividness.",
     [("Margot Robbie came away with numerous red marks.",
       "Shooting the bed scene with fake money gave Margot Robbie tons of paper cuts.", ""),
      ("The real co-founder who inspired Jonah Hill's character actually ate a live goldfish.",
       "The real Danny Porush really did eat a live goldfish, just like in the film.",
       "name the person and keep the vivid verb"),
      ("Emmett was literally written as 'the Luke Wilson Character'.",
       "Emmett was written for Luke Wilson.", "state it plainly")]),
    ("long_stands_alone",
     "The full fact (the longDescription field; a shorter 140-character long is cut from it later) "
     "must restate the short's claim (naming the people again) and then add ONE new specific "
     "that sharpens the hook: a quote, a second party, a number, a reason. It must read "
     "correctly on its own.",
     "The long appears when someone taps for more, possibly after the short has scrolled "
     "away. It cannot assume the reader remembers the short or knows it is 'a continuation'. "
     "A long that only adds a loosely related detail confuses; a long that only repeats "
     "adds nothing. Restate, then deepen.",
     [("(short) Elle's bunny costume was fitted at a store called 'Trashy Lingerie'. "
       "(long, before) Costume designer Sophie de Rakoff had the famous ensemble built and "
       "fitted for the lead at Trashy Lingerie on La Cienega.",
       "Elle's bunny costume was created by designer Sophie de Rakoff, who said they did the "
       "fitting at Trashy Lingerie 'in the back room with all the bras and the weird people "
       "wandering around'.", "restates who/what, then adds the quote")]),
    ("precision",
     "Say exactly who did what, to whom, and why — only as far as the source says. Do not "
     "turn 'after' into 'because', do not blame a person for something a source attributes "
     "to someone else, and separate 'happened on set' from 'appears in the film'.",
     "A wrong or overstated card is worse than no card. Real review questions: was a "
     "four-hour cut blocked by the studios or withheld by the director? is the punch that "
     "broke the teeth actually in the final film? does the card report a real person's view "
     "of the film's accuracy the way she actually put it? Only claim what a source states.",
     []),
    ("propriety",
     "No alcohol or drug framing, and nothing negative or demeaning about an actor, "
     "filmmaker or studio. Rephrase to the neutral version of the same fact.",
     "These cards appear on a family-brand surface; they are an enrichment feature, not a "
     "place for gossip or controversy. The same fact can usually be told neutrally.",
     [("The 'Bend and Snap' was a drunken, spur-of-the-moment creation by the screenwriters.",
       "The 'Bend and Snap' was dreamed up by the screenwriters on a night out at a bar.",
       "drop 'drunken'"),
      ("Studio execs thought Reese Witherspoon was 'repellent' for Elle Woods.",
       "Studio execs doubted Reese Witherspoon could play bubbly Elle after her role as "
       "Election's ruthless Tracy Flick.", "frame by the roles, not an insult")]),
    ("consistency_and_setup",
     "A viewer sees several cards in one sitting: cards must not contradict each other. "
     "Context-setting facts about the title itself (what it is based on; who worked together "
     "again) are welcome and best shown early.",
     "Contradictory cards destroy trust (one card says Reese was the only person considered, "
     "another says several actresses were offered the role). A 'based on the novel by Amanda "
     "Brown' card orients the viewer and makes later cards make sense.",
     []),
    # --- Added from the reviewer's comments on the Step Brothers / Matilda round (Oct 2026) ---
    ("film_title_is_not_needed",
     "Do not open the short or long by naming the movie, and never write 'In <title>,' or "
     "'<Title>' as a possessive when the title is also a character's name (Matilda). The "
     "viewer is already watching it. Name the film only when the fact is about the film "
     "itself or compares it to another.",
     "Cards are read mid-movie. Introducing the title wastes the few characters of a short "
     "and is confusing when the title is a person: 'Matilda's iconic cake scene' reads as if "
     "Matilda owns it. It has come up on several facts.",
     [("In Matilda, the Chokey's terrifying nails were actually made of harmless rubber.",
       "The Chokey closet's terrifying nails were actually made of harmless rubber.",
       "no title, and a few words say what the Chokey is"),
      ("Step Brothers' Richard Jenkins once worked for co-star John C. Reilly's father.",
       "Actor Richard Jenkins (Dale's Dad) worked for co-star John C. Reilly's dad.",
       "drop the title, name the role instead")]),
    ("famous_names_need_no_title",
     "For household names (Danny DeVito, Brad Pitt, Leonardo DiCaprio, Will Ferrell) drop "
     "'the actor' / 'the director'. Keep a short descriptor for people whose names are not "
     "universally known (co-stars like John C. Reilly, crew, minor cast).",
     "Saying 'director Danny DeVito' to a general audience adds length without helping; saying "
     "just 'Reilly' to the same audience does not tell them who that is.",
     [("Director Danny DeVito read 'Matilda' to his kids every night.",
       "Danny DeVito's kids' love for the book inspired him to make the film.",
       "no title for DeVito; also leads with the human angle")]),
    ("role_in_parentheses",
     "When there is room, say who a character is or which actor plays them, in parentheses: "
     "'Derek (played by Adam Scott)', 'Richard Jenkins (Dale's Dad)'.",
     "The viewer may not connect a name to a role. A few extra words let them place the fact.",
     [("Jon Hamm was one of the finalists for the role of Derek.",
       "Jon Hamm was one of the finalists for the role of Derek (played by Adam Scott).",
       "uses the spare characters to say who Derek is")]),
    ("explain_unfamiliar_terms",
     "If the fact depends on an in-film name or term (the Chokey, the Catalina Wine Mixer, "
     "Mr. Han), add a few words saying what it is.",
     "A viewer who has not tracked every name cannot decode 'the Chokey'. A short descriptor "
     "makes the fact stand alone.",
     [("The nails in Matilda's Chokey were harmless rubber.",
       "The nails in the torture closet, called the 'Chokey', were harmless rubber.",
       "says what the Chokey is")]),
    ("attribute_conflicts",
     "When sources disagree on a number or detail, corroborate with other sources, prefer the "
     "higher tier or the person who was there, and attribute it: 'According to Will Ferrell, "
     "it cost $10,000.' Mention the other figure only if there is room.",
     "Two outlets repeating different numbers makes a card wrong half the time. Attributing "
     "to the person who knows keeps it true even if the other number is also reported.",
     [("The prop testicles rubbed on the drum set cost up to $25,000.",
       "According to Will Ferrell, the prop testicles cost $10,000.",
       "Ferrell said $10,000; the director's commentary reportedly said $25,000")]),
    ("real_world_actionable",
     "If something from the film can be experienced in real life (an event, a place, a "
     "product), say that plainly in the short: 'You can buy tickets to...', 'You can visit...'.",
     "A viewer who can go do the thing will repeat the fact. Vague phrasing ('it is now a "
     "three-day festival') hides the part that matters.",
     [("The Catalina Wine Mixer began as a real event in 2015. It is now a three-day festival.",
       "You can buy tickets to the real Catalina Wine Mixer.",
       "state what the viewer can actually do")]),
    ("juiciest_detail",
     "Of the details a source gives, lead with the most vivid, specific one: the odd gift, the "
     "exact count, the quotable line. Not the generic version of it.",
     "'Shot 1.5 million feet of film' is fine; 'Kodak sent champagne' is what people repeat. "
     "'The gardener had no idea' is fine; 'the most incredible bully to her clients' is the "
     "line.",
     [("Due to improv, the first cut of 'Step Brothers' was five hours long. The production "
       "shot so much film that Kodak sent a note.",
       "Step Brothers shot 1.5 million feet of film thanks to all the improv, and Kodak sent "
       "over champagne.", "the champagne is the detail"),
      ("Pam Ferris based Miss Trunchbull on her gardener, who had no idea.",
       "Pam Ferris based Miss Trunchbull on her gardener, whom she called 'the most "
       "incredible bully to her clients.'", "quote the memorable line"),
      ("Painting on chocolate smudges made the cake scene take forever.",
       "Cinematographer Stefan Czapsky says the cake scene was shot 144 times from multiple angles.",
       "a count beats a vague 'took forever'; keep the source's unit (shots, not angles)")]),
    ("name_the_reference",
     "Name the actual thing a fact refers to (the song, the scene, the line) instead of an "
     "unexplained phrase, so the viewer knows why it matters.",
     "'Before the project was scrapped' means nothing to someone who does not know there was a "
     "rap in the movie. Naming 'Boats N Hoes' ties the fact to something they just heard. Put the name in the long, not the short.",
     [("A rap album was planned before the project was scrapped.",
       "Adam McKay says they wrote 'four or five' rough raps for a Dale and Brennan album like "
       "the movie's 'Boats N Hoes.'", "names the song")]),
    ("only_what_sources_say",
     "Use the words the source uses. Do not turn a phrase into a count ('one-word pitch' for "
     "'bunk beds', which is two words) or add a motive or a sequence the source does not give.",
     "A reviewer caught 'one-word pitch' for 'bunk beds'. The wording came from a paraphrase "
     "that no source actually used. Quote the phrase itself instead.",
     [("Sony greenlit Step Brothers based on a one-word pitch: 'bunk beds'.",
       "Step Brothers grew from a one-line 'bunk beds' joke Will Ferrell and John C. Reilly "
       "made while working on Talladega Nights.", "describes what the sources say")]),
    ("short_is_only_the_hook",
     "The short is just the hook: one clean claim, with no extra clause and no reference the "
     "viewer would have to look up. Put the explanation, the quote, or the named reference in "
     "the long.",
     "A reviewer found 'like Boats N Hoes' confusing in a short, and wanted the cake short left "
     "as just 'almost cut'. The short has about 80 characters; every extra clause makes it harder "
     "to read across a room.",
     [("The cake scene was almost cut, but eating was Bruce's 'superpower.'",
       "The iconic chocolate cake scene was actually almost cut!", "the short is only the hook"),
      ("A full Dale and Brennan rap album was planned, like 'Boats N Hoes.'",
       "A full Dale and Brennan rap album was planned.", "the named song belongs in the long")]),
    ("answer_the_why",
     "If a fact raises a question (why did they leave? why was it cut?), the card must answer it. "
     "The reason is the interesting part. If the sources do not give the reason, look for it "
     "before writing; if it cannot be found, do not write the card.",
     "'Mothers took small children out of the first screening, and the team was depressed' was "
     "rejected: the team being depressed is not interesting, why the mothers left is.",
     [("Some mothers took small children out of the first screening, and the team was 'very depressed.'",
       "At the first screening, some mothers walked out with their small children because "
       "(the reason the source gives).", "the reason is the payoff")]),
    ("numbers_need_a_benchmark",
     "A bare number means little unless the viewer can tell if it is big. Add the comparison "
     "the source gives (a normal length, the final version, the budget) or a ratio that follows "
     "from numbers the sources state. If no comparison exists in the sources, find one or lower "
     "the fact's priority.",
     "'The first script was 180 pages' does not land unless you know a normal script is 90-120 pages. "
     "The reviewer suggested the original being about 1.5x the final script, and the 8 or 9 "
     "rewrites it took to cut it down.",
     [("The first draft of the script was a massive 180 pages long.",
       "The original Step Brothers script was almost 1.5x as long as the final one and took 8 or 9 "
       "rewrites to cut down.", "gives the viewer a benchmark")]),
    ("do_not_overstate",
     "Describe what the source describes, no bigger. Do not upgrade a part to the whole "
     "('improvised musical' for a commentary track with some songs), and do not state a hedged "
     "figure as certain ('I think it might have been 144 shots'). Keep the source's own unit "
     "and attribute the hedge ('reportedly', 'says').",
     "A fact-check found the DVD commentary is mostly spoken commentary with musical moments, not "
     "a full musical. The 144 figure was a guess in the interview and the unit was shots, not angles.",
     [("The 'Step Brothers' DVD commentary is an improvised musical.",
       "The 'Step Brothers' DVD commentary includes songs the cast improvised on the spot.",
       "matches what the commentary actually is"),
      ("The cake scene was shot from 144 different angles.",
       "The chocolate cake scene was shot 144 times from multiple angles.", "the source's unit")]),
    ("name_the_specific_thing",
     "Name the specific place, brand or person when the source does, rather than a generic "
     "stand-in ('a chef friend'). Add nothing the source does not say about it.",
     "'A chef friend baked the cake, the Magnolia kind he loves' left out the one detail worth "
     "knowing (the famous bakery) and added a preference nobody stated.",
     [("A chef friend of Danny DeVito baked the cake, the Magnolia kind he loves.",
       "Danny DeVito's friend, who works at the famous Magnolia Bakery, baked the iconic "
       "chocolate cake.", "the specific, sourced detail")]),
    ("cast_contrast_in_plain_words",
     "When actors say a performer is the opposite of their character, say it directly and name "
     "the character: 'Cast say Pam Ferris is the opposite of her mean Trunchbull character.' "
     "Do not describe the actor as 'Trunchbull's actress'.",
     "The reviewer rewrote it so the contrast is the first thing a viewer reads.",
     [("Mara Wilson says terrifying Trunchbull's actress is 'the kindest' person.",
       "Cast say Pam Ferris is the opposite of her mean Trunchbull character.", "the contrast first")]),
    ("stunts_name_the_stunt_and_the_risk",
     "For a stunt, say which stunt it was and how it was done, and lead the long with any injury "
     "or near-miss a named person describes.",
     "'The pigtail swing stunt used wires, fake braids and a crane. Pam Ferris said it nearly took "
     "her finger off' is the version the reviewer wrote.",
     [("The pigtail swing used wires through fake braids and a crane.",
       "The pigtail swing stunt used wires through fake braids and a crane. Pam Ferris said it "
       "nearly took her finger off.", "the near-miss is the new specific")]),
    ("easter_eggs_in_plain_words",
     "Say plainly what the viewer can spot: 'the real Catalina Island is visible in the "
     "background', not 'seen in the background' with no subject.",
     "A trailing fragment ('seen in the background') forces the reader to rebuild the sentence.",
     [("The Catalina Wine Mixer was not filmed on Catalina, seen in the background.",
       "During the Catalina Wine Mixer, the real Catalina Island is a visible easter egg.",
       "a complete sentence with the subject")]),
]

# Kinds of fact SceneIQ does NOT show, from the reviewer's comments on the five-title
# review (What Men Want, ATL). Each: (id, rule, why).
EXCLUSIONS = [
    ("cameos",
     "No fact whose point is that a known person appears in the film when the cameo is "
     "obvious (cameos, 'also appears', celebrity or athlete appearances, a real person "
     "playing themselves). A low-key cameo that most viewers would miss IS fine to show.",
     "Viewers spot obvious cameos on their own, so telling them first removes the fun; a "
     "cameo they would not notice is a discovery worth sharing."),
    ("cast_credits",
     "No fact that only says who plays whom, who else is in the cast, or an actor's other "
     "roles. Casting STORIES are fine: who was almost cast, who turned the part down, how "
     "someone won the role.",
     "The cast is already shown in each scene, so a credit tells the viewer nothing new."),
    ("other_film",
     "The fact must be about THIS film. Do not lead with another film's story. (Why an actor "
     "chose a different movie over this one, or what the director made next, is fine when it "
     "explains something about this film.)",
     "A viewer watching this film wants to learn about this film."),
    ("unflattering",
     "Nothing that makes a real person look bad: being fired or nearly fired, being late, "
     "conflicts, rudeness, failures, scandals. This holds even when the person tells the "
     "story themselves.",
     "It is a family-brand enrichment surface and the reviewer called such a card 'not a good look'."),
    ("off_topic",
     "No fact about a person's private life, diet, hobbies or favorite places unless it ties "
     "into this film: it was happening at the time of filming, or it contrasts with or "
     "explains the character the person plays (a lead actor or star's real personality "
     "against their role, a habit visible on screen, whether the role is typecasting, a "
     "romance during the shoot). Include drama only if it is not political, not about "
     "breaking a law, is 100% confirmed (never rumor, gossip or speculation) and does not "
     "damage the person's career or reputation.",
     "A private-life fact is only interesting to a viewer of this film when it connects to the film."),
    ("plain_info",
     "No plain reference facts: content rating, release date, runtime, home-video extras, "
     "soundtrack track lists, who composed or directed (with no story), filmography lists. "
     "Money facts (budget, box office, pay) are fine only with a surprising comparison or "
     "a named source.",
     "A fact must open a curiosity gap; reference data does not."),
    ("standalone",
     "The short must be interesting and make sense with NO knowledge of the plot. Do not rely "
     "on unexplained in-film devices ('his inner thought', 'this reboot', 'the psychic'). "
     "Say what the film or thing is, then the hook. If the short only works after reading the "
     "long, rewrite it or drop the fact.",
     "The short is often the only thing read, on a card that appears out of context."),
]


FAVORED = (
    "Kinds of fact viewers like most (use them to choose the angle to lead with and to rank; "
    "never invent): an impressive money figure (box office, budget against gross, or an actor's "
    "pay when it is striking); a memorable comment from the director or cast about the film; "
    "easter eggs; how something was filmed; how an actor prepared for a role; a lead actor's real "
    "life or personality set against their character, a habit visible in their scenes, "
    "typecasting, or a confirmed romance during filming.")


def exclusions_block() -> str:
    """The exclusion rules as prompt text (writer, candidate filter, judge, ranker)."""
    lines = ["DO NOT SHOW these kinds of fact:"]
    for eid, rule, why in EXCLUSIONS:
        lines.append(f"- {eid.upper()}: {rule} (Why: {why})")
    return "\n".join(lines)


# What a 5, a 3 and a 1 look like, from real reviewer ratings.
RATING_ANCHORS = """\
RATING ANCHORS (reviewer's 1-5 scale for how interesting a card is):
5 = a concrete story about named, known people with a surprising turn. Examples: "Reese \
Witherspoon's contract let her keep all of Elle Woods' outfits." / "Jon Bernthal accidentally \
punched Jonah Hill and broke his prosthetic teeth, and Scorsese kept filming." / "The \
novel's manuscript was picked from a slush pile because it was written on pink paper."
4 = interesting, but the hook is buried or the wording is clumsy.
3 = true and mildly interesting, but not memorable. Example: the costume designer chose \
the pink courtroom dress to show Elle's 'most true self' (describes an intention).
1-2 = describes a design choice, repeats what is on screen, or a plain statistic. Examples: \
the production designer contrasted the sorority house with the dorm; the license plate \
reads 22BLONDE; Elle scored a 179 on her LSAT."""


# Principles about WHICH facts are interesting. Left out of the writer's guide while the
# 1-5 interest scale is being redefined; the writer is only asked to PHRASE facts well.
_SELECTION_PRINCIPLES = {"story_over_description", "not_obvious"}


def wording_examples(limit: int = 24) -> str:
    """Before -> after pairs from reviewer edits, for the final copy-edit prompt."""
    ids = {"film_title_is_not_needed", "famous_names_need_no_title", "role_in_parentheses",
           "explain_unfamiliar_terms", "attribute_conflicts", "real_world_actionable",
           "juiciest_detail", "name_the_reference", "only_what_sources_say",
           "easter_eggs_in_plain_words", "short_is_only_the_hook", "answer_the_why",
           "numbers_need_a_benchmark", "do_not_overstate", "name_the_specific_thing",
           "cast_contrast_in_plain_words", "stunts_name_the_stunt_and_the_risk"}
    out = []
    for pid, rule, _why, ex in PRINCIPLES:
        if pid in ids:
            out.append(f"* {rule}")
            for b, a, note in ex:
                out.append(f'    BEFORE: "{b}"\n    AFTER:  "{a}"' + (f"   ({note})" if note else ""))
    return "\n".join(out[: limit * 2])


def writer_block() -> str:
    """Principles for the card writer (rule + reason + examples)."""
    parts = ["EDITORIAL GUIDE — how to phrase a card and why.", CONTEXT, ""]
    for pid, rule, why, ex in PRINCIPLES:
        if pid in _SELECTION_PRINCIPLES:
            continue
        parts.append(f"* {pid.upper().replace('_', ' ')}: {rule}")
        parts.append(f"  WHY: {why}")
        for b, a, note in ex:
            parts.append(f'  - BEFORE: "{b}"  AFTER: "{a}"' + (f"  ({note})" if note else ""))
    parts += ["", exclusions_block(), "", FAVORED]
    return "\n".join(parts)


def interest_block() -> str:
    """The parts of the guide that decide how interesting a fact is."""
    keep = {"prestige", "hook", "story_over_description", "not_obvious"}
    parts = [CONTEXT, ""]
    for pid, rule, why, _ in PRINCIPLES:
        if pid in keep:
            parts.append(f"* {pid.upper().replace('_', ' ')}: {rule} WHY: {why}")
    parts.append("")
    parts.append(RATING_ANCHORS)
    return "\n".join(parts)


def render_markdown() -> str:
    lines = ["# SceneIQ editorial guide", "",
             "Source of truth: `sceneiq/editorial_guide.py`. Written from the reviewer's "
             "comments on the Wolf of Wall Street and Legally Blonde review docs. Each "
             "principle gives the rule, the reason behind it, and before/after examples.",
             "", "## Context", CONTEXT, ""]
    for pid, rule, why, ex in PRINCIPLES:
        lines += [f"## {pid.replace('_', ' ').title()}", f"**Rule.** {rule}", "",
                  f"**Why.** {why}", ""]
        for b, a, note in ex:
            lines.append(f"- Before: {b}\n  - After: {a}" + (f"  _({note})_" if note else ""))
        lines.append("")
    lines += ["## What we do not show"]
    for eid, rule, why in EXCLUSIONS:
        lines += [f"- **{eid.replace('_', ' ')}.** {rule} _{why}_"]
    lines += ["", "## Rating anchors", RATING_ANCHORS, ""]
    return "\n".join(lines)
