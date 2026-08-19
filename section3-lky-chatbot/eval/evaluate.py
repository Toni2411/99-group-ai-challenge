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


def judge(client, question: str, context: str, answer: str) -> dict:
    response = client.models.generate_content(
        model=config.CHAT_MODEL,
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
    args = parser.parse_args()

    cases = [json.loads(line) for line in
             DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        cases = cases[:args.limit]

    bot = LKYChatbot()
    scored, refusals, rows = [], [], []

    for case in cases:
        print(f"  {case['id']}: {case['question'][:60]}...")
        answer = bot.ask(case["question"])

        if not case["expect_grounded"]:
            # Out-of-corpus question: declining is the only correct behaviour,
            # so this is pass/fail rather than a graded score.
            passed = not answer.grounded
            refusals.append(passed)
            rows.append({
                "id": case["id"], "kind": "refusal", "question": case["question"],
                "passed": passed, "answer": answer.text,
            })
            continue

        context = "\n\n".join(
            f"({s['title']}, {s['year']})" for s in answer.sources)
        verdict = judge(bot.client, case["question"], context, answer.text)
        scored.append(verdict)
        rows.append({
            "id": case["id"], "kind": "graded", "question": case["question"],
            "answer": answer.text, "sources": answer.sources, **verdict,
        })

    # -- report --------------------------------------------------------------
    lines = ["# Evaluation results", ""]
    lines.append(f"Model: `{config.CHAT_MODEL}` · Embeddings: "
                 f"`{config.EMBED_MODEL}` · top_k={config.TOP_K} "
                 f"(reranked from {config.CANDIDATE_K})")
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
    lines.append("")
    lines.append("## Per-case")
    lines.append("")

    for row in rows:
        lines.append(f"### {row['id']} — {row['question']}")
        lines.append("")
        if row["kind"] == "refusal":
            lines.append(f"Expected a refusal. "
                         f"**{'PASS' if row['passed'] else 'FAIL'}**")
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
        print(f"  {'refusal_accuracy':18s} {sum(refusals)}/{len(refusals)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
