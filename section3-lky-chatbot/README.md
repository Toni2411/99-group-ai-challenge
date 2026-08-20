# Lee Kuan Yew — RAG chatbot

Section 3 of the 99 Group AI Aptitude Challenge.

A chatbot that answers open-ended questions — governance, geopolitics, race and
language policy, housing, leadership — in Lee Kuan Yew's voice, grounded in an
indexed archive of his speeches, memoirs and interviews. It cites what it used,
and it declines when the archive has nothing rather than inventing a quotation.

Built from scratch in Python rather than assembled in a visual builder, so the
retrieval decisions are inspectable rather than hidden behind a canvas.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env               # free key from https://aistudio.google.com/apikey

python fetch_corpus.py             # download speeches from the NAS archive
python ingest.py                   # chunk, embed, index
python app.py                      # chat UI at http://127.0.0.1:7860

python eval/evaluate.py            # eval → eval/results.md
python eval/calibrate_floor.py     # re-measure the relevance floor
```

`rag.py` also runs standalone as a terminal REPL.

## Architecture

```
data/corpus/*.txt
      │  parse header (title, year, type, source, url)
      ▼
  chunk_text()          recursive character split, 1200 / 200 overlap
      │                 breaks on paragraph → sentence → word
      ▼
  gemini-embedding-001  task_type=RETRIEVAL_DOCUMENT, 768-dim
      │                 content-hash ids ⇒ re-running is idempotent
      ▼
  ChromaDB (cosine, persistent on disk)

  ── query time ────────────────────────────────────────────────

  question + history
      │
      ▼
  rewrite_query()       follow-up → standalone query
      ▼
  retrieve()            top-20 candidates, RETRIEVAL_QUERY embedding
      ▼
  relevance floor       all candidates < 0.59 similarity ⇒ refuse, stop here
      ▼
  rerank()              lite model scores usefulness
      ▼
  _diversify()          max 2 chunks per document, keeps top-6
      ▼
  CHAT_MODEL            persona + grounding rules + cited excerpts
      ▼
  Answer(text, sources[], contexts[], grounded)
```

## Design decisions

**Why chunks of 1200 characters, not the usual 500.** Speeches argue in long
arcs — a claim, then the reasoning, then the concrete example. Splitting at 500
characters routinely severed a claim from its support, and retrieval returned
assertions with no argument attached. The 200-character overlap exists for the
same reason.

**Why embeddings are asymmetric.** Documents are embedded with
`task_type=RETRIEVAL_DOCUMENT` and questions with `RETRIEVAL_QUERY`. A question
and the passage answering it are not paraphrases of each other, and using one
task type for both sides measurably degrades recall.

**Why query rewriting.** "And what about housing?" carries almost no
retrievable content. Rewriting it against the last three turns into a standalone
query is the difference between retrieving the housing material and retrieving
noise. It fails soft: if the rewrite call errors, the original question is used.

**Why over-retrieve then rerank.** Cosine similarity reliably surfaces passages
that share vocabulary with the question but not its subject — asking about
*press freedom* pulls every passage containing the word "freedom". Pulling 20
candidates and having the model pick the 6 genuinely useful ones costs one
extra call and removes most of that noise. A cross-encoder reranker would be
better still; that is the first thing I would add next.

**Why a relevance floor.** This is the decision I care most about. A chatbot
impersonating a real historical figure that fabricates a plausible-sounding
quotation is worse than useless — it puts words in a dead man's mouth, and the
output is confident enough that a reader has no way to catch it. When nothing
retrieved clears the floor, the pipeline stops before generation and returns a
refusal in character. The grounding rules in the system prompt reinforce this,
but the floor enforces it without trusting the model to comply.

**Why the floor is measured rather than chosen.** The first version of this file
set the floor to `0.25`, which reads like a sensible "low similarity" cutoff.
It was wrong by a wide margin, and wrong in the worst direction: the check could
never fire, so the safety mechanism I cared most about was dead code that looked
alive. Asked for a chicken rice recipe, the retriever returned six speeches at
similarity 0.52–0.55 and the pipeline sailed straight past the floor into
generation.

`eval/calibrate_floor.py` measures the two distributions instead:

| | min | mean | max |
|---|---|---|---|
| In-corpus questions (n=8) | 0.623 | 0.665 | 0.696 |
| Out-of-corpus questions (n=6) | 0.509 | 0.533 | 0.556 |

They separate cleanly with a gap of 0.067, and the floor is the midpoint,
**0.59**. The lesson generalises: cosine similarity is not calibrated to human
intuitions about relatedness, and the absolute values move with the embedding
model and the dimensionality. Any threshold picked by feel is a guess, and a
guess that silently disables a guardrail is worse than having no guardrail at
all — at least an absent one is visible. Re-run the calibration whenever the
embedding model or `EMBED_DIM` changes.

**Why chunks are capped per document.** Adjacent chunks of one speech are
near-duplicates by construction — they overlap by 200 characters and argue the
same point. Retrieval was filling six context slots from three documents, and
the eval judge kept flagging the excerpts as redundant. `MAX_PER_DOC = 2` trades
a little depth on the best-matching speech for breadth across the archive, which
is the better trade for open-ended questions, and refills from the excluded
candidates when the cap would otherwise under-deliver.

**Why the models are split by role — and why the split is not the one I
designed.** The intent was three models: the best available for generation, a
lite model for the mechanical work (query rewriting, reranking), and a third for
judging. The free tier forced a compromise. It caps the full flash models at
**20 generate requests per day, per model**. One eval run costs 39 calls — 12
generation, 12 rerank, 15 judge and decline-checks — so the generation calls fit
inside the cap but leave only 8 for anything else that day. Between debugging
runs and the Section 2 workflow hitting the same model, that headroom vanished
and an eval run died partway through. Generation moved down to the lite model,
where the utility calls already were.

The half of the split that survived is the one that matters: the judge is
deliberately a *different generation* from the generator, because a model
scoring its own output inflates faithfulness — it finds its own reasoning
persuasive. Not true independence, since both are Gemini, but meaningfully
better than grading your own homework.

Moving generation to `flash-lite` cost real quality — `persona_fidelity` fell
from 0.82 to 0.67. On a paid tier `CHAT_MODEL` moves back up and nothing else
in the pipeline changes.

**Why content-hash chunk ids.** Re-running `ingest.py` after editing one
document re-embeds only that document's chunks. On a free API tier with rate
limits, that is the difference between a 5-second update and a full rebuild.

## Evaluation

`eval/evaluate.py` scores the system on a hand-written dataset of 15 cases —
12 answerable, 3 deliberately outside the corpus.

Four LLM-as-judge metrics, each targeting a distinct failure:

| Metric | Catches |
|---|---|
| `faithfulness` | Invented quotations, dates, statistics — the worst failure mode |
| `answer_relevancy` | Reciting retrieved material instead of answering |
| `context_relevancy` | A retriever padding the prompt with noise |
| `persona_fidelity` | Drifting into a neutral assistant summarising him |

The three out-of-corpus cases are scored as hard pass/fail on `refusal_accuracy`
— a question about a 2024 election or about cryptocurrency has exactly one
correct behaviour, and a graded score would let a fluent fabrication pass.

**Measure the behaviour, not the mechanism.** There are two independent
defences against answering an unanswerable question: the relevance floor stops
retrieval before generation, and the grounding rules make the model decline.
The first version of this harness checked only whether the floor had fired, and
scored this reply as a FAILURE:

> *"The provided archive does not reach the year 2024, so I cannot comment on
> the 2024 Singapore general election result."*

That is a correct refusal. The question is about Singapore politics, so
retrieval cleared the floor and the prompt caught it instead — right answer,
wrong door. A metric that punishes correct behaviour for arriving through the
unexpected path is worse than no metric, because it will drive you to "fix" a
system that is working. The harness now asks whether the system declined, and
reports separately which layer caught it.

**A judge is not ground truth.** Every judgement is written to
`eval/results.md` with the full answer and the retrieved citations so a human
can audit it. The aggregate is a signal for tuning chunk size, `top_k` and the
relevance floor — not a certificate of correctness.

### Results

15/15 cases, generator `gemini-3.5-flash-lite`, judge `gemini-3.1-flash-lite`.

| Metric | Mean | Min |
|---|---|---|
| faithfulness | **1.00** | 1.00 |
| answer_relevancy | 0.87 | 0.50 |
| context_relevancy | 0.47 | 0.00 |
| persona_fidelity | 0.67 | 0.20 |
| refusal_accuracy | **3/3** | |
| ├ stopped by the relevance floor | 2/3 | |
| └ stopped by the grounding prompt | 1/3 | |

Full per-case output, with every answer and its citations, is in
[`eval/results.md`](eval/results.md).

**Faithfulness is the number that matters here, and it is 1.00 across every
case** — no invented quotations, dates or statistics. That is the failure this
system was built to prevent, and both refusal layers demonstrably fire.

**`context_relevancy` at 0.47 is the honest weak spot.** Roughly half of what
the retriever hands the generator is not useful. The answers survive it because
the generator ignores the noise, but that is luck rather than design: more noise
in the prompt is more opportunity to drift. A cross-encoder reranker is the
obvious fix.

**`persona_fidelity` at 0.67 is a cost I chose.** It measured 0.82 when
generation ran on the full `gemini-3.5-flash`. Moving to `flash-lite` — forced
by the 20-requests-per-day free tier cap, which one eval run exhausts on its
own — flattened the voice. On a paid tier, moving `CHAT_MODEL` back up should
recover it without touching the retrieval pipeline.

## Corpus

26 speech transcripts from the National Archives of Singapore, one per year
across 1965–1989, totalling ~261,000 characters and 219 indexed chunks.
`fetch_corpus.py` reproduces it in one command.

Sampling one speech per year rather than crawling sequentially is deliberate: a
corpus built from the first 26 hits in 1965 would only know about separation
from Malaysia. Spreading across the whole period gives the retriever something
to say about housing, language policy, foreign relations and succession too.

The corpus itself is not committed — see [`data/sources.md`](data/sources.md)
for the provenance and copyright position. The evidence that the system works
is [`eval/results.md`](eval/results.md), which carries real answers with their
citations.

## Limitations and next steps

- The judge and the generator are the same model family, which biases
  faithfulness scores upward. A different model as judge would be a real
  improvement and costs nothing but a second API key.
- Reranking with an LLM is slower and weaker than a proper cross-encoder
  (`bge-reranker`, Cohere Rerank).
- The corpus is only as good as what is placed in `data/corpus/`; recall is
  bounded by coverage, and there is no signal today that distinguishes
  "the archive is silent" from "retrieval missed it".
- No conversational memory beyond the last three turns.
- The floor is calibrated on 14 probe questions. That is enough to show a clean
  separation but too few to trust the exact midpoint; a question phrased
  unusually could fall below 0.623 and be refused when the corpus does cover it.
- Inline `[n]` citations help traceability and hurt the persona — Lee Kuan Yew
  did not speak in footnotes. The `persona_fidelity` metric exists partly to
  keep this tension visible rather than letting it drift.
- Persona fidelity is judged, not measured against held-out real quotations —
  a stronger test would be a cloze evaluation on withheld passages.

## Layout

```
config.py           every tunable in one place
ingest.py           corpus → chunks → embeddings → ChromaDB
rag.py              rewrite → retrieve → floor → rerank → generate
app.py              Gradio chat UI
fetch_corpus.py     downloads NAS speech transcripts, spread across years
eval/dataset.jsonl  15 hand-written cases, 3 of them unanswerable
eval/evaluate.py    LLM-as-judge harness → results.md
eval/calibrate_floor.py  measures the similarity distribution → MIN_RELEVANCE
data/sources.md     provenance, copyright position, file format
```
