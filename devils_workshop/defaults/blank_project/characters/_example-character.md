---
# Files whose names start with _ are ignored. Copy this file to start a new character.
name: Example Person
type: character
aliases: [Example, the example]   # other names the story uses for them
case_sensitive_aliases: []        # aliases that must match exact case, e.g. [Will]
context: when_mentioned           # always | when_mentioned | never
fields:
  occupation: Example
  status: alive
---
## Profile
Who they are, what they want, how they speak.

## Appearance
What they look like at the start of the story.

## @ch3 add
From chapter 3 on, this is added under "Developments so far".

## @ch5.2 replace Appearance
From chapter 5, scene 2 on, this replaces the Appearance section.

## @ch7 set
status: missing

## secret @ch8
Never sent before chapter 8. From chapter 8 on, sent like any other fact.
