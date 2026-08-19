"""Measure the retrieval similarity distribution to set MIN_RELEVANCE.

Run:  python eval/calibrate_floor.py

The relevance floor decides when the pipeline refuses to answer instead of
generating from weak context. Picking that threshold by intuition does not
work: cosine similarity from these embeddings is not calibrated to human
notions of "related", and the absolute numbers depend on the embedding model,
the dimensionality and the corpus. The only way to set it is to measure where
in-corpus and out-of-corpus questions actually land.

This prints both distributions and the separation between them, so the floor in
config.py is a measured value with a recorded justification rather than a guess.
"""

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: E402
from rag import LKYChatbot  # noqa: E402

IN_CORPUS = [
    "Why did Singapore have to remain multiracial?",
    "What was your reasoning for making English the working language?",
    "How did you justify limits on press freedom?",
    "Why did you push public housing and home ownership?",
    "What did separation from Malaysia mean to you?",
    "How should a small state deal with great powers?",
    "How did you approach corruption in the civil service?",
    "What makes for good government?",
]

OUT_OF_CORPUS = [
    "What is the best recipe for chicken rice?",
    "Which cryptocurrency would you invest in today?",
    "How do I fix a memory leak in a React component?",
    "What were the rules of the Byzantine tax system?",
    "Who won the 2022 FIFA World Cup?",
    "How do I train a dog not to bark at the postman?",
]


def top_scores(bot: LKYChatbot, questions: list[str]) -> list[tuple[str, float]]:
    out = []
    for question in questions:
        best = max(c["score"] for c in bot.retrieve(question))
        out.append((question, best))
        print(f"  {best:.3f}  {question[:62]}")
    return out


def main() -> int:
    bot = LKYChatbot()

    print("IN-CORPUS (the floor must let all of these through)")
    inside = top_scores(bot, IN_CORPUS)
    print("\nOUT-OF-CORPUS (the floor should stop all of these)")
    outside = top_scores(bot, OUT_OF_CORPUS)

    in_scores = [s for _, s in inside]
    out_scores = [s for _, s in outside]
    in_min, out_max = min(in_scores), max(out_scores)

    print("\n" + "=" * 62)
    print(f"in-corpus     min {in_min:.3f}   "
          f"mean {statistics.mean(in_scores):.3f}   max {max(in_scores):.3f}")
    print(f"out-of-corpus min {min(out_scores):.3f}   "
          f"mean {statistics.mean(out_scores):.3f}   max {out_max:.3f}")
    print("=" * 62)

    if in_min > out_max:
        floor = round((in_min + out_max) / 2, 3)
        print(f"\nClean separation of {in_min - out_max:.3f}.")
        print(f"Midpoint floor: MIN_RELEVANCE = {floor}")
    else:
        # Overlap means no threshold separates the two sets perfectly. Prefer
        # answering a weak question over refusing a good one, so sit just under
        # the lowest real question and accept the false negatives above it.
        floor = round(in_min - 0.01, 3)
        leaks = sum(1 for s in out_scores if s >= floor)
        print(f"\nOverlap: lowest real question {in_min:.3f} sits below the "
              f"highest unrelated one {out_max:.3f}.")
        print(f"No threshold separates them cleanly. Choosing {floor} keeps "
              f"every real question and lets {leaks}/{len(out_scores)} "
              f"unrelated ones through to the generator, where the grounding "
              f"rules in the system prompt are the second line of defence.")
        print(f"\nMIN_RELEVANCE = {floor}")

    print(f"\nconfig.py currently has MIN_RELEVANCE = {config.MIN_RELEVANCE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
