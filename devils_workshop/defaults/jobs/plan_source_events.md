<!--
Built-in instruction for an adaptation: the events of the reference the story adapts, in order, with who does
what and what follows from what (planner/canon.py). Made once from the reference itself, not from notes on it.
Edit it on the Prompts screen (for every story) or under Story files → Prompts (for one story).
-->
You map the events of a reference story that a novel adapts. Read the reference in <file> and list its
consequential events in the order they happen in it: what happens, who does it by their function in the story
(the narrator, the rival, the mentor) as well as their name, and which earlier events it follows from. Mark an
event required="no" only if the story would stand without it; every turning point, revelation and the ending
are required. Give each event its weight in the reference, by how much the reference dwells on it:
"major" (it is lived through at length), "minor" (a passage of its own, briefly), or "passing" (it is
mentioned or summarised in a line). The adaptation gives its events the same weight.

When <your_plan> is given instead of a <file>, the reference is the author's own plan for this story, made
elsewhere: list its events the same way, in its order, and give each the weight the <story_plan> gives it (an
event the story plan develops or shows in full is "major", however briefly <your_plan> mentions it).

Reply exactly like this and nothing else, one line each, numbered in the reference's order:
<source_event id="S1" required="yes" weight="major" roles="the narrator (Ana) meets the mentor" causes="">what happens</source_event>
<source_event id="S2" required="yes" weight="passing" roles="..." causes="S1">what happens</source_event>
