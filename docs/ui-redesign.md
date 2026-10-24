# Devil's Workshop: UI redesign (v0.4)

Why: the v0.3 interface looked dated and was hard to find your way around (see
[ui-audit.md](ui-audit.md)). This document is the design the v0.4 code follows.

## Principles

1. **Writing first.** The main screen is your manuscript: the chapter list and the
   chapter you are on. Everything else is one step away, but out of the way.
2. **Always show the next step.** Every screen has one obvious primary action.
   Empty screens say what to do, with the button to do it.
3. **The writer's words, not the developer's.** No file paths, model codes, tokens,
   YAML or log folders in the main flow. They stay available behind "Details".
4. **Calm and modern.** Neutral paper-white surfaces, one accent colour used sparingly,
   sentence-case buttons, generous spacing, a serif for story text and titles.
5. **Nothing hidden that matters.** The review before sending, costs and every
   version stay, but read like a summary first and raw text second.

## Information architecture

Left sidebar (always visible when a story is open):

| Item | What it is | Replaces |
|---|---|---|
| **Overview** | Progress, the next step, problems to fix, spending | Dashboard |
| **Chapters** | The manuscript: chapter list + the chapter workspace | Write |
| **Auto-write** | Write many chapters without review stops | Auto-write |
| **Read** | The story set for reading | Read |
| **Story files** | Files · System prompt · Problems · What's sent (own files) | Files, Prompts, Plan-check, Your files |
| **Activity** | Every request: what, model, result, words, cost; open one for the full text | Costs, Logs |
| **History** | Every saved change, in plain words; undo any | History |

Bottom of the sidebar: **Settings** and **All stories**. Old addresses (`/story/check`,
`/story/prompts`, `/story/costs`, `/story/logs`, `/story/setup`) still work and open the
matching tab.

Without a story open: **All stories** (home) and **Settings** only.

## Design tokens

- **Type.** UI: the Windows system font (`Segoe UI Variable`, `Segoe UI`, system-ui).
  Story titles and prose: `Georgia`, `Cambria`, `Palatino Linotype`, serif.
  Sizes: 13 (meta), 14 (body), 16 (lead), 20 (section), 28 (page title, serif).
- **Colour (light).** Background `#F7F6F3`, surface `#FFFFFF`, border `#E7E4DF`,
  text `#1F1D1A`, muted `#6F6A63`, accent (crimson) `#A3253B`, accent soft `#F8E9EC`.
  Status: not started `#8A847B`, draft `#B26B00` on `#FDF3E1`, final `#2E7D4F` on `#E6F4EC`,
  writing `#2F5BD3` on `#E8EEFC`, problem `#B42318` on `#FDECEA`.
- **Colour (dark).** Background `#151413`, surface `#1E1D1B`, border `#34312D`,
  text `#ECE8E1`, muted `#A39D94`, accent `#E0576F`. Settings → Appearance: Light, Dark
  or Match Windows.
- **Shape and depth.** Radius 10 px (cards), 8 px (buttons, inputs). Borders instead of
  shadows; a soft shadow only on dialogs and menus.
- **Spacing.** 4-px grid; page padding 32 px; card padding 20 px; 12–16 px between items.
- **Buttons.** Sentence case. Primary: filled accent. Secondary: white with border.
  Quiet: text only. One primary per area.

## Components

Page header (title, one-line description, actions on the right) · Card (title, optional
description, body) · Status chip (Not started / Draft / Final / Writing… / Problem) ·
Empty state (icon, sentence, primary button) · Progress bar (words or chapters) ·
Stat (value + label) · Notice (info / warning / problem, with an action) · Side panel
for details · Confirm dialog for anything that overwrites.

## Screens

### All stories (home)
- **First run:** a welcome with three steps, each with its status: *Connect to OpenRouter*
  (paste the key right there, save and test in one click), *Choose models* (defaults
  shown, change in Settings), *Start a story*.
- **Start a story:** three cards: *New story*, *Use my own files* (pick a folder),
  *Try the sample story*.
- **Your stories:** cards with title, progress bar, chapters written of planned, words,
  last opened. The whole card opens the story; a card whose folder is gone says so and
  offers *Remove from list*.

### Overview
Story title (serif), one-line facts, then:
- **Next step** card: "Chapter 4 · The Missing Page is next" with *Write chapter 4*
  and *Write several chapters automatically*. When everything is written: *Read the story*.
- **Chapters** list: number, title, status chip, word progress against target.
- **Problems** card (only when there are any): count and the first three, with
  *See all*.
- **Subplots** (only when there are any): dots per chapter with words in the legend.

### Chapters (the workspace)
Two columns: a chapter list on the left (status dot, number, title, words) and the
chapter on the right.
- **Header:** "Chapter 3", the title in serif, status chip, words of target, version,
  and a ⋯ menu (Versions, Compare, Restore, Mark as draft, Read this chapter).
- **The brief:** what the chapter should do (its instructions and scenes), shown
  above the text, open while the chapter is empty, collapsed once written. A link
  edits it in Story files.
- **Not written yet:** an empty state with *Write chapter 3* (and, in scene mode,
  *Write scene 1* and *Write all scenes automatically*), the whole/scenes switch next
  to it, and an optional *Extra instructions for this attempt*.
- **Writing:** the text appears in the reading column, with a status line (model by
  name, "thinking…", retries) and *Stop*.
- **Written:** the text in a reading column (serif, 68 characters wide). A composer
  fixed at the bottom: *Ask for changes…* with *Review & send*, plus *Continue writing*
  (highlighted when the reply was short), *Mark as final*, *Next chapter →*.

### Review before sending (dialog, full screen)
- Header: "Review before sending · Chapter 3", *Cancel*, *Send to GLM 5.3*.
- A summary line: size against your limit, target length, estimated cost.
- Left: **What's included**, grouped (Always sent · Characters and places · Subplots ·
  Your files · Added by you), each with why ("point of view", "mentioned twice") and a
  checkbox; **Not included** folded; *Add a file*.
- Right: **The request**, as readable sections (instructions folded, each reference
  file folded, the task open), with *Show exact text* and *Edit exact text*.

### Auto-write
A form in three short sections: *Which chapters* (from/to), *How* (as set / whole /
scene by scene, with one line on what each means), *Safety* (skip written chapters,
carry on short replies, plan scenes, spending cap with the estimate beside it).
Running: a step list with icons (done, writing, skipped, stopped), the text as it
arrives, *Stop after this one* / *Stop now*. Finished: the result and *Read the story*.

### Read
Unchanged reading surface; the chapter picker and *Save as one file* move into the
page header.

### Story files
Tabs: **Files** (a tree with friendly names, the editor, *Save*, preview "as the AI
sees it at chapter N"), **System prompt**, **Problems** (the plan-check, grouped by
file, each with *Open*), **What's sent** (stories in your own files).

### Activity
Total spent and spending by chapter and by model at the top; below, every request
(when, what, model, result, words, cost). Click one for a side panel with the request,
the reply and the details.

### History
A timeline grouped by day, actions in plain words ("Chapter 3 written",
"Edited characters/mara-venn.md"), each with *Undo* (with a confirm).

### Settings
Sections: **Connection** (status, paste key, *Save & test*), **Models** (main and
fallback by name, with price and context size; per-model options folded),
**Privacy & providers**, **Appearance**, **Advanced** (retries, file locations).
One *Save* bar fixed at the bottom when something changed.

## Behaviour notes
- The sidebar shows a small "Auto-writing…" pill while a run is going.
- Model names come from OpenRouter's list ("Z.ai: GLM 5.3"); the code is shown only
  in Settings.
- Small windows (≈1100×700): the sidebar narrows, the chapter list folds into a picker,
  and the primary action stays visible without scrolling.

## Additions from the audit review
- **Notifications** appear at the top right, never over inputs, and replace each other
  instead of stacking.
- **Money** is shown for people: "$0.06", "under $0.01".
- **Status while writing:** the header says "Writing version 2…" and the status line
  counts words against the target.
- **Rewriting a whole chapter** moves into the ⋯ menu; the composer is for changes.
- **Narrow windows (under 1200 px):** the sidebar becomes an icon rail with tooltips.
- **Read:** the active theme is highlighted; *Save as one file (.html)*; previous/next
  chapter links.
- **Test connection** answers in one line ("Connected. Your key works.") with the
  diagnostics folded under *Technical details*.
- **Word files** (.docx) can be used as your own files (their text is read).
- **No story open:** story screens go straight to *All stories* instead of a dead end.
