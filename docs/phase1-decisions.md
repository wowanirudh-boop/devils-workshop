# Phase 1 Decisions

Your answers to the open questions in `phase1-research-report.md`, recorded 2026-10-18. Where this file and the report disagree, **this file wins**.

Items marked *(interpretation)* are my reading of an answer that didn't map exactly to an option. Please correct any of them during the PLAN.md review.

## Scope changes from the original brief

| Original brief | Now |
|---|---|
| Command-line tool | **Windows desktop app with a UI.** The story engine and command-line commands stay underneath, for testing and scripting. |
| API key from the `OPENROUTER_API_KEY` environment variable only | **Settings screen**, with the key stored in Windows Credential Manager. The environment variable still works and takes priority. |
| Editable prompt templates with variables | **One system prompt per story.** The app inserts project files automatically. Built-in job instructions are editable under Advanced. |
| Prior-scene handoff text | **No word-for-word text carries over between chapters.** Each chapter is a fresh request. |
| Report section 3: "skip GUI" | Superseded by the desktop app decision. |

## Story input and import

1. **Architecture documents:** several files per story, of types `.md`, `.txt` and `.pdf`. The importer reads a whole folder.
   - PDF text is extracted with `pypdf`. Scanned (image-only) PDFs won't import without OCR, which is out of scope.
2. **Importer:** a template parser for documents that follow the app's heading layout, plus a model-assisted extraction for free-form documents. The review step is **mandatory** before anything is saved.

## Models and API

3. **Model choice** *(interpretation of "The tool should use an API key")*:
   - You pick a primary and a fallback model in the app, from OpenRouter's live model list.
   - There is an app-wide default, which each story can override.
   - Candidates `moonshotai/kimi-k3` and `z-ai/glm-5.3` are still **not API-confirmed**.
4. **Model-name check:** a Models screen, plus a check at startup that warns if a configured model name doesn't exist. You run the one real-API test on your own machine.
5. **Unsupported settings** (e.g. `temperature` on Kimi K3): dropped automatically, with a warning shown and logged. Based on each model's `supported_parameters`.
6. **Providers:** an allow-list and a block-list in settings. The "no data-collecting providers" filter is available but off by default.
7. **Both models fail, refuse or return nothing:** stop and report. Nothing is overwritten. What was sent and what came back are saved to the log.
8. **Spending:** warn only. Cost is tracked per call, per chapter and per project, and is never blocked.

## Writing

9. **Generation mode:** whole chapter in one request, or scene by scene. Chosen per story, overridable per run.
10. **Context across chapters:** no prose from earlier chapters is sent word for word. Each chapter is a fresh request built from:
    - the project files;
    - the chapter summaries and continuity ledger *(assumption: tell me if you want these left out too)*.
11. **Context within a chapter** (scene mode): later scenes get the earlier scenes of the same chapter word for word. This is a setting; you can switch it to summaries only.
12. **Lookahead:** the model sees the remaining beats of the current chapter, marked "do not write yet". It sees nothing from later chapters.
13. **Secrets:** author-only notes with a reveal point (chapter, optionally scene). They are never sent before that point.
14. **Progressions:** use the latest one at or before the current chapter/scene. Never a future one.
15. **Word count:**
    - project default of 3,000 words per chapter;
    - each chapter can set its own target in the outline;
    - in scene mode, the target is split evenly across the scenes.
16. **Scenes in the outline:** sometimes present. If a chapter has only chapter beats and you choose scene mode, the model proposes a scene breakdown for you to approve or edit first.
17. **POV and tense:** a book-level default, overridable per scene.
18. **Revise:** you give free-form instructions each time. The model gets the original text, your instructions and the story context. The old version is kept as a numbered backup, and the app shows what changed.

## Prompts and review

19. **System prompt:** one per story, applied to every job in that story. Built-in instructions for each job (summarize, continuity check, scene proposal, import, revise) have defaults and can be edited under Advanced.
20. **Pre-send review:** before each generation you can:
    - tick or untick any file or section the app picked;
    - add files it didn't pick;
    - edit the full assembled text.

    These overrides apply to that run only; project files are unchanged.
21. **After each chapter:** the app saves a summary and a contradiction report. It then **proposes** ledger entries and new progressions, and you accept or reject each one before anything changes.

## App, install and storage

22. **UI:** NiceGUI shown in its own window via pywebview, using Edge WebView2, which is built into Windows 11.
23. **Install:** a double-click `.exe`, built by GitHub Actions on a Windows build machine.
    - It's unsigned, so Windows SmartScreen will warn on first run.
    - I can't build or test the `.exe` in this Linux build environment (PyInstaller doesn't cross-compile). You test the `.exe` on your machine; I test the UI here in a browser.
    - Builds use GitHub Actions minutes on your private repo. My training knowledge says the free plan includes 2,000 minutes a month, with Windows minutes counted double. Medium confidence; check your GitHub billing page.
24. **API key:** pasted once in Settings and stored in Windows Credential Manager via `keyring`. It is never written to project files, prompts or logs.
25. **History:** built-in snapshots. Before any change, a dated copy of the affected files goes to a hidden history folder inside the project. A History screen restores any copy.
26. **Logs:** every assembled prompt and response is saved inside the project folder. Logs contain your story text, so they stay with the project.
27. **Libraries:**
    - runtime: `nicegui`, `pywebview`, `httpx`, `PyYAML`, `keyring`, `pypdf`;
    - build/test only: `pyinstaller`, `pytest`.

    Exact versions get pinned in PLAN.md.

## Plan review (2026-10-18)

These supersede the earlier items where they conflict. Details are in `PLAN.md` §15.

28. **No summaries, ledger or continuity check.** Chapters are self-contained, built from their instructions and the project files. This supersedes the assumption in D10, and D21.
29. **Each chapter is a new chat.** One **Next chapter** button opens the following chapter's chat.
30. **Chat-style changes.** The model gets the setup, your earlier change requests, the current draft and the new request, and writes a new full version. All versions are kept. This refines D18.
31. **`@ch5` timing:** visible from the start of chapter 5.
32. **Review screen before every request.**
33. **File selection:** files are picked automatically by mention, plus the POV character, `always` entries and entries listed in the outline.
34. **Over the size limit:** the app warns and you choose. Nothing is cut automatically.
35. **App name:** Devil's Workshop. File `DevilsWorkshop.exe`, Python package `devils_workshop`.
36. **D11 adjusted:** the "summaries only" option becomes `beats_only`, since summaries no longer exist.

## Build approach (2026-10-18)

37. **Straight through to a working app.** You asked for the desktop app itself rather than terminal steps. Milestones M4–M6 and the Windows `.exe` are built without approval stops between them, and you review the result as a whole.
    - **The OpenRouter live check** is now the **Test connection** button in Settings.
    - **Still to come:** the importer (M7: turning your existing architecture documents into story files) and code signing for the `.exe`.
    - **Not built:** the command-line `write-chapter`/`revise` commands. The app does both.

## Your own files (2026-10-18)

38. **A folder of your own files opens as it is.** Opening a folder without `story.yaml` used to fail with "has no story.yaml". Now the app lists the folder's `.md`, `.txt` and `.pdf` files and you choose what each is for: *every chapter*, *chapter outline* (split at its "Chapter N" headings), *chapter N only*, *system prompt* or *not sent*. The list is saved in `story.yaml`, in a `files:` section. Your files are never converted, moved or changed.
    - "Scene N" headings inside a chapter's instructions make scenes. Scenes the app proposes and you approve go into `app-scenes.md`.
    - With your own files there is no mention detection, no `@chN` timeline and no point-of-view line unless `story.yaml` sets one: the files themselves carry that. A new file in the folder is reported until you choose what it is for.
    - This takes the place of the importer for now. The model-assisted importer (M7) is still possible if you later want your files converted to the app's format.

## Writing without clicks, and reading (2026-10-18)

39. **Auto-write.** You asked for writing without approving each step. A run writes a range of chapters, each still its own chat, with no review screen. Defaults: chapters that already have text are left alone (tick to write them again as a new version); a reply under 60% of its target is continued once; chapters in scene mode without scenes get a plan that is used without asking (undo in History).
    - **Coherence within a chapter:** all of a chapter's scenes are written in that chapter's one chat, so each scene sees the scenes before it in full, plus the beats of later scenes marked "do not write yet". Nothing is carried between chapters, as decided in D28–D29.
    - **It stops, with the reason, when:** both models fail on a request (nothing saved, D27); a request is over the size limit (in place of the "warn, I choose" of D34, which needs you); the next request could pass the spending cap (pre-filled at 1.5× the rough high estimate); or you press Stop.
    - The review screen still comes before every request you start by hand (D32).
40. **One click for a chapter's scenes.** "Write all scenes automatically" on the chapter screen runs auto-write for that chapter only.
41. **Read screen.** The whole story or one chapter, scenes joined by a break (scene labels on request), with contents, text size and light/sepia/dark. "Save as one file" writes a self-contained `.html` to `exports/`.

## Interface redesign (2026-10-18)

42. **A new interface (v0.4).** You found the old one dated and hard to use. It was audited from screenshots
    of every screen ([ui-audit.md](ui-audit.md)) and redesigned ([ui-redesign.md](ui-redesign.md)):
    - The sidebar follows the writing: Overview, Chapters, Auto-write, Read, Story files, then Activity and
      History. Files, System prompt, Problems and What's sent are tabs of Story files; Costs and Logs
      became Activity. Old addresses still open the matching place.
    - Each screen has one main action; the chapter screen shows what the chapter should do above the text;
      the review before sending reads as a summary first, with the exact text a click away.
    - Plain words in the main flow (model names instead of codes, no file paths or tokens except where
      they matter), consistent money, notifications out of the way, light and dark themes, and an icon
      sidebar for narrow windows.
    - Word files (.docx) can be used as your own files.

## Faster writing, and instructions for every story (2026-10-19)

43. **Several requests at the same time.** You asked for writing that isn't one request after another.
    - **Chapters side by side:** a run sends up to 1–6 requests at once (Speed, default 3); a free slot
      goes to the earliest chapter waiting. Nothing changes in the chapters themselves: each was already
      its own chat with nothing carried between chapters (D28–D29).
    - **A chapter's scenes together (your choice: "smooth the joins"):** once the scenes are planned,
      they are all written at the same time, each in a fresh chat of its own with the scenes before it
      as beats (the `beats_only` form of D36) and the later ones marked "do not write yet". One small
      request per chapter then shows the model the end of each scene and the opening of the next, and
      asks for a new opening (the first paragraph or two) for scenes 2 onwards; nothing else in a scene
      changes. A new opening out of proportion to the one it replaces isn't used. Its instruction can
      be changed per story like the others (`prompts/jobs/join.md`).
    - **Trade-off, stated plainly:** a scene written together sees the plan of the scene before it, not
      its text, so a detail invented there (a name, an object) is unknown to it; the smoothing fixes the
      transitions, not such details. "One after another" (the old way) remains a choice.
    - **Stopping:** after a failure, a size problem, the spending cap or "Stop after these", no new
      request starts; requests under way are finished and saved, and a chapter whose scenes are all
      written still gets its joins smoothed. "Stop now" abandons everything under way. The spending cap
      counts the worst case of the requests under way. Joins left unsmoothed are done by the next run
      for that chapter, or from the chapter screen.
44. **Default instructions.** Files you add once in the app (Default instructions in the sidebar) and
    every story uses: *system prompt* (sent first, before the story's own system prompt), *every
    chapter* (sent before the story's own files) or *not sent*. They live in the app's folder, not in
    any story, so editing one changes what every story sends from its next request (D38's "your files
    are never changed" still holds for story folders). A story can switch them off
    (`default_instructions: off` in `story.yaml`); a file whose text a story already has isn't sent twice.

