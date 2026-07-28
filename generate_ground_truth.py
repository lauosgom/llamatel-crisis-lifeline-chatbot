"""
generate_ground_truth.py
Generates synthetic evaluation questions for the FAQ RAG pipeline, adapted
from the llm-zoomcamp ground-truth generation pattern (an LLM writes
realistic user questions per knowledge-base entry, so retrieval evaluation
tests real-world phrasing rather than the trivial "ask the FAQ's own
question back" case).

Since this bot answers questions for a mental health crisis lifeline,
phrasing range matters more here than in a typical FAQ bot: someone
reaching out may be anxious, indirect, or informal rather than using
clean/clinical wording. The prompt below asks the model to reflect that
range, while explicitly avoiding generating first-person crisis
disclosures as test data - these are meant to be realistic questions
*about* the service (hours, confidentiality, how it works, etc.), not
simulated crisis messages.

IMPORTANT: Read through the generated questions before using them. LLM
output in a sensitive domain like this should get a human pass, not just
be trusted blindly - see the printed sample at the end of this script.

Reads faq_data.json (the same file build_index.py uses) and writes
data/ground-truth-retrieval.csv with `id` and `question` columns, matching
the id scheme build_index.py assigns ("faq-0", "faq-1", ...).
"""

import json
import os

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI
from tqdm.auto import tqdm

load_dotenv()

FAQ_JSON_PATH = "/home/lauosgom/anomaly/llamatel-crisis-lifeline-chatbot/faq_data.json"
OUTPUT_PATH = "/home/lauosgom/anomaly/llamatel-crisis-lifeline-chatbot/data/ground-truth-retrieval.csv"
MODEL = "gpt-4o-mini"
QUESTIONS_PER_FAQ = 2

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

PROMPT_TEMPLATE = """
You are helping evaluate a support chatbot that answers informational
questions for a mental health crisis lifeline. The chatbot answers using
an official FAQ - it does not give clinical advice and is not a
replacement for a real crisis responder.

For the FAQ entry below, generate {n} different questions IN SPANISH a real person
might type to ask a chat assistant for this specific information. Vary
tone, structure, and phrasing realistically:
- Some questions should be calm and direct, stated plainly with no
  preamble.
- Some can be more anxious, informal, or hesitant in tone - but keep
  these about the information itself (e.g. "wait, is this actually free
  and confidential?"), NOT a first-person description of a personal
  crisis or suicidal ideation. Do not write crisis disclosures.
- Genuinely vary sentence structure and length, not just the wording.
  Real people don't all open their questions the same way.
- IMPORTANT: Do NOT start questions with a filler/confirmation opener
  like "Espera," "Oye,"  or similar - do not reuse the same
  opening word or phrase across the questions you generate. Each question
  should read as if written by a different person, in their own words.

Return ONLY a JSON array of strings, no other text, e.g.:
["...", "...", "..."]

FAQ Question: {question}
FAQ Answer: {answer}
""".strip()


def generate_for_entry(item: dict, n: int = QUESTIONS_PER_FAQ) -> list[str]:
    prompt = PROMPT_TEMPLATE.format(question=item["question"], answer=item["answer"], n=n)

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )

    raw = response.choices[0].message.content.strip().strip("`")
    if raw.lower().startswith("json"):
        raw = raw[4:].strip()
    return json.loads(raw)


def main():
    with open(FAQ_JSON_PATH, "r", encoding="utf-8") as f:
        faqs = json.load(f)

    results = []
    skipped = []

    for i, item in enumerate(tqdm(faqs)):
        faq_id = f"faq-{i}"
        try:
            questions = generate_for_entry(item)
        except (json.JSONDecodeError, KeyError) as e:
            skipped.append(faq_id)
            print(f"Skipping {faq_id} due to a parse error: {e}")
            continue

        for q in questions:
            results.append({"id": faq_id, "question": q})

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    df_questions = pd.DataFrame(results)
    df_questions.to_csv(OUTPUT_PATH, index=False)

    print(f"\nWrote {len(df_questions)} ground-truth questions to {OUTPUT_PATH}")
    if skipped:
        print(f"Skipped {len(skipped)} entries (parse errors): {skipped}")

    print("\nSample - please read through these before using them for eval:")
    print(df_questions.head(10).to_string(index=False))


if __name__ == "__main__":
    main()