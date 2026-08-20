"""Evaluation harness for the LKY chatbot.

Run:  python eval/evaluate.py            # full dataset
      python eval/evaluate.py --limit 5  # quick smoke run

Four metrics, chosen because each catches a different way this system can fail:

  faithfulness      Every claim in the answer is supported by the retrieved
                    excerpts. This is the metric that catches invented
                    quotations, the single worst failure mode for a chatbot
                    impersonating a real person.

  answer_relevancy  The answer addresses the question that was asked, rather
                    than reciting whatever the retriever happened to return.

  context_relevancy Share of retrieved chunks that were actually useful. Low
                    scores here mean the retriever is padding the prompt with
                    noise, which drags faithfulness down with it.

  persona_fidelity  The answer reads as Lee Kuan Yew rather than as a neutral
                    assistant summarising him.

Scoring is LLM-as-judge on a 0-1 scale. A judge is not ground truth, so the
harness writes every judgement to eval/results.md for a human to audit; the
aggregate is a tracking signal for tuning, not a certificate of correctness.
Refusal cases are scored separately as a hard pass/fail: on an out-of-corpus
question the only correct behaviour is to decline.
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google.genai import types  # noqa: E402

import config  # noqa: E402
from rag import LKYChatbot  # noqa: E402

DATASET = Path(__file__).parent / "dataset.jsonl"
REPORT = Path(__file__).parent / "results.md"

RUBRIC = """You are grading a chatbot that answers as Lee Kuan Yew using only
retrieved excerpts from his speeches and memoirs.

QUESTION
{question}

RETRIEVED EXCERPTS
{context}

CHATBOT ANSWER
{answer}

Score each dimension from 0.0 to 1.0:

faithfulness: Is every factual claim, quotation, date and statistic in the
  answer supported by the excerpts? Score 0.0 if the answer invents a quotation
  or a specific fact that does not appear above. Confident fabrication is the
  worst outcome; an answer that admits the excerpts fall short scores high.
answer_relevancy: Does the answer actually address the question asked?
context_relevancy: What share of the excerpts were genuinely useful for
  answering? Judge the retrieval, not the answer.
persona_fidelity: Does it read as Lee Kuan Yew - direct, consequentialist,
  unsentimental, reasoning from concrete cases - rather than as a neutral
  assistant describing his views in the third person?

Reply with JSON only:
{{"faithfulness": 0.0, "answer_relevancy": 0.0, "context_relevancy": 0.0,
  "persona_fidelity": 0.0, "note": "one sentence on the weakest dimension"}}"""

METRICS = ["faithfulness", "answer_relevancy", "context_relevancy", "persona_fidelity"]

DECLINE_CHECK = """Here is a chatbot's reply to a question it was not supposed to
be able to answer.

REPLY
{answer}

Did the chatbot decline - saying it lacks the information, that its sources do
not cover this, or that the question falls outside what it can speak to?
Answer with exactly one word: YES or NO."""


def declined(client, answer: str) -> bool:
    """Did the system refuse, by whichever mechanism?

    There are two independent defences against answering an unanswerable
    question: the relevance floor stops retrieval before generation, and the
    grounding rules in the system prompt make the model decline. An earlier
    version of this harness measured only the first, by checking whether the
    floor had fired. It scored a correct, explicit refusal - "the archive does
    not reach 2024" - as a FAILURE, because the floor had not been the thing
    that caught it. The metric was punishing right behaviour for arriving
    through the wrong door. What matters to a user is that the system declined.
    """
    reply = client.models.generate_content(
        model=config.JUDGE_MODEL,
        contents=DECLINE_CHECK.format(answer=answer),
        config=types.GenerateContentConfig(temperature=0.0),
    )
    return (reply.text or "").strip().upper().startswith("YES")


def judge(client, question: str, context: str, answer: str) -> dict:
    response = client.models.generate_content(
        model=config.JUDGE_MODEL,
        contents=RUBRIC.format(question=question, context=context, answer=answer),
        config=types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
        ),
    )
    try:
        return json.loads(response.text)
    except (json.JSONDecodeError, TypeError):
        return {m: 0.0 for m in METRICS} | {"note": "judge returned unparseable output"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    # A graded case costs three model calls (rerank, generate, judge). The free
    # tier caps generation per DAY, not per minute, so pacing cannot buy more
    # headroom - it only avoids tripping the per-minute limit on top of it. The
    # three calls are split across three models partly for this reason.
    parser.add_argument("--delay", type=float, default=12.0,
                        help="seconds between cases; lower it on a paid tier")
    args = parser.parse_args()

    cases = [json.loads(line) for line in
             DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        cases = cases[:args.limit]

    bot = LKYChatbot()
    scored, refusals, rows, failures = [], [], [], []
    floor_stops = []  # of the refusals, how many the relevance floor caught

    # A previous run's report must not survive this one. If this run dies
    # halfway, a stale results.md sitting on disk reads as current and is worse
    # than no file at all - the same trap as an eval that scores blind.
    REPORT.unlink(missing_ok=True)

    for index, case in enumerate(cases):
        if index:
            time.sleep(args.delay)
        print(f"  {case['id']}: {case['question'][:60]}...")
        try:
            answer = bot.ask(case["question"])
        except Exception as exc:
            # Usually a quota wall. Record it and keep going: a report covering
            # 12 of 15 cases with the gap named is useful, losing all 12 is not.
            reason = type(exc).__name__
            print(f"       FAILED ({reason})")
            failures.append((case["id"], reason))
            continue

        if not case["expect_grounded"]:
            # Out-of-corpus question: declining is the only correct behaviour,
            # so this is pass/fail rather than a graded score.
            try:
                passed = declined(bot.client, answer.text)
            except Exception as exc:
                print(f"       decline check FAILED ({type(exc).__name__})")
                failures.append((case["id"], f"decline check: {type(exc).__name__}"))
                continue
            refusals.append(passed)
            floor_stops.append(not answer.grounded)
            rows.append({
                "id": case["id"], "kind": "refusal", "question": case["question"],
                "passed": passed, "by_floor": not answer.grounded,
                "answer": answer.text,
            })
            continue

        # The judge scores faithfulness by checking each claim against the
        # source text, so it must receive the excerpts themselves. An earlier
        # version passed only (title, year) pairs. The judge dutifully returned
        # near-zero faithfulness and context_relevancy on every case, and the
        # result read like a broken retriever rather than a broken harness -
        # the failure mode of an eval that is itself wrong.
        context = "\n\n".join(
            f"[{i + 1}] ({src['title']}, {src['year']})\n{text}"
            for i, (src, text) in enumerate(zip(answer.sources, answer.contexts))
        )
        try:
            verdict = judge(bot.client, case["question"], context, answer.text)
        except Exception as exc:
            print(f"       judge FAILED ({type(exc).__name__})")
            failures.append((case["id"], f"judge: {type(exc).__name__}"))
            continue
        scored.append(verdict)
        rows.append({
            "id": case["id"], "kind": "graded", "question": case["question"],
            "answer": answer.text, "sources": answer.sources, **verdict,
        })

    # -- report --------------------------------------------------------------
    lines = ["# Evaluation results", ""]
    lines.append(f"Generator: `{config.CHAT_MODEL}` · Judge: "
                 f"`{config.JUDGE_MODEL}` · Rerank/rewrite: "
                 f"`{config.UTILITY_MODEL}`  ")
    lines.append(f"Embeddings: `{config.EMBED_MODEL}` "
                 f"({config.EMBED_DIM}-dim) · top_k={config.TOP_K} reranked "
                 f"from {config.CANDIDATE_K} · floor={config.MIN_RELEVANCE}")
    lines.append("")
    graded = len(scored)
    lines.append(f"Coverage: {graded + len(refusals)}/{len(cases)} cases "
                 f"({graded} graded, {len(refusals)} refusal checks)")
    if failures:
        lines.append("")
        lines.append("> **Partial run.** These cases did not complete, so the "
                     "aggregate below covers only the cases that did:")
        lines.append("")
        for case_id, reason in failures:
            lines.append(f"> - `{case_id}` — {reason}")
    lines.append("")
    lines.append("## Aggregate")
    lines.append("")
    lines.append("| Metric | Mean | Min |")
    lines.append("|---|---|---|")
    for metric in METRICS:
        values = [float(v.get(metric, 0) or 0) for v in scored]
        if values:
            lines.append(f"| {metric} | {statistics.mean(values):.2f} "
                         f"| {min(values):.2f} |")
    if refusals:
        lines.append(f"| refusal_accuracy | {sum(refusals)}/{len(refusals)} | |")
        lines.append(f"| ├ stopped by the relevance floor | "
                     f"{sum(floor_stops)}/{len(refusals)} | |")
        lines.append(f"| └ stopped by the grounding prompt | "
                     f"{sum(refusals) - sum(floor_stops)}/{len(refusals)} | |")
    lines.append("")
    lines.append("## Per-case")
    lines.append("")

    for row in rows:
        lines.append(f"### {row['id']} — {row['question']}")
        lines.append("")
        if row["kind"] == "refusal":
            caught = ("relevance floor" if row["by_floor"]
                      else "grounding rules in the prompt")
            lines.append(f"Expected a refusal. "
                         f"**{'PASS' if row['passed'] else 'FAIL'}** "
                         f"— caught by the {caught}.")
        else:
            lines.append(" · ".join(
                f"{m}: {float(row.get(m, 0) or 0):.2f}" for m in METRICS))
            lines.append("")
            lines.append(f"Judge: {row.get('note', '')}")
            if row.get("sources"):
                cited = ", ".join(f"{s['title']} ({s['year']})"
                                  for s in row["sources"])
                lines.append("")
                lines.append(f"Retrieved: {cited}")
        lines.append("")
        lines.append("> " + row["answer"].replace("\n", "\n> "))
        lines.append("")

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {REPORT}")

    for metric in METRICS:
        values = [float(v.get(metric, 0) or 0) for v in scored]
        if values:
            print(f"  {metric:18s} {statistics.mean(values):.2f}")
    if refusals:
        print(f"  {'refusal_accuracy':18s} {sum(refusals)}/{len(refusals)} "
              f"(floor caught {sum(floor_stops)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
