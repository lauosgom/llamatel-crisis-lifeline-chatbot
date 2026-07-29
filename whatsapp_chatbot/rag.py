"""
rag.py
Core retrieval-augmented generation logic: embed a question, retrieve the
closest FAQ entries from BigQuery via VECTOR_SEARCH, and ask OpenAI to
answer using only that context. Kept separate from the WhatsApp transport
layer so you can test it independently (see the __main__ block).

Also logs every question that reaches the bot's trigger logic (see
log_interaction) to a separate BigQuery table, so real questions - matched
or not - can be reviewed later to enrich the FAQ. Only the question text
and match outcome are stored - no sender/group identifying info.
"""

import os
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from google.cloud import bigquery
from openai import OpenAI

load_dotenv()

EMBEDDING_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o-mini"

PROJECT_ID = os.environ["GCP_PROJECT_ID"]
DATASET_ID = os.environ.get("BQ_DATASET_ID", "faq_bot")
TABLE_ID = os.environ.get("BQ_TABLE_ID", "faq_embeddings")
TABLE_FQN = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"

QUERY_LOG_TABLE_ID = os.environ.get("BQ_QUERY_LOG_TABLE_ID", "query_logs")
QUERY_LOG_TABLE_FQN = f"{PROJECT_ID}.{DATASET_ID}.{QUERY_LOG_TABLE_ID}"

QUERY_LOG_SCHEMA = [
    bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("timestamp", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("question", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("matched_faq_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("distance", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("responded", "BOOL", mode="REQUIRED"),
    bigquery.SchemaField("answer", "STRING", mode="NULLABLE"),
]

# Below this cosine distance, we treat the FAQ as "no good match" and stay
# silent rather than guessing. COSINE distance ranges 0 (identical) to 2
# (opposite) — LOWER = more similar. Tune against real questions from your
# group (print the distance in retrieve() while testing).
DISTANCE_THRESHOLD = 0.5

TOP_K = 3

SYSTEM_PROMPT = (
    "You are a helpful FAQ assistant for a WhatsApp group. "
    "Answer the user's question using ONLY the provided FAQ context. "
    "Keep answers short and conversational, suitable for a chat message. "
    "If the context doesn't actually answer the question, say you're not sure "
    "and suggest they ask a human admin."
)

openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
bq_client = bigquery.Client(project=PROJECT_ID)


def embed_query(text: str) -> list[float]:
    resp = openai_client.embeddings.create(model=EMBEDDING_MODEL, input=[text])
    return resp.data[0].embedding


def retrieve(question: str, top_k: int = TOP_K, table_fqn: str | None = None):
    """Returns a list of (id, question, answer, distance) tuples for the
    closest FAQ entries, using BigQuery's VECTOR_SEARCH function.
    Pass table_fqn to query a different table than the module default -
    used by evaluate.py to A/B compare embedding variants."""
    table_fqn = table_fqn or TABLE_FQN
    query_embedding = embed_query(question)

    sql = f"""
        SELECT
            base.id AS id,
            base.question AS question,
            base.answer AS answer,
            distance
        FROM VECTOR_SEARCH(
            TABLE `{table_fqn}`,
            'embedding',
            (SELECT @query_embedding AS embedding),
            top_k => @top_k,
            distance_type => 'COSINE'
        )
        ORDER BY distance
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("query_embedding", "FLOAT64", query_embedding),
            bigquery.ScalarQueryParameter("top_k", "INT64", top_k),
        ]
    )
    results = bq_client.query(sql, job_config=job_config).result()
    return [(row.id, row.question, row.answer, row.distance) for row in results]


def has_good_match(question: str) -> bool:
    """Cheap check for whether an untriggered '?' message has a confident
    FAQ match. Not used by whatsapp_bot.py's main loop (which reuses a
    single retrieve() call for both the confidence check and generation -
    see decide_and_answer below) but kept here for standalone testing."""
    matches = retrieve(question, top_k=1)
    return bool(matches) and matches[0][3] <= DISTANCE_THRESHOLD


def generate_answer(question: str, matches: list) -> str:
    """Generates an answer from already-retrieved matches. Split out from
    answer_question() so whatsapp_bot.py can retrieve once, use the result
    both to decide whether to respond and to log the interaction, and only
    then generate - instead of retrieving twice per message."""
    context = "\n\n".join(f"Q: {q}\nA: {a}" for _, q, a, _ in matches)

    completion = openai_client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"FAQ context:\n{context}\n\nUser question: {question}",
            },
        ],
        temperature=0.2,
    )
    return completion.choices[0].message.content.strip()


def answer_question(question: str) -> str:
    """Convenience wrapper: retrieve then generate in one call. Used by the
    __main__ block below for quick CLI testing."""
    matches = retrieve(question)
    return generate_answer(question, matches)


def ensure_query_log_table_exists():
    try:
        bq_client.get_table(QUERY_LOG_TABLE_FQN)
    except Exception:
        table = bigquery.Table(QUERY_LOG_TABLE_FQN, schema=QUERY_LOG_SCHEMA)
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY, field="timestamp"
        )
        bq_client.create_table(table)
        print(f"Created table {QUERY_LOG_TABLE_FQN}")


def log_interaction(
    question: str, responded: bool, answer: str | None = None, matches: list | None = None
):
    """Logs a question and its outcome to BigQuery for later FAQ review -
    no sender/group identifying info is stored, only the question text,
    the best match (if any), and whether/what the bot answered.
    Logging failures are swallowed so a BigQuery hiccup never breaks the
    bot's actual response to the group."""
    top_id, top_distance = (None, None)
    if matches:
        top_id, top_distance = matches[0][0], matches[0][3]

    row = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "question": question,
        "matched_faq_id": top_id,
        "distance": top_distance,
        "responded": responded,
        "answer": answer,
    }

    try:
        errors = bq_client.insert_rows_json(QUERY_LOG_TABLE_FQN, [row])
        if errors:
            print(f"Failed to log interaction: {errors}")
    except Exception as e:
        print(f"Failed to log interaction: {e}")


if __name__ == "__main__":
    # Quick manual test: python rag.py "how do I get a refund?"
    import sys

    q = sys.argv[1] if len(sys.argv) > 1 else "How do I reset my password?"
    print("Q:", q)
    print("A:", answer_question(q))
