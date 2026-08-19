"""Central configuration. Every tunable knob lives here so the retrieval
pipeline can be re-tuned without touching the logic."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

ROOT = Path(__file__).parent
CORPUS_DIR = ROOT / "data" / "corpus"
CHROMA_DIR = ROOT / "chroma_db"
COLLECTION = "lky"

# --- Models -----------------------------------------------------------------
# Free tier on Google AI Studio covers both of these.
CHAT_MODEL = "gemini-2.5-flash"
EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 768  # gemini-embedding-001 supports MRL truncation; 768 keeps the
                 # index small without a meaningful recall loss.

# --- Chunking ---------------------------------------------------------------
# Speeches argue in long arcs, so chunks are larger than the usual 500-token
# default; the overlap keeps a claim and its supporting sentence together when
# a split lands mid-argument.
CHUNK_SIZE = 1200        # characters
CHUNK_OVERLAP = 200      # characters

# --- Retrieval --------------------------------------------------------------
TOP_K = 6                # chunks passed to the generator
CANDIDATE_K = 20         # chunks pulled before reranking
MIN_RELEVANCE = 0.25     # cosine similarity floor; below this we treat the
                         # corpus as having no answer and say so.
