import json
import logging
import math
import os
import sqlite3
import time
from contextlib import closing
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from functools import partial
from uuid import uuid4

import anyio

from src import db

logger = logging.getLogger(__name__)
request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
STARTED_AT = time.monotonic()
METRIC_NAMES = ("correctness", "answer_relevance", "groundedness", "context_relevance")


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def current_request_id() -> str | None:
    return request_id_context.get()


def retention_days() -> int:
    return max(1, min(int(os.getenv("LOG_RETENTION_DAYS", "30")), 365))


def init_observability(connection: sqlite3.Connection) -> None:
    statements = [
        """CREATE TABLE IF NOT EXISTS request_logs (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, user_id TEXT,
            method TEXT NOT NULL, route TEXT NOT NULL, status_code INTEGER NOT NULL,
            duration_ms REAL NOT NULL, error_type TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS query_logs (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, user_id TEXT NOT NULL,
            request_id TEXT, outcome TEXT NOT NULL, top_k INTEGER NOT NULL,
            model TEXT NOT NULL, question_chars INTEGER NOT NULL,
            duration_ms REAL, setup_ms REAL, retrieval_ms REAL, rerank_ms REAL,
            generation_ms REAL, input_tokens INTEGER, output_tokens INTEGER,
            total_tokens INTEGER, reported_cost_usd REAL, llm_calls INTEGER,
            source_count INTEGER, avg_rerank_score REAL, answer_chars INTEGER,
            error_type TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS ingestion_logs (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, user_id TEXT NOT NULL,
            document_id TEXT NOT NULL, job_id TEXT, outcome TEXT NOT NULL,
            duration_ms REAL, queue_ms REAL, chunk_count INTEGER, file_bytes INTEGER,
            error_type TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS evaluation_runs (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
            owner_id TEXT NOT NULL REFERENCES users(id), dataset TEXT NOT NULL,
            model TEXT NOT NULL, judge_model TEXT NOT NULL, top_k INTEGER NOT NULL,
            status TEXT NOT NULL, case_count INTEGER NOT NULL,
            completed_count INTEGER NOT NULL DEFAULT 0, duration_ms REAL, error_type TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS evaluation_cases (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES evaluation_runs(id),
            case_id TEXT NOT NULL, answerability TEXT NOT NULL, question TEXT NOT NULL,
            actual_answer TEXT, expected_answer TEXT, source_count INTEGER,
            duration_ms REAL NOT NULL, metrics TEXT NOT NULL,
            UNIQUE (run_id, case_id)
        )""",
        "CREATE INDEX IF NOT EXISTS request_logs_time ON request_logs(created_at)",
        "CREATE INDEX IF NOT EXISTS request_logs_status_time ON request_logs(status_code, created_at)",
        "CREATE INDEX IF NOT EXISTS query_logs_time ON query_logs(created_at)",
        "CREATE INDEX IF NOT EXISTS query_logs_request ON query_logs(request_id)",
        "CREATE INDEX IF NOT EXISTS ingestion_logs_time ON ingestion_logs(created_at)",
        "CREATE INDEX IF NOT EXISTS evaluation_runs_time ON evaluation_runs(created_at)",
        "CREATE INDEX IF NOT EXISTS evaluation_cases_run ON evaluation_cases(run_id)",
    ]
    for statement in statements:
        connection.execute(statement)


def _runtime_write(sql: str, parameters: tuple) -> bool:
    try:
        with closing(db._connect(timeout=1)) as connection, connection:
            connection.execute(sql, parameters)
        return True
    except sqlite3.Error as exc:
        logger.warning("Telemetry write skipped (%s)", type(exc).__name__)
        return False


def start_query(owner_id: str, top_k: int, model: str, question_chars: int) -> str | None:
    event_id = str(uuid4())
    stored = _runtime_write(
        """INSERT INTO query_logs
        (id, created_at, user_id, request_id, outcome, top_k, model, question_chars)
        VALUES (?, ?, ?, ?, 'processing', ?, ?, ?)""",
        (event_id, utc_now(), owner_id, current_request_id(), top_k, model, question_chars),
    )
    return event_id if stored else None


def finish_query(query_id: str | None, *, outcome: str, duration_ms: float,
                 setup_ms: float | None = None, retrieval_ms: float | None = None,
                 rerank_ms: float | None = None, generation_ms: float | None = None,
                 input_tokens: int | None = None, output_tokens: int | None = None,
                 total_tokens: int | None = None, reported_cost_usd: float | None = None,
                 llm_calls: int | None = None, source_count: int | None = None,
                 avg_rerank_score: float | None = None, answer_chars: int | None = None,
                 error_type: str | None = None) -> None:
    if query_id is None:
        return
    _runtime_write(
        """UPDATE query_logs SET outcome=?, duration_ms=?, setup_ms=?, retrieval_ms=?,
        rerank_ms=?, generation_ms=?, input_tokens=?, output_tokens=?, total_tokens=?,
        reported_cost_usd=?, llm_calls=?, source_count=?, avg_rerank_score=?, answer_chars=?,
        error_type=? WHERE id=?""",
        (outcome, duration_ms, setup_ms, retrieval_ms, rerank_ms, generation_ms,
         input_tokens, output_tokens, total_tokens, reported_cost_usd, llm_calls,
         source_count, avg_rerank_score, answer_chars, error_type, query_id),
    )


def start_ingestion(owner_id: str, document_id: str, job_id: str | None,
                    queue_ms: float | None, file_bytes: int | None) -> str | None:
    event_id = str(uuid4())
    stored = _runtime_write(
        """INSERT INTO ingestion_logs
        (id, created_at, user_id, document_id, job_id, outcome, queue_ms, file_bytes)
        VALUES (?, ?, ?, ?, ?, 'processing', ?, ?)""",
        (event_id, utc_now(), owner_id, document_id, job_id, queue_ms, file_bytes),
    )
    return event_id if stored else None


def finish_ingestion(event_id: str | None, *, outcome: str, duration_ms: float,
                     chunk_count: int | None = None, error_type: str | None = None) -> None:
    if event_id is not None:
        _runtime_write(
            """UPDATE ingestion_logs SET outcome=?, duration_ms=?, chunk_count=?,
            error_type=? WHERE id=?""",
            (outcome, duration_ms, chunk_count, error_type, event_id),
        )


def start_evaluation_run(owner_id: str, dataset: str, model: str, judge_model: str,
                          top_k: int, case_count: int) -> str:
    run_id = str(uuid4())
    with closing(db._connect()) as connection, connection:
        connection.execute(
            """INSERT INTO evaluation_runs
            (id, created_at, owner_id, dataset, model, judge_model, top_k, status, case_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)""",
            (run_id, utc_now(), owner_id, dataset, model, judge_model, top_k, case_count),
        )
    return run_id


def add_evaluation_case(run_id: str, case_id: str, answerability: str, question: str,
                        actual_answer: str | None, expected_answer: str | None,
                        source_count: int | None, duration_ms: float, metrics: dict) -> str:
    cleaned = {}
    for name in METRIC_NAMES:
        value = metrics.get(name, {})
        score = value.get("score")
        threshold = value.get("threshold", 0.7)
        if score is not None and (not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1):
            raise ValueError(f"Invalid evaluation score for {name}")
        if not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError(f"Invalid evaluation threshold for {name}")
        cleaned[name] = {
            "score": score, "threshold": threshold, "reason": value.get("reason"),
            "skip_reason": value.get("skip_reason"), "error_type": value.get("error_type"),
        }
    case_row_id = str(uuid4())
    with closing(db._connect()) as connection, connection:
        connection.execute(
            """INSERT INTO evaluation_cases
            (id, run_id, case_id, answerability, question, actual_answer, expected_answer,
             source_count, duration_ms, metrics) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (case_row_id, run_id, case_id, answerability, question, actual_answer,
             expected_answer, source_count, duration_ms, json.dumps(cleaned, allow_nan=False)),
        )
        connection.execute(
            "UPDATE evaluation_runs SET completed_count=completed_count+1 WHERE id=?", (run_id,)
        )
    return case_row_id


def finish_evaluation_run(run_id: str, *, status: str, duration_ms: float,
                           error_type: str | None = None) -> None:
    with closing(db._connect()) as connection, connection:
        connection.execute(
            "UPDATE evaluation_runs SET status=?, duration_ms=?, error_type=? WHERE id=?",
            (status, duration_ms, error_type, run_id),
        )


def prune_runtime_logs() -> None:
    cutoff = (datetime.now(UTC) - timedelta(days=retention_days())).isoformat(timespec="milliseconds")
    try:
        with closing(db._connect(timeout=2)) as connection, connection:
            for table in ("request_logs", "query_logs", "ingestion_logs"):
                connection.execute(f"DELETE FROM {table} WHERE created_at < ?", (cutoff,))
    except sqlite3.Error as exc:
        logger.warning("Telemetry retention skipped (%s)", type(exc).__name__)


def _record_request(*, event_id: str, user_id: str | None, method: str,
                     route: str, status_code: int, duration_ms: float,
                     error_type: str | None) -> None:
    _runtime_write(
        """INSERT INTO request_logs
        (id, created_at, user_id, method, route, status_code, duration_ms, error_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (event_id, utc_now(), user_id, method, route, status_code, duration_ms, error_type),
    )


class RequestMetricsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = time.perf_counter()
        event_id = str(uuid4())
        token = request_id_context.set(event_id)
        scope.setdefault("state", {})["request_id"] = event_id
        status_code, finished, error_type = 500, None, None

        async def capture(message):
            nonlocal status_code, finished
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-request-id", event_id.encode()),
                ]
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                finished = time.perf_counter()

        try:
            await self.app(scope, receive, capture)
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            request_id_context.reset(token)
            matched = scope.get("route")
            route = getattr(matched, "path", "unmatched")
            route = route.removeprefix("/api") or "/"
            is_api = route.startswith(("/auth", "/documents", "/jobs", "/ask", "/admin"))
            if (is_api or status_code >= 400) and not (route.startswith("/admin") and status_code < 400):
                await anyio.to_thread.run_sync(partial(
                    _record_request, event_id=event_id,
                    user_id=scope["state"].get("user_id"), method=scope["method"],
                    route=route, status_code=status_code,
                    duration_ms=round(((finished or time.perf_counter()) - started) * 1000, 3),
                    error_type=error_type or (f"HTTP{status_code}" if status_code >= 400 else None),
                ))
