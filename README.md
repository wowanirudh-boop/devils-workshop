# Devil's Workshop

A Windows desktop app for writing novels chapter by chapter with models on OpenRouter.
You keep the story in plain text files (Markdown and YAML) in a folder of your own; the app
builds each request from them, shows it to you, sends it, and saves the result.

- Design: [PLAN.md](PLAN.md) · Research and decisions: [docs/](docs/)

## Install

1. Open the [Releases page](../../releases) and download **DevilsWorkshop.exe**.
2. Double-click it. The first time, Windows SmartScreen may say it protected your PC,
   because the app isn't signed: choose **More info → Run anyway**.

Nothing else is needed (no Python). The app opens in its own window. It starts in a few
seconds, since it unpacks itself each time.

## First run

The first screen walks you through it:

1. **Connect to OpenRouter:** paste your key and press **Save key**. It is kept in Windows
   Credential Manager, never in a file. (Settings → **Check the connection** makes one tiny
   request, well under $0.01, to confirm the key and the models work.)
2. **Models:** the writer's and the planner's models are shown; change them any time in **Settings**.
3. **Start a story:** *Plan a new story*, *New story*, *Use my own files* or *Try the sample story*.

## Your stories

**All stories** is your library: every story you open or start, in two tabs, **In progress** and
**Completed**. Each story shows its genre, key characters, chapters and words, and when you last opened
it (or completed it). Search by title, genre or a character's name; sort by date (the default: newest
first), by title (A–Z) or by when it was started; and see them as cards or as a list, which suits a long
library. You mark a story completed yourself, from its menu (…), its Overview or its Story settings, and
can put it back in progress.

A completed story can have a **new version** (its menu → Start a new version): a copy of its foundation
(settings, plan, decisions, notes, the writer's guide, the outline, the prompts; for your own files, every
file but the chapters), without its chapters, in a folder next to it. Change the plan first, or not, then
write it again; the first version stays as it is. The library shows a story's versions together: each tab
shows its latest version there, and the menu opens the others.

## Models: the writer and the planner

The app has two jobs for a model, and each has its own models in **Settings**, side by side:
- **Writer models** write your chapters and scenes, and change them when you ask.
- **Planner models** brainstorm the story plan with you, then design the chapters and brief every scene.

Each has a **main model** and a **fallback model**, which steps in when the main one fails or refuses,
and each of those four has its own **Providers…** (see [Choosing providers](#choosing-providers)), so the
same model can run on one provider for the writer and another for the planner. **Use the writer's
models** (or **Use the planner's models**) copies one pair to the other. A story can still choose its own
writer models in its Story settings, and its own main planner model on its **Planner** screen. **Activity**
shows what the planner and the writer each spent. Settings from before v0.17 keep working: the planner
starts with the models and providers the writer had.

**Options** (in each card) set how that job's models write, and start at the recommended values:

| | Temperature | Top-p | Thinking | Longest reply | Warn above |
|---|---|---|---|---|---|
| Writer | 0.8 | 0.95 | low | 32,000 tokens | 60,000 tokens sent |
| Planner | 0.5 | 0.95 | medium | 32,000 tokens | 120,000 tokens sent |

They suit the mid-size open models the app is mostly used with. The
writer writes prose: 0.8 keeps sentences fresh without the drift these models show when warmer, and a
little thinking holds a scene together without flattening the voice. There is no repetition penalty: it
grows with every repeat, so over a long reply (thinking included) it pushes a model off its commonest words
until the text falls apart. The planner designs: 0.5 keeps plans, briefs and reply formats reliable, and medium thinking checks
continuity and structure first.

**Thinking** is sent only to a model that takes it, and the Options panel says what each chosen model does:
it takes a level (the nearest level it offers is used; between two as near, the deeper one, so Medium on a model with Low and High thinks at High), it only thinks or doesn't (**Off** turns it off,
on Venice with its own "disable thinking" switch; any other choice leaves it on), or it doesn't think at all.
Thinking can run to 15,000 tokens for one scene, which is why the writer's longest reply is 32,000 (only what
is used is paid for); a reply that still runs out is sent once more with twice the room. Prose that runs far past its length or falls into a loop is never saved: it is
written once more, then the fallback model is tried. Thinking a model writes
into its reply (between `<think>` tags) is always taken out, so it never ends up in the story; it is kept
in the request's log. Change
any value and **Save settings** keeps it; **Use the recommended values** puts them back. Leave a value
empty to use the model's own default. Whatever a model doesn't support is left out of its requests.

## Planning a story from an idea

**All stories → Plan a new story** asks for a working title and where to save it, then opens the
**Planner** (it also appears in the sidebar for that story).

1. **Build the foundation.** Tell the planner your idea, as rough or as long as you like. Use **Attach…**
   to add notes or documents (any text file, PDF or Word) and reference pictures (`.png`, `.jpg`, `.webp`,
   `.gif`, up to 5 MB). The planner keeps an **agenda**: every important decision still open (the
   premise, the stakes, the ending, point of view, length, whatever this story turns on), as many as the
   story needs, most important first. Each turn it:
   - writes your answers into the **Story plan** on the right, and fills in what follows from them and the
     small details itself, marked "(my choice)". Under its message it says which sections it changed;
   - updates the agenda (**Still to decide** under the plan) from what you just said, and asks you about
     the most important open decisions, a few at a time, each with options and its recommendation. It
     never asks again about anything settled (**Decided so far**);
   - offers new ideas only, never one it suggested before. Each shows as a card with **Yes** / **No**.
     Your answers go with your next message (the button shows **Send (2 answers)**), and the accepted
     ones go into the plan.

   It says the plan is ready only when nothing important is left open. You can also type what you want
   ("make Mara older"), edit the plan yourself, or press **Fill the gaps for me** to have it settle every
   open decision itself. When you approve in your own words ("looks good, I approve"), it finalizes the
   plan.

   **The planner's memory is its files in the story folder**, not the conversation (it sees only the
   latest messages): `Story plan.md`, `Decisions.md` (settled, still open, ruled out, and your
   preferences: what you reject is never offered again, and a lasting preference you state, such as "keep
   it bleak", holds for the whole book) and `Source notes.md`. Every request is built from these files, and
   your edits to them are read back first. **A file or picture you attach is used once**, in its own
   request in the turn you attach it, guided by what you said it is for; the planner's notes on it go into
   `Source notes.md`, and from then on only the notes are sent, never the file again (a short text file
   goes in as it is). A model that can't see pictures gets only the picture's name.
   **A plan you made elsewhere** (with another tool): attach its files, choose **Plan made elsewhere** above
   the conversation and pick the files, in order. The planner reads them in full (never as notes) and
   carries them into the story plan as they are: the same events, characters and chapters, in the same
   order. How each part is shown follows your default instructions, not the other tool: what it kept
   vague, summarised or off the page is planned the way your instructions want it on the page. Its reply
   says what it shows more fully than your plan did and asks only about what your plan leaves open; you
   review and approve as usual, and the chapters are then held to your plan's events like an adaptation.
2. **Approve the plan.** Set the words per scene (800 unless the plan says otherwise) and per scene brief
   (500 for an 800-word scene; longer scenes get longer briefs), and a spending limit if you like. The
   planner then writes, one request at a time:
   - the **writer's guide**: the lasting facts the writer needs for every scene: voice and style,
     characters, world and setting, themes, your rules. No plot, so the writer can never run ahead;
   - the **chapter architecture**: as many chapters as the plan agreed (usually 5 to 20 or more), each
     with its purpose, summary, number of scenes (usually 6 to 10), length, point of view, beats and ending,
     and its long view: where each main character's arc stands, what it sets up for later and what pays
     off here;
   - every chapter's **scene list**: each of the chapter's key beats given to exactly one scene, and for
     each scene the beats it carries, how it opens, where it turns and how it ends;
   - the **scene briefs for chapter 1**. Each one stands on its own: where and when, how things stand
     coming in, who is there and what they want, the scene **choreographed as a numbered beat sheet**
     (for each beat: about how many words it takes, the action and blocking, what is said, the
     point-of-view character's inner reaction and a sensory detail, with the turn marked), the exact
     ending, and what not to contradict or reveal. The writer writes every beat, in order, as continuous
     prose. A short check follows each brief: if it hints at anything that happens later, it is written
     again without it.

   **A brief is written from what it needs to know:** the writer's guide, your preferences, its chapter's
   frame (purpose, point of view, place and time, and the long view above), its own line in the scene list,
   and how things stand coming in. Not the story plan, the source notes, other chapters or the other scenes
   (about 5,000 tokens a request, instead of about 24,000), so there is little for it to give away or drift
   into. A chapter planned before v0.23 has no long view, so its briefs get a fuller request (the plan without
   its plot, the chapters, the scenes either side) until **Re-plan all chapters…** plans it again.

   **The chapter plan is the story's only plot once it exists.** Only it is planned from the whole story plan
   and the source notes. Everything planned after it (the scene lists, the briefs, the guide planned again,
   following what was written, the changes you ask for in the Reader) gets the plan *without* its plot sections
   (Logline, Premise, Story arc, Ending) and *without* the source notes, so the whole story, or a reference it
   came from, can't pull events into a chapter that the chapter plan didn't put there. The writer's guide never
   gets the plot sections (it holds no plot); it gets the source notes when it is written before the chapters.

   **How long a brief is** sets how closely the scene is choreographed: about 500 words for an 800-word
   scene (stories planned before v0.22 have 200). **Brief length…** on the Planner screen changes it at any
   time, for the briefs written from then on; **Re-plan the briefs…** writes the briefs of every chapter not
   written yet again, from scratch, at the new length and in the beat-sheet form (their scene lists, the
   chapters and the guide stay; a copy of the outline stays in History).

   **Briefs just in time** (the default): each later chapter's briefs are written just before that chapter
   is, from what was actually written before it. The planner first reads the previous chapter's text and
   notes how things really stand at its end (and where the text departed from the plan), so the briefs
   follow on from the story as it is, not as it was planned, and changes you make while reading never
   leave later briefs out of date. Untick it when you approve to plan every brief up front.

   The chapter and scene lists come back in a simple tagged form that mid-size models keep to more easily
   than JSON; a reply that can't be read is asked for once more before planning stops. You can stop at any
   time and **Continue planning** later.

   **Several chapters at once.** When more than one chapter's briefs are due (planning every brief up
   front, or **Write all the briefs now**), the chapters are briefed side by side, as many at a time as
   **Speed** on the Auto-write screen (1 to 20), each chapter's scenes still one after another, each following
   on from the scene before. A chapter briefed beside the one before it starts from that chapter's planned
   ending (from the chapter architecture), since its last brief isn't written yet. Speed 1 writes every brief
   in order, each chapter following on from the actual last brief of the one before.

   To change something, say what should change:
   - **a chapter:** **Change this chapter…** under its scenes ("the storm comes first; cut the dinner"). Its
     scene list is planned again with your note, then its briefs; for a chapter whose briefs are written just
     in time, only the scene list now, and the briefs still just before it is written;
   - **one scene's brief:** open it with **Brief**, write what should change in the box under it, and **Write
     it again with my changes**. The planner changes the brief it had as you say, keeping the rest;
   - **the guide:** **Redo…** beside it; **every chapter:** **Re-plan all chapters…**.

   **Write all the briefs now** (on the Planner screen, while later chapters' briefs are still to come) writes
   every remaining chapter's briefs up front, from the plan, so you can read them all before anything is
   written; a change you approve in the Reader that reaches later chapters still sends their briefs back to
   just in time.

   **Change the plan** goes back to the conversation; approving a changed plan writes a new guide. The
   progress bar counts the briefs due now: with briefs just in time, that is chapter 1's when planning
   finishes, and each later chapter's when you write it.
3. **Write, one chapter at a time.** The writer gets only two kinds of file, and writes each scene on its
   own, with the guide and that scene's brief (and your system prompt) and nothing else, so a chapter's
   scenes are written at the same time:
   - `Writer guide.md`: sent with every scene;
   - `Chapter outline.md`: the chapters and their scenes' briefs, each with its word target.

   For you, not sent: `Story plan.md`, `Decisions.md`, `Source notes.md`, `Chapter architecture.md` (every
   chapter in full), `Continuity.md` (how things stand after each scene) and `planner/` (the conversation,
   your attachments and the planner's progress). Edit any of it under **Story files** (your edits are read
   back). Every planning step can be undone in **History**.
4. **Read, ask for changes, then write the next chapter**, all in the **Reader**. Choose a chapter, and
   beside it, under **Ask for changes**, tell the planner what to change ("make the argument in scene 3
   colder; she shouldn't cry yet"). It plans the change: which scenes change, whether each is **revised**
   (its text kept and changed only where needed) or **written again** from a new brief, each scene's
   updated brief, what it means for later chapters, and any ideas; if it needs your answer first, it asks.
   **Approve and make the changes**, and the writer changes those scenes at the same time. The chapter's
   text is replaced: no earlier version is kept, but History has one Undo for each change. When you're
   happy, **Write chapter N** has the planner follow the chapter as it now is, write the next chapter's
   briefs, and the writer write its scenes, without leaving the Reader. Auto-write does the same for a
   range of chapters, and pauses after each one so you can read it (you can switch the pause off).

The planner never changes the story's text, and the writer never changes the planner's files: the app
refuses either.

## Prompts, and editing with AI

Every prompt can be edited. **Prompts** (in the sidebar) lists what the writer and the planner are told
for each job: writing, planning a chapter's scenes and smoothing joins for the writer; the planner's own
system prompt, brainstorming, notes on what you attach, the writer's guide, the chapters, the scene lists,
the scene briefs and their check, following what was written, the changes you ask for in the Reader, and
editing a file for you. Saving one there makes your copy for every story; **Story files → Prompts** keeps a copy for
one story, and a story's copy wins over yours. The story's system prompt is edited there too. The
writer's system prompt is the writer's only: the planner has its own, and is given the writer's as
instructions to plan within.

A PDF or Word file (a system prompt, a story file or a default instruction) can't be edited as it is:
**Edit as text** saves its text as a `.md` file you can edit, which is used in its place from now on (the
original is kept, and no longer sent).

Every text file can be edited two ways: by hand, or with **Ask AI to change** beside its editor (story
files, the planner's files, story settings, prompts, system prompts, default instructions). Say what
should change; the planner's model proposes it, you see exactly what would change, line by line, and
**Accept and save** saves it (with an Undo in History). It never edits the story's own text: changes to
the prose go to the writer, in the Reader.

## Story settings

Each story's settings are in **Story settings.md** in its folder, a Markdown file you can read and edit:
"- Name: value" lines under **Story** (title, genre, key characters, in progress or completed), **Writing**
(point of view, person, tense, words per chapter, whole chapters or scene by scene, and what a scene sees:
earlier scenes' text, their beats, or only its own brief), **Models** (this story's own writer models) and
**Files** (for your own files, what each one is for). The writer reads it on every request. **Story files →
Settings** shows it as a form. Stories made before v0.17 had `story.yaml`; it becomes Story settings.md the
first time the story is opened (the old file is kept in History).

## Your own story files

Already have your story in your own files? Any text file works (`.md`, `.txt`, `.json`, `.yaml`,
`.csv`, `.html`…), and so do PDF and Word (`.docx`) files. **All stories →
Open a folder** and pick that folder. The app lists its files; choose what each one is for:

| Choice | What it does |
|---|---|
| Every chapter | Sent with every chapter |
| Chapter outline | Split at its "Chapter 1", "Chapter Two", "# Chapter 3: Title"… headings; each chapter gets only its part. Text before the first chapter is sent with every chapter |
| Chapter N only | The instructions for that one chapter |
| System prompt | Sent as the system message |
| Not sent | Never sent |

The app never changes your files unless you ask it to. The list is saved in `Story settings.md` in the
same folder; change it any time under **Story files → What's sent**. To bring in another file, use
**Add files…** there (or **Add** under *Files*); it is copied into the folder and sent the way its
name and text suggest, which you can change. "Scene N" headings inside a chapter's
instructions let you write it scene by scene.

- **Lengths:** if your outline states them, they're used: "Target 4,700 words" near the top of a
  chapter, and a short line like "*Morning · 800 words*" under a scene heading. A `words: 4700` line
  right under a heading does the same. Otherwise **Words per chapter** applies, shared among the
  chapter's scenes.
- **The same document twice:** two files that are the same document, such as a handoff saved as
  both `.md` and `.docx`, would give the model everything twice. With a chapter outline, every
  scene would come twice. The copy starts as **Not sent** when you set up the folder or add it.
  If both are already being sent, Overview, every chapter, Auto-write and What's sent show a red
  warning with **Stop sending <the copy>**.

Pictures, archives and other files that aren't text
are listed but can't be sent; old Word `.doc` files and spreadsheets need saving as `.docx` or
CSV first. Scanned PDFs (pictures of pages) have no text
the app can read.

## Default instructions

Instruction files you'd otherwise give every story (your usual system prompt, house style,
rules) are added once: **Default instructions** in the sidebar → **Add files…** (any text file,
such as `.md`, `.txt` or `.json`, or a PDF or Word file) or **Write one here**. For each, choose:

| Choice | What it does |
|---|---|
| System prompt | Sent first in every writing request, before the story's own system prompt (the planner is given it too, as the writer's rules to plan within) |
| Writer and planner | Sent to the writer with every chapter and to the planner with every request, so the plan and the prose both follow it: a goal, terminology, rules. A file you haven't chosen for starts here |
| Writer only (every chapter) | Sent with every chapter. The planner is given it too, as the writer's rules to plan within, so the guide and the briefs never ask for what it forbids |
| Planner only | Your planning preferences for every story ("I like morally grey leads", "never more than 15 chapters"), sent with every planner request |
| Not sent | Kept, but not used |

The writer gets "Writer and planner" and "Writer only" files as your instructions, at the end of its system
prompt, after the built-in writing instruction: where they differ, yours are the ones it follows.

Every story uses them, old and new; edit them there any time and the next request uses the new
version. A story can switch them off under **Story files → Prompts** (or Settings). A PDF or Word file
can be turned into an editable text copy with **Edit as text**, and any of them changed with **Ask AI to
change**. If a story already
has a file with the same text, it isn't sent twice. They are kept in the app's folder
(`%APPDATA%\DevilsWorkshop\Default instructions`), with a backup of every edit.

## Daily use

The sidebar has the story's sections:

- **Overview:** where the story stands and the next step (*Write chapter 4*), problems in
  your files, spending and models.
- **Chapters:** the chapter list and the chapter you're on. *What this chapter should do*
  shows its instructions and scenes. *Write chapter N* opens **Review before sending**: what's
  included and why, and the request itself (readable, exact, or editable); then **Send**. The
  text appears as it is written.
  - Not right? Type in **Ask for changes** at the bottom → **Review & send**. You get a new full
    version; every earlier version is kept (⋯ → *Compare versions*, *Restore an earlier version*).
  - Too short? **Continue writing**. Happy? **Mark as final**, then the next chapter's button
    (each chapter starts a fresh chat).
  - In scene mode, **Write all scenes automatically** writes the remaining scenes the way set on
    the Auto-write screen (below).
- **Auto-write** writes a range of chapters without stopping for review, several requests at the
  same time (**Speed**, 1 to 20; earlier chapters go first). Chapters never see each other's text, so
  writing them side by side changes nothing in them. Scene by scene, either:
  - **one after another** (the default): in the chapter's one chat, so each scene follows on from
    the actual text of the one before it, and the chapter reads as one piece; or
  - **at the same time** (faster): once a chapter's scenes are planned, they are all written at
    once, each from the scene plan (the scenes before it as beats, not their text). One small extra
    request then rewrites the opening paragraph or two of each scene from scene 2 on, so it follows
    on from the end of the scene before. The rest of each scene is kept as written, so the chapter
    may not feel continuous. Both steps can be undone in History.

  It can plan scenes for chapters that have none, asks the model to carry on once if a reply is
  short, and stops, saying why, if a request fails, is over your size limit or would pass your
  spending limit (counting the requests under way), or when you press Stop. Requests already
  under way are finished and saved first, unless you press **Stop now**.

  **How long it will take** is shown before you start (from your last run's pace) and while it runs
  ("About 40 min left · done around 02:30", from this run's own pace once it has written a scene). The
  planning run shows the same from how long each of its steps usually takes you, and a planner reply says
  how long one usually takes. Every request's time is already in the story's logs; the estimates are rough,
  rounded, and correct themselves as the run goes.
- **Cut off by a restart?** A planning run or an Auto-write run that the app closing, the PC restarting
  or a crash cut off carries on by itself the next time the app starts (with Start with Windows on, as
  soon as you sign in). It waits up to 2 minutes for the internet first, then goes on from the next brief,
  list, chapter or scene still to do, within what was left of its spending limit. Only the one request
  under way when it was cut off is lost and sent again. A run you stopped, that failed, that finished or
  that paused after a chapter isn't carried on, and neither is one cut off more than 48 hours earlier.
- **Read** shows the whole story, or one chapter, set for reading, with scenes joined and contents.
  - **Aa** sets the text size, line spacing (tight, normal, roomy), width (narrow, wide, full) and page
    colour (light, sepia, dark). On a phone it starts at full width with a smaller font, so more text
    fits.
  - **Full page** fills the whole screen with the story; **✕ Exit full page** (or Esc) brings back the
    app, at the same place in the story.
  - **Save as PDF…** makes a PDF book in `exports/`: a title page, contents, each chapter on a new page,
    with A5 (good on phones), A4 or Letter pages and four text sizes. It covers the whole story or just
    the chapter you're reading. A **web page (.html)** is still offered.
  - On the phone the button is **Download…**, and the file goes to the phone.
- **Story files:** *Files*, *System prompt*, *Problems* (anything in the files the app can't use),
  and *What's sent* for your own files. Under *Files*:
  - **New** writes a new file; **Add** brings in files from your computer (any text file, PDF or
    Word). In a story made by the app you choose what each one is (a character, a place, lore,
    a subplot, a note, or the outline, system prompt or a bible file it replaces); PDF and Word files
    are turned into text.
  - Click a file to edit it. PDF and Word files can't be edited here; the screen shows the text the
    app reads from them.
  - **⋯ → Rename…** or **Delete…**. Every add, edit, rename and delete is kept in History, so one
    **Undo** puts it back (for your own files, together with what it was for). `Story settings.md` and
    written chapters can't be deleted here.
- **Activity:** every request, what it cost and its full text, and what the story has spent: this version, and every version
  together (the number on the story's card). **History:** every saved
  change in plain words, each with Undo.
- **Settings:** connection, Venice AI, Experiential Labs, other services, models and their providers, privacy,
  phone access, starting with Windows, appearance (light, dark or match Windows). Narrow windows show the sidebar as icons.

**Copying text.** Select any text and press Ctrl+C (Ctrl+A selects everything in a text box or editor).
In the app window, right-click for **Copy**, **Cut** and **Select all**. The ⧉ **Copy** buttons copy a
whole thing at once: each planner message, the story plan, a scene brief, a chapter's text, the story in
**Read**, the request in **Review before sending**, a request's reply, what was sent and its error details
in **Activity**, and every problem or warning message. They work on the phone too.

## Venice AI

Venice's own models can sit beside OpenRouter's.
- **Settings → Venice AI:** paste your Venice API key and press **Save key**. It's kept in Windows
  Credential Manager (or set `VENICE_API_KEY`). **Check the connection** sends one tiny request to make
  sure it works.
- Venice's text models then appear in every model list as **Venice · <model>**: the main and fallback
  models of the writer and the planner. Their prices, context size and whether they see pictures are known, so
  cost estimates and pictures work as they do for OpenRouter models.
- **Mix them freely.** For example, write with a Venice model and fall back to an OpenRouter one, or the
  other way round. If one service turns your key or credit down, the fallback on the other service is
  still tried.
- Venice normally adds its own system prompt to every request. The app turns that off, so only your
  instructions are sent. The switch **Let Venice add its own system prompt** turns it back on.

## Experiential Labs

Experiential Labs (experientiallabs.ai) is a gateway to many models, some of which it runs itself.
- **Settings → Experiential Labs:** paste your key (create one at platform.experientiallabs.ai; it starts with
  `xpl_`) and press **Save key**. It's kept in Windows Credential Manager (or set `EXPLABS_API_KEY`).
- Saving the key adds its models to the lists: choose them in **Writer models / Planner models**.
- Every model your key can call appears as **Experiential Labs · <model>**, with its price, context size,
  whether it sees pictures and the thinking levels it takes, from Experiential Labs' public catalog. The
  cost of each request is what Experiential Labs reports.
- **Check the connection** asks for the models your key can call (it costs nothing).
- When your credit runs out or a spend limit is reached, the request stops at once (no waiting and
  trying again) and the fallback model is tried.

## Other services

Any service that speaks OpenAI's chat format can sit beside OpenRouter and Venice: for example
Featherless, NanoGPT, or a model on your own PC in LM Studio or Ollama.
- **Settings → Other services → Add a service:** give it a name, its address (base URL) and its API key,
  if it needs one. For example `https://api.featherless.ai/v1`, `https://nano-gpt.com/api/v1`,
  `http://localhost:1234/v1` (LM Studio) or `http://localhost:11434/v1` (Ollama). The key is kept in
  Windows Credential Manager, never in a file. **Check the connection** asks the service for its models.
- Its models then appear in every model list as **<Service> · <model>**: the writer's and the planner's
  main and fallback models. Most services don't report prices, so the price shows as unknown and costs as
  $0 (estimated); context size and pictures are used when the service reports them.
- **Each service is its own service.** If one turns your key or balance down, a fallback on another
  service (OpenRouter, Venice or another added one) is still tried.
- To change a service's address or key, **Remove** it and add it again.

## Choosing providers

On OpenRouter, a model is often run by several providers, at different prices and speeds. **Settings →
Writer models / Planner models → Providers…** next to a model (and next to the model on the Planner
screen) lists them all, each with:
- **price** in and out per million tokens, and the rough cost of a chapter;
- context size and uptime;
- **speed**: tokens per second and time to first text. These are OpenRouter's figures for the last 30
  minutes when it gives them; otherwise the speed your own requests got from that provider.

Then choose how requests pick a provider:
- **Let OpenRouter choose** (the default), **Cheapest first**, **Fastest first** or **Quickest to start**;
- **My choice**: mark providers **Prefer** (tried in the order you marked them), **Only** or **Never**.
  **If my choices fail, allow other providers** decides whether OpenRouter may go elsewhere.

Each model keeps its own choice, separately for the writer and the planner. The **Privacy** switch (only providers that don't store or train on your
text) applies to every model, on top of its choice. Venice models have no providers to choose: Venice
runs them itself.

## Using it on your iPhone (Chrome)

The PC still does all the work: your stories, your OpenRouter key and every request stay on it.
The iPhone only shows the screens, in Chrome (Safari works the same way). So **the PC must be on,
with Devil's Workshop open**, whenever you use it from the phone. The window can be minimized;
closing it closes the app, and phone access with it.

While a chapter, an auto-write run or a planning run is under way, the app stops Windows from going
to sleep because the PC looks idle (it does look idle while you use it from the phone), and lets
go as soon as it's done. The screen can still turn off, and you can still put the PC to sleep
yourself. Between requests the usual sleep settings apply, so to reach the PC whenever you want,
set **Windows Settings → System → Power → Sleep** to **Never** while it's plugged in.

**Turn it on (once, on the PC):** **Settings → Phone and other devices**. Set a password (at
least 8 characters; only a scrambled form of it is saved), then switch on **Allow access from my
phone**. No restart is needed. If Windows asks whether Devil's Workshop may use your network,
allow it on **Private networks**. The card then shows the address to open and a QR code for it.

**At home, on the same Wi-Fi:** on the iPhone, scan the QR code under **At home (Wi-Fi)** with
the Camera app, or in Chrome (touch and hold the Chrome icon → **Scan QR Code**), or type the
address (something like `http://192.168.1.20:8730`). Enter the password once and leave
**Remember this phone** ticked. If the phone can't connect, check that Windows treats your Wi-Fi
as a private network. On home Wi-Fi the connection isn't encrypted; use Tailscale (below) if
that matters to you.

**Away from home, with Tailscale:** Tailscale is a free app that links your own devices
privately, so nothing is opened to the internet.

1. On the PC: install Tailscale from tailscale.com/download and sign in (a free personal account
   is enough).
2. On the iPhone: install Tailscale from the App Store, sign in with the same account and leave
   it switched on.
3. In Settings, the **Anywhere (Tailscale)** tab now shows the PC's Tailscale address (like
   `http://my-pc.tail1234.ts.net:8730`) and its QR code. Open that in Chrome. It works at home
   too, so it's the one to keep. The connection is encrypted by Tailscale.

**As an app icon:** in Chrome on the iPhone (iOS 16.4 or later), open the address, tap the
**Share** button, then **Add to Home Screen**. The icon opens Devil's Workshop full screen. It
keeps its own sign-in, so enter the password once more there.

**Without your PC:** the app can run on a small cloud server instead (about €6 a month), so your phone
reaches it even when the PC is off. [docs/cloud-setup.md](docs/cloud-setup.md) walks through it, step by step:
a Hetzner server, one setup command, and Tailscale.

**After the PC restarts:** **Settings → Start with Windows** starts Devil's Workshop in the background
(without a window) whenever you sign in to Windows, with phone access as you set it, so the phone can reach
it without anyone opening the app. Open Devil's Workshop as usual to see it in a window: while it runs in the
background, that opens a window onto it, and closing the window leaves it running (in that window, paste
folder paths instead of using Browse…). **Stop the background app** is in the same card when it is running
that way.
- Windows still has to be signed in to after a restart. For the restarts Windows Update makes, switch on
  **Windows Settings → Accounts → Sign-in options → Use my sign-in info to automatically finish setting up
  after an update**: Windows signs you in and locks the screen, and the app starts.
- To have it start after any restart (a power cut, say) before anyone signs in, use Task Scheduler instead
  of the switch: **Create Task…**, choose **Run whether user is logged on or not**, add the trigger **At
  startup**, and the action `DevilsWorkshop.exe` with the argument `--background`; on **Settings**, untick
  **Stop the task if it runs longer than**. Windows asks for your password to save it. Tailscale also starts
  before sign-in, so the Tailscale address works as soon as the PC is up.
- Set **Sleep** to **Never** (above), or the PC goes to sleep between your phone's visits.

**Good to know:**

- The screens fit the phone: the sidebar becomes the **☰** menu, chapters have a picker at the
  top, and Story files has a file picker above the editor.
- If the phone sleeps or you switch apps while a chapter is being written, the writing goes on
  on the PC; when you come back, the page reconnects and shows it. Auto-write and the planner
  carry on too, however long the phone is away.
- The OpenRouter key and the phone settings can only be changed on the PC. **Sign out of this
  device** is at the bottom of the menu; **Settings → Phone and other devices → Port and
  signed-in phones** signs out every phone, and changing the password does too.
- After several wrong passwords, the sign-in page makes you wait before trying again.

## Privacy: running hidden, and a passcode

**Run hidden** (Settings → Start with Windows and run hidden) keeps Devil's Workshop out of sight: it runs in
the background, with no window and no taskbar button, and only a plain icon called **Workshop** beside the
clock shows it is there (Windows 11 usually tucks a new icon under the **^**). Click the icon for:
- **Open**: a window onto the app. Closing the window hides the app again; writing, planning and phone
  access carry on.
- **Lock now** (with a passcode).
- **Quit**: stops it until you open it again.

Opening Devil's Workshop from the Start menu does the same as **Open**. With **Start in the background when I
sign in to Windows** on as well, it is running, hidden, from the moment you sign in. In a window onto the
hidden app, paste folder paths instead of using Browse….

**Passcode lock** (Settings → Passcode lock) asks for a code of your own (at least 4 characters; digits are
fine) before this PC's screens show anything: whenever the app's window opens, after it has gone unused for a
while (5 minutes unless you choose otherwise, or only when you lock it), and when you choose **Lock now** (at
the bottom of the sidebar, or on the icon). Writing and planning already under way carry on while it is
locked. After several wrong codes it makes you wait before trying again. Only a scrambled form of the code is
saved; your phone keeps its own password and isn't locked by it. The lock hides the screens from someone at
your PC; it doesn't encrypt the story files on the disk.

**Forgot the passcode?** Quit the app (the icon → Quit, or Task Manager), open
`%APPDATA%\DevilsWorkshop\settings.yaml` in Notepad, empty the `code_hash:` line under `lock:` (so it reads
`code_hash: ''`), save, and open the app again.

## Removing a story

Made a story by mistake, or finished with one? On **All stories**, the **⋯** on its card has:

- **Remove from this list**: the folder and its files stay where they are.
- **Delete this story…**: the whole folder goes to the Windows **Recycle Bin**, so you can still
  restore it from there. For a folder of your own files, the dialog lists the files of yours that
  would go with it. The app won't delete a whole drive, your user folder, a folder like Documents,
  or a folder that holds other stories.

## A story folder made by the app

*New story* creates this layout (not needed if you use your own files):

```
Story settings.md   this story's settings: title, genre, key characters, status, point of view,
                    tense, words per chapter, whole chapters or scene by scene, what a scene sees,
                    its own writer models, and (for your own files) what each file is for
system_prompt.md    sent with every request
outline.md          "# Chapter N: Title" with instructions; optional "## Scene N" beats
bible/              style guide, story bible, world rules (always sent)
characters/ places/ lore/   one file each; sent when mentioned in the chapter's instructions
subplots/           with a status per chapter
chapters/           the text (chapters/versions/ keeps earlier versions)
notes/              never sent unless you add a note in the review screen
```

In a character file, `## @ch5 add`, `## @ch5.2 replace Appearance`, `## @ch7 set` change
it from that chapter (or scene) on, and `## secret @ch8` stays hidden until chapter 8.
The sample story and the files starting with `_example` show every option.

## If something goes wrong

- **The window doesn't open**: start it in your browser instead: in File Explorer, go
  to the folder with the .exe, type `cmd` in the address bar, then run
  `DevilsWorkshop.exe --browser`.
- **A request failed**: the chapter screen says why, and nothing is overwritten. Every
  attempt is in **Logs**.
- **App log**: `%APPDATA%\DevilsWorkshop\app.log`.

## For developers

```
pip install -r requirements.txt pytest==9.1.1
python -m pytest                      # all tests (the API is faked; no network)
python -m devils_workshop app --browser
python -m devils_workshop app --background   # no window: as when started with Windows
python -m devils_workshop --help      # command line: init, plan-check, preview, models, cost, live-test...
```

The Windows build runs in GitHub Actions (`.github/workflows/build-windows.yml`).
On a Linux server it runs as a systemd service: `deploy/setup-server.sh` sets it up (see
[docs/cloud-setup.md](docs/cloud-setup.md)), and the `server` job in `.github/workflows/tests.yml` checks that
setup on Ubuntu.
