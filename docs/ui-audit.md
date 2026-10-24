# UI audit of v0.3 (2026-10-18)

How: 45 screenshots of a simulated user session at 1400×900 and 1100×700 (first run
without a key, sample story, writing and revising a chapter, scene mode, auto-write,
reading, files, settings, records, and a folder of the writer's own files), reviewed
twice: by the developer and by an independent design review. The redesign that answers
it is in [ui-redesign.md](ui-redesign.md).

**Verdict:** a debugging console with a writing tool inside it. Internals (prompts,
tokens, YAML, logs, paths, model codes) sit at the same level as the writing, and the
look is unmodified Material/Quasar defaults.

## Top problems, ranked by impact on a non-technical writer

1. **Developer internals in the main flow:** raw prompt with XML tags in the review
   screen, a code editor with YAML for story files, JSON logs, a diagnostic dump from
   *Test connection*, file paths and model codes (`z-ai/glm-5.3`) everywhere.
2. **Navigation doesn't follow the writing:** ten flat items; Logs, Costs and Plan-check
   weigh as much as Write; *Files* and *Your files* side by side; Settings opens with the
   story sidebar but nothing selected; Home keeps the previous story's sidebar.
3. **No single primary action on the chapter screen:** up to four filled buttons, two
   competing text boxes (*Notes for this request* and *Ask for changes*), the full
   rewrite next to the gentle revision, key buttons below the fold.
4. **The chapter's plan is invisible where you write:** an empty chapter is a huge box
   saying "Nothing written yet"; scene mode says "3 scenes" but never lists them.
5. **The review before sending builds anxiety, not trust:** tokens, limits, model codes
   and a raw prompt dump instead of "this is what chapter 3 will cover, with these
   characters, for about this much".
6. **Unguided first run:** a yellow box saying "Add your OpenRouter API key in Settings"
   with no explanation, no steps, and errors such as "ConnectError".
7. **Typing folder paths:** the dialogs show long paths cut off at either end.
8. **Misleading status:** the header shows the old version while a new one is being
   written; "Saved" toasts linger, stack and cover inputs; no progress while text streams.
9. **History is unusable as undo:** identical cards with ISO times, internal action codes
   and a Restore button each; no grouping or plain words.
10. **Dated look:** heavy maroon bar, all-caps buttons, pink selection, shadows on nested
    cards, Times-like fallback for prose, three input styles.
11. **Inconsistent layout:** the content's left edge moves between 204 and 440 px from
    page to page; the sidebar background stops at 900 px on long pages.
12. **Poor-contrast status colours:** green 2.4:1, orange 2.7:1, a third red for Stop.
13. **A spreadsheet for a dashboard:** nine columns ("Mode", "v1", "$0.000"), no
    "continue" action, I/A/C/R codes, counts that disagree (4 files vs 3).
14. **Money in four formats** ($0.000, $0.005, $0.0054, $0.0648) for amounts of cents.
15. **Small windows:** a fixed 200 px sidebar and a 74 px bar push the main action off
    screen at 1100×700; boxes scroll inside a scrolling page.

Also noted: Word (.docx) files rejected in a footnote; the reader's theme buttons don't
show which is active; auto-write repeats the same note five times and says
"request(s)"; truncated labels in Settings ("Atte…"); "No story is open." is a dead
end; the compare dialog is a raw diff table.

## Out of scope for this redesign (noted for later)

- Form-based editors for character and place files instead of Markdown with
  `@chN` headings. For stories kept in your own files this markup isn't needed at all.
- Export to Word, EPUB or PDF (the reader saves a single HTML file).
- A guided "new story" wizard that drafts the story bible from a premise.
