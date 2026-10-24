# Phase 1 Research Report: Long-form Fiction Harness

Date: 2026-10-18. Status: **awaiting your review and answers (section 7)**. No code has been written.

---

## 0. How this research was done, and its limits

| Item | What happened | Effect on this report |
|---|---|---|
| Web access | This cloud container could not reach any external host directly (`curl` and page fetches failed DNS for openrouter.ai, docs.novelcrafter.com, docs.novelai.net, docs.sudowrite.com). Only a web *search* tool worked, and it returns summaries of indexed pages, not the pages themselves. | Every fact below comes from search-engine summaries of the source pages. Summaries can misquote. I capped confidence at **Medium-High** even for official pages. |
| OpenRouter API | **I could not call `/api/v1/models`**, so the model slugs are **not API-confirmed**, as you required. Section 5 gives candidate slugs from openrouter.ai's own model pages, plus a one-line command to confirm them. | Treat the slugs as unconfirmed until that command is run. |
| Your architecture file | Not in this repo, by design. Your story files live in a separate project folder on your machine. | Section 6 describes how the importer copes without me seeing your document. |

Source-quality key used below:
- **Official**: the vendor's own docs, help center or changelog.
- **3rd-party**: reviews, blogs, other API resellers.
- **Anecdotal**: Reddit, forum posts.

I disregarded a number of unofficial mirror sites that copy OpenRouter pages (for example `*.sslip.io`, `shopot.ai`, `corpsoft24.ru`).

---

## 1. Novelcrafter: feature design (public docs only)

| Feature | How it works, per public docs | Confidence | Source |
|---|---|---|---|
| **Codex entries** | Typed entries (Subplot is one confirmed type). Each has a name, **aliases**, a description, optional **custom details**, relations to other entries, a **tracking** toggle and an **AI-context** setting. Entries with no text are filtered out of prompts. | Med-High | Official ([anatomy](https://novelcrafter.com/help/docs/codex/anatomy-codex-entry), [types](https://www.novelcrafter.com/help/docs/codex/codex-types)) |
| **Custom details (fields)** | Extra boxes under the description that split information into segments for the AI. Types: *Line* (single line, e.g. Occupation), *Text* (paragraphs, e.g. Backstory), *Dropdown* (user-defined options, e.g. Status: Alive/Deceased/Missing). There is a 4th type I could not retrieve. | Medium | Official course ([custom details](https://www.novelcrafter.com/courses/ultimate-beginners-guide/structuring-your-codex-with-custom-details)), [codex details](https://www.novelcrafter.com/help/docs/codex/codex-details) |
| **AI-context modes** | Always include / Include when detected (mention-based) / Don't include when detected / Never include. | Med-High | Official ([codex context in prompting](https://www.novelcrafter.com/help/faq/ai-and-prompting/codex-context-in-prompting), [Dec 2024 changelog](https://feedback.novelcrafter.com/changelog/december-21-2024)) |
| **Mention detection** | Matches entry names **and aliases** in prose, scene summaries, notes and snippets. Requires tracking to be on. The docs imply, but do not state explicitly, that alias hits trigger inclusion. | Medium | Official ([tracking](https://www.novelcrafter.com/help/docs/codex/codex-tracking), [aliases](https://www.novelcrafter.com/help/docs/codex/aliases)) |
| **Context collection order** | (1) entries mentioned in the beat or chat message and in the text before it; (2) manual scene references; (3) the POV character; (4) global ("always") entries; (5) child entries of anything already collected, via relations; then empty entries are dropped. Final **sort: global first → by type → by name**, so mention order does not matter. | Med-High (help page reported as updated 2025-12-02) | Official ([codex context in prompting](https://www.novelcrafter.com/help/faq/ai-and-prompting/codex-context-in-prompting)) |
| **Relations / nesting** | Linking entries pulls the linked entries in when the parent is mentioned. The app warns about runaway recursion. | Medium | Official course ([relations](https://www.novelcrafter.com/courses/codex-cookbook/codex-relations)) |
| **Progressions** (renamed from "Additions", Dec 2024) | An addendum attached to a codex entry **at a point in the manuscript**. The AI sees it only in scenes at or after that point; going back to an earlier scene drops it. Two modes: **addition** (append; the default) or **replacement** (overwrite). Can target a single text detail, not just the description. The docs say they are for major changes, *not* plot points. When chat spans several scenes, entries "progress" to the chronologically latest scene. | Med-High | Official ([progressions](https://www.novelcrafter.com/help/docs/codex/progressions-codex-details), [track changes FAQ](https://novelcrafter.com/help/faq/codex/track-character-changes), [detail progressions](https://www.novelcrafter.com/help/faq/codex/detail-progressions), [chat multiple scenes](https://www.novelcrafter.com/help/faq/chat/chat-multiple-scenes)) |
| **Subplots** | A codex entry of type *Subplot* (aliases like `SP1`). Notes hold a summary **without later events**; progressions record the timeline; the matrix view shows which scenes each subplot or character touches. | Med-High | Official ([subplots](https://www.novelcrafter.com/help/docs/write/subplots), [matrix](https://www.novelcrafter.com/help/docs/plan/planning-with-the-matrix)) |
| **Structure** | Acts → Chapters → Scenes. Three plan views (grid, outline, matrix). **Scene beats** are inserted with `/`; the recommended form is goal / conflict / outcome. | Medium | Official ([plan views](https://www.novelcrafter.com/help/docs/plan/plan-views), [writing guide](https://novelcrafter.com/docs/guides/writing-guide)) |
| **Summaries** | Per-scene summaries (the default prompt targets ~80 words). **Summaries of prior scenes form the "story so far" context.** Users should regenerate them if they get long or inaccurate. The docs advise **not** putting a whole-book summary where the AI can see it, because models then foreshadow the ending. | Med-High | Official ([glossary](https://docs.novelcrafter.com/en/articles/9329937-glossary-of-novelcrafter-terms), [summaries FAQ](https://www.novelcrafter.com/help/faq/plan/where-do-i-put-my-scene-chapter-act-book-summary-what-about-my-beats)) |
| **Prompt templates** | User-editable prompts with a function syntax, e.g. `{context.storySoFar}`, `{context.chapterSoFar}`, `{context.wordsBefore(N)}`, `{codex.get("Name")}`, `{#if …}` blocks, `takeLast(…, 3)`. XML tags are recommended for structure. Comments are not sent. Some functions only work in some prompt types. A **Preview** tab shows the full assembled prompt before it is sent. | Med-High | Official ([prompt functions](https://novelcrafter.com/docs/ai/prompt-functions), [prompts 102](https://docs.novelcrafter.com/en/articles/9419414-prompts-102-basic-prompt-changes), [preview](https://www.novelcrafter.com/help/docs/prompts/prompt-preview)) |
| **Token budgeting** | **Not found.** Size control appears to be manual (`wordsBefore(N)`, `takeLast`, shorter summaries). "Not found" does not mean it doesn't exist. | Low | n/a |

---

## 2. Comparison: Sudowrite and NovelAI

| Problem | Sudowrite (Story Bible) | NovelAI | Confidence |
|---|---|---|---|
| Story knowledge store | Braindump, Genre, Style, Synopsis, Characters, Worldbuilding, Outline, chapter Beats/Scenes. Data flows one way: Synopsis → Characters/Worldbuilding, and Style → Beats and Draft. | **Lorebook** entries; **Memory** (near the top of context); **Author's Note** (inserted near the *end*, just before the most recent story text). | Med-High (official: [SW](https://docs.sudowrite.com/using-sudowrite/1ow1qkGqof9rtcyGnrWUBS/what-is-story-bible/jmWepHcQdJetNrE991fjJC), [NAI story settings API](https://docs.novelai.net/en/scripting/story-settings-api)) |
| Selective inclusion | A per-card or per-trait **visibility toggle** (eyeball); the Draft view underlines the elements it detected and used. | **Activation keys** (case-insensitive; `/regex/`; `&` = all keys must appear within the search range) plus "Always On". | Med-High (official: [SW 2025 changelog](https://feedback.sudowrite.com/changelog/mag-story-bible-detection-and-visibility-update), [NAI lorebook](https://docs.novelai.net/en/text/lorebook)) |
| Facts that change over time | **None found.** | **None found** (manual edits; "ephemeral context" exists for timed entries). | Low-Medium |
| Token budgeting | Not documented (opaque). | Explicit. **Token budget** per entry (absolute, or a fraction of max context − output length). **Reserved tokens** are reserved *before* placement and capped at the entry's length. Then insertion order and position, a trim type, and a **Context Viewer** that shows what made it in. | Med-High for budget/reserved ([official lorebook](https://docs.novelai.net/en/text/lorebook)). **Low** for cascading activation and negative insertion order (developer Reddit comment only, anecdotal). |
| Chapter continuity | Link each document to an Outline chapter and set its "previous" document. Draft length scales with the number of scenes (an older doc says each beat ≈ 200–300 words). | n/a (one continuous story text). | Medium ([SW continuity](https://docs.sudowrite.com/using-sudowrite/1ow1qkGqof9rtcyGnrWUBS/chapter-continuity/4KL8gFeLZQ6GSBjDWtSbV6), [SW beats](https://docs.sudowrite.com/using-sudowrite/1ow1qkGqof9rtcyGnrWUBS/beats--chapter-prose/v7UCtPmdHTU9mkwFKCutw2)) |

**What to borrow from each:**
- From **Novelcrafter**: progressions (the only tool of the three with explicit time-versioned facts) and the summary-based "story so far".
- From **NovelAI**: explicit budgeting with priorities and reservations, the Author's-Note idea (put the current instructions at the *end* of the prompt), and a context viewer.
- From **Sudowrite**: per-item visibility and a "what got used" report.

---

## 3. What matters for your workflow, and what to skip

Your workflow: CLI, files on disk, 10+ chapters of ~3,000 words, scene-by-scene generation, OpenRouter only.

**Keep: core of the tool**
1. **Codex-style files** for characters, places and lore. Each has: name, aliases, a base profile, optional custom fields, and an AI-context mode (`always` / `when_mentioned` / `never`).
2. **Progressions** keyed to `(chapter, scene)`:
   - append or replace mode, optionally per field;
   - strict rule: use only the latest progression **at or before** the current position, never a future one.
3. **Mention detection** on names and aliases (whole-word, case-insensitive), scanning:
   - the current scene's beats;
   - the handoff text from the previous scene;
   - optionally, recent summaries.
4. **Subplot files** with a status for each chapter (Novelcrafter's subplot-plus-progression idea, as plain YAML).
5. **Outline** with beats per chapter and per scene (goal / conflict / outcome is a useful default shape).
6. **Scene and chapter summaries** feeding a "story so far" block, plus a verbatim **handoff** of the last N words of the previous scene.
7. **Editable prompt templates** with simple `{{variable}}` substitution.
8. A **dry-run / preview** of the assembled prompt (Novelcrafter Preview, NovelAI Context Viewer), with a token count for each section. This becomes `plan-check`.
9. **Priority-based token budget** with reservations (NovelAI-style).
10. **Logging and cost tracking**.

**Skip or defer**
- GUI, plan grid and matrix views. A text report from `plan-check` covers the matrix use case.
- Chat/"workshop" mode.
- Recursive relations: one level at most, if at all.
- Regex keys and cascading activation (`&` key logic).
- Separate "snippets" (merge them into the bible).
- Series-level codex and multimodal input.
- Embedding/semantic retrieval. It isn't needed at this scale: a 30,000-word manuscript is roughly 40k tokens (at an approximate rule of thumb of ~1.3 tokens per English word; the real ratio depends on the tokenizer). That is far below the ~1M-token windows listed for both target models.

**What follows from that:** both models list ~1M-token context windows, so the token budget is mostly about **cost and keeping the model focused**, not about hitting a hard limit. Long-context models are known to use information in the middle of long prompts less reliably ("Lost in the Middle", Liu et al., TACL 2024; peer-reviewed, cited from my training knowledge, not tested on these two models). That supports putting the current scene's instructions **last**, as NovelAI does with the Author's Note.

---

## 4. OpenRouter API: findings relevant to the design

| Topic | Finding | Confidence | Source |
|---|---|---|---|
| Endpoint | `POST https://openrouter.ai/api/v1/chat/completions`, OpenAI-compatible request/response format with small differences. Bearer-token auth. Optional attribution headers: `HTTP-Referer`, `X-OpenRouter-Title` (`X-Title` accepted as alias). | Med-High | Official ([API overview](https://openrouter.ai/docs/api/reference/overview)) |
| Streaming | `stream: true` gives Server-Sent Events. **Lines starting with `:` are keep-alive comments** (`: OPENROUTER PROCESSING`) and must be skipped before JSON parsing. **Mid-stream errors arrive with HTTP 200**: a chunk with a top-level `error` and `finish_reason: "error"`, which can be the first and only event. | Med-High | Official ([streaming](https://openrouter.ai/docs/api-reference/streaming)) |
| Usage and cost | Usage is **now always included**; the old `usage: {include: true}` flag is deprecated and has no effect. When streaming it arrives in the **final SSE chunk**. Fields cover prompt, completion, reasoning and cached tokens, plus **`cost`** (in OpenRouter credits). It can be looked up later via `/api/v1/generation?id=…`. | Med-High | Official ([usage accounting](https://openrouter.ai/docs/guides/administration/usage-accounting)) |
| Provider routing | A `provider` object with `order` (list), `allow_fallbacks` (default true), `require_parameters` (default false; when true, only providers supporting *every* sent parameter are used), and `data_collection: "allow"\|"deny"`. `sort` (`price`/`throughput`/`latency`) appears only in third-party docs, so it is unverified. By default traffic is load-balanced with a preference for price. | Med-High (sort: Low) | Official ([provider routing](https://openrouter.ai/docs/features/provider-routing)) |
| Model fallback | A `models: [primary, fallback, …]` array. *Any error* (context length, moderation flag, rate limit, downtime) moves on to the next model. You are billed for the model actually used, and the response's `model` field says which one that was. **It does not trigger on a refusal or empty output delivered as a normal 200**, so the harness needs its own detection, as you specified. | Med-High | Official ([model fallbacks](https://openrouter.ai/docs/guides/routing/model-fallbacks)) |
| Errors | 402 = out of credits; 403 = permissions, guardrail or moderation flag; 429 = rate limited; 502 = model down or invalid response; 503 = no provider meets the routing requirements. 429 and 503 may carry `Retry-After`. | Medium (status list confirmed mainly via mirrors of the official page) | Official ([errors](https://openrouter.ai/docs/api/reference/errors-and-debugging)) |
| Reasoning | `reasoning: {effort \| max_tokens, exclude}`. **Reasoning tokens are billed as output tokens.** `exclude: true` hides them from the response, but the model still reasons and you are still billed. **My inference, not documented:** reasoning can use up `max_tokens` and leave empty `content`. The harness should treat "empty content + `finish_reason: length`" as a distinct failure. | Med-High (empty-content: Low, inference) | Official ([reasoning tokens](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)) |
| Models list | `GET /api/v1/models` returns a `data[]` list. Each model has `id`, `context_length`, `pricing` (strings, USD per token), `supported_parameters`, `top_provider` and `architecture`. **No API key needed.** | Medium (no full official schema retrieved) | Official ([FAQ](https://openrouter.ai/docs/faq), [SDK schema](https://openrouter.ai/docs/agent-sdk/typescript/api-reference/models/modelslistresponse)) |

---

## 5. Model slugs: **NOT API-confirmed**

| | Kimi K3 | GLM-5.3 |
|---|---|---|
| Candidate slug | `moonshotai/kimi-k3` | `z-ai/glm-5.3` |
| Dated snapshot | `moonshotai/kimi-k3-20260715` | `z-ai/glm-5.3-20260816` |
| Other variants seen | `:nitro`, `:batch`, `~moonshotai/kimi-latest` (moving alias) | `:batch` |
| Context window | 1,048,576 | 1,048,576 |
| Max output | **Conflicting:** 974,842 (openrouter.ai pricing page) vs ~131K (others) | 131,072 |
| Price per 1M tokens, input/output | **Conflicting:** $2.60/$13 (cache read $0.29) vs $3/$15 | $1.40/$4.40 (cache read $0.26) |
| Reasoning | Always on. openrouter.ai page: effort supports **only `max`** for now. | **Always on, cannot be disabled.** Effort low/high/max reported (mirror page). |
| Sampling parameters | **Possible blocker:** 3rd-party sources say K3 **fixes or rejects** `temperature`/`top_p`/`seed`. They disagree on whether sending them errors or is ignored. | Not found. |
| Stated focus | Coding, knowledge work, agent workflows | Coding, long-running agent work |
| Sources | openrouter.ai model and pricing pages (official, via search); [Oxen docs](https://docs.oxen.ai/inference-api/reference/models/kimi-k3), [OrcaRouter blog](https://www.orcarouter.ai/de/blog/claude-sonnet-5-5-vs-kimi-k3) (3rd-party) | openrouter.ai model and benchmark pages (official, via search); [LLM Reference](https://www.llmreference.com/model/glm-5.3/openrouter) (3rd-party) |

Overall confidence that the slugs are correct: **Medium-High** (they appear consistently on openrouter.ai URLs). Prices and limits: **Low-Medium** (sources conflict).

**To confirm (no API key needed), run on your machine:**
```bash
curl -s https://openrouter.ai/api/v1/models | python3 -c "import sys,json; [print(m['id'], m.get('context_length'), m['pricing']['prompt'], m['pricing']['completion'], m.get('supported_parameters')) for m in json.load(sys.stdin)['data'] if 'kimi' in m['id'] or 'glm' in m['id']]"
```
Alternatively, add `openrouter.ai` to this cloud environment's allowed network domains: environment settings → Network access → Allowed domains ([steps](https://code.claude.com/docs/en/cloud-environments#network-access)). Phase 3's opt-in integration test needs that access anyway.

**Design implications (these feed Phase 2):**
1. **Both are always-on reasoning models.** Expect:
   - extra billed output tokens (an unknown amount per call);
   - a long silent gap before the first prose token, during which only keep-alive comments arrive;
   - possible empty-content failures.

   Streaming to a file still works; it just starts late.
2. **Per-model parameter filtering:** the harness should send only the parameters a model lists in `supported_parameters`. Otherwise `temperature` could cause errors on K3, or, with `require_parameters: true`, a 503 because no provider matches. Your "temperature and other sampling params" config may simply not apply to K3.
3. **Pin dated snapshots** (e.g. `…-20260715`) so the prose voice doesn't change mid-novel if a slug is repointed. Medium confidence that undated slugs can be repointed; aliases like `~…-latest` clearly can.
4. **Fiction quality is unproven.** Both models are described as coding/agent-focused. I found no reliable source evaluating their long-form prose. You'll have to judge it on your own chapters.
5. **Illustrative cost per 3,000-word chapter** (4 scene calls, each ~30k input tokens, ~1k visible output tokens, plus an *assumed* 5k reasoning tokens per call; the reasoning figure is a guess):
   - **Kimi K3:** ≈ $0.62–0.72;
   - **GLM-5.3:** ≈ $0.27;
   - plus summary and continuity-check calls.

   Ordering the prompt so stable text (style guide, bible) comes first may allow cached-input pricing. Whether these providers cache automatically through OpenRouter is **unknown**.

---

## 6. Your story architecture file

Not needed in this repo. The harness is generic, and your architecture document and story files stay in a project folder on your own machine. I will never see the document, so the importer has to handle formats it wasn't written for. Proposed design (detailed in Phase 2):

1. **Template path.** If the document follows a heading layout the harness defines (e.g. `## Characters` → `### Name`), a deterministic parser converts it directly.
2. **Model-assisted path, for free-form documents.** The importer sends the document to your configured model with an editable extraction prompt and asks for the project's file layout.
   - The result is checked against the expected structure.
   - It is shown to you for review before anything is saved.
   - This runs on your machine with your API key, so the content never passes through this build session.
   - It uses the same refusal/empty-output detection and fallback as chapter generation.
   - Risk: the model may merge, drop or invent details. That is why the review step can't be skipped.

Still needed: the document's **file type** (section 7, Q1).

---

## 7. Open questions

**Blocking for Phase 2**
1. **Architecture document format (not its content):** what file type is it (.md, .docx, .txt, other)? Is it one file or several? Optional: a headings-only skeleton with the story text removed, so I can tune the template parser to it.
2. **Models:** is Kimi K3 the primary and GLM-5.3 the fallback, or the other way round? Will you run the confirm command above, or enable network access so I can?
3. **Sampling parameters on K3:** OK for the harness to auto-drop unsupported parameters per model (logged as a warning)? Or should it fail loudly?
4. **Future knowledge:** while writing chapter N, should the model see:
   - (a) nothing beyond the current scene's beats,
   - (b) the next scene's or chapter's beats, for setup, or
   - (c) author-only "secrets" (marked fields that are never sent until a given chapter)?

   Novelcrafter warns that visible endings cause foreshadowing.
5. **Scene granularity:** typical scenes per chapter? Is the word target set per scene or per chapter (split evenly)? Does your outline already have scene-level beats?
6. **Approval gates:** after a chapter, should the continuity check and ledger update be **report-only**? Or should they propose edits (new progressions, ledger rows) for you to accept, or auto-apply them?

**Needed before Phase 3 (defaults proposed)**

7. **POV and tense:** single or multiple POV? Should there be a per-scene `pov` field? (Default: per-scene field, falling back to a book-level setting.)
8. **Handoff length:** how many words of the previous scene, verbatim? (Default: last ~800 words.)
9. **`revise` command:** rewrite a whole scene from your notes, or make targeted edits to marked passages? (Default: whole-scene rewrite with your notes plus the original text as input.)
10. **Privacy:** set `data_collection: "deny"` for the unpublished manuscript? It may shrink the provider pool. Any providers to exclude? (Default: deny.)
11. **Spend guard:** a hard stop per chapter or per project, e.g. "abort if the projected cost is over $X"? (Default: warn only.)
12. **Fallback exhaustion:** if both models refuse or return empty, stop and log? (Default: yes. Never loop.)
13. **Your setup:** Windows, macOS or Linux? Which Python version? Do you use git? (This shapes the README.)
14. **Dependencies:** OK with `httpx`, `PyYAML`, and `pytest` (dev only)? Templates would use simple `{{var}}` substitution, not Jinja. Do you need `if`-style conditionals in templates? (Default: no.)

---

## 8. Assumptions I made
- "GLM-5.3" means Z.ai's GLM 5.3. "Kimi K3" means Moonshot AI's model of that name.
- You write in English (affects the tokens-per-word estimate).
- The tool runs on your own machine. This cloud container is only for building and testing it.
- Test fixtures will use an invented, neutral placeholder story, per your constraints.
