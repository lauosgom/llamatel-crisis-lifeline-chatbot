"""
review_logs.py
CLI tool for manually reviewing answers the bot has given, and rating
each one good/bad. Complements reaction-based feedback from the group
(see whatsapp_bot.py's handle_reaction) - this is the "just me reviewing"
side of it.

Only shows questions the bot actually answered (responded=True) that
don't already have an admin rating - so re-running this only surfaces
new answers since your last review pass, not ones you've already rated.

Usage:
    python review_logs.py                # review up to 20 unrated answers
    python review_logs.py --limit 50
"""

import argparse

from dotenv import load_dotenv

import whatsapp_chatbot.rag as rag

load_dotenv()


def fetch_unreviewed(limit: int):
    sql = f"""
        SELECT q.id, q.timestamp, q.question, q.answer, q.distance
        FROM `{rag.QUERY_LOG_TABLE_FQN}` q
        WHERE q.responded = TRUE
          AND NOT EXISTS (
            SELECT 1 FROM `{rag.FEEDBACK_TABLE_FQN}` f
            WHERE f.query_log_id = q.id AND f.source = 'admin'
          )
        ORDER BY q.timestamp ASC
        LIMIT @limit
    """
    from google.cloud import bigquery

    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("limit", "INT64", limit)]
    )
    return list(rag.bq_client.query(sql, job_config=job_config).result())


def prompt_rating(row) -> None:
    print("\n" + "-" * 60)
    print(f"[{row.timestamp}]  distance={row.distance:.3f}" if row.distance is not None else f"[{row.timestamp}]")
    print(f"Q: {row.question}")
    print(f"A: {row.answer}")

    while True:
        choice = input("\n[g]ood / [b]ad / [s]kip / [q]uit > ").strip().lower()
        if choice in ("g", "good"):
            rag.record_feedback(row.id, source="admin", rating="good")
            print("Marked good.")
            return
        if choice in ("b", "bad"):
            note = input("Optional note on what was wrong (Enter to skip): ").strip() or None
            rag.record_feedback(row.id, source="admin", rating="bad", note=note)
            print("Marked bad.")
            return
        if choice in ("s", "skip"):
            print("Skipped - will show again next run.")
            return
        if choice in ("q", "quit"):
            raise KeyboardInterrupt
        print("Please enter g, b, s, or q.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20, help="Max answers to review this run")
    args = parser.parse_args()

    rag.ensure_feedback_table_exists()
    rows = fetch_unreviewed(args.limit)

    if not rows:
        print("Nothing to review - no unrated answers found.")
        return

    print(f"{len(rows)} answer(s) to review. Ctrl+C or 'q' to stop at any point.\n")

    try:
        for row in rows:
            prompt_rating(row)
    except KeyboardInterrupt:
        print("\nStopped early - progress so far is saved.")


if __name__ == "__main__":
    main()
