import argparse
import json
import math
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from src import db
from src.observability import METRIC_NAMES, add_evaluation_case

PREFIX = "ai.observability."
LABELS = {"Correctness": "correctness", "Answer Relevance": "answer_relevance",
          "Groundedness": "groundedness", "Context Relevance": "context_relevance"}


def _timestamp(value):
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _archive_id(owner_id, dataset, cases):
    content = [{"record_id": case["root"][PREFIX + "record_id"], "case_id": case["item"]["id"],
                "question": case["item"]["question"], "answerability": case["item"]["answerability"],
                "expected_answer": case["item"].get("expected_answer"),
                "actual_answer": case["root"].get(PREFIX + "record_root.output"),
                "metrics": {name: {field: case["metrics"].get(name, {}).get(field)
                                   for field in ("score", "threshold", "reason", "skip_reason", "error_type")}
                            for name in METRIC_NAMES}, "source_count": case["source_count"],
                "duration_ms": case["duration_ms"]} for case in cases]
    return str(uuid5(NAMESPACE_URL, json.dumps([owner_id, dataset, sorted(content, key=lambda c: c["case_id"])],
                                              sort_keys=True, ensure_ascii=False, allow_nan=False)))


def main():
    parser = argparse.ArgumentParser(description="Import existing TruLens OTel scores without model calls")
    parser.add_argument("--database", type=Path, default=Path("default.sqlite"))
    parser.add_argument("--questions", type=Path, default=Path("evals/evaluation_questions_2.json"))
    parser.add_argument("--owner-id", required=True, help="Local account responsible for this imported archive")
    parser.add_argument("--threshold", type=float, default=0.7)
    args = parser.parse_args()
    if not math.isfinite(args.threshold) or not 0 <= args.threshold <= 1:
        parser.error("Threshold must be between 0 and 1")
    db.init_db()
    if db.get_user_by_id(args.owner_id) is None:
        parser.error("Owner account does not exist")
    if args.database.resolve() == db.DB_PATH.resolve():
        parser.error("Source must be the separate TruLens database")
    try:
        entries = json.loads(args.questions.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise ValueError("Dataset must be a JSON list")
        questions = {item["question"]: item for item in entries}
        if len(questions) != len(entries) or len({item["id"] for item in entries}) != len(entries):
            raise ValueError("Dataset questions and IDs must be unique")
        if any(item["answerability"] not in ("answerable", "partial", "unanswerable") for item in entries):
            raise ValueError("Invalid answerability")
        with closing(sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            events = [(start, end, json.loads(attributes)) for start, end, attributes in source.execute(
                "SELECT start_timestamp, timestamp, record_attributes FROM trulens_events ORDER BY start_timestamp"
            )]
        roots = {}
        for start, end, attributes in events:
            question = attributes.get(PREFIX + "record_root.input")
            if attributes.get(PREFIX + "span_type") == "record_root" and question in questions:
                roots[question] = (start, end, attributes)
        if not roots:
            raise ValueError("No matching benchmark records found")
        cases = []
        for question, (start, end, root) in roots.items():
            record_id = root[PREFIX + "record_id"]
            related = [attributes for _, _, attributes in events
                       if attributes.get(PREFIX + "record_id") == record_id
                       or attributes.get(PREFIX + "eval.target_record_id") == record_id]
            metrics = {name: {"score": None, "threshold": args.threshold,
                              "skip_reason": "Not recorded in TruLens archive"} for name in METRIC_NAMES}
            for attributes in related:
                if attributes.get(PREFIX + "span_type") != "eval_root":
                    continue
                label = attributes.get(PREFIX + "eval_root.metric_name")
                name = LABELS.get(label)
                value = attributes.get(PREFIX + "eval_root.score")
                if name and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1:
                    reasons = [a.get(PREFIX + "eval.metadata.reason") or a.get(PREFIX + "eval.metadata.reasons")
                               or a.get(PREFIX + "eval.explanation") for a in related
                               if a.get(PREFIX + "eval.metric_name") == label]
                    reasons = [value for value in reasons if value]
                    reasons = list(dict.fromkeys(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                                                  for value in reasons))
                    metrics[name] = {"score": value, "threshold": args.threshold,
                                     "reason": "\n\n".join(reasons) if reasons else None}
            item = questions[question]
            if item["answerability"] != "answerable":
                metrics["context_relevance"] = {"score": None, "threshold": args.threshold,
                                                  "skip_reason": "Only applicable to fully answerable questions"}
            ranks = [a[PREFIX + "reranking.output_ranks"] for a in related
                     if isinstance(a.get(PREFIX + "reranking.output_ranks"), list)]
            source_count = len(ranks[-1]) if ranks else None
            cases.append({"item": item, "root": root, "metrics": metrics, "source_count": source_count,
                          "duration_ms": max(0, (_timestamp(end) - _timestamp(start)).total_seconds() * 1000)})
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        parser.error(f"Cannot import archive ({type(exc).__name__})")
    run_id = _archive_id(args.owner_id, args.questions.name, cases)
    with closing(db._connect()) as connection, connection:
        existing = connection.execute("SELECT id FROM evaluation_runs WHERE id = ?", (run_id,)).fetchone()
        if existing:
            print(f"Archive already imported: {run_id}")
            return
        connection.execute("""
            INSERT INTO evaluation_runs
            (id, created_at, owner_id, dataset, model, judge_model, top_k, status, case_count)
            VALUES (?, ?, ?, ?, ?, ?, 0, 'running', ?)
        """, (run_id, max(_timestamp(start) for start, _, _ in roots.values()).isoformat(timespec="milliseconds"),
              args.owner_id, f"TruLens archive · {args.questions.name}", "Not recorded (archive)",
              "Not recorded (archive)", len(cases)))
    try:
        for case in cases:
            item = case["item"]
            add_evaluation_case(run_id, item["id"], item["answerability"], item["question"],
                                case["root"].get(PREFIX + "record_root.output"), item.get("expected_answer"),
                                case["source_count"], case["duration_ms"], case["metrics"])
        with closing(db._connect()) as connection, connection:
            connection.execute("UPDATE evaluation_runs SET status='completed' WHERE id=?", (run_id,))
    except BaseException:
        with closing(db._connect()) as connection, connection:
            connection.execute("DELETE FROM evaluation_cases WHERE run_id=?", (run_id,))
            connection.execute("DELETE FROM evaluation_runs WHERE id=?", (run_id,))
        raise
    print(f"Imported {len(cases)} benchmark cases: {run_id}. No model calls made.")


if __name__ == "__main__":
    main()
