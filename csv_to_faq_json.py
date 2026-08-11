"""
csv_to_faq_json.py
Converts a CSV with "question" and "answer" columns into faq_data.json,
the schema build_index.py expects:
    [{"question": "...", "answer": "..."}, ...]

Usage:
    python csv_to_faq_json.py faq_data.csv
    python csv_to_faq_json.py faq_data.csv --output faq_data.json
"""

import argparse
import csv
import json


def convert(csv_path: str, output_path: str):
    faqs = []

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        # Normalize header names so "Question"/" question "/"QUESTION" all work.
        reader.fieldnames = [name.strip().lower() for name in reader.fieldnames]

        if "question" not in reader.fieldnames or "answer" not in reader.fieldnames:
            raise ValueError(
                f"Expected 'question' and 'answer' columns, found: {reader.fieldnames}"
            )

        for row in reader:
            question = (row.get("question") or "").strip()
            answer = (row.get("answer") or "").strip()

            if not question or not answer:
                continue  # skip blank/incomplete rows

            faqs.append({"question": question, "answer": answer})

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(faqs, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(faqs)} FAQ entries to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", help="Path to the input CSV file")
    parser.add_argument(
        "--output", default="faq_data.json", help="Output JSON path (default: faq_data.json)"
    )
    args = parser.parse_args()

    convert(args.csv_path, args.output)
