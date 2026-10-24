<!--
Built-in instruction for smoothing the joins between scenes that were written at
the same time. It is sent after the story's system prompt. Edit it on the Prompts
screen (for every story) or under Story files → Prompts (for one story).
-->
How to use this request:
- The scenes of this chapter were written separately, at the same time, from the scene plan. Each one is shown by its ending and, from scene 2 on, its opening.
- Rewrite each opening so the scene follows on naturally from the ending of the scene before it: the time, the place, who is present and the mood should carry over, without repeating what the reader already knows.
- Keep each opening's events, point of view, tense and voice, and keep it about the same length. Its last sentence must still lead into the rest of the scene as it stands.
- Don't change anything that isn't shown, and don't add scene headings or commentary.
- Reply with every opening shown, in order, each in its own block, exactly like this:
<opening scene="2">
The new opening of scene 2.
</opening>
- If an opening already follows on well, copy it unchanged into its block.
