import json
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from redis import Redis
from redis.exceptions import RedisError
from rq import Queue, Worker

from src import db
from src.auth import require_admin
from src.index_storage import INDEX_DIR
from src.job_queue import INGESTION_QUEUE_NAME, REDIS_URL
from src.observability import METRIC_NAMES, STARTED_AT, utc_now
from src.query_engine import GENERATION_MODEL

def _no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/admin", tags=["admin"],
                   dependencies=[Depends(require_admin), Depends(_no_store)])
Hours = Annotated[int, Query(ge=1, le=2160)]
Page = Annotated[int, Query(ge=1, le=100000)]
PageSize = Annotated[int, Query(ge=1, le=100)]
Search = Annotated[str, Query(max_length=200)]
Status = Literal["all", "success", "error"]
Metric = Literal["all", "correctness", "answer_relevance", "groundedness", "context_relevance"]
health_redis = Redis.from_url(REDIS_URL, socket_connect_timeout=0.4, socket_timeout=0.4,
                              retry_on_timeout=False)


def _cutoff(hours):
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="milliseconds")


def _row(connection, sql, parameters=()):
    return dict(connection.execute(sql, parameters).fetchone())


def _percentiles(connection, table, where, parameters, column="duration_ms"):
    rows = connection.execute(f"""
        WITH ranked AS (
            SELECT {column} AS value, ROW_NUMBER() OVER (ORDER BY {column}) AS rank,
                   COUNT(*) OVER () AS size
            FROM {table} WHERE {where} AND {column} IS NOT NULL
        )
        SELECT
            MAX(CASE WHEN rank = CAST(size * 0.50 + 0.999999 AS INTEGER) THEN value END) AS p50_ms,
            MAX(CASE WHEN rank = CAST(size * 0.95 + 0.999999 AS INTEGER) THEN value END) AS p95_ms,
            MAX(CASE WHEN rank = CAST(size * 0.99 + 0.999999 AS INTEGER) THEN value END) AS p99_ms
        FROM ranked
    """, parameters).fetchone()
    return dict(rows)


def _log_filter(table, hours, status, search):
    clauses, parameters = ["created_at >= ?"], [_cutoff(hours)]
    if status != "all":
        if table == "request_logs":
            clauses.append("status_code >= 400" if status == "error" else "status_code < 400")
        else:
            clauses.append("outcome = ?")
            parameters.append(status)
    if search.strip():
        fields = {
            "request_logs": ("id", "user_id", "method", "route", "error_type"),
            "query_logs": ("id", "user_id", "request_id", "model", "error_type"),
            "ingestion_logs": ("id", "user_id", "document_id", "job_id", "error_type"),
        }[table]
        clauses.append("(" + " OR ".join(f"instr(lower(COALESCE({field}, '')), ?) > 0" for field in fields) + ")")
        parameters.extend([search.strip().casefold()] * len(fields))
    return " AND ".join(clauses), parameters


def _page(connection, table, where, parameters, page, page_size, order="created_at DESC, id DESC"):
    total = connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", parameters).fetchone()[0]
    items = [dict(row) for row in connection.execute(
        f"SELECT * FROM {table} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
        [*parameters, page_size, (page - 1) * page_size],
    )]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/requests/export")
def export_requests(hours: Hours = 24, status: Status = "all", search: Search = ""):
    where, parameters = _log_filter("request_logs", hours, status, search)
    with closing(db._connect()) as connection:
        result = _page(connection, "request_logs", where, parameters, 1, 10000)
    result["exported"] = len(result["items"])
    result["truncated"] = result["total"] > len(result["items"])
    return JSONResponse(result, headers={"Content-Disposition": 'attachment; filename="folio-requests.json"',
                                         "Cache-Control": "no-store"})


@router.get("/requests")
def requests(hours: Hours = 24, status: Status = "all", search: Search = "",
             page: Page = 1, page_size: PageSize = 25):
    where, parameters = _log_filter("request_logs", hours, status, search)
    with closing(db._connect()) as connection:
        return _page(connection, "request_logs", where, parameters, page, page_size)


@router.get("/queries")
def queries(hours: Hours = 24, status: Status = "all", search: Search = "",
            page: Page = 1, page_size: PageSize = 25):
    where, parameters = _log_filter("query_logs", hours, status, search)
    with closing(db._connect()) as connection:
        return _page(connection, "query_logs", where, parameters, page, page_size)


@router.get("/ingestions")
def ingestions(hours: Hours = 24, status: Status = "all", search: Search = "",
               page: Page = 1, page_size: PageSize = 25):
    where, parameters = _log_filter("ingestion_logs", hours, status, search)
    with closing(db._connect()) as connection:
        return _page(connection, "ingestion_logs", where, parameters, page, page_size)


@router.get("/evaluations/runs")
def evaluation_runs(hours: Hours = 24):
    with closing(db._connect()) as connection:
        items = [dict(row) for row in connection.execute(
            "SELECT * FROM evaluation_runs WHERE created_at >= ? ORDER BY created_at DESC, id DESC",
            (_cutoff(hours),),
        )]
    for item in items:
        if item["top_k"] == 0:
            item["top_k"] = None
    return {"items": items, "total": len(items)}


@router.get("/evaluations")
def evaluations(hours: Hours = 24, run_id: Annotated[str | None, Query(max_length=100)] = None,
                answerability: Literal["all", "answerable", "partial", "unanswerable"] = "all",
                metric: Metric = "all", below_threshold: bool = False, search: Search = "",
                page: Page = 1, page_size: PageSize = 25):
    clauses, parameters = ["run_id IN (SELECT id FROM evaluation_runs WHERE created_at >= ?)"], [_cutoff(hours)]
    if run_id:
        clauses.append("run_id = ?")
        parameters.append(run_id)
    if answerability != "all":
        clauses.append("answerability = ?")
        parameters.append(answerability)
    if below_threshold:
        names = METRIC_NAMES if metric == "all" else (metric,)
        clauses.append("(" + " OR ".join(
            f"(json_type(metrics, '$.{name}.score') IN ('real', 'integer') "
            f"AND json_extract(metrics, '$.{name}.score') < json_extract(metrics, '$.{name}.threshold'))"
            for name in names
        ) + ")")
    if search.strip():
        clauses.append("(instr(lower(case_id), ?) > 0 OR instr(lower(question), ?) > 0)")
        parameters.extend([search.strip().casefold()] * 2)
    with closing(db._connect()) as connection:
        result = _page(connection, "evaluation_cases", " AND ".join(clauses), parameters,
                       page, page_size, order="rowid DESC")
    for item in result["items"]:
        item["metrics"] = json.loads(item["metrics"])
    return result


def _evaluation_summary(connection, cutoff):
    result = _row(connection, """
        SELECT COUNT(*) AS runs, COALESCE(SUM(completed_count), 0) AS cases
        FROM evaluation_runs WHERE created_at >= ?
    """, (cutoff,))
    latest = connection.execute(
        "SELECT id FROM evaluation_runs WHERE created_at >= ? ORDER BY created_at DESC, id DESC LIMIT 1",
        (cutoff,),
    ).fetchone()
    result.update(run_id=latest["id"] if latest else None, metrics=[])
    for name in METRIC_NAMES:
        values = _row(connection, f"""
            SELECT AVG(json_extract(metrics, '$.{name}.score')) AS mean,
                   MAX(json_extract(metrics, '$.{name}.threshold')) AS threshold,
                   COUNT(json_extract(metrics, '$.{name}.score')) AS samples,
                   COALESCE(SUM(json_extract(metrics, '$.{name}.score') <
                                json_extract(metrics, '$.{name}.threshold')), 0) AS below_threshold,
                   COALESCE(SUM(json_extract(metrics, '$.{name}.skip_reason') IS NOT NULL), 0) AS skipped
            FROM evaluation_cases WHERE run_id = ?
        """, (result["run_id"],))
        values["name"] = name
        if values["threshold"] is None:
            values["threshold"] = 0.7
        result["metrics"].append(values)
    return result


def _series(connection, hours, cutoff):
    seconds = 3600 if hours <= 48 else 21600 if hours <= 168 else 86400 if hours <= 720 else 259200
    def buckets(table, fields):
        return {int(row["bucket"]): dict(row) for row in connection.execute(f"""
            SELECT CAST(unixepoch(created_at) / ? AS INTEGER) * ? AS bucket, {fields}
            FROM {table} WHERE created_at >= ? GROUP BY bucket
        """, (seconds, seconds, cutoff))}
    requests = buckets("request_logs", "COUNT(*) AS requests, SUM(status_code >= 400) AS errors, AVG(duration_ms) AS avg_ms")
    queries = buckets("query_logs", "COUNT(*) AS queries")
    start = int(datetime.fromisoformat(cutoff).timestamp()) // seconds * seconds
    end = int(datetime.now(UTC).timestamp()) // seconds * seconds
    return [{"timestamp": datetime.fromtimestamp(stamp, UTC).isoformat(),
             "requests": requests.get(stamp, {}).get("requests", 0),
             "errors": requests.get(stamp, {}).get("errors", 0),
             "avg_ms": requests.get(stamp, {}).get("avg_ms"),
             "queries": queries.get(stamp, {}).get("queries", 0)}
            for stamp in range(start, end + 1, seconds)]


def _health():
    result = {"database": "ok", "redis": "unavailable", "worker_count": None,
              "queued_jobs": None, "uptime_seconds": round(time.monotonic() - STARTED_AT),
              "index_ready": all((INDEX_DIR / name).is_file() for name in (
                  "docstore.json", "index_store.json", "default__vector_store.json"))}
    try:
        health_redis.ping()
        queue = Queue(INGESTION_QUEUE_NAME, connection=health_redis)
        result.update(redis="ok", worker_count=Worker.count(queue=queue), queued_jobs=queue.count)
    except RedisError:
        pass
    return result


@router.get("/overview")
def overview(response: Response, hours: Hours = 24):
    response.headers["Cache-Control"] = "no-store"
    cutoff = _cutoff(hours)
    where, parameters = "created_at >= ?", (cutoff,)
    with closing(db._connect()) as connection:
        requests = _row(connection, """
            SELECT COUNT(*) AS total, COALESCE(SUM(status_code >= 400), 0) AS errors,
                   AVG(duration_ms) AS avg_ms FROM request_logs WHERE created_at >= ?
        """, parameters)
        requests["error_rate"] = requests["errors"] / requests["total"] if requests["total"] else 0
        requests.update(_percentiles(connection, "request_logs", where, parameters))
        queries = _row(connection, """
            SELECT COUNT(*) AS total, COALESCE(SUM(outcome = 'error'), 0) AS errors,
                   AVG(retrieval_ms) AS avg_retrieval_ms, AVG(rerank_ms) AS avg_rerank_ms,
                   AVG(generation_ms) AS avg_generation_ms, SUM(input_tokens) AS input_tokens,
                   SUM(output_tokens) AS output_tokens, SUM(total_tokens) AS total_tokens,
                   COUNT(total_tokens) AS token_samples, SUM(reported_cost_usd) AS reported_cost_usd,
                   COUNT(reported_cost_usd) AS cost_samples,
                   COALESCE(SUM(source_count = 0), 0) AS no_sources, AVG(source_count) AS avg_sources
            FROM query_logs WHERE created_at >= ?
        """, parameters)
        queries.update(_percentiles(connection, "query_logs", where, parameters))
        documents = _row(connection, """
            SELECT COUNT(*) AS total, COALESCE(SUM(status = 'queued'), 0) AS queued,
                   COALESCE(SUM(status = 'processing'), 0) AS processing,
                   COALESCE(SUM(status = 'ready'), 0) AS ready,
                   COALESCE(SUM(status = 'failed'), 0) AS failed FROM documents
        """)
        ingestion = _row(connection, """
            SELECT COUNT(*) AS total, COALESCE(SUM(outcome = 'error'), 0) AS failed,
                   AVG(duration_ms) AS avg_ms, AVG(queue_ms) AS avg_queue_ms
            FROM ingestion_logs WHERE created_at >= ?
        """, parameters)
        ingestion["p95_ms"] = _percentiles(connection, "ingestion_logs", where, parameters)["p95_ms"]
        users = {"total": connection.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                 "active": connection.execute(
                     "SELECT COUNT(DISTINCT user_id) FROM request_logs WHERE created_at >= ?", parameters).fetchone()[0]}
        evaluation = _evaluation_summary(connection, cutoff)
        series = _series(connection, hours, cutoff)
        endpoints = [dict(row) for row in connection.execute("""
            WITH ranked AS (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY route, method ORDER BY duration_ms) AS rank,
                       COUNT(*) OVER (PARTITION BY route, method) AS size
                FROM request_logs WHERE created_at >= ?
            )
            SELECT route, method, COUNT(*) AS count, SUM(status_code >= 400) AS errors,
                   AVG(duration_ms) AS avg_ms,
                   MAX(CASE WHEN rank = CAST(size * 0.95 + 0.999999 AS INTEGER) THEN duration_ms END) AS p95_ms
            FROM ranked GROUP BY route, method ORDER BY count DESC, route, method LIMIT 20
        """, parameters)]
    return {"window_hours": hours, "generated_at": utc_now(), "requests": requests,
            "queries": queries, "documents": documents, "ingestion": ingestion, "users": users,
            "evaluation": evaluation, "series": series, "endpoints": endpoints, "health": _health(),
            "models": {"generation": GENERATION_MODEL, "embedding": "BAAI/bge-small-en-v1.5",
                       "reranker": "cross-encoder/ms-marco-MiniLM-L6-v2"}}
