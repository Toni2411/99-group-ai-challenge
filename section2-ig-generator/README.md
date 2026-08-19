# Weekly Instagram Post Generator — Rumah123

Section 2, Option A of the 99 Group AI Aptitude Challenge.

An n8n workflow that runs every Monday morning, reads the week's Indonesian
property and business news, decides which stories are actually worth posting
about, writes Rumah123-voiced Instagram captions for the survivors, runs them
past a compliance gate, and files the week's output as a markdown digest split
into *ready to post* and *held for review*.

The full workflow is in
[`weekly-ig-generator.workflow.json`](weekly-ig-generator.workflow.json) — 21
nodes, importable directly into any n8n instance.

## Running it

```bash
npx n8n                       # http://localhost:5678
```

Then: **Workflows → Import from File →** `weekly-ig-generator.workflow.json`.

One credential to add — *Google Gemini (AI Studio)*, free key from
https://aistudio.google.com/apikey. Both LLM nodes reference it. Click
**Execute Workflow** to run it on demand rather than waiting for Monday.

Output lands in `./output/digest-YYYY-MM-DD.md` relative to the n8n process.
A sample run is committed at [`output/`](output/).

## The pipeline

```
Schedule (Mon 08:00 WIB)
   ├─→ Tempo Bisnis  ─┐
   ├─→ Detik Properti ├─→ Merge ─→ Normalise & Window ─→ Drop Seen Stories
   └─→ CNBC Market   ─┘              (7-day cutoff,        (cross-run dedupe
                                      HTML stripped)        on article URL)
                                                                  │
                                                                  ▼
                                                            Top 3 Stories
                                                                  │
                                    ┌─────────────────────────────┘
                                    ▼
                          Score Newsworthiness ──── Gemini (temp 0.2)
                          (structured: relevant,     + Editor Schema
                           angle, audience, reason)
                                    │
                            Worth Posting? ──── no ──→ No Story This Week
                                    │ yes
                                    ▼
                          Carry Brief Forward
                                    │
                                    ▼
                             Write Caption ─────── Gemini (temp 0.8)
                          (structured: hook,        + Caption Schema
                           caption, hashtags,
                           image_prompt, claims)
                                    │
                                    ▼
                          Brand Safety Gate  ← deterministic regex guardrail
                                    │
                                    ▼
                             Build Digest ─→ Digest to File ─→ Save Digest
                        (approved vs held for review)
```

## Design decisions

**Two models at two temperatures, not one.** The editor decides *whether* a
story is worth posting and runs at temperature 0.2 — that is a judgement call
where consistency matters and creativity is a defect. The copywriter runs at
0.8, because a caption that reads like it came off an assembly line is the
whole failure mode of automated social content. Splitting them also means the
editorial decision is inspectable on its own: the `reason` field records why
each story was accepted or rejected.

**Structured output on both LLM calls.** Each chain has a JSON schema attached.
A free-text caption would have to be parsed with a regex somewhere downstream,
and that parser breaks the first time the model adds a preamble. The schema
means `hashtags` is an array I can count and `claims` is a list I can audit.

**Deterministic guardrail behind the prompt.** The brand rules are in the
prompt, but a prompt is a request, not a constraint — when a model ignores it,
it fails silently and the post ships. `Brand Safety Gate` is plain regex over
the finished caption, checking for the claims that would actually cause
Rumah123 a problem: guaranteed returns, promised loan approval, manufactured
urgency, and any percentage that does not appear verbatim in the source
article. That last check has an explicit carve-out — a rate quoted straight
from the article is legitimate, an invented one is not.

**Flagged posts are routed, not dropped.** Anything the gate catches goes to a
*held for review* section rather than the bin. A false positive should cost a
human a glance, not cost the team a story. This is the human-in-the-loop point:
a person reads one short digest per week and approves, rather than supervising
every run.

**Cross-run deduplication.** `Drop Seen Stories` uses n8n's persistent
"seen in previous executions" mode keyed on the article URL. Without it, a story
that stays in a feed for ten days generates a near-identical post every week —
the single most obvious tell of an unattended content bot.

**Broad feeds plus an editorial filter, not narrow feeds.** Only Detik Properti
is property-specific; Tempo Bisnis and CNBC Market are general business. That is
deliberate. Mortgage-rate moves, tax changes and construction-material costs all
matter to homebuyers and none of them appear in a property vertical. The editor
node earns its place by filtering a broad intake rather than a narrow one
pre-filtering for it.

## Limitations and next steps

- Runs on RSS summaries, not full article text. Fetching the article body would
  give the copywriter more to work with and reduce the temptation to pad; it
  also means scraping, rate limits and per-site parsers.
- No image generation. `image_prompt` is produced and left for a designer or a
  downstream image model.
- No posting step. Publishing to the Instagram Graph API is a token and app
  review problem, not a workflow problem, and posting unreviewed generated
  content to a live brand account is the wrong default anyway.
- The compliance gate is Indonesian- and English-keyed regex. It catches the
  obvious phrasings; a determined paraphrase gets through. A second model call
  as a compliance judge would raise the ceiling at the cost of another API call
  and a second thing that can silently misbehave.
- No A/B signal. Engagement data flowing back to tune the editor's notion of
  "worth posting" is the obvious next iteration.

## Files

```
weekly-ig-generator.workflow.json   the workflow, importable into n8n
prompts.md                          both prompts as plain text
output/                             a committed sample run
```
