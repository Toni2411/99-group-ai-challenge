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
# Three roles, three models, for two separate reasons.
#
# Quality: generation is the only step whose output a human reads, so it gets
# the strongest model. Query rewriting and reranking are mechanical - pick the
# useful passages, resolve a pronoun - and a lite model does them just as well
# for a fraction of the quota.
#
# Independence: the judge in eval/ must NOT be the model being judged. Scoring
# your own output inflates faithfulness, because a model finds its own
# reasoning persuasive. JUDGE_MODEL is deliberately from a different generation
# than CHAT_MODEL. It is not true independence - both are Gemini - but it is
# meaningfully better than grading your own homework.
# A third constraint turned out to dominate the first two: the free tier caps
# the full flash models at 20 generate requests per DAY. One eval run costs ~27
# calls, so generation cannot live there - a single evaluation would exhaust the
# day's quota before finishing. The lite models carry a far higher daily
# allowance, so everything at query time runs on lite.
#
# This is a real quality trade, not a free win: flash-lite writes a noticeably
# flatter answer than flash. On a paid tier CHAT_MODEL should move back up. It
# is recorded here rather than quietly absorbed because the eval numbers were
# produced under this constraint and would improve with a better generator.
CHAT_MODEL = "gemini-3.5-flash-lite"     # generation, the answer itself
UTILITY_MODEL = "gemini-3.5-flash-lite"  # query rewriting and reranking
JUDGE_MODEL = "gemini-3.1-flash-lite"    # eval only, never used at query time
                                         # deliberately a different generation
                                         # from CHAT_MODEL - see above

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
MAX_PER_DOC = 2          # chunks any one document may contribute to TOP_K.
                         # Adjacent chunks of a speech overlap by design, so
                         # without this one document routinely filled half the
                         # context with restatements of the same paragraph.

# Cosine similarity floor: below this the corpus is treated as having no answer
# and the pipeline refuses before generating.
#
# Measured, not guessed - run eval/calibrate_floor.py to reproduce. On this
# corpus, real questions score 0.623-0.696 and unrelated ones 0.509-0.556, a
# clean gap of 0.067; 0.59 is the midpoint. The first version of this file had
# 0.25, which felt like a reasonable "low similarity" threshold and was in fact
# so far below the noise floor that the check could never fire. Cosine
# similarity is not calibrated to human intuitions about relatedness, and the
# absolute values shift with the embedding model and dimensionality - so this
# number has to be re-measured whenever either changes.
MIN_RELEVANCE = 0.59
