# PLAN.md: Devil's Workshop

Phase 2 deliverable: a plan only, no code yet. It builds on:
- `docs/phase1-research-report.md`;
- `docs/phase1-decisions.md`, whose decisions are written **[D#]**. D28–D36 come from your review of the first draft of this plan.

**Status: revised after your review, awaiting approval.** Section 15 lists what changed.

The placeholder story used in all examples and tests is *The Saltmarsh Clock*, a neutral cozy mystery I invented. A clockmaker, Mara Venn, investigates why the harbour clock of Brindle Cove stopped.

---

## 1. What we're building

A **Windows desktop app** that writes a novel chapter by chapter with models on OpenRouter.
- **You** own the story files.
- **The app** builds each request, shows it to you, sends it, saves the result and tracks cost.
- **The model** writes the prose.

**Each chapter is its own chat [D29]:**
- It starts fresh from your project files and that chapter's instructions.
- Nothing from earlier chapters is carried over: no prose, no summaries [D28].
- Inside a chapter's chat you can ask for changes, and the model rewrites the chapter [D30].
- **Next chapter** opens a new chat for the following chapter.
- Nothing else happens automatically after a chapter.

Three layers, each depending only on the one below:

```
┌──────────────────────────────────────────────┐
│  UI (NiceGUI in its own window)              │  screens, buttons, editors
├──────────────────────────────────────────────┤
│  CLI (command line, same functions)          │  testing, scripting, debugging
├──────────────────────────────────────────────┤
│  Engine (plain Python)                       │  story files, request assembly,
│                                              │  OpenRouter client, chapter chats,
│                                              │  logs, costs, history
└──────────────────────────────────────────────┘
```

The engine never imports UI code, so almost everything can be tested without a window.

---

## 2. Your project folder (one folder per story)

All files are plain **Markdown** (`.md`) or **YAML** (`.yaml`). You can open them in Notepad and track them in git. The only exception is `sessions/`, the app's machine-readable chat records.

```
The Saltmarsh Clock/
├── story.yaml                  # title, defaults, per-story overrides
├── system_prompt.md            # ONE system prompt for this story [D19]
├── bible/
│   ├── style_guide.md          # voice, tone, prose rules
│   ├── story_bible.md          # premise, themes, backstory
│   └── world_rules.md          # how the world works; hard constraints
├── characters/                 # one file per character
│   ├── mara-venn.md
│   └── tobias-reed.md
├── places/                     # one file per place (same format as characters)
│   └── brindle-cove-clock-tower.md
├── lore/                       # objects, organisations, history (same format)
│   └── the-harbour-ledger.md
├── subplots/                   # one file per subplot/arc, with per-chapter status
│   └── the-forged-ledger.md
├── outline.md                  # chapter instructions/beats, optional scene beats
├── chapters/
│   ├── ch01.md                 # the current version of each chapter
│   └── versions/ch01.v1.md     # every earlier version [D30]
├── sessions/ch01.json          # chapter chat record (app-managed)
├── notes/                      # your scratch notes; NEVER sent unless you add them in review
├── prompts/jobs/               # only exists if you edit a built-in instruction (Advanced) [D19]
├── logs/                       # every request/response + costs.jsonl [D26]
└── .history/                   # automatic snapshots [D25] (hidden folder)
```

`characters/`, `places/` and `lore/` share one format, called an **entry**.

### 2.1 File formats

**`story.yaml`**: settings for this story. Anything left out uses the app-wide default.
```yaml
title: The Saltmarsh Clock
language: English
defaults:
  pov: Mara Venn              # book-level POV [D17]
  person: third               # first | second | third
  tense: past                 # past | present
  chapter_words: 3000         # default target [D15]
  mode: whole                 # whole | scenes [D9]
  in_chapter_context: full    # full | beats_only (scene mode) [D11, see §15]
models:                       # optional; overrides app defaults [D3]
  primary: z-ai/glm-5.3
  fallback: moonshotai/kimi-k3
```

**Entry file** (character/place/lore): YAML front matter, a Markdown profile, then progressions and secrets as specially named headings.
```markdown
---
name: Mara Venn
type: character
aliases: [Mara, the clockmaker]
case_sensitive_aliases: []     # e.g. [Will] so "will" (the verb) doesn't match
context: when_mentioned        # always | when_mentioned | never
fields:
  occupation: Clockmaker
  status: alive
---
## Profile
Thirty-four, precise, distrusts anything she can't take apart.

## Appearance
Ink-stained fingers; hair tied back with clock wire.

## @ch3 add
She now wears her late father's pocket watch.

## @ch5.2 replace Appearance
Burn scar across her left palm from the tower fire.

## @ch7 set
status: missing

## secret @ch8
She altered the harbour ledger years ago to protect her father.
```

**Heading rules** (plan-check reports any malformed heading):

| Heading | Meaning |
|---|---|
| `## @chN` or `## @chN add` | From chapter N on, append this text to "Developments so far". |
| `## @chN.M ...` | Same, from scene M of chapter N. |
| `## @chN add <Section>` | Append to a named section, e.g. `add Appearance`. |
| `## @chN replace <Section>` | From that point, this text replaces the named section. |
| `## @chN set` | Body lines are `field: value` updates to the front-matter fields. |
| `## secret @chN[.M]` | Never sent before that point. From that point on it's sent like a normal fact [D13]. |

**Subplot file:**
```markdown
---
name: The forged ledger
aliases: [ledger plot, SP1]
status:            # per-chapter status; carries forward until changed
  1: dormant
  2: introduced
  4: active
  9: climax
  10: resolved
---
## Summary
Someone altered the 1890s harbour ledger; the stopped clock is the first clue.

## ch2
Ada mentions the ledger's missing page in passing.

## ch4
Mara finds the page hidden in the clock mechanism.
```

**Subplot rules:**
- A subplot is included while its status is `introduced`, `active` or `climax`.
- Only the current chapter's note is sent. Future chapter notes are never sent.

**`outline.md`:** chapters as `#`, optional scenes as `##`. `key: value` lines right after a heading are settings. Everything else is that chapter's (or scene's) instructions and beats. Per your decision, these instructions are what make each chapter self-contained [D28].
```markdown
# Chapter 1: The Stopped Clock
words: 3200
- Dawn. Mara is called to the tower: the clock stopped at 3:12.
- Tobias, harbourmaster, insists it's mechanical failure.
- Mara finds a fresh scratch on the escapement. Not wear: a tool.

# Chapter 2: Ink and Salt
mode: scenes
## Scene 1: The archive
characters: [Ada Quill]
- Ada shows Mara the ledger room.
## Scene 2: The harbour at night
lookahead: false
- Mara sees a light in the tower.
```

**Outline settings:**
- **Chapter level:** `words`, `pov`, `person`, `tense`, `mode`.
- **Scene level:** `pov`, `words`, `characters` (always include these entries), `lookahead: false` (hide this scene's beats from earlier scenes, e.g. a scene whose beat reveals a secret).

**Chapter file (`chapters/ch02.md`):** front matter plus prose. In scene mode, scenes are separated by invisible markers.
```markdown
---
chapter: 2
title: Ink and Salt
version: 3
words: 3054
status: draft        # draft | final
---
<!-- scene 1 -->
The ledger room smelled of brine and ...
<!-- scene 2 -->
...
```

---

## 3. App settings (outside any project)

Stored in `%APPDATA%\DevilsWorkshop\settings.yaml`. This is the model config the brief asks for. Everything in it is editable on the Models & Settings screen.

```yaml
roles:                         # app-wide defaults; story.yaml can override [D3]
  primary: z-ai/glm-5.3
  fallback: moonshotai/kimi-k3
models:
  z-ai/glm-5.3:
    size_limit_tokens: 60000       # warning threshold for a request (§4.4)
    max_output_tokens: 32000       # cap incl. reasoning; not billed unless used
    reasoning: { effort: low }     # sent only if the model supports it
    params: { temperature: 0.9, top_p: 0.95 }   # unsupported ones dropped + warned [D5]
  moonshotai/kimi-k3:
    size_limit_tokens: 60000
    max_output_tokens: 32000
    params: { temperature: 0.9 }   # likely dropped for K3 (reported unsupported)
providers:                     # [D6]
  allow: []                    # empty = any provider
  block: []
  order: []
  data_collection: allow       # allow | deny
  require_parameters: false
retries: { attempts_per_model: 3, base_delay_s: 2, max_delay_s: 60 }
refusal_patterns_file: default # or a path to your own list (§6.3)
recent_projects: []
```

**Model names are checked live.**
- On startup and on the Models screen, the app fetches `GET /api/v1/models`. It warns if a configured name doesn't exist, and shows context size, prices and supported settings [D4].
- The Providers picker uses OpenRouter's per-model endpoints list to show which providers serve a model. I haven't verified that endpoint in this research, so it's checked in M3.

**Allow/block lists in the request** *(not verified against the docs; checked in M3)*:
- Allow-list → `provider.only`, and block-list → `provider.ignore`.
- If those fields don't exist, the allow-list becomes `provider.order` with `allow_fallbacks: false`.

**API key [D24]:**
- Pasted once in Settings and stored in Windows Credential Manager via `keyring`.
- If the `OPENROUTER_API_KEY` environment variable is set, it takes priority.
- The key is never written to any file, log or prompt. A test checks this (§13).

---

## 4. Building the request for chapter N (scene M)

One engine function builds every request:
`assemble(project, chapter=N, scene=M or None, mode)`

### 4.1 Position rule: the "no future leaks" rule [D14, D31]

**Positions:**
- A position is `(chapter, scene)`. `@ch5` means `(5, 0)`, i.e. "true from the **start** of chapter 5". It's visible while writing chapter 5 and after.
- For something that happens *during* chapter 5, key it to a scene (`@ch5.3`) or to `@ch6`.
- Positions compare in reading order: `(4, 3) < (5, 0) < (5, 2) < (6, 0)`.

**The rule:** an item keyed at position *p* is **active** while writing position *w* if *p ≤ w*. Items keyed after *w* are **never** included.

What *w* is:
- **Scene mode:** *w* = `(N, M)`.
- **Whole-chapter mode:** *w* = `(N, 0)`, plus a special case for items keyed *inside* chapter N, at `(N, k)` with k ≥ 1. The model is writing all of chapter N, so it needs to know those items. They're included and labelled *"applies from scene k of this chapter; not before"*.

The same rule covers progressions, secrets, subplot statuses and per-chapter subplot notes. It's tested exhaustively (§13).

### 4.2 Rendering an entry "as of" position *w*
1. Start from the profile sections and front-matter fields.
2. Apply active progressions in position order (ties keep file order):
   - `add` appends;
   - `replace` swaps out the named section;
   - `set` updates fields.
3. Active secrets become normal facts. Inactive secrets are dropped entirely.
4. Output looks like:
```
<entry name="Mara Venn" type="character" aliases="Mara, the clockmaker">
occupation: Clockmaker | status: alive
## Profile ...
## Appearance (as of ch5.2) ...
## Developments so far
- (ch3) She now wears her late father's pocket watch.
</entry>
```

### 4.3 What goes in [D28, D33]

| Source | When included |
|---|---|
| `system_prompt.md` + the built-in writing instruction | Always |
| Task block: chapter/scene number and title, the chapter's instructions and beats, word target, POV/person/tense, your run notes | Always |
| `style_guide.md`, `world_rules.md`, `story_bible.md` | Always |
| POV character's entry | Always |
| Entries listed in the outline's `characters:` for this chapter/scene | Always |
| Entries with `context: always` | Always |
| Entries with `context: when_mentioned` | Name or alias found in the **scan text** (below) |
| Entries with `context: never` | Never (you can still add one in review) |
| Subplots | Status at chapter N is introduced/active/climax. Summary plus this chapter's note only. |
| Earlier scenes of **this** chapter | Scene mode, M > 1: already in the chapter's chat [D11] |
| Remaining beats of this chapter, marked "do not write yet" | Scene mode, except scenes with `lookahead: false` [D12] |
| Anything from earlier chapters (prose, summaries) | **Never** [D28] |
| `notes/` | Never, unless you add it in review |

**Scan text for mentions:**
- this chapter's (or scene's) instructions and beats;
- your run notes;
- the lookahead beats;
- the earlier scenes of this chapter;
- any change request (§6.2).

**Matching:**
- Whole words, case-insensitive (except `case_sensitive_aliases`).
- Possessives match too ("Mara's").
- Plan-check warns when two entries share an alias.

### 4.4 Size limit: warn, you choose [D34]

- **Estimate:** there's no local tokenizer for these models.
  - The app starts with an estimate of about 4 characters per token for English.
  - After each call it compares its estimate with the `prompt_tokens` OpenRouter reports, and keeps a per-model correction factor.
- **Limit:** each model has a `size_limit_tokens` setting (default 60,000 ≈ 45,000 words).
- **Over the limit:**
  - **Nothing is cut automatically.**
  - The review screen shows a warning with the total, and lists the included files largest first.
  - You untick files until it fits, or press **Send anyway**.
- **Hard limit:** if a request exceeds the model's actual context window (≈1M listed for both), the app refuses to send it and explains why.

### 4.5 Order inside the request

Stable material comes first, so repeated calls share a prefix (cached-input pricing may apply; unverified). The task comes **last**, closest to where the model starts writing.

```
[system]     system_prompt.md  +  built-in writing instruction
[user]       <style_guide> <world_rules> <story_bible>
             <characters> (POV first, then by name) <places_and_lore>
             <subplots>
             <task> chapter instructions/beats · "do not write yet" beats ·
                    target words · POV/person/tense · your notes · output rules
[assistant]  draft (or scene 1)                        ┐
[user]       change request (or "write scene 2" + its   │ chapter chat
             beats)                                     │ continues here (§6.2)
[assistant]  new full version (or scene 2) …           ┘
```

The XML-style tags make section boundaries clear to the model, as Novelcrafter's docs recommend.

---

## 5. Review before every request [D20, D32]

Before **every** request (new chapter, each scene, each change request), the app shows what it's about to send:
- each section with a checkbox, its token estimate, and why it was included (e.g. "mentioned 3× in instructions" or "always");
- the size-limit warning, if over (§4.4);
- **Add file** for any project file, including `notes/`;
- **Edit full text**, which turns the whole request into one editable text box;
- the estimated cost (input tokens × live price, plus a range for output).

Then you press **Send**.

Overrides apply to **this request only**. Project files don't change. The log records exactly what was sent.

---

## 6. Chapter chats

### 6.1 Writing a chapter
```
open chapter N (a fresh chat; nothing from earlier chapters):
  if mode == scenes and the chapter has no scenes in the outline:
      propose scenes → you approve/edit → saved to outline.md (snapshot first) [D16]
  whole mode:  request = assemble(N) → review → send → stream → version 1
  scene mode:  for each scene k:
                 request = chat so far + "write scene k" + its beats (+ lookahead)
                 → review → send → stream → appended to the chapter
  the chapter is saved as chapters/chNN.md (snapshot first); the chat is saved to sessions/chNN.json
```
- **Mode** comes from the outline, then `story.yaml`, then the run's own setting [D9].
- **Word targets** [D15]:
  - the chapter target comes from `words:` in the outline, otherwise the project default (3,000);
  - in scene mode it's split evenly across scenes, rounded to 50;
  - a scene's own `words:` wins.
- **Live text:** text streams to the screen and to `chapters/chNN.partial.md`. On success it replaces the chapter file. On failure, nothing is overwritten (§6.4).
- **Stop button:** cancels the stream. Partial text is saved to the log.

### 6.2 Asking for changes (chat-style) [D30]

You type a change request, e.g. *"Make the ending colder; Tobias shouldn't apologise."* The next message in the chapter's chat contains:
- the original setup (unchanged);
- a short list of your **earlier** change requests for this chapter, so earlier fixes aren't undone;
- the **current** draft;
- your new request.

Older drafts are left out of the chat to keep it focused. They're still saved in `chapters/versions/`.

The model writes a **new full version**.
- **Saving:** the previous version moves to `chapters/versions/chNN.vK.md`, and the new one becomes `chapters/chNN.md`.
- **Comparing:** a side-by-side diff shows what changed. **Restore version** brings back any earlier one.
- **New names:** if your request mentions a character or place not yet in the chat, the review screen offers to add its file (pre-ticked).
- **Scope:** in scene mode you choose whether the request applies to the whole chapter or to one scene.

**Next chapter** closes this chat, with nothing carried over, and opens chapter N+1 if the outline has one. You can reopen any chapter's chat later from the Dashboard.

### 6.3 Calling OpenRouter
- `POST /api/v1/chat/completions` with `stream: true`, through `httpx` (async).
- **Request body:**
  - `model`, `messages`;
  - `max_tokens` from the model config;
  - `params` filtered against the model's `supported_parameters` (dropped ones are warned and logged [D5]);
  - `reasoning` only if supported;
  - `provider` built from settings.
- **Stream parsing:**
  - lines starting with `:` (keep-alive comments) are ignored, but they reset the "no data" timer;
  - `data: [DONE]` ends the stream;
  - `delta.content` is collected as prose;
  - reasoning text, if returned, goes to a separate log file and never into the chapter;
  - the final chunk's `usage` (tokens, `cost`) is recorded;
  - a chunk with a top-level `error` or `finish_reason: "error"` is a **mid-stream failure**, even though the HTTP status is 200.
- **Timeouts:**
  - connect: 15 s;
  - no bytes received at all, not even keep-alives: 180 s;
  - whole call: 30 min.

  Reasoning models can be silent for a long time before the first prose appears.

### 6.4 Failures, retries, refusals, fallback

Each attempt ends in exactly one outcome:

| Outcome | Detected by | Action |
|---|---|---|
| OK | Prose returned, none of the below | Save |
| Transient | Network error, timeout, 408/429/500/502/503, mid-stream error | Retry the **same** model, up to 3 attempts. Delays 2 s → 4 s → 8 s (±20%, max 60 s), honouring `Retry-After` (over 120 s → treat as failed). Then switch to fallback. |
| Moderation | HTTP 403 with moderation/guardrail info, or `finish_reason: content_filter` | Switch to fallback immediately |
| Refusal | Output under max(150 words, 25% of target) **and** the first ~400 characters match a refusal pattern | Switch to fallback |
| Empty | Under 20 words of prose | If `finish_reason: length` (reasoning likely used up the budget), retry once on the same model with double `max_tokens`. Otherwise, or if that fails, switch to fallback. |
| Fatal | 400 (bad request / too long), 401 (bad key), 402 (no credits) | Stop with a plain-language message. No fallback; it wouldn't help. |

- **Fallback:** done by the app, not OpenRouter's `models` array, so refusals and empty replies are handled too and every attempt is logged.
  - The fallback model gets the same chat.
  - Later requests in that chapter's chat go back to the primary model.
- **If the fallback also fails:** stop and report. Nothing is overwritten [D7].
- **Refusal patterns:** an editable list, e.g. *"I'm sorry, but I can't"*, *"as an AI"*, *"content policy"*, *"I must decline"*. A refusal needs both a short output **and** a pattern near the start. That keeps a character's line like *"I can't do this," she said* from counting. This is a heuristic: some refusals will slip through and some false alarms will happen. The log shows which pattern fired.
- **Short output:** under 60% of the target is accepted with a warning. A **Continue** button asks the model, in the same chat, to carry on to the end of the instructions.
- **Display:** the word count is shown, with a warning outside 60–150% of target. The cost and the model used are shown too, including whether the fallback was used.

---

## 7. After a chapter [D28]

**Nothing happens automatically.** There's no summary, continuity check or ledger.

The chapter screen offers four actions:
- **Ask for changes**;
- **Continue** (only when the output was short);
- **Mark final**;
- **Next chapter**.

---

## 8. Other tools

- **Propose scenes [D16]:** only used when scene mode is chosen for a chapter with no scenes in the outline. The chapter instructions plus context go in. A list of scenes (title, beats, POV, characters) comes out. You approve or edit it, and it's written into `outline.md`.
- **Plan-check:** no API call. It reports:
  - malformed headings or front matter;
  - unknown names in `characters:`;
  - duplicate aliases;
  - progressions or secrets keyed beyond the last outlined chapter;
  - scene keys in chapters without scenes;
  - chapters with secrets that also have lookahead beats.

  It also shows a **subplot × chapter matrix** and a preview, with token counts, of the request for any chapter or scene.

---

## 9. Importer [D1–D2]

Input: a folder (or selected files) of `.md`, `.txt` and `.pdf`.

1. **Extract text.**
   - `.md`/`.txt` are read as UTF-8. If that fails, Windows-1252 is tried.
   - `.pdf` uses `pypdf`. Pages with no extractable text are flagged as possibly scanned, which isn't supported.
2. **Template path:** if a file uses the app's import headings (documented in the README, e.g. `# Characters` → `## Mara Venn`), a deterministic parser converts it. No API call.
3. **Model-assisted path**, for everything else:
   - **Inventory request:** lists the characters, places, lore, subplots, outline, style and world-rule material found, and where.
   - **Extraction requests, by category:** produce structured output matching the file formats in §2.1. The model is asked to **copy wording verbatim** where possible.
   - JSON-schema output is used when the model supports it. Otherwise the reply is parsed leniently, and retried once on failure.
   - Same failure, refusal and fallback handling as writing (§6.4).
4. **Coverage check:** a heuristic that compares the source text with the output and lists source passages that don't seem to appear anywhere. Anything unassigned is saved to `notes/unsorted-import.md`, so nothing is silently lost.
5. **Review (mandatory):** the proposed file tree is shown next to the source excerpts. You edit, accept or reject each file, then save.
   - If the project already exists, changes appear as diffs and a snapshot is taken first.

---

## 10. Logging, costs, history

**Logs [D26]:** one folder per request, `logs/2026-10-18/ch05-03/`, containing:

| File | Contents |
|---|---|
| `prompt.md` | The request as you'd read it, with section labels and token estimates |
| `request.json` | Exact body sent. No headers, so no key. |
| `response.md` | Prose received, including partial text on failure |
| `reasoning.md` | Only if the model returned reasoning |
| `meta.json` | Model, provider, every attempt and its outcome, `finish_reason`, timings, usage, cost, generation id, warnings (e.g. dropped settings) |

A Logs screen lists and opens these. Logs hold your story text, so they stay inside the project folder.

**Costs [D8]:**
- Every attempt appends one line to `logs/costs.jsonl`: time, chapter/scene, version, model, provider, token counts (prompt, completion, reasoning, cached), and `cost` from OpenRouter's `usage`.
- If `cost` is missing, the app estimates it from live prices and marks it "estimated".
- The Costs screen totals per request, chapter (all versions), model and project.
- Warn only: cost is shown before sending (§5) and after each call. Nothing is ever blocked.

**History [D25]:**
- Every write to a project file goes through one function. It first copies the affected files to `.history/<time>_<action>/` with a small manifest.
- The History screen restores any snapshot. The current files are snapshotted before a restore, so restores are undoable too.

---

## 11. Code layout (this repository)

```
devils-workshop/
├── PLAN.md  README.md  requirements.txt  requirements-dev.txt  pyproject.toml
├── devils_workshop/
│   ├── project/     files.py (all reads/writes + snapshots), entries.py, outline.py,
│   │                subplots.py, story_config.py, chapters.py
│   ├── context/     positions.py (§4.1), mentions.py, render.py, size.py, assemble.py
│   ├── llm/         openrouter.py, sse.py, catalog.py (models list, param filter),
│   │                outcomes.py (§6.4 classification), refusal.py, costs.py
│   ├── chat/        session.py (chapter chats, versions, change requests), scenes.py
│   ├── tools/       importer.py, plancheck.py
│   ├── logs.py  history.py  settings.py (incl. keyring)  wordcount.py
│   ├── defaults/    system_prompt.md, jobs/*.md, refusal_patterns.txt, sample_project/
│   ├── cli.py
│   └── ui/          app.py, pages/, components/
├── tests/           unit/, ui/, live/, fixtures/
└── .github/workflows/  tests.yml, build-windows.yml
```

### CLI commands (same engine functions as the UI)

| Command | Does | UI equivalent |
|---|---|---|
| `init <folder>` | New project from template or sample | Home → New story |
| `import <sources> --into <folder>` | Import; writes proposals to `.import-review/`, then `apply` | Import screen |
| `plan-check` | Validation + subplot matrix | Plan-check screen |
| `preview <ch> [--scene M]` | Print the assembled request, no API call | Review screen |
| `write-chapter <ch> [--mode whole\|scenes]` | Start the chapter's chat and write it (§6.1) | Chapter screen |
| `write-scene <ch> <scene>` | Write one scene in the chapter's chat | Chapter screen |
| `revise <ch> [--scene M] --notes "..."` | Change request in the chapter's chat (§6.2) | Ask for changes |
| `versions <ch> [--restore K]` | List or restore chapter versions | Version list / diff |
| `cost [--chapter N]` | Totals | Costs screen |
| `models [--search kimi]` | Live model list, prices, supported settings | Models screen |
| `history [--restore ID]` | Snapshots | History screen |

`summarize` and `continuity-check` from the original brief are **dropped** [D28].

---

## 12. Desktop app

### 12.1 Window and technology

**Window:**
- NiceGUI 3.17 started with `ui.run(native=True, host="127.0.0.1", reload=False, title="Devil's Workshop")`.
- It opens a pywebview window that uses Edge WebView2.
- It's bound to your computer only (`127.0.0.1`), never the network.
- If WebView2 is missing (possible on older Windows 10), the app falls back to opening in your default browser.

**Verified in this environment:** NiceGUI 3.17.1 has `native` mode, a `codemirror` editor component, a `nicegui.testing` toolkit, and the `nicegui-pack` packager.

### 12.2 Screens
1. **Home:** recent stories, New story, Open folder, Import, Open sample story.
2. **Dashboard:** chapters with status (outlined / drafted / final), version count, words and cost. Opens any chapter's chat. Shows the subplot matrix.
3. **Chapter:** the chapter's chat (below).
4. **Files:** project file tree plus Markdown editor, with a preview of an entry rendered "as of chapter N".
5. **Prompts:** this story's system prompt. Advanced: built-in instructions.
6. **Models & Settings:** API key (paste, test), model pickers from the live list, per-model settings and size limit, provider allow/block lists, retries, refusal patterns.
7. **Costs**, 8. **Logs**, 9. **History**, 10. **Plan-check**, 11. **Import**.

Chapter screen sketch:
```
┌ Chapter 5: The Tower Fire ▾   Mode: whole ▾   v3 ▾ [Diff] ── $0.31 so far ┐
│ ┌ Chapter text (current version) ───────────────────────────────────────┐ │
│ │ (streams here while writing)                                          │ │
│ │                                                                        │ │
│ └────────────────────────────────────────────────────────────────────────┘ │
│ Changes so far: 1) colder ending  2) cut the dream sequence               │
│ Ask for changes: [___________________________________________] [Review ▸] │
│ [Stop]  [Continue]  [Mark final]                       [Next chapter ▸]   │
└────────────────────────────────────────────────────────────────────────────┘
Review screen (before every send): sections ☑/☐ with tokens · size warning ·
[+ Add file] · [Edit full text] · est. cost · [Send]
```

### 12.3 Installing (.exe) [D23]

**Building:**
- A GitHub Actions workflow on a Windows build machine runs the tests, then `nicegui-pack --onefile --windowed --name DevilsWorkshop`.
- It then runs a no-window `--self-test` of the built `.exe`: start the engine, load the sample story, assemble a request, exit.
- The `.exe` is attached to a GitHub Release, and you download it from the repo's Releases page.

**Running it:**
- The `.exe` is unsigned: SmartScreen warns on first run (*More info → Run anyway*).
- Single-file `.exe`s unpack themselves, so start-up takes a few seconds.
- Some antivirus tools flag PyInstaller apps by mistake (common report; medium confidence). A zipped folder build is produced as a backup.

**Limits:**
- I can't build or run the `.exe` in this Linux environment. You test the Windows window. I test the UI here in a browser and send screenshots.
- Actions minutes: about 2,000 a month on the free plan, with Windows minutes counted double (medium confidence). Windows builds run only when I tag a release. Tests on every push run on Linux, which costs half as much.

### 12.4 Libraries (pinned; each release at least 2 weeks old as of 2026-10-18)

| Library | Version | Purpose | Licence |
|---|---|---|---|
| nicegui | 3.17.1 | UI | MIT |
| pywebview | 6.2.1 | App window | BSD-3 |
| httpx | 0.28.1 | API calls, streaming, test mocking | BSD-3 |
| PyYAML | 6.0.3 | YAML files | MIT |
| keyring | 25.7.0 | Windows Credential Manager | MIT |
| pypdf | 6.19.0 | PDF import | BSD-3 |
| pyinstaller *(build)* | 6.22.3 | `.exe` | GPL + bootloader exception (allows distributing apps) |
| pytest *(dev)* | 9.1.1 | Tests | MIT |

- **"Minimal" is relative:** NiceGUI has 24 direct dependencies of its own (web server etc.), and more counting theirs. On Windows, pywebview adds `pythonnet` and keyring adds `pywin32-ctypes`.
- **Python:** the `.exe` is built with Python 3.12. Code supports 3.11–3.13.
- **Sources:** versions and dates come from PyPI. Licences for nicegui, pywebview and keyring are from PyPI; the rest are from my training knowledge.

---

## 13. Testing strategy

**Rules:**
- The API is **mocked in all unit tests**, using `httpx.MockTransport` (built into httpx).
- All fixtures use *The Saltmarsh Clock*.

| Area | What's tested |
|---|---|
| Parsers | Entries, progression headings, secrets, outline settings, subplots, chapter scene markers. Malformed input gives readable errors. |
| **No future leaks** | For every item in the sample story and every chapter/scene position, in both modes: nothing keyed later than the writing position appears in the request |
| **No cross-chapter leaks** | A chapter's request contains no text from any other chapter's prose or chat |
| Rendering | Add/replace/set order; mid-chapter labels in whole mode |
| Mentions | Aliases, case, possessives, word boundaries, `case_sensitive_aliases`, duplicate-alias warning, change-request scanning |
| Size limit | Warning threshold, per-model calibration factor, hard-limit refusal. Nothing is ever cut automatically. |
| Assembly | Golden files: the full request for selected chapters/scenes is compared against reviewed snapshots |
| Chapter chats | Change-request message contents, version numbering, restore, scene-mode turns, resuming a saved chat |
| SSE parser | Comments, `[DONE]`, usage chunk, reasoning deltas, mid-stream error, error-as-first-event |
| Outcomes | Every row of §6.4, retry delays (fake clock), `Retry-After`, fallback order, both-fail stop |
| Refusal | True refusals vs. dialogue containing "I can't"; editable patterns |
| Params | Unsupported settings dropped and warned |
| Costs | Aggregation per chapter across versions; estimated cost when `cost` is missing |
| Safety | The fake API key string never appears in any log, request file or project file |
| History | Snapshot before every write; restore; restore of a restore |
| Importer | Template path (deterministic); model path with mocked replies; coverage report; PDF without text |
| UI | NiceGUI `user` fixture: open chapter → review → send → ask for changes → next chapter, against the mocked API. Browser screenshots for you. |
| **Live (opt-in)** | `pytest -m live`, needs `DW_LIVE_TEST=1` and a key. Verifies the configured model names exist, makes **one** small request to the cheaper model (estimated < $0.01), checks that usage and cost come back, and checks the provider fields. **You run this on your machine.** |
| Windows | `--self-test` of the built `.exe` in Actions |

---

## 14. Milestones

After each milestone I run the tests, show you the results and screenshots, and **wait for your approval**.

| # | Milestone | Done when |
|---|---|---|
| **M1** | Project format: parsers, sample story, `init`, `plan-check` (CLI) | Parser and plan-check tests pass; sample story validates |
| **M2** | Request assembly: positions, rendering, mentions, size check, `preview` | No-leak tests pass; you review the golden previews |
| **M3** | OpenRouter client: models list, streaming, retries, fallback, refusal/empty detection, param filtering, logs, costs | All mocked tests pass; you run the live test once and paste the output (confirms model names and provider fields) |
| **M4** | Chapter chats: write (both modes), change requests, versions, Continue, propose scenes, history (CLI end to end on mocks) | A full mocked chapter cycle passes |
| **M5** | UI part 1: Home, Dashboard, Files/editor, Prompts, Models & Settings (key in Credential Manager); **first Windows `.exe` build** | UI tests pass; screenshots; you open the `.exe` on Windows |
| **M6** | UI part 2: Chapter screen with review, streaming, changes, diff, Next chapter; Costs, Logs, History, Plan-check | UI tests pass; screenshots; you write a real chapter |
| **M7** | Importer: extraction, template parser, model-assisted path, review screen | Importer tests pass; you import your real files on your machine |
| **M8** | README (Windows setup + daily use), sample story in-app, release 1.0 | A fresh install from the README works |

Illustrative cost (whole mode). These figures **assume** about 5k reasoning tokens per request, and use unconfirmed prices:

| Model | First version (≈40k tokens in, ≈9k out incl. reasoning) | Each change request (≈45k in, ≈9k out) |
|---|---|---|
| GLM-5.3 | ≈ $0.10 | ≈ $0.10 |
| Kimi K3 | ≈ $0.22–0.26 | ≈ $0.23–0.27 |

Real figures appear on the Costs screen from M3 on.

---

## 15. Changes after your review (2026-10-18)

| # | Your decision | Effect on this plan |
|---|---|---|
| D28 | No summaries, ledger or continuity check. Chapters are self-contained from their instructions and project files. | Removed from §2, §4, §7 and the CLI. `summarize`/`continuity-check` dropped. |
| D29 | Each chapter is a new chat; one **Next chapter** button | §1, §6.2, §12.2 |
| D30 | Changes are chat-style: setup + earlier requests + current draft + new request → new full version; all versions kept | §6.2 |
| D31 | `@ch5` is visible from the start of chapter 5 | §4.1 |
| D32 | Review screen before every request | §5 |
| D33 | Files picked automatically by mention (plus POV, `always`, listed) | §4.3 |
| D34 | Over the size limit: warn, you choose; nothing cut automatically | §4.4 |
| D35 | Name: **Devil's Workshop** (`DevilsWorkshop.exe`, package `devils_workshop`) | Throughout |
| D36 | **My adjustment:** D11's "summaries only" option for earlier scenes no longer works without summaries. It becomes `beats_only`: later scenes get the earlier scenes' beats, not their text. | §2.1 |

**Repo vs app name:** the repo is `devils-workshop`, like the app.

---

## 16. Risks and unknowns

| Risk | Mitigation |
|---|---|
| Model names, `provider.only/ignore`, and reasoning vs. `max_tokens` behaviour aren't verified (no OpenRouter access from here) | Live test in M3; config-driven, so a fix is a settings change |
| These models' fiction quality is unknown | The app makes switching models a dropdown; compare on your own chapters |
| Without summaries or a continuity check, contradictions between chapters are caught only by you, your instructions and progressions | Progressions keep character state; plan-check catches file errors. A continuity check can be added later if you want it. |
| Refusal heuristics miss some refusals and flag some false ones | Editable patterns; every decision logged; re-run any time |
| What a model will write depends on the model and provider's own policies | The app sends your prompts unchanged. It detects refusals and empty replies and falls back to your second model. It doesn't change what models allow. |
| Token estimates are off without the real tokenizers | Per-model calibration from reported usage |
| A lookahead beat can reveal a secret early | `lookahead: false` on that scene; plan-check warns |
| WebView2 missing / antivirus flags the `.exe` | Browser fallback; zipped folder build |
| Logs, versions and history hold your full story | They live only in the project folder. If you back it up to GitHub, use a **private** repo. |
