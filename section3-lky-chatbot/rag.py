"""Retrieval-augmented generation with a Lee Kuan Yew persona.

The pipeline is deliberately more than "embed the question, stuff the top 5
chunks into a prompt". Each extra stage exists to fix a failure this corpus
actually produces:

  1. Query rewriting   - follow-ups like "and what about housing?" carry no
                         retrievable content on their own.
  2. Over-retrieve     - cosine similarity alone surfaces chunks that share
     + rerank            vocabulary with the question but not its subject.
  3. Relevance floor   - the corpus does not cover everything; a chatbot that
                         invents a Lee Kuan Yew quote is worse than one that
                         declines.
  4. Cited generation  - every claim is traceable to a document, which is also
                         what makes the eval in eval/ possible.
"""

from dataclasses import dataclass, field

import chromadb
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

import config

PERSONA = """You are answering as Lee Kuan Yew, founding Prime Minister of Singapore.

VOICE
- Direct, unsentimental, and precise. You do not pad or hedge for comfort.
- You argue from consequences and hard evidence, not from ideology or slogans.
- You readily state uncomfortable truths, and you concede a point when the
  facts are against you.
- You reason through concrete examples, often from Singapore's own history.
- Measured length. Two to five short paragraphs. No bullet lists, no headings.

GROUNDING - this overrides the voice instructions
- Answer ONLY from the excerpts provided below. They are your own speeches,
  memoirs and interviews.
- Never invent a quotation, statistic, date or anecdote. If the excerpts do not
  support a claim, do not make it.
- If the excerpts do not address the question, say so plainly in your own voice
  and answer only as far as they take you. A short honest answer is correct;
  a confident fabricated one is a failure.
- Cite the documents you drew on using their [n] markers inline.
- You are reasoning from a fixed archive. If asked about events after your
  lifetime, say the archive does not reach that far."""


@dataclass
class Answer:
    text: str
    sources: list[dict] = field(default_factory=list)
    # The chunk text actually placed in the prompt. Callers that need to verify
    # the answer - the eval harness above all - need the excerpts themselves,
    # not just their titles.
    contexts: list[str] = field(default_factory=list)
    rewritten_query: str = ""
    grounded: bool = True


class LKYChatbot:
    def __init__(self) -> None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set; copy .env.example to .env")
        self.client = genai.Client(api_key=config.GEMINI_API_KEY)
        chroma = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
        self.collection = chroma.get_collection(config.COLLECTION)

    # -- model helpers -------------------------------------------------------

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(min=2, max=30))
    def _generate(self, prompt: str, system: str = "", temperature: float = 0.4,
                  model: str | None = None) -> str:
        response = self.client.models.generate_content(
            model=model or config.CHAT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system or None,
                temperature=temperature,
            ),
        )
        return (response.text or "").strip()

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(min=2, max=30))
    def _embed_query(self, text: str) -> list[float]:
        result = self.client.models.embed_content(
            model=config.EMBED_MODEL,
            contents=[text],
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_QUERY",
                output_dimensionality=config.EMBED_DIM,
            ),
        )
        return result.embeddings[0].values

    # -- pipeline stages -----------------------------------------------------

    def rewrite_query(self, question: str, history: list[tuple[str, str]]) -> str:
        """Turn a context-dependent follow-up into a standalone query."""
        if not history:
            return question

        transcript = "\n".join(
            f"User: {user}\nLee Kuan Yew: {reply[:300]}"
            for user, reply in history[-3:]
        )
        prompt = (
            f"{transcript}\n\nFollow-up question: {question}\n\n"
            "Rewrite the follow-up as a standalone search query that makes "
            "sense without the conversation above. Resolve every pronoun and "
            "implicit reference. Reply with the query only."
        )
        try:
            rewritten = self._generate(prompt, temperature=0.0,
                                       model=config.UTILITY_MODEL)
            return rewritten or question
        except Exception:
            return question  # never let rewriting take the whole turn down

    def retrieve(self, query: str) -> list[dict]:
        vector = self._embed_query(query)
        result = self.collection.query(
            query_embeddings=[vector],
            n_results=config.CANDIDATE_K,
            include=["documents", "metadatas", "distances"],
        )
        return [
            {
                "text": doc,
                "meta": meta,
                # Chroma reports cosine distance; similarity is the useful end.
                "score": 1.0 - dist,
            }
            for doc, meta, dist in zip(
                result["documents"][0],
                result["metadatas"][0],
                result["distances"][0],
            )
        ]

    def rerank(self, query: str, candidates: list[dict]) -> list[dict]:
        """Score each candidate for actual usefulness, not vocabulary overlap.

        A cross-encoder would be better, but a single batched LLM call keeps the
        stack to one free API and is a large improvement over raw similarity.
        """
        if len(candidates) <= config.TOP_K:
            return candidates

        listing = "\n\n".join(
            f"[{i}] {c['text'][:500]}" for i, c in enumerate(candidates))
        prompt = (
            f"Question: {query}\n\nPassages:\n{listing}\n\n"
            f"Which passages actually help answer the question? Reply with the "
            f"{config.TOP_K} most useful passage numbers, best first, as a "
            "comma-separated list of digits and nothing else."
        )
        ranked = candidates
        try:
            reply = self._generate(prompt, temperature=0.0,
                                   model=config.UTILITY_MODEL)
            order = [int(t) for t in reply.replace(" ", "").split(",")
                     if t.isdigit() and int(t) < len(candidates)]
            if order:
                chosen = dict.fromkeys(order)
                ranked = ([candidates[i] for i in chosen]
                          + [c for i, c in enumerate(candidates)
                             if i not in chosen])
        except Exception:
            pass
        return self._diversify(ranked)

    @staticmethod
    def _diversify(ranked: list[dict]) -> list[dict]:
        """Cap how many chunks any single document may contribute.

        Adjacent chunks of one speech are near-duplicates by construction: they
        overlap by 200 characters and argue the same point. Retrieval happily
        returned six slots filled by three documents, and the eval judge kept
        flagging the excerpts as redundant. Capping per document trades a little
        depth on the best-matching speech for breadth across the archive, which
        is the better trade when the question is open-ended.
        """
        kept, seen = [], {}
        for candidate in ranked:
            key = candidate["meta"].get("filename", "?")
            if seen.get(key, 0) >= config.MAX_PER_DOC:
                continue
            seen[key] = seen.get(key, 0) + 1
            kept.append(candidate)
            if len(kept) == config.TOP_K:
                break

        # If the cap starved the result - a narrow question the archive answers
        # in one speech - refill from what it excluded rather than under-deliver.
        if len(kept) < config.TOP_K:
            already = {id(c) for c in kept}
            kept += [c for c in ranked
                     if id(c) not in already][:config.TOP_K - len(kept)]
        return kept

    # -- entry point ---------------------------------------------------------

    def ask(self, question: str, history: list[tuple[str, str]] | None = None) -> Answer:
        history = history or []
        query = self.rewrite_query(question, history)
        candidates = self.retrieve(query)

        strong = [c for c in candidates if c["score"] >= config.MIN_RELEVANCE]
        if not strong:
            return Answer(
                text=(
                    "My papers do not touch on that. I would rather tell you "
                    "plainly that I have nothing on it than manufacture an "
                    "opinion and pass it off as considered judgement."
                ),
                grounded=False,
                rewritten_query=query,
            )

        chosen = self.rerank(query, strong)

        excerpts = "\n\n".join(
            f"[{i + 1}] ({c['meta'].get('title', 'untitled')}, "
            f"{c['meta'].get('year', 'n.d.')})\n{c['text']}"
            for i, c in enumerate(chosen)
        )
        recent = "\n".join(
            f"User: {u}\nYou: {a[:300]}" for u, a in history[-3:])
        prompt = (
            (f"Conversation so far:\n{recent}\n\n" if recent else "")
            + f"Excerpts from your own writings and speeches:\n\n{excerpts}\n\n"
            + f"Question: {question}"
        )

        return Answer(
            text=self._generate(prompt, system=PERSONA),
            contexts=[c["text"] for c in chosen],
            sources=[
                {
                    "title": c["meta"].get("title", "untitled"),
                    "year": c["meta"].get("year", "n.d."),
                    "type": c["meta"].get("type", ""),
                    "url": c["meta"].get("url", ""),
                    "score": round(c["score"], 3),
                }
                for c in chosen
            ],
            rewritten_query=query,
        )


if __name__ == "__main__":
    bot = LKYChatbot()
    turns: list[tuple[str, str]] = []
    print("Ask Lee Kuan Yew. Ctrl-C to leave.\n")
    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            continue
        answer = bot.ask(question, turns)
        print(f"\nLee Kuan Yew: {answer.text}\n")
        for source in answer.sources:
            print(f"  - {source['title']} ({source['year']}) {source['score']}")
        print()
        turns.append((question, answer.text))
