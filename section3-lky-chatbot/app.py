"""Gradio chat UI.  Run: python app.py  ->  http://127.0.0.1:7860"""

import gradio as gr

from rag import LKYChatbot

bot = LKYChatbot()

INTRO = (
    "Ask about governance, geopolitics, race and language policy, housing, "
    "leadership, or Singapore's separation from Malaysia. Answers are drawn "
    "only from an indexed archive of Lee Kuan Yew's speeches, memoirs and "
    "interviews — when the archive has nothing, the bot says so rather than "
    "inventing a quotation."
)


def respond(message, chat_history):
    turns = [(turn["content"], chat_history[i + 1]["content"])
             for i, turn in enumerate(chat_history)
             if turn["role"] == "user" and i + 1 < len(chat_history)]

    answer = bot.ask(message, turns)

    reply = answer.text
    if answer.sources:
        cited = "  \n".join(
            f"- *{s['title']}* ({s['year']}) — similarity {s['score']}"
            for s in answer.sources
        )
        reply += f"\n\n---\n**Drawn from:**  \n{cited}"

    return reply


demo = gr.ChatInterface(
    fn=respond,
    type="messages",
    title="Lee Kuan Yew — RAG chatbot",
    description=INTRO,
    examples=[
        "Why did Singapore have to remain multiracial?",
        "How should a small state deal with great powers?",
        "What did separation from Malaysia mean to you?",
        "Was press freedom a price worth paying?",
    ],
)

if __name__ == "__main__":
    demo.launch()
