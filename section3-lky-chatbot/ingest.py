"""Build the vector index from the LKY corpus.

Run:  python ingest.py            # incremental, skips unchanged chunks
      python ingest.py --rebuild  # drop the collection and start over

Corpus format: one .txt or .md file per document under data/corpus/, with an
optional YAML-ish header so every chunk carries provenance into retrieval:

    ---
    title: Speech at the Political Study Centre
    year: 1966
    type: speech
    source: National Archives of Singapore
    url: https://...
    ---
    <body text>

Provenance matters here beyond tidiness: the chatbot cites what it used, and a
speech from 1966 and a memoir passage from 1998 carry very different authority
on the same question.
"""

import argparse
import hashlib
import re
import sys
import time
from pathlib import Path

import chromadb
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

import config

HEADER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_document(path: Path) -> tuple[dict, str]:
    """Split an optional metadata header from the body text."""
    raw = path.read_text(encoding="utf-8")
    meta = {"title": path.stem, "year": "unknown", "type": "unknown",
            "source": "unknown", "url": ""}

    match = HEADER_RE.match(raw)
    if match:
        for line in match.group(1).splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()
        raw = raw[match.end():]

    meta["filename"] = path.name
    return meta, raw.strip()


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Recursive-character splitting.

    Try to break on the largest natural boundary that fits — paragraph, then
    sentence, then whitespace — so a chunk rarely ends mid-thought. Falling
    back to a hard character cut only happens for pathological input.
    """
    separators = ["\n\n", "\n", ". ", " "]

    def split(chunk: str, seps: list[str]) -> list[str]:
        if len(chunk) <= size:
            return [chunk]
        if not seps:
            return [chunk[i:i + size] for i in range(0, len(chunk), size)]

        sep, rest = seps[0], seps[1:]
        parts, out, buf = chunk.split(sep), [], ""
        for part in parts:
            candidate = f"{buf}{sep}{part}" if buf else part
            if len(candidate) <= size:
                buf = candidate
            else:
                if buf:
                    out.append(buf)
                buf = part if len(part) <= size else ""
                if not buf:
                    out.extend(split(part, rest))
        if buf:
            out.append(buf)
        return out

    pieces = [p.strip() for p in split(text, separators) if p.strip()]

    # Re-glue with overlap so a claim and its supporting sentence survive a cut.
    if overlap <= 0 or len(pieces) < 2:
        return pieces
    return [pieces[0]] + [pieces[i - 1][-overlap:] + " " + pieces[i]
                          for i in range(1, len(pieces))]


@retry(stop=stop_after_attempt(8), wait=wait_exponential(min=5, max=120))
def embed_batch(client: genai.Client, texts: list[str], task: str) -> list[list[float]]:
    """Embed a batch, retrying through the free tier's rate limits.

    task is RETRIEVAL_DOCUMENT when indexing and RETRIEVAL_QUERY when asking.
    Using the matched pair is not cosmetic — asymmetric embeddings measurably
    beat using one task type for both sides.
    """
    result = client.models.embed_content(
        model=config.EMBED_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(
            task_type=task,
            output_dimensionality=config.EMBED_DIM,
        ),
    )
    return [e.values for e in result.embeddings]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true",
                        help="drop the existing collection first")
    args = parser.parse_args()

    if not config.GEMINI_API_KEY:
        print("GEMINI_API_KEY is not set. Copy .env.example to .env first.")
        return 1

    docs = sorted(list(config.CORPUS_DIR.glob("*.txt"))
                  + list(config.CORPUS_DIR.glob("*.md")))
    if not docs:
        print(f"No documents in {config.CORPUS_DIR}. See data/sources.md.")
        return 1

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    chroma = chromadb.PersistentClient(path=str(config.CHROMA_DIR))

    if args.rebuild:
        try:
            chroma.delete_collection(config.COLLECTION)
        except Exception:
            pass

    collection = chroma.get_or_create_collection(
        name=config.COLLECTION, metadata={"hnsw:space": "cosine"})

    existing = set(collection.get(include=[])["ids"])
    pending_ids, pending_texts, pending_meta = [], [], []

    for path in docs:
        meta, body = parse_document(path)
        chunks = chunk_text(body, config.CHUNK_SIZE, config.CHUNK_OVERLAP)
        print(f"  {path.name}: {len(chunks)} chunks")

        for index, chunk in enumerate(chunks):
            # Content-hash id makes re-running cheap and idempotent: edit one
            # document and only its chunks are re-embedded.
            chunk_id = hashlib.sha256(
                f"{path.name}:{chunk}".encode("utf-8")).hexdigest()[:32]
            if chunk_id in existing:
                continue
            pending_ids.append(chunk_id)
            pending_texts.append(chunk)
            pending_meta.append({**meta, "chunk_index": index})

    if not pending_texts:
        print(f"\nIndex already current ({len(existing)} chunks).")
        return 0

    # The free tier meters individual texts, not calls: a batch of 25 spends 25
    # of the 100-per-minute allowance. Retrying into a per-minute quota mostly
    # burns the quota again, so the loop paces itself to stay under the limit
    # and keeps retries for genuine transient failures.
    print(f"\nEmbedding {len(pending_texts)} new chunks...")
    BATCH = 25
    RATE_LIMIT_PER_MIN = 100
    SAFETY = 0.75

    for start in range(0, len(pending_texts), BATCH):
        stop = start + BATCH
        batch = pending_texts[start:stop]
        began = time.monotonic()

        vectors = embed_batch(client, batch, "RETRIEVAL_DOCUMENT")
        collection.add(
            ids=pending_ids[start:stop],
            documents=batch,
            metadatas=pending_meta[start:stop],
            embeddings=vectors,
        )
        print(f"  indexed {min(stop, len(pending_texts))}/{len(pending_texts)}")

        if stop < len(pending_texts):
            budget = len(batch) * 60.0 / (RATE_LIMIT_PER_MIN * SAFETY)
            pause = budget - (time.monotonic() - began)
            if pause > 0:
                time.sleep(pause)

    print(f"\nDone. Collection holds {collection.count()} chunks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
