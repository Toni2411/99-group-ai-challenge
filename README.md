# 99 Group AI Aptitude Challenge

Muhammad Fathoni · [github.com/Toni2411](https://github.com/Toni2411)

Three sections. Each folder is self-contained and has its own README covering
architecture, the reasoning behind each decision, and what I would fix next.

| Section | What it is | Where |
|---|---|---|
| 1 | Instagram post for Rumah123 + the prompt that produced it | [`section1-instagram/`](section1-instagram/) |
| 2 | Weekly IG post generator — 21-node n8n workflow | [`section2-ig-generator/`](section2-ig-generator/) |
| 3 | Lee Kuan Yew RAG chatbot, built from scratch in Python | [`section3-lky-chatbot/`](section3-lky-chatbot/) |

## The thread running through all three

Every section here is a system that puts words in someone else's mouth — a
brand's, or a dead statesman's. So each one is built around the same question:
**what stops this from confidently saying something false?**

- **Section 1** forbids the model from stating any figure it cannot source.
  Asked about the costs of buying a home, a model will fluently produce
  "sekitar 5–7% dari harga rumah". It is plausible, unsourced, and on a
  property marketplace's account it is the kind of error that costs trust.
- **Section 2** puts a deterministic regex gate *behind* the brand-voice prompt.
  A prompt is a request, not a constraint; when the model ignores it, the
  failure is silent and the post ships. Anything the gate catches is routed to
  human review rather than binned, because a false positive should cost a
  glance, not a story.
- **Section 3** stops the pipeline *before* generation when nothing retrieved
  clears a relevance floor. A chatbot impersonating a real historical figure
  that invents a quotation is worse than useless, and the output is fluent
  enough that a reader has no way to catch it.

The pattern is the same each time: state the rule in the prompt, then enforce it
in code that cannot be talked out of it.

## Running any of it

Each folder's README has its own setup. In short:

```bash
# Section 2
npx n8n                                    # import the workflow JSON

# Section 3
cd section3-lky-chatbot
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
cp .env.example .env                       # free key: aistudio.google.com/apikey
python fetch_corpus.py && python ingest.py
python app.py                              # chat UI
python eval/evaluate.py                    # eval → eval/results.md
```

Everything runs on free tiers: a Google AI Studio key for both LLM and
embeddings, ChromaDB on local disk, and self-hosted n8n. No paid service is
required to reproduce any of it.

## A note on how this was built

I used AI assistance throughout — which seemed like the honest reading of an AI
aptitude assessment. What I made sure of is that every architectural decision in
here is one I can defend: why chunks are 1200 characters and not 500, why the
editor and copywriter run at different temperatures, why the relevance floor
sits in front of generation rather than in the prompt. The reasoning is written
out in each README rather than left implicit, and the limitations sections are
honest about what these systems still get wrong.
