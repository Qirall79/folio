import math
from time import perf_counter

from llama_index.core.callbacks import CBEventType, EventPayload
from llama_index.core.callbacks.base_handler import BaseCallbackHandler


def _field(value, name):
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _number(value, integer=False):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result) or result < 0:
        return None
    return int(result) if integer and result.is_integer() else (None if integer else result)


class QueryMetrics(BaseCallbackHandler):
    def __init__(self):
        super().__init__(event_starts_to_ignore=[], event_ends_to_ignore=[])
        self._started = {}
        self._durations = {}
        self._usage = []
        self._llm_depth = 0
        self._outer_llm_events = set()
        self.llm_calls = 0
        self.source_count = None
        self.avg_rerank_score = None

    def on_event_start(self, event_type, payload=None, event_id="", parent_id="", **kwargs):
        if event_type in (CBEventType.RETRIEVE, CBEventType.RERANKING, CBEventType.SYNTHESIZE):
            self._started[event_id] = (event_type, perf_counter())
        elif event_type == CBEventType.LLM:
            if self._llm_depth == 0:
                self.llm_calls += 1
                self._outer_llm_events.add(event_id)
            self._llm_depth += 1
        return event_id

    def on_event_end(self, event_type, payload=None, event_id="", **kwargs):
        started = self._started.pop(event_id, None)
        if started is not None:
            self._durations[event_type] = self._durations.get(event_type, 0) + (perf_counter() - started[1]) * 1000
        payload = payload or {}
        if event_type == CBEventType.RERANKING and EventPayload.NODES in payload:
            nodes = payload[EventPayload.NODES]
            self.source_count = len(nodes)
            scores = [float(node.score) for node in nodes if node.score is not None and math.isfinite(float(node.score))]
            self.avg_rerank_score = sum(scores) / len(scores) if scores else None
        elif event_type == CBEventType.LLM:
            self._llm_depth = max(0, self._llm_depth - 1)
            if event_id in self._outer_llm_events:
                self._outer_llm_events.remove(event_id)
                response = payload.get(EventPayload.RESPONSE) or payload.get(EventPayload.COMPLETION)
                usage = _field(_field(response, "raw"), "usage")
                self._usage.append({
                    "input_tokens": _number(_field(usage, "prompt_tokens"), integer=True),
                    "output_tokens": _number(_field(usage, "completion_tokens"), integer=True),
                    "total_tokens": _number(_field(usage, "total_tokens"), integer=True),
                    "reported_cost_usd": _number(_field(usage, "cost")),
                })

    def start_trace(self, trace_id=None):
        pass

    def end_trace(self, trace_id=None, trace_map=None):
        pass

    def summary(self):
        durations = dict(self._durations)
        now = perf_counter()
        for event_type, started in self._started.values():
            durations[event_type] = durations.get(event_type, 0) + (now - started) * 1000
        values = {
            "retrieval_ms": durations.get(CBEventType.RETRIEVE),
            "rerank_ms": durations.get(CBEventType.RERANKING),
            "generation_ms": durations.get(CBEventType.SYNTHESIZE),
            "llm_calls": self.llm_calls,
            "source_count": self.source_count,
            "avg_rerank_score": self.avg_rerank_score,
        }
        for name in ("input_tokens", "output_tokens", "total_tokens", "reported_cost_usd"):
            reported = [usage[name] for usage in self._usage]
            values[name] = sum(reported) if reported and len(reported) == self.llm_calls and all(value is not None for value in reported) else None
        return values
