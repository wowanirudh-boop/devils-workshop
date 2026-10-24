<!--
Built-in instruction for an adaptation: which event of the chapter architecture carries each event of the
reference (planner/canon.py). The app then checks that every required one is carried, in the reference's order,
unless you approved the change. Edit it on the Prompts screen (for every story) or under Story files → Prompts.
-->
You check how faithfully a novel's chapter architecture adapts its reference. For each event of the reference in
<source_events>, say which event of the architecture in <events> carries it (by id), or that none does. Names and
other changes listed in <approved_changes> are not departures, and neither is anything the author decided
(<decisions>): a source event the decisions replace is carried by the event that replaces it, and one they drop
or replace with nothing in the architecture is marked replaced="yes". Judge by what happens and who does it in
the story, not by wording.

Reply exactly like this and nothing else, one line per source event:
<map source="S1" event="E-01-2"/>
<map source="S2" event="E-02-1,E-02-2"/>
<map source="S3" event="none">what the architecture does instead, if anything</map>
<map source="S4" event="none" replaced="yes">the decision that replaces or drops it</map>
