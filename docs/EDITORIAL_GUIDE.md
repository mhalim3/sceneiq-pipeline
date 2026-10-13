# SceneIQ editorial guide

Source of truth: `sceneiq/editorial_guide.py`. Written from the reviewer's comments on the Wolf of Wall Street and Legally Blonde review docs. Each principle gives the rule, the reason behind it, and before/after examples.

## Context
WHO READS THESE: a person watching a movie on a TV across the room who has just pressed pause. A small card appears for a few seconds. They are a general viewer, not a film buff, and may not have seen this movie recently. They will read the headline (the "short") in about two seconds; many will never tap for the "long". A card succeeds if the viewer thinks "huh, I didn't know that" and would repeat it to the person next to them.

## Prestige
**Rule.** When a true fact can be told through a bigger, more widely-known name, place or person, tell it that way. Lead with the most famous element.

**Why.** People find a fact more interesting when it involves things they already know and care about. Harvard and Stanford are famous, high-prestige schools; USC and UCLA are not (to a general audience). A fact about Stanford refusing to be named is something everyone has an opinion on; a fact about a fictional 'CULA' stand-in for USC is not. The same holds for actors (Brad Pitt almost cast beats a lesser-known actor considered), studios, directors and locations. Familiarity makes the surprise land. This is about choosing which TRUE angle to lead with, never about adding claims.

- Before: USC and Stanford refused their names for Legally Blonde, so Elle Woods attended 'CULA'.
  - After: In the original novel, the lead character attended USC before Stanford Law. Stanford refused to let the film use its name, so the story moved to Harvard.  _(lead with Stanford/Harvard, drop the obscure USC/CULA detail)_
- Before: Brad Pitt was almost the lead, Jordan Belfort, with Ridley Scott directing.
  - After: Brad Pitt was almost cast as the lead character, Jordan Belfort.  _(the famous name carries the hook; the director detail is secondary)_

## Hook
**Rule.** Put the single most surprising, specific detail in the first words of the short. Name the odd, concrete thing itself rather than describing it.

**Why.** The short is often the only thing read. If the memorable part (a funny store name, a startling admission, a number) is buried or replaced by a generic description ('an intimates shop's stockroom'), the card loses the exact detail a viewer would repeat to a friend. Ask: what is the one detail someone would text a friend?

- Before: The lead's iconic pink bunny costume was created in an intimates shop's stockroom.
  - After: Elle's iconic pink bunny costume was created at a store called 'Trashy Lingerie'.  _(the store's name is the hook)_
- Before: Christina Applegate turned down the lead role of Elle Woods, hesitant to play another stereotypical blonde.
  - After: Christina Applegate says she regrets turning down the role of Elle Woods.  _(a person's regret is a stronger, more human hook than her reason for passing)_
- Before: The lead's red Bottega bag was originally her mother's in the prequel series Elle.
  - After: The new prequel series 'Elle' shows her red bag was originally her mother's.  _(lead with what is new and newsworthy (the new series), not the object)_

## Story Over Description
**Rule.** Prefer something that HAPPENED to or was DONE BY a named person (an accident, an improvisation, a near-miss, a regret, an admission) over a description of a design choice or intention.

**Why.** Stories with a person and a turn of events are retellable. 'The designer contrasted two color palettes' is abstract: a viewer learns nothing surprising and has nothing to say about it. Reviewers rated design-intention cards 1-3 out of 5 and story cards 5 out of 5.

- Before: The production designer intentionally contrasted the lead's colorful sorority house with her muted Harvard dorm. (rated 1/5)
  - After: (drop it, or find the concrete story behind it)  _(design intention, nothing surprising)_
- Before: Jonah Hill's prosthetic teeth broke when co-star Jon Bernthal accidentally punched him. (rated 5/5)
  - After: (keep as is)  _(a person, an accident, a vivid result)_

## Not Obvious
**Rule.** Do not state what the viewer can already see or work out from the movie, unless the card adds a twist the viewer would not know.

**Why.** A card has to open a curiosity gap. 'Elle scored a 179 on her LSAT' is stated in the film itself, so it is not insider information, and the card was rated boring. A bare 'the license plate reads 22BLONDE' only restates what is on screen. If you keep such a fact, the card must add something that is not visible (a nod, a reason, a 'blink-and-you'll-miss-it' pointer).

- Before: The lead character, Elle Woods, scored a near-perfect 179 on her LSAT. (rated 2/5)
  - After: (only keep with an outside-the-film detail, e.g. a source saying why 179)  _(already stated in the movie)_
- Before: The lead character's convertible sported a vanity license plate reading '22BLONDE'. (rated 1/5)
  - After: Elle's Porsche plate reads '22BLONDE', a detail that's easy to miss.  _(at minimum point out what is easy to miss; better, add a sourced reason)_

## Name People
**Rule.** On first mention, name the character AND (where it helps) the actor: 'the lead character, Elle Woods,' 'Jordan Belfort (Leonardo DiCaprio)'. Never a bare 'the lead', 'the protagonist' or a surname alone. If a person is not a household name, add a few words saying who they are.

**Why.** The card is read with no context. 'The lead' forces the viewer to decode who is meant, and a surname alone ('Davis') does not say whether it is a character or an actor. Names are also what make a fact feel real and shareable. Identifying an unfamiliar person ('Tommy Chong, of Cheech and Chong') lets the viewer feel the surprise instead of wondering who that is. Use the full name once, then short forms.

- Before: Davis based his character on former President George W. Bush.
  - After: Matthew Davis based his character, Warner Huntington III, on former President George W. Bush.  _(full actor and character names)_
- Before: The film's memoir was inspired by actor Tommy Chong, the protagonist's prison cellmate.
  - After: Jordan Belfort wrote his memoir because his cellmate Tommy Chong, of Cheech and Chong, encouraged him to.  _(says who Chong is and who did what to whom)_

## Plain Words
**Rule.** Write the way you would tell a friend. Simple, concrete, active verbs. No stiff or awkward phrasing, no passive constructions that hide who did what.

**Why.** The card is read quickly on a TV. Stilted wording ('came away with numerous red marks', 'was added by DiCaprio', 'was literally written as') makes the reader work and drains the fun. Plain verbs ('gave her tons of paper cuts', 'smother his feet in dog food', 'really did eat a live goldfish') carry the vividness.

- Before: Margot Robbie came away with numerous red marks.
  - After: Shooting the bed scene with fake money gave Margot Robbie tons of paper cuts.
- Before: The real co-founder who inspired Jonah Hill's character actually ate a live goldfish.
  - After: The real Danny Porush really did eat a live goldfish, just like in the film.  _(name the person and keep the vivid verb)_
- Before: Emmett was literally written as 'the Luke Wilson Character'.
  - After: Emmett was written for Luke Wilson.  _(state it plainly)_

## Long Stands Alone
**Rule.** The full fact (the longDescription field; a shorter 140-character long is cut from it later) must restate the short's claim (naming the people again) and then add ONE new specific that sharpens the hook: a quote, a second party, a number, a reason. It must read correctly on its own.

**Why.** The long appears when someone taps for more, possibly after the short has scrolled away. It cannot assume the reader remembers the short or knows it is 'a continuation'. A long that only adds a loosely related detail confuses; a long that only repeats adds nothing. Restate, then deepen.

- Before: (short) Elle's bunny costume was fitted at a store called 'Trashy Lingerie'. (long, before) Costume designer Sophie de Rakoff had the famous ensemble built and fitted for the lead at Trashy Lingerie on La Cienega.
  - After: Elle's bunny costume was created by designer Sophie de Rakoff, who said they did the fitting at Trashy Lingerie 'in the back room with all the bras and the weird people wandering around'.  _(restates who/what, then adds the quote)_

## Precision
**Rule.** Say exactly who did what, to whom, and why — only as far as the source says. Do not turn 'after' into 'because', do not blame a person for something a source attributes to someone else, and separate 'happened on set' from 'appears in the film'.

**Why.** A wrong or overstated card is worse than no card. Real review questions: was a four-hour cut blocked by the studios or withheld by the director? is the punch that broke the teeth actually in the final film? does the card report a real person's view of the film's accuracy the way she actually put it? Only claim what a source states.


## Propriety
**Rule.** No alcohol or drug framing, and nothing negative or demeaning about an actor, filmmaker or studio. Rephrase to the neutral version of the same fact.

**Why.** These cards appear on a family-brand surface; they are an enrichment feature, not a place for gossip or controversy. The same fact can usually be told neutrally.

- Before: The 'Bend and Snap' was a drunken, spur-of-the-moment creation by the screenwriters.
  - After: The 'Bend and Snap' was dreamed up by the screenwriters on a night out at a bar.  _(drop 'drunken')_
- Before: Studio execs thought Reese Witherspoon was 'repellent' for Elle Woods.
  - After: Studio execs doubted Reese Witherspoon could play bubbly Elle after her role as Election's ruthless Tracy Flick.  _(frame by the roles, not an insult)_

## Consistency And Setup
**Rule.** A viewer sees several cards in one sitting: cards must not contradict each other. Context-setting facts about the title itself (what it is based on; who worked together again) are welcome and best shown early.

**Why.** Contradictory cards destroy trust (one card says Reese was the only person considered, another says several actresses were offered the role). A 'based on the novel by Amanda Brown' card orients the viewer and makes later cards make sense.


## What we do not show
- **cameos.** No fact whose point is that a known person appears in the film (cameos, 'also appears', celebrity or athlete appearances, a real person playing themselves). _Viewers discover cameos on their own while watching; telling them first removes the fun._
- **cast credits.** No fact that only says who plays whom, who else is in the cast, or an actor's other roles. Casting STORIES are fine: who was almost cast, who turned the part down, how someone won the role. _The cast is already shown in each scene, so a credit tells the viewer nothing new._
- **other film.** The fact must be about THIS film. Do not lead with another film (why an actor chose a different movie over this one, what the director made next). _A viewer watching this film wants to learn about this film._
- **unflattering.** Nothing that makes a real person look bad: being fired or nearly fired, being late, conflicts, rudeness, failures, scandals. This holds even when the person tells the story themselves. _It is a family-brand enrichment surface and the reviewer called such a card 'not a good look'._
- **off topic.** No fact about a person's private life, diet, hobbies or favorite places that is not about making or being in this film. _Not interesting or relevant to the movie._
- **plain info.** No plain reference facts: content rating, release date, runtime, home-video extras, soundtrack track lists, who composed or directed (with no story), filmography lists. Money facts (budget, box office, pay) are fine only with a surprising comparison or a named source. _A fact must open a curiosity gap; reference data does not._
- **standalone.** The short must be interesting and make sense with NO knowledge of the plot. Do not rely on unexplained in-film devices ('his inner thought', 'this reboot', 'the psychic'). Say what the film or thing is, then the hook. If the short only works after reading the long, rewrite it or drop the fact. _The short is often the only thing read, on a card that appears out of context._

## Rating anchors
RATING ANCHORS (reviewer's 1-5 scale for how interesting a card is):
5 = a concrete story about named, known people with a surprising turn. Examples: "Reese Witherspoon's contract let her keep all of Elle Woods' outfits." / "Jon Bernthal accidentally punched Jonah Hill and broke his prosthetic teeth, and Scorsese kept filming." / "The novel's manuscript was picked from a slush pile because it was written on pink paper."
4 = interesting, but the hook is buried or the wording is clumsy.
3 = true and mildly interesting, but not memorable. Example: the costume designer chose the pink courtroom dress to show Elle's 'most true self' (describes an intention).
1-2 = describes a design choice, repeats what is on screen, or a plain statistic. Examples: the production designer contrasted the sorority house with the dorm; the license plate reads 22BLONDE; Elle scored a 179 on her LSAT.
