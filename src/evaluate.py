import argparse
import json
import math
import dotenv
from pathlib import Path
from statistics import mean
from time import perf_counter

from src.answer import answer
from src.db import get_user_by_id, init_db
from src.index_storage import PROJECT_ROOT
from src.observability import (
    add_evaluation_case,
    finish_evaluation_run,
    start_evaluation_run,
)
from src.query_engine import GENERATION_MODEL


DEFAULT_QUESTIONS = PROJECT_ROOT / "evals" / "evaluation_questions_2.json"
METRIC_NAMES = ("correctness", "answer_relevance", "groundedness", "context_relevance")


def _load_cases(path, limit):
    items = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(items, list) or not items:
        raise ValueError("The dataset must contain a nonempty JSON list")
    seen = set()
    for position, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Dataset entry {position} must be an object")
        case_id = item.get("id")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise ValueError(f"Dataset entry {position} must have a unique nonempty string ID")
        seen.add(case_id)
        if item.get("answerability") not in ("answerable", "partial", "unanswerable"):
            raise ValueError(f"Dataset entry {position} has an invalid answerability")
        if not isinstance(item.get("question"), str) or not item["question"].strip():
            raise ValueError(f"Dataset entry {position} must have a nonempty question")
        reference = item.get("expected_answer")
        if reference is not None and not isinstance(reference, str):
            raise ValueError(f"Dataset entry {position} has an invalid expected_answer")
        facts = item.get("expected_facts")
        if facts is not None and (
            not isinstance(facts, list)
            or any(not isinstance(fact, str) or not fact.strip() for fact in facts)
        ):
            raise ValueError(f"Dataset entry {position} has invalid expected_facts")
    return items[:limit] if limit else items


def _metric(threshold, *, score=None, reason=None, skip_reason=None, error_type=None):
    return {
        "score": score,
        "threshold": threshold,
        "reason": reason,
        "skip_reason": skip_reason,
        "error_type": error_type,
    }


def _score(judge_call, threshold):
    try:
        score, reasons = judge_call()
        if isinstance(score, bool):
            raise ValueError("Invalid judge score")
        score = float(score)
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("Invalid judge score")
        reason = reasons.get("reason") if isinstance(reasons, dict) else None
        if reason is not None and not isinstance(reason, str):
            reason = json.dumps(reason, ensure_ascii=False)
        if reason is None and reasons:
            reason = json.dumps(reasons, ensure_ascii=False)
        if reason == "No non-trivial statements to evaluate":
            return _metric(threshold, skip_reason=reason)
        return _metric(threshold, score=score, reason=reason)
    except Exception as exc:
        return _metric(threshold, error_type=type(exc).__name__)


def _correctness(judge, item, actual_answer, context):
    return judge.generate_score_and_reasons(
        system_prompt=(
            "Evaluate answer correctness from 0 to 10. "
            "Use the required facts as the checklist, interpreted against what the question asks. "
            "Penalize contradictions and missing required information. "
            "Accept equivalent wording and valid alternative examples supported by the retrieved context. "
            "Do not require optional details from the reference answer. "
            "For partially answerable questions, require an answer to the supported parts and explicit abstention on the rest. "
            "For fully unanswerable questions, reward acknowledging missing evidence without inventing details. "
            "Return a score and explain any missing or incorrect facts."
        ),
        user_prompt=json.dumps({
            "question": item["question"],
            "answerability": item["answerability"],
            "required_facts": item.get("expected_facts") or [item.get("expected_answer")],
            "reference_answer": item.get("expected_answer"),
            "actual_answer": actual_answer,
            "retrieved_context": context,
        }, ensure_ascii=False),
        min_score_val=0,
        max_score_val=10,
        temperature=0,
    )


def _context_relevance(judge, question, contexts):
    results = [judge.context_relevance_with_cot_reasons(question, context) for context in contexts]
    scores = [float(result[0]) for result in results]
    if any(not math.isfinite(score) or not 0 <= score <= 1 for score in scores):
        raise ValueError("Invalid context relevance score")
    return mean(scores), {"contexts": [
        {"index": index, "score": score, "reasons": result[1]}
        for index, (score, result) in enumerate(zip(scores, results), 1)
    ]}


def _evaluate_metrics(judge, item, actual_answer, contexts, threshold):
    context = "\n\n".join(contexts)
    metrics = {}
    if (item.get("expected_answer") or "").strip() or item.get("expected_facts"):
        metrics["correctness"] = _score(
            lambda: _correctness(judge, item, actual_answer, context), threshold
        )
    else:
        metrics["correctness"] = _metric(threshold, skip_reason="No reference answer or required facts")
    metrics["answer_relevance"] = _score(
        lambda: judge.relevance_with_cot_reasons(item["question"], actual_answer), threshold
    )
    if contexts:
        metrics["groundedness"] = _score(
            lambda: judge.groundedness_measure_with_cot_reasons(context, actual_answer), threshold
        )
    else:
        metrics["groundedness"] = _metric(threshold, skip_reason="No retrieved context")
    if item["answerability"] != "answerable":
        metrics["context_relevance"] = _metric(threshold, skip_reason="Only applicable to fully answerable questions")
    elif not contexts:
        metrics["context_relevance"] = _metric(threshold, skip_reason="No retrieved context")
    else:
        metrics["context_relevance"] = _score(
            lambda: _context_relevance(judge, item["question"], contexts), threshold
        )
    return metrics


def _unavailable_metrics(item, threshold, error_type):
    metrics = {name: _metric(threshold, error_type=error_type) for name in METRIC_NAMES}
    if not (item.get("expected_answer") or "").strip() and not item.get("expected_facts"):
        metrics["correctness"] = _metric(threshold, skip_reason="No reference answer or required facts")
    if item["answerability"] != "answerable":
        metrics["context_relevance"] = _metric(threshold, skip_reason="Only applicable to fully answerable questions")
    return metrics


def _create_judge(model):
    import litellm
    from trulens.core.feedback.endpoint import Endpoint
    from trulens.providers.litellm import LiteLLM

    class SingleAttemptEndpoint(Endpoint):
        def run_in_pace(self, func, *args, **kwargs):
            self.pace_me()
            return func(*args, **kwargs)

    litellm.num_retries = 0
    pricing = litellm.model_cost.get("gpt-4.1-mini")
    if pricing:
        litellm.register_model({
            name: {**pricing, "litellm_provider": "openrouter"}
            for name in ("openrouter/gpt-4.1-mini", "openrouter/openai/gpt-4.1-mini")
        })
    judge = LiteLLM(model_engine=model)
    judge.endpoint = SingleAttemptEndpoint(name="rag-evaluation", retries=0)
    return judge


def main():
    parser = argparse.ArgumentParser(description="Evaluate RAG answers and persist each metric for the admin panel")
    parser.add_argument("--owner-id", required=True, help="User ID whose uploaded documents should be evaluated")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--judge-model", default="openrouter/openai/gpt-4.1-mini")
    parser.add_argument("--threshold", type=float, default=0.7)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if not 1 <= args.top_k <= 20:
        parser.error("--top-k must be between 1 and 20")
    if not math.isfinite(args.threshold) or not 0 <= args.threshold <= 1:
        parser.error("--threshold must be between 0 and 1")
    if not args.judge_model.strip():
        parser.error("--judge-model must be nonempty")
    try:
        items = _load_cases(args.questions, args.limit)
    except OSError as exc:
        parser.error(f"Cannot load dataset ({type(exc).__name__})")
    except json.JSONDecodeError:
        parser.error("The dataset is not valid JSON")
    except ValueError as exc:
        parser.error(str(exc))
    init_db()
    if get_user_by_id(args.owner_id) is None:
        parser.error("--owner-id must identify an existing user")
    dotenv.load_dotenv()
    started = perf_counter()
    run_id = start_evaluation_run(
        args.owner_id, args.questions.name, GENERATION_MODEL,
        args.judge_model, args.top_k, len(items),
    )
    print(f"Evaluation run {run_id}: {len(items)} cases", flush=True)
    try:
        judge = _create_judge(args.judge_model)
        for item in items:
            case_started = perf_counter()
            try:
                response = answer(item["question"], owner_id=args.owner_id, top_k=args.top_k)
                actual_answer = str(response)
                contexts = [node.node.get_content() for node in response.source_nodes]
            except Exception as exc:
                add_evaluation_case(
                    run_id, item["id"], item["answerability"], item["question"], None,
                    item.get("expected_answer"), None, (perf_counter() - case_started) * 1000,
                    _unavailable_metrics(item, args.threshold, type(exc).__name__),
                )
                raise
            duration_ms = (perf_counter() - case_started) * 1000
            metrics = _evaluate_metrics(judge, item, actual_answer, contexts, args.threshold)
            add_evaluation_case(
                run_id, item["id"], item["answerability"], item["question"], actual_answer,
                item.get("expected_answer"), len(response.source_nodes), duration_ms, metrics,
            )
            errors = sum(metric["error_type"] is not None for metric in metrics.values())
            print(f"Evaluated {item['id']} [{item['answerability']}] ({errors} metric errors)", flush=True)
        finish_evaluation_run(run_id, status="completed", duration_ms=(perf_counter() - started) * 1000)
    except BaseException as exc:
        finish_evaluation_run(
            run_id, status="failed", duration_ms=(perf_counter() - started) * 1000,
            error_type=type(exc).__name__,
        )
        raise SystemExit(f"Evaluation run {run_id} failed ({type(exc).__name__})") from None


if __name__ == "__main__":
    main()
