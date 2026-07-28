"""
build_index.py
Reads faq_data.json, embeds each entry with OpenAI, and loads the result
into a BigQuery table. Run this once whenever your FAQ document changes
(it truncates and reloads the table each time, so it's safe to re-run).

Supports two embedding modes, so you can build both as separate tables and
compare retrieval quality with evaluate.py:
  --embedding-mode combined       (default) embeds "Q: ...\\nA: ..." - the
                                   answer's wording is folded into the vector
  --embedding-mode question_only  embeds just the question text - the
                                   answer is never part of what's searched,
                                   only what's returned

Usage:
    python build_index.py                                          # combined, default table
    python build_index.py --embedding-mode question_only \\
                           --table-id faq_embeddings_qonly           # question-only variant

Requires:
  - A .env file (copy .env.example to .env and fill in) with your
    OPENAI_API_KEY, GCP_PROJECT_ID, and optionally
    GOOGLE_APPLICATION_CREDENTIALS if not using ADC
  - A GCP project with the BigQuery API enabled
  - A dataset already created (see README) — this script creates the
    table itself if it doesn't exist
"""

import argparse
import json
import os

from dotenv import load_dotenv
from google.cloud import bigquery
from openai import OpenAI

load_dotenv()

FAQ_JSON_PATH = "llamatel-crisis-lifeline-chatbot/faq_data.json"
EMBEDDING_MODEL = "text-embedding-3-small"

PROJECT_ID = os.environ["GCP_PROJECT_ID"]
DATASET_ID = os.environ.get("BQ_DATASET_ID", "faq_bot")
DEFAULT_TABLE_ID = os.environ.get("BQ_TABLE_ID", "faq_embeddings")

openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
bq_client = bigquery.Client(project=PROJECT_ID)

SCHEMA = [
    bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("question", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("answer", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("embedding", "FLOAT64", mode="REPEATED"),
]


def embed_texts(texts: list[str]) -> list[list[float]]:
    resp = openai_client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [d.embedding for d in resp.data]


def ensure_table_exists(table_fqn: str):
    try:
        bq_client.get_table(table_fqn)
    except Exception:
        table = bigquery.Table(table_fqn, schema=SCHEMA)
        bq_client.create_table(table)
        print(f"Created table {table_fqn}")


def build(table_id: str = DEFAULT_TABLE_ID, embedding_mode: str = "combined"):
    table_fqn = f"{PROJECT_ID}.{DATASET_ID}.{table_id}"

    with open(FAQ_JSON_PATH, "r", encoding="utf-8") as f:
        faqs = json.load(f)

    if embedding_mode == "question_only":
        texts_to_embed = [item["question"] for item in faqs]
    else:
        # Each FAQ entry is its own chunk — no need to split further.
        texts_to_embed = [f"Q: {item['question']}\nA: {item['answer']}" for item in faqs]

    print(f"Embedding {len(texts_to_embed)} FAQ entries (mode={embedding_mode})...")
    embeddings = embed_texts(texts_to_embed)

    rows = [
        {
            "id": f"faq-{i}",
            "question": item["question"],
            "answer": item["answer"],
            "embedding": embedding,
        }
        for i, (item, embedding) in enumerate(zip(faqs, embeddings))
    ]

    ensure_table_exists(table_fqn)

    # Truncate + reload so re-running this script never leaves stale/duplicate rows.
    bq_client.query(f"TRUNCATE TABLE `{table_fqn}`").result()

    job_config = bigquery.LoadJobConfig(
        schema=SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    load_job = bq_client.load_table_from_json(rows, table_fqn, job_config=job_config)
    load_job.result()  # wait for completion

    print(f"Loaded {len(rows)} FAQ entries into {table_fqn}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--table-id",
        default=DEFAULT_TABLE_ID,
        help=f"BigQuery table to write to (default: {DEFAULT_TABLE_ID}, from BQ_TABLE_ID)",
    )
    parser.add_argument(
        "--embedding-mode",
        choices=["combined", "question_only"],
        default="combined",
        help="What text gets embedded per FAQ entry (default: combined)",
    )
    args = parser.parse_args()

    build(table_id=args.table_id, embedding_mode=args.embedding_mode)