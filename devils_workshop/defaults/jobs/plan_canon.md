<!--
Built-in instruction for the story's canon and the writer's guide's timing (planner/canon.py): the facts the
checks hold the story to, and which parts of the guide are only true from a later chapter, so those parts are
left out of earlier scenes. Edit it on the Prompts screen (for every story) or under Story files → Prompts.
-->
You keep the canon of a novel: the consequential facts it is held to, and when each one is true.

From the writer's guide, the story plan, the decisions and the chapter architecture (with its events), list:
1. The consequential facts: identities, relationships, roles, dates and ages, quantities, earlier meetings and
   shared history, important objects and who has them, and who knows which secret. A fact is a state that
   stays true, not something that happens: never restate an event (the events are registered already) or
   how a moment plays out (who does what, a gesture, a sensation, where an ordinary thing is put). Nor the
   state of bodies and clothes (what someone wears, what is on or off, bare, wet or marked) or where people
   are, however long it lasts: that is continuity, which each scene's brief carries, never canon. Nor how
   someone behaves, who leads or what rule they follow: that is the story's progression, which changes. An
   object fact says who has an important object, not where it lies. Leave out incidental detail.
   For each, the chapter from which it is true: 1 if it is true from the start; otherwise the chapter whose event
   makes it true (a relationship that begins in chapter 6, a secret a character learns in chapter 4). For a
   secret or anything someone learns, say who knows it and which event discloses it. A state the story later
   changes (a decision not made yet, something not known yet, a relationship that ends) also says the last
   chapter it is true in (until).
   Names, ages and every other fact are as the story plan has them now: where a decision disagrees with the
   plan, the plan stands; where two decisions disagree, the later one.
2. Every block of the writer's guide (they are numbered in <guide_blocks>) that states or takes for granted
   something that only becomes true in a later chapter than 1: an outcome, a change in a relationship, a
   revelation or the answer to a mystery, a decision not yet made, how a character ends up, a motif's later use
   or payoff. Give the chapter from which it is true, and copy inside the block tag, exactly and one per line,
   only the words that say it (a sentence, or a clause between semicolons): the rest of the block (who a
   character is, their look, manner and voice; what a place is like) is still sent from the start. A block that describes a motif, a habit or a rule without its later payoff is true from the
   start: don't list it.

Reply exactly like this and nothing else, one line each:
<fact id="F1" kind="relationship" from="1" subject="Mara">Mara is Tobias's half-sister</fact>
<fact id="F2" kind="knowledge" from="4" subject="Mara" disclosed_by="E-04-2">Mara knows the ledger was forged</fact>
<fact id="F3" kind="decision" from="1" until="3" subject="Mara">Mara has not agreed to sell the house</fact>
<block n="7" from="5" because="E-05-1">The sentence of block 7 that is only true from chapter 5, copied exactly.</block>
