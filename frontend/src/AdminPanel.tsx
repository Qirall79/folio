import { Fragment, useEffect, useId, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Activity, AlertCircle, ArrowDownToLine, ArrowRight, Check, ChevronLeft, ChevronRight, Clock3, Database, FileText, LoaderCircle, RefreshCw, Search, Server, X } from "lucide-react";
import "./admin.css";

type Api = <T>(path: string, options?: RequestInit, authenticated?: boolean) => Promise<T>;
type Nullable = number | null;
type MetricName = "correctness" | "answer_relevance" | "groundedness" | "context_relevance";
type MetricResult = { score: Nullable; threshold: number; reason: string | null; skip_reason: string | null; error_type: string | null };
type Overview = {
  window_hours: number; generated_at: string;
  requests: { total: number; errors: number; error_rate: number; p50_ms: Nullable; p95_ms: Nullable; p99_ms: Nullable; avg_ms: Nullable };
  queries: { total: number; errors: number; p50_ms: Nullable; p95_ms: Nullable; avg_retrieval_ms: Nullable; avg_rerank_ms: Nullable; avg_generation_ms: Nullable; input_tokens: Nullable; output_tokens: Nullable; total_tokens: Nullable; token_samples: number; reported_cost_usd: Nullable; cost_samples: number; no_sources: number; avg_sources: Nullable };
  documents: { total: number; queued: number; processing: number; ready: number; failed: number };
  ingestion: { total: number; failed: number; avg_ms: Nullable; p95_ms: Nullable; avg_queue_ms: Nullable };
  users: { total: number; active: number };
  evaluation: { runs: number; cases: number; run_id: string | null; metrics: { name: MetricName; mean: Nullable; threshold: number; samples: number; below_threshold: number; skipped: number }[] };
  series: { timestamp: string; requests: number; errors: number; avg_ms: Nullable; queries: number }[];
  endpoints: { route: string; method: string; count: number; errors: number; avg_ms: Nullable; p95_ms: Nullable }[];
  health: { database: "ok"; redis: "ok" | "unavailable"; worker_count: Nullable; queued_jobs: Nullable; index_ready: boolean; uptime_seconds: number };
  models: { generation: string; embedding: string; reranker: string };
};
type RequestLog = { id: string; created_at: string; user_id: string | null; method: string; route: string; status_code: number; duration_ms: number; error_type: string | null };
type QueryLog = { id: string; created_at: string; user_id: string; request_id: string | null; outcome: "processing" | "success" | "error"; top_k: number; model: string; duration_ms: Nullable; setup_ms: Nullable; retrieval_ms: Nullable; rerank_ms: Nullable; generation_ms: Nullable; input_tokens: Nullable; output_tokens: Nullable; total_tokens: Nullable; reported_cost_usd: Nullable; llm_calls: number; source_count: Nullable; avg_rerank_score: Nullable; question_chars: number; answer_chars: number; error_type: string | null };
type IngestionLog = { id: string; created_at: string; user_id: string; document_id: string; job_id: string | null; outcome: "processing" | "success" | "error"; duration_ms: Nullable; queue_ms: Nullable; chunk_count: Nullable; file_bytes: Nullable; error_type: string | null };
type EvaluationRun = { id: string; created_at: string; owner_id: string; dataset: string; model: string; judge_model: string; top_k: Nullable; status: "running" | "completed" | "failed"; case_count: number; completed_count: number; duration_ms: Nullable; error_type: string | null };
type EvaluationCase = { id: string; run_id: string; case_id: string; answerability: string; question: string; actual_answer: string | null; expected_answer: string | null; source_count: Nullable; duration_ms: Nullable; metrics: Record<MetricName, MetricResult> };
type LogRow = RequestLog | QueryLog | IngestionLog | EvaluationCase;
type PageData<T> = { items: T[]; total: number; page: number; page_size: number };
type Tab = "overview" | "requests" | "queries" | "ingestions" | "evaluations";
const tabs: { key: Tab; label: string }[] = [{ key: "overview", label: "Overview" }, { key: "requests", label: "Requests" }, { key: "queries", label: "Queries" }, { key: "ingestions", label: "Ingestion" }, { key: "evaluations", label: "Evaluations" }];
const metricLabels: Record<MetricName, string> = { correctness: "Correctness", answer_relevance: "Answer relevance", groundedness: "Groundedness", context_relevance: "Context relevance" };
const metricNames = Object.keys(metricLabels) as MetricName[];
const number = (value: Nullable | undefined, digits = 0) => value == null || !Number.isFinite(value) ? "—" : value.toLocaleString(undefined, { maximumFractionDigits: digits });
const duration = (value: Nullable | undefined) => value == null || !Number.isFinite(value) ? "—" : value < 1000 ? `${number(value)} ms` : value < 60_000 ? `${number(value / 1000, 2)} s` : `${number(value / 60_000, 1)} min`;
const score = (value: Nullable | undefined) => value == null || !Number.isFinite(value) ? "—" : value.toFixed(3);
const money = (value: Nullable | undefined) => value == null || !Number.isFinite(value) ? "—" : new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 6 }).format(value);
const timestamp = (value: string) => new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
const message = (error: unknown) => error instanceof Error ? error.message : "Could not load this data. Please try again.";

function useRemote<T>(api: Api, path: string | null, revision: number) {
  const [state, setState] = useState<{ path: string | null; data: T | null; loading: boolean; error: string }>({ path, data: null, loading: !!path, error: "" });
  const previousPath = useRef(path);
  useEffect(() => {
    let active = true;
    const changed = previousPath.current !== path;
    previousPath.current = path;
    if (!path) {
      setState({ path, data: null, loading: false, error: "" });
      return;
    }
    setState((current) => ({ path, data: changed ? null : current.data, loading: true, error: "" }));
    void api<T>(path).then((data) => {
      if (active) setState({ path, data, loading: false, error: "" });
    }).catch((error: unknown) => {
      if (active && !(error instanceof DOMException && error.name === "AbortError")) setState((current) => ({ ...current, loading: false, error: message(error) }));
    });
    return () => { active = false; };
  }, [api, path, revision]);
  return state.path === path ? state : { path, data: null, loading: !!path, error: "" };
}
function Loading({ children = "Loading activity…" }: { children?: ReactNode }) {
  return <div className="admin-empty" role="status"><LoaderCircle size={23} className="spin" /><p>{children}</p></div>;
}
function ErrorNotice({ children, retry }: { children: ReactNode; retry: () => void }) {
  return <div className="notice admin-error" role="alert"><AlertCircle size={18} /><span>{children}</span><button className="text-button" onClick={retry}>Retry</button></div>;
}
function Status({ value, label }: { value: string; label?: ReactNode }) {
  const success = ["success", "completed", "ok", "ready"].includes(value);
  const failed = ["error", "failed", "unavailable"].includes(value);
  return <span className={`admin-status ${success ? "good" : failed ? "bad" : "neutral"}`}>{success ? <Check size={12} /> : failed ? <AlertCircle size={12} /> : <Clock3 size={12} />}{label || value}</span>;
}
function Score({ result }: { result: MetricResult }) {
  const available = result.score != null && Number.isFinite(result.score);
  return <span className={`admin-score ${available ? result.score! >= result.threshold ? "good" : "bad" : "neutral"}`} title={result.skip_reason || result.error_type || undefined}>{available ? score(result.score) : result.skip_reason ? "Skipped" : "Unavailable"}<small>{available ? result.score! >= result.threshold ? "Meets" : "Below" : "Threshold"} {score(result.threshold)}</small></span>;
}
function MetricCard({ label, value, detail, icon, onClick }: { label: string; value: ReactNode; detail: ReactNode; icon: ReactNode; onClick?: () => void }) {
  return <div className="admin-metric"><div className="admin-metric-label">{label}{icon}</div><strong>{value}</strong><div className="admin-metric-detail">{detail}{onClick && <button className="icon-button" aria-label={`View ${label.toLowerCase()}`} onClick={onClick}><ArrowRight size={16} /></button>}</div></div>;
}

function TrendChart({ series, latency = false }: { series: Overview["series"]; latency?: boolean }) {
  const id = useId();
  const [active, setActive] = useState<number | null>(null);
  const data = series.map((item) => ({ ...item, first: latency ? item.avg_ms : item.requests, second: item.errors }));
  const hasData = data.some((item) => latency ? item.first != null : item.requests > 0);
  const max = Math.max(1, ...data.flatMap((item) => latency ? [item.first ?? 0] : [item.requests, item.errors]));
  const top = Math.ceil(max / (max > 10 ? 10 : 1)) * (max > 10 ? 10 : 1);
  const x = (index: number) => 52 + (index / Math.max(1, data.length - 1)) * 618;
  const y = (value: number) => 174 - (value / top) * 144;
  const path = (key: "first" | "second") => {
    let fresh = true;
    return data.map((item, index) => {
      const value = item[key];
      if (value == null) { fresh = true; return ""; }
      const point = `${fresh ? "M" : "L"}${x(index)},${y(value)}`;
      fresh = false;
      return point;
    }).join(" ");
  };
  const dateLabel = (value: string) => new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const shown = active == null ? null : data[active];
  return <section className="admin-panel admin-chart-panel"><div className="admin-section-heading"><div><h2>{latency ? "Response time" : "Request activity"}</h2><p>{latency ? "Mean request duration per interval" : "Recorded API requests and errors"}</p></div><span className="admin-unit">{latency ? "ms" : "requests"}</span></div>
    {!hasData ? <div className="admin-empty admin-chart-empty"><Activity size={24} /><p>{latency ? "No response times recorded in this window." : "No requests recorded in this window."}</p></div> : <>
      <div className="admin-chart-wrap">
        <svg className="admin-chart" viewBox="0 0 700 218" role="img" tabIndex={0} aria-labelledby={`${id}-title ${id}-desc`} onMouseLeave={() => setActive(null)} onFocus={() => setActive(0)} onBlur={() => setActive(null)} onKeyDown={(event) => {
            if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
              event.preventDefault();
              setActive((current) => event.key === "Home" ? 0 : event.key === "End" ? data.length - 1 : Math.max(0, Math.min(data.length - 1, (current ?? 0) + (event.key === "ArrowRight" ? 1 : -1))));
            }
          }}>
          <title id={`${id}-title`}>{latency ? "Average request response times" : "API request and error counts"}</title><desc id={`${id}-desc`}>Time increases from left to right. Use the left and right arrow keys to inspect intervals, or open the exact-value data table below.</desc>
          {[0, 1, 2, 3].map((tick) => <g key={tick}><line x1="52" x2="670" y1={y(top * tick / 3)} y2={y(top * tick / 3)} className="admin-gridline" /><text x="42" y={y(top * tick / 3) + 4} textAnchor="end" className="admin-axis">{number(top * tick / 3, top < 3 ? 1 : 0)}</text></g>)}
          <path d={path("first")} className="admin-chart-line" />{!latency && <path d={path("second")} className="admin-chart-line error-line" />}
          {data.map((item, index) => <g key={item.timestamp}>
            {item.first != null && <circle cx={x(index)} cy={y(item.first)} r={data.length === 1 || index === active ? 4 : 2} className="admin-chart-dot" />}
            <rect x={x(index) - Math.max(7, 309 / data.length)} y="18" width={Math.max(14, 618 / data.length)} height="160" fill="transparent" aria-label={`${timestamp(item.timestamp)}: ${latency ? duration(item.first) : `${number(item.requests)} requests, ${number(item.errors)} errors`}`} onMouseEnter={() => setActive(index)}><title>{`${timestamp(item.timestamp)} · ${latency ? duration(item.first) : `${number(item.requests)} requests · ${number(item.errors)} errors`}`}</title></rect>
          </g>)}
          {[0, Math.floor((data.length - 1) / 2), data.length - 1].filter((value, index, list) => list.indexOf(value) === index).map((index) => <text key={index} x={x(index)} y="204" textAnchor={index === 0 ? "start" : index === data.length - 1 ? "end" : "middle"} className="admin-axis">{dateLabel(data[index].timestamp)}</text>)}
        </svg>
        {shown && <div className="admin-chart-tooltip" role="status"><strong>{timestamp(shown.timestamp)}</strong><span>{latency ? duration(shown.first) : `${number(shown.requests)} requests · ${number(shown.errors)} errors`}</span></div>}
      </div>
      <div className="admin-chart-legend"><span><i />{latency ? "Average duration" : "Requests"}</span>{!latency && <span><i className="error-dot" />Errors</span>}</div>
    </>}
    {hasData && <details className="admin-chart-data"><summary>View chart data</summary><div className="admin-table-scroll"><table><caption className="visually-hidden">{latency ? "Response time" : "Request activity"} by interval</caption><thead><tr><th>Interval</th>{latency ? <th>Average duration</th> : <><th>Requests</th><th>Errors</th><th>Queries</th></>}</tr></thead><tbody>{series.map((item) => <tr key={item.timestamp}><td>{timestamp(item.timestamp)}</td>{latency ? <td>{duration(item.avg_ms)}</td> : <><td>{number(item.requests)}</td><td>{number(item.errors)}</td><td>{number(item.queries)}</td></>}</tr>)}</tbody></table></div></details>}
  </section>;
}

function OverviewContent({ data, changeTab }: { data: Overview; changeTab: (tab: Tab) => void }) {
  return <>
    <div className="admin-metrics">
      <MetricCard label="API requests" value={number(data.requests.total)} detail={<span>{number(data.requests.errors)} errors · {number(data.requests.error_rate * 100, 2)}% error rate</span>} icon={<Activity size={17} />} onClick={() => changeTab("requests")} />
      <MetricCard label="Response time · p95" value={duration(data.requests.p95_ms)} detail={<span>p50 {duration(data.requests.p50_ms)} · p99 {duration(data.requests.p99_ms)}</span>} icon={<Clock3 size={17} />} />
      <MetricCard label="Queries" value={number(data.queries.total)} detail={<span>{number(data.queries.errors)} errors · p95 {duration(data.queries.p95_ms)}</span>} icon={<Search size={17} />} onClick={() => changeTab("queries")} />
      <MetricCard label="Documents ready" value={number(data.documents.ready)} detail={<span>{number(data.documents.total)} total · {number(data.documents.failed)} failed</span>} icon={<FileText size={17} />} onClick={() => changeTab("ingestions")} />
    </div>
    <div className="admin-chart-grid"><TrendChart series={data.series} /><TrendChart series={data.series} latency /></div>
    <div className="admin-overview-grid">
      <section className="admin-panel"><div className="admin-section-heading"><div><h2>Query pipeline</h2><p>Average recorded stage durations</p></div><button className="text-button" onClick={() => changeTab("queries")}>View queries <ArrowRight size={14} /></button></div>
        <div className="admin-stage-grid">{[["Retrieval", data.queries.avg_retrieval_ms], ["Reranking", data.queries.avg_rerank_ms], ["Generation", data.queries.avg_generation_ms]].map(([label, value]) => <div key={String(label)}><span>{label}</span><strong>{duration(value as Nullable)}</strong></div>)}</div>
        <dl className="admin-key-values"><div><dt>Query latency · p50 / p95</dt><dd>{duration(data.queries.p50_ms)} / {duration(data.queries.p95_ms)}</dd></div><div><dt>Average sources / without sources</dt><dd>{number(data.queries.avg_sources, 1)} / {number(data.queries.no_sources)}</dd></div><div><dt>Reported tokens</dt><dd>{number(data.queries.total_tokens)} <small>{number(data.queries.token_samples)} sampled queries</small></dd></div><div><dt>Input / output tokens</dt><dd>{number(data.queries.input_tokens)} / {number(data.queries.output_tokens)}</dd></div><div><dt>Reported model cost</dt><dd>{money(data.queries.reported_cost_usd)} <small>{number(data.queries.cost_samples)} sampled queries</small></dd></div></dl>
      </section>
      <section className="admin-panel"><div className="admin-section-heading"><div><h2>Latest evaluation</h2><p>{data.evaluation.run_id ? `${number(data.evaluation.cases)} cases · ${number(data.evaluation.runs)} runs in this window` : "No evaluation run in this window"}</p></div><button className="text-button" onClick={() => changeTab("evaluations")}>View cases <ArrowRight size={14} /></button></div>
        {data.evaluation.metrics.length ? <div className="admin-eval-summary">{data.evaluation.metrics.map((metric) => <div key={metric.name}><div><strong>{metricLabels[metric.name] || metric.name}</strong><small>{number(metric.samples)} scored · {number(metric.skipped)} skipped · {number(metric.below_threshold)} below</small></div><Score result={{ score: metric.mean, threshold: metric.threshold, reason: null, skip_reason: null, error_type: null }} /></div>)}</div> : <div className="admin-empty"><Database size={23} /><p>Evaluation scores appear after a recorded run.</p></div>}
        {data.evaluation.run_id && <p className="admin-panel-note">Mean scores from the latest run only. Each metric has its own threshold.</p>}
      </section>
      <section className="admin-panel"><div className="admin-section-heading"><div><h2>Ingestion & workspace</h2><p>Current document state and ingestion activity</p></div><FileText size={18} /></div><div className="admin-document-states">{(["queued", "processing", "ready", "failed"] as const).map((state) => <div key={state}><span>{state}</span><strong>{number(data.documents[state])}</strong></div>)}</div><dl className="admin-key-values"><div><dt>Ingestions / failed</dt><dd>{number(data.ingestion.total)} / {number(data.ingestion.failed)}</dd></div><div><dt>Ingestion average / p95</dt><dd>{duration(data.ingestion.avg_ms)} / {duration(data.ingestion.p95_ms)}</dd></div><div><dt>Average queue time</dt><dd>{duration(data.ingestion.avg_queue_ms)}</dd></div><div><dt>Users / active in window</dt><dd>{number(data.users.total)} / {number(data.users.active)}</dd></div></dl></section>
      <section className="admin-panel"><div className="admin-section-heading"><div><h2>System health</h2><p>Live service state</p></div><Server size={18} /></div><dl className="admin-key-values"><div><dt>Database</dt><dd><Status value={data.health.database} label="Connected" /></dd></div><div><dt>Redis</dt><dd><Status value={data.health.redis} label={data.health.redis === "ok" ? "Connected" : "Unavailable"} /></dd></div><div><dt>Document index</dt><dd><Status value={data.health.index_ready ? "ready" : "pending"} label={data.health.index_ready ? "Ready" : "Not ready"} /></dd></div><div><dt>Workers / queued jobs</dt><dd>{number(data.health.worker_count)} / {number(data.health.queued_jobs)}</dd></div><div><dt>Uptime</dt><dd>{duration(data.health.uptime_seconds * 1000)}</dd></div></dl><details className="admin-models"><summary>Model configuration</summary><dl className="admin-key-values">{Object.entries(data.models).map(([name, model]) => <div key={name}><dt>{name}</dt><dd>{model || "—"}</dd></div>)}</dl></details></section>
    </div>
    <section className="admin-panel admin-endpoints"><div className="admin-section-heading"><div><h2>Endpoint performance</h2><p>Request totals and latency by route</p></div></div>{data.endpoints.length ? <div className="admin-table-scroll"><table><caption className="visually-hidden">Endpoint request counts and response times</caption><thead><tr><th>Endpoint</th><th>Requests</th><th>Errors</th><th>Average</th><th>p95</th></tr></thead><tbody>{data.endpoints.map((endpoint) => <tr key={`${endpoint.method}-${endpoint.route}`}><td><span className="admin-method">{endpoint.method}</span><code>{endpoint.route}</code></td><td>{number(endpoint.count)}</td><td>{number(endpoint.errors)}</td><td>{duration(endpoint.avg_ms)}</td><td>{duration(endpoint.p95_ms)}</td></tr>)}</tbody></table></div> : <div className="admin-empty"><p>No endpoint activity in this window.</p></div>}</section>
  </>;
}

function DetailList({ items }: { items: [string, ReactNode][] }) {
  return <dl className="admin-detail-list">{items.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value == null || value === "" ? "—" : value}</dd></div>)}</dl>;
}
function QueryDetails({ item }: { item: QueryLog }) {
  return <><DetailList items={[["Query ID", item.id], ["Request ID", item.request_id], ["User ID", item.user_id], ["Model", item.model], ["Top k", number(item.top_k)], ["Setup", duration(item.setup_ms)], ["Retrieval", duration(item.retrieval_ms)], ["Reranking", duration(item.rerank_ms)], ["Generation", duration(item.generation_ms)], ["LLM calls", number(item.llm_calls)], ["Input / output tokens", `${number(item.input_tokens)} / ${number(item.output_tokens)}`], ["Reported total tokens", number(item.total_tokens)], ["Reported model cost", money(item.reported_cost_usd)], ["Sources", number(item.source_count)], ["Mean reranker score", score(item.avg_rerank_score)], ["Question / answer characters", `${number(item.question_chars)} / ${number(item.answer_chars)}`], ["Error type", item.error_type]]} /><p className="admin-panel-note">Reranker scores are ranking signals, not evaluation scores or probabilities. Runtime logs contain metadata only.</p></>;
}
function EvaluationDetails({ item }: { item: EvaluationCase }) {
  return <><DetailList items={[["Case ID", item.case_id], ["Run ID", item.run_id], ["Answerability", item.answerability], ["Duration", duration(item.duration_ms)], ["Sources", number(item.source_count)]]} /><div className="admin-eval-text">{[["Benchmark question", item.question], ["Actual answer", item.actual_answer], ["Expected answer", item.expected_answer]].map(([label, text]) => <div key={label}><h3>{label}</h3><p>{text || "—"}</p></div>)}</div><div className="admin-eval-reasons">{metricNames.map((name) => { const result = item.metrics[name]; return <section key={name}><div><h3>{metricLabels[name]}</h3><Score result={result} /></div><p>{result.skip_reason || result.error_type || result.reason || "No explanation recorded."}</p></section>; })}</div></>;
}
function RecordsTable({ tab, items, selected, setSelected }: { tab: Tab; items: LogRow[]; selected: string | null; setSelected: (id: string | null) => void }) {
  const headers = tab === "requests" ? ["Time", "Endpoint", "Status", "Duration", "User", ""] : tab === "queries" ? ["Time", "Query", "Status", "Duration", "Sources", ""] : tab === "ingestions" ? ["Time", "Document", "Status", "Duration", "Queue", ""] : ["Case", "Answerability", ...metricNames.map((name) => metricLabels[name]), ""];
  return <div className="admin-table-scroll"><table className={`admin-records ${tab === "evaluations" ? "admin-evaluation-table" : ""}`}><caption className="visually-hidden">{tabs.find((item) => item.key === tab)?.label} records</caption><thead><tr>{headers.map((header, index) => <th key={index}>{header || <span className="visually-hidden">Details</span>}</th>)}</tr></thead><tbody>{items.map((row) => {
    const expanded = selected === row.id;
    let cells: ReactNode;
    let detail: ReactNode;
    if (tab === "requests") {
      const item = row as RequestLog;
      cells = <><td className="admin-time">{timestamp(item.created_at)}</td><td><span className="admin-method">{item.method}</span><code>{item.route}</code></td><td><Status value={item.status_code >= 400 ? "error" : "success"} label={item.status_code} /></td><td>{duration(item.duration_ms)}</td><td><code className="admin-id" title={item.user_id || undefined}>{item.user_id || "Anonymous"}</code></td></>;
      detail = <DetailList items={[["Request ID", item.id], ["Time", timestamp(item.created_at)], ["User ID", item.user_id || "Anonymous"], ["Endpoint", `${item.method} ${item.route}`], ["HTTP status", item.status_code], ["Duration", duration(item.duration_ms)], ["Error type", item.error_type]]} />;
    } else if (tab === "queries") {
      const item = row as QueryLog;
      cells = <><td className="admin-time">{timestamp(item.created_at)}</td><td><code className="admin-id" title={item.id}>{item.id}</code></td><td><Status value={item.outcome} /></td><td>{duration(item.duration_ms)}</td><td>{number(item.source_count)}</td></>;
      detail = <QueryDetails item={item} />;
    } else if (tab === "ingestions") {
      const item = row as IngestionLog;
      cells = <><td className="admin-time">{timestamp(item.created_at)}</td><td><code className="admin-id" title={item.document_id}>{item.document_id}</code></td><td><Status value={item.outcome} /></td><td>{duration(item.duration_ms)}</td><td>{duration(item.queue_ms)}</td></>;
      detail = <DetailList items={[["Ingestion ID", item.id], ["Document ID", item.document_id], ["Job ID", item.job_id], ["User ID", item.user_id], ["Duration", duration(item.duration_ms)], ["Queue time", duration(item.queue_ms)], ["Chunks", number(item.chunk_count)], ["File size", item.file_bytes == null ? "—" : `${number(item.file_bytes / 1024, 1)} KB`], ["Error type", item.error_type]]} />;
    } else {
      const item = row as EvaluationCase;
      cells = <><td><strong>{item.case_id}</strong><span className="admin-case-question">{item.question}</span></td><td><span className="admin-answerability">{item.answerability}</span></td>{metricNames.map((name) => <td key={name}><Score result={item.metrics[name]} /></td>)}</>;
      detail = <EvaluationDetails item={item} />;
    }
    return <Fragment key={row.id}><tr className={expanded ? "selected" : ""}>{cells}<td><button className="button quiet small" aria-expanded={expanded} aria-controls={`record-${row.id}`} onClick={() => setSelected(expanded ? null : row.id)}>{expanded ? "Close" : "Details"}{expanded ? <X size={13} /> : <ArrowRight size={13} />}</button></td></tr>{expanded && <tr id={`record-${row.id}`} className="admin-detail-row"><td colSpan={headers.length}>{detail}</td></tr>}</Fragment>;
  })}</tbody></table></div>;
}

export default function AdminPanel({ api }: { api: Api }) {
  const [hours, setHours] = useState(24);
  const [tab, setTab] = useState<Tab>("overview");
  const [revision, setRevision] = useState(0);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [status, setStatus] = useState("all");
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const [runId, setRunId] = useState("");
  const [answerability, setAnswerability] = useState("all");
  const [metric, setMetric] = useState("all");
  const [belowThreshold, setBelowThreshold] = useState(false);
  const overview = useRemote<Overview>(api, `/admin/overview?hours=${hours}`, revision);
  const runs = useRemote<{ items: EvaluationRun[]; total: number }>(api, tab === "evaluations" ? `/admin/evaluations/runs?hours=${hours}` : null, revision);
  const activeRun = runId || runs.data?.items[0]?.id || "";
  const query = new URLSearchParams({ hours: String(hours), status, search, page: String(page), page_size: "25" });
  if (tab === "evaluations") { query.delete("status"); query.set("run_id", activeRun); query.set("answerability", answerability); query.set("metric", metric); query.set("below_threshold", String(belowThreshold)); }
  const tablePath = tab === "overview" || (tab === "evaluations" && !activeRun) ? null : `/admin/${tab}?${query}`;
  const records = useRemote<PageData<LogRow>>(api, tablePath, revision);
  const refreshing = overview.loading || records.loading || runs.loading;
  const busyRef = useRef(refreshing);
  busyRef.current = refreshing;
  const refresh = () => setRevision((value) => value + 1);
  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => { if (document.visibilityState === "visible" && !busyRef.current) setRevision((value) => value + 1); }, 15_000);
    return () => clearInterval(timer);
  }, [autoRefresh]);
  const changeTab = (value: Tab) => { setTab(value); setPage(1); setSelected(null); setSearch(""); setSearchDraft(""); setStatus("all"); };
  const resetPage = () => { setPage(1); setSelected(null); };
  const searchRecords = (event: FormEvent) => { event.preventDefault(); setSearch(searchDraft.trim()); resetPage(); };
  const currentRun = runs.data?.items.find((run) => run.id === activeRun);
  const totalPages = Math.max(1, Math.ceil((records.data?.total || 0) / 25));
  const noRuns = tab === "evaluations" && !runs.loading && !runs.error && !runs.data?.items.length;
  const exportQuery = new URLSearchParams({ hours: String(hours), status, search });
  return <div className="admin-page">
    <div className="page-heading admin-page-heading"><div><span className="eyebrow">OPERATIONS</span><h1>Admin<span className="heading-dot">.</span></h1><p>Activity, performance, and answer quality.</p></div><div className="admin-top-controls"><label className="admin-window"><span className="visually-hidden">Reporting window</span><select value={hours} onChange={(event) => { setHours(Number(event.target.value)); resetPage(); setRunId(""); }}><option value={24}>Last 24 hours</option><option value={168}>Last 7 days</option><option value={720}>Last 30 days</option></select></label><button className="button secondary" disabled={refreshing} onClick={refresh}>{refreshing ? <LoaderCircle size={16} className="spin" /> : <RefreshCw size={16} />}Refresh</button></div></div>
    <div className="admin-subline"><span>{overview.data ? `Updated ${timestamp(overview.data.generated_at)}` : refreshing ? "Loading activity…" : "Activity unavailable"}{overview.error && overview.data ? " · Refresh failed; showing previous data" : ""}</span><label className="admin-auto-refresh"><input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} />Refresh every 15 seconds</label></div>
    <nav className="admin-tabs" aria-label="Admin sections">{tabs.map((item) => <button key={item.key} className={item.key === tab ? "active" : ""} aria-pressed={item.key === tab} onClick={() => changeTab(item.key)}>{item.label}</button>)}</nav>
    {overview.error && <ErrorNotice retry={refresh}>{overview.error}</ErrorNotice>}
    {tab === "overview" ? overview.data ? <OverviewContent data={overview.data} changeTab={changeTab} /> : overview.loading ? <Loading /> : null : <>
      {tab === "evaluations" && <section className="admin-panel admin-runs"><div className="admin-section-heading"><div><h2>Evaluation runs</h2><p>Recorded benchmark results, scored separately for each metric.</p></div><span className="admin-count">{runs.data ? number(runs.data.total) : "—"} runs</span></div>{runs.error && <ErrorNotice retry={refresh}>{runs.error}</ErrorNotice>}{runs.loading && !runs.data ? <Loading>Loading evaluation runs…</Loading> : noRuns ? <div className="admin-empty"><Database size={24} /><p>No evaluation runs recorded in this window.</p></div> : runs.data?.items.length ? <><label className="admin-run-select">Run<select value={activeRun} onChange={(event) => { setRunId(event.target.value); resetPage(); }}>{runs.data.items.map((run) => <option key={run.id} value={run.id}>{timestamp(run.created_at)} · {run.dataset} · {run.status} · {run.id.slice(0, 8)}</option>)}</select></label>{currentRun && <div className="admin-run-summary"><div><Status value={currentRun.status} /><span>{number(currentRun.completed_count)} / {number(currentRun.case_count)} cases</span><span>{duration(currentRun.duration_ms)}</span></div><details><summary>Run configuration</summary><DetailList items={[["Run ID", currentRun.id], ["Owner ID", currentRun.owner_id], ["Dataset", currentRun.dataset], ["Model", currentRun.model], ["Judge model", currentRun.judge_model], ["Top k", number(currentRun.top_k)], ["Error type", currentRun.error_type]]} /></details></div>}</> : null}</section>}
      {!noRuns && (tab !== "evaluations" || activeRun) && <section className="admin-panel admin-log-panel"><div className="admin-section-heading"><div><h2>{tab === "requests" ? "Request log" : tab === "queries" ? "Query log" : tab === "ingestions" ? "Ingestion log" : "Evaluation cases"}</h2><p>{tab === "requests" ? "API traffic, excluding admin dashboard polling." : tab === "queries" ? "Runtime metadata and pipeline timings." : tab === "ingestions" ? "Processing outcomes and queue times." : "Individual metric scores and recorded judge explanations."}</p></div>{tab === "requests" && <a className="button secondary small" href={`/api/admin/requests/export?${exportQuery}`} download><ArrowDownToLine size={15} />Export JSON</a>}</div>
        <div className="admin-record-controls"><form onSubmit={searchRecords} className="admin-search"><label><Search size={16} /><span className="visually-hidden">{tab === "evaluations" ? "Search benchmark cases" : "Search log metadata"}</span><input value={searchDraft} onChange={(event) => setSearchDraft(event.target.value)} placeholder={tab === "evaluations" ? "Search cases…" : tab === "requests" ? "Search IDs, routes, errors…" : "Search IDs, errors…"} /></label><button className="button secondary small" type="submit">Search</button>{search && <button className="icon-button" type="button" aria-label="Clear search" onClick={() => { setSearch(""); setSearchDraft(""); resetPage(); }}><X size={16} /></button>}</form>{tab !== "evaluations" ? <label className="admin-select-field">Status<select value={status} onChange={(event) => { setStatus(event.target.value); resetPage(); }}><option value="all">All statuses</option><option value="success">Success</option><option value="error">Errors</option></select></label> : <><label className="admin-select-field">Answerability<select value={answerability} onChange={(event) => { setAnswerability(event.target.value); resetPage(); }}><option value="all">All cases</option><option value="answerable">Answerable</option><option value="partial">Partial</option><option value="unanswerable">Unanswerable</option></select></label><label className="admin-select-field">Metric<select value={metric} onChange={(event) => { setMetric(event.target.value); resetPage(); }}><option value="all">All metrics</option>{metricNames.map((name) => <option value={name} key={name}>{metricLabels[name]}</option>)}</select></label><label className="admin-checkbox"><input type="checkbox" checked={belowThreshold} onChange={(event) => { setBelowThreshold(event.target.checked); resetPage(); }} />Below threshold</label></>}</div>
        {records.error && <ErrorNotice retry={refresh}>{records.data ? `Refresh failed; showing previous records. ${records.error}` : records.error}</ErrorNotice>}
        {records.loading && !records.data ? <Loading /> : records.data?.items.length ? <RecordsTable tab={tab} items={records.data.items} selected={selected} setSelected={setSelected} /> : !records.error && <div className="admin-empty"><Search size={24} /><p>{search || status !== "all" || (tab === "evaluations" && (answerability !== "all" || belowThreshold)) ? "No records match these filters." : "No records in this window."}</p></div>}
        {records.data && <div className="admin-pagination"><span>{number(records.data.total)} records{tab === "requests" ? " · Export limited to 10,000 records" : ""}</span><div><button className="icon-button" disabled={page <= 1 || records.loading} aria-label="Previous page" onClick={() => { setPage((value) => Math.max(1, value - 1)); setSelected(null); }}><ChevronLeft size={17} /></button><span>Page {number(page)} of {number(totalPages)}</span><button className="icon-button" disabled={page >= totalPages || records.loading} aria-label="Next page" onClick={() => { setPage((value) => value + 1); setSelected(null); }}><ChevronRight size={17} /></button></div></div>}
      </section>}
    </>}
    <p className="admin-footnote">Times shown in your local time zone. Unavailable measurements appear as —.</p>
  </div>;
}
