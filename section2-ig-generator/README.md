# Weekly Instagram Post Generator — Rumah123

Section 2, Option A of the 99 Group AI Aptitude Challenge.

An n8n workflow that runs every Monday morning, reads the week's Indonesian
property and business news, decides which stories are actually worth posting
about, writes Rumah123-voiced Instagram captions for the survivors, runs them
past a compliance gate, and files the week's output as a markdown digest split
into *ready to post* and *held for review*.

- [`weekly-ig-generator.workflow.json`](weekly-ig-generator.workflow.json) — 23 nodes, importable into any n8n instance
- [`prompts.md`](prompts.md) — both prompts as plain text
- [`output/digest-2026-08-20.md`](output/) — a real run, not a mock-up
- [`build_workflow.py`](build_workflow.py) — generates the workflow JSON

## Running it

```bash
npm install -g n8n
n8n start                          # http://localhost:5678
```

Import the JSON, then add one credential — *Google Gemini(PaLM) Api*, free key
from https://aistudio.google.com/apikey — named **`Google Gemini (AI Studio)`**,
which is what both LLM nodes reference. Press **Execute Workflow** rather than
waiting for Monday.

To run it headless, the write step needs its output directory whitelisted:

```bash
N8N_RESTRICT_FILE_ACCESS_TO="$(pwd)" n8n execute --id 3784e5f6e2935e
```

## The pipeline

```
Schedule (Mon 08:00 WIB) ─┐
Run On Demand ────────────┤
                          ├─→ Tempo Bisnis  ─┐
                          ├─→ Detik Properti ├─→ Merge
                          └─→ CNBC Market   ─┘    │
                                                  ▼
                                        Normalise & Window
                                    (7-day cutoff, HTML stripped,
                                       topical pre-score)
                                                  │
                                                  ▼
                                        Drop Seen Stories
                                    (cross-run dedupe on article URL)
                                                  │
                                    Rank By Topic → Top 8 Candidates
                                                  │
                                                  ▼
                                    Score Newsworthiness ── Gemini lite, temp 0.2
                                    (relevant, angle, audience, reason)
                                                  │
                                        Worth Posting? ── no ──→ No Story This Week
                                                  │ yes
                                                  ▼
                                        Carry Brief Forward
                                                  ▼
                                          Write Caption ── Gemini lite, temp 0.8
                                    (hook, caption, hashtags, image_prompt, claims)
                                                  │
                                                  ▼
                                        Brand Safety Gate ← deterministic regex
                                                  │
                                                  ▼
                                Build Digest → Digest to File → Save Digest
                                  (ready to post vs held for review)
```

## Design decisions

**Two models at two temperatures, not one.** The editor decides *whether* a
story is worth posting and runs at 0.2 — consistency matters and invention is a
defect. The copywriter runs at 0.8, because a caption that reads like it came
off an assembly line is the whole failure mode of automated social content.
Splitting them also makes the editorial decision inspectable on its own: the
`reason` field records why each story was accepted or rejected.

**Structured output on both LLM calls.** Each chain has a JSON schema attached.
A free-text caption would need parsing downstream, and that parser breaks the
first time the model adds a preamble. The schema means `hashtags` is an array I
can count and `claims` is a list I can audit.

**Deterministic guardrail behind the prompt.** The brand rules are in the
prompt, but a prompt is a request, not a constraint — when a model ignores it,
the failure is silent and the post ships. `Brand Safety Gate` is plain regex
over the finished caption, checking for the claims that would actually cause
Rumah123 a problem: guaranteed returns, promised loan approval, manufactured
urgency, and any percentage that does not appear verbatim in the source. That
last check has a carve-out — a rate quoted straight from the article is
legitimate, an invented one is not.

**Flagged posts are routed, not dropped.** Anything the gate catches goes to a
*held for review* section rather than the bin. A false positive should cost a
human a glance, not cost the team a story. This is the human-in-the-loop point:
one short digest per week to approve, rather than supervising every run.

**Cross-run deduplication.** `Drop Seen Stories` uses n8n's persistent "seen in
previous executions" mode keyed on article URL. Without it, a story that lingers
in a feed for ten days generates a near-identical post every week — the most
obvious tell of an unattended content bot.

**Broad feeds, then filter.** Only Detik Properti is property-specific; Tempo
Bisnis and CNBC Market are general business. That is deliberate — mortgage rate
moves, tax changes and construction costs all matter to homebuyers and none of
them appear in a property vertical. The BI rate decision that produced this
week's best post came from CNBC, not the property feed.

## What running it actually revealed

The first end-to-end run produced nothing. The workflow reported success and the
output directory stayed empty, which looked like a broken pipeline. It was not —
the editor had correctly rejected all three stories it was given, one of which
was a laptop promotion:

> *"This is a product promotional article about a laptop and offers no direct
> benefit or relevance to property seekers or homeowners."*

The filter was working; the **intake** was starved. `Limit` sat *before* the
editor, so three items were taken at random from a mostly-irrelevant pool. The
fix was to score items topically first (cheap keyword weighting in the Normalise
step), rank by that, and hand the editor eight plausible candidates instead of
three arbitrary ones. The same run now yields seven usable posts.

Three further bugs only surfaced by executing it, not by reading it:

- **`md` returned outside `json`.** An n8n item carries only `json` and
  `binary`; a sibling field is dropped silently, so Convert to File found
  nothing.
- **The write path collapsed to a directory.** Convert to File replaces the
  item's json with binary data, so `{{ $json.filename }}` was empty by the time
  the write node read it. The filename is now derived from the clock.
- **The editorial brief was gone by the compliance step.** Write Caption
  replaces each item's json with the model's output, so title, link and angle
  rendered as `undefined` in the digest — and, worse, the percentage carve-out
  was comparing every number against an empty string, flagging a legitimate
  `5,75%` that *was* quoted from the source. The gate now fetches the brief from
  the upstream node by name.

That last one is the instructive failure: the guardrail did not break loudly, it
became silently over-strict. A workflow that only ever got read would have
shipped it.

## Limitations and next steps

- **Both models run on `flash-lite`**, not `flash`. The free tier caps the full
  flash models at 20 generate requests per day, and one workflow run plus one
  Section 3 eval exhausts that. Captions from `flash` are noticeably better; on
  a paid tier the copywriter should move back up. The committed sample output
  was produced under this constraint.
- Runs on RSS summaries, not full article text. Fetching article bodies would
  give the copywriter more to work with, at the cost of scraping and per-site
  parsers.
- No image generation — `image_prompt` is produced and left for a designer or a
  downstream image model.
- No posting step. Publishing to the Instagram Graph API is a token and app
  review problem, and auto-posting unreviewed generated content to a live brand
  account is the wrong default regardless.
- The compliance gate is Indonesian- and English-keyed regex. It catches the
  obvious phrasings; a determined paraphrase gets through. A second model call
  as a compliance judge would raise the ceiling and add another thing that can
  silently misbehave.
- The topical pre-score is hand-weighted keywords. It works, but it is a
  heuristic someone has to maintain as vocabulary shifts.
- No engagement feedback. Post performance flowing back to tune the editor's
  notion of "worth posting" is the obvious next iteration.
