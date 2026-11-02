from time import perf_counter

from src.observability import finish_query, start_query
from src.query_engine import GENERATION_MODEL, get_query_engine
from src.query_metrics import QueryMetrics


def answer(query: str, owner_id: str, top_k: int = 5):
    started = perf_counter()
    query_id = start_query(owner_id, top_k, GENERATION_MODEL, len(query))
    metrics = QueryMetrics()
    setup_started = perf_counter()
    setup_ms = None
    try:
        engine = get_query_engine(owner_id=owner_id, top_k=top_k, metrics=metrics)
        setup_ms = (perf_counter() - setup_started) * 1000
        response = engine.query(query)
        metrics.source_count = len(response.source_nodes)
        finish_query(
            query_id, outcome="success", duration_ms=(perf_counter() - started) * 1000,
            setup_ms=setup_ms, answer_chars=len(str(response)), **metrics.summary(),
        )
        return response
    except Exception as exc:
        finish_query(
            query_id, outcome="error", duration_ms=(perf_counter() - started) * 1000,
            setup_ms=setup_ms if setup_ms is not None else (perf_counter() - setup_started) * 1000,
            error_type=type(exc).__name__, **metrics.summary(),
        )
        raise
