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
cp .env.example .env          # add a free key from https://aistudio.google.com/apikey

# put speech/memoir .txt files in data/corpus/ — see data/sources.md
python ingest.py              # build the index
python app.py                 # chat UI at http://127.0.0.1:7860
python eval/evaluate.py       # run the eval, writes eval/results.md
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
  relevance floor       all candidates < 0.25 similarity ⇒ refuse, stop here
      ▼
  rerank()              LLM scores usefulness, keeps top-6
      ▼
  gemini-2.5-flash      persona + grounding rules + cited excerpts
      ▼
  Answer(text, sources[], grounded)
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
clears 0.25 similarity the pipeline stops before generation and returns a
refusal in character. The grounding rules in the system prompt reinforce this,
but the floor enforces it without trusting the model to comply.

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

**A judge is not ground truth.** Every judgement is written to
`eval/results.md` with the full answer and the retrieved citations so a human
can audit it. The aggregate is a signal for tuning chunk size, `top_k` and the
relevance floor — not a certificate of correctness.

Results are in [`eval/results.md`](eval/results.md).

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
- Persona fidelity is judged, not measured against held-out real quotations —
  a stronger test would be a cloze evaluation on withheld passages.

## Layout

```
config.py           every tunable in one place
ingest.py           corpus → chunks → embeddings → ChromaDB
rag.py              rewrite → retrieve → floor → rerank → generate
app.py              Gradio chat UI
eval/dataset.jsonl  15 hand-written cases, 3 of them unanswerable
eval/evaluate.py    LLM-as-judge harness → results.md
data/sources.md     provenance, copyright position, file format
```
