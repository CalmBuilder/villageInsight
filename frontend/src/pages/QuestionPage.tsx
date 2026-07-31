import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { useSearchParams } from "react-router-dom";
import {
  createQuestionConversation,
  deleteQuestionConversations,
  getQuestionConversation,
  getQuestionConversations,
  getQuestionSources,
  renameQuestionConversation,
  stopQuestionRun,
  streamQuestionRun,
  type QuestionAnswer,
  type QuestionConversation,
  type QuestionConversationDetail,
  type QuestionRun,
  type QuestionSourcePage,
  type QuestionStreamEvent,
  type QuestionToolTrace,
  type CurrentUser,
} from "../lib/api";
import { questionInputKeyAction } from "./questionInput";

const villageSuggestedQuestions = [
  "全村总人数是多少？",
  "按村组统计人数",
  "已审核的数据来自多少个文件？",
] as const;

const tenantSuggestedQuestions = [
  "所有村总人数是多少？",
  "按村统计总人数",
  "各村已审核的数据来自多少个文件？",
] as const;

type LiveRun = {
  id: string;
  question: string;
  startedAt: number;
  answerText: string;
  answer: QuestionAnswer;
  status: string;
  toolTrace: QuestionToolTrace[];
  reasoningActive: boolean;
  clarification?: {
    question: string;
    choices: string[];
  };
};

function formatConversationTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatQueryDuration(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  if (seconds < 60) return `${seconds}秒`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder ? `${minutes}分 ${remainder}秒` : `${minutes}分`;
}

function storedRunDuration(run: QuestionRun): string {
  if (!run.completed_at) return "";
  const startedAt = Date.parse(run.started_at);
  const completedAt = Date.parse(run.completed_at);
  if (!Number.isFinite(startedAt) || !Number.isFinite(completedAt)) return "";
  return formatQueryDuration(completedAt - startedAt);
}

function renderCell(value: unknown): string {
  if (value == null || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function markdownCell(value: unknown): string {
  return renderCell(value).replaceAll("|", "\\|").replaceAll("\n", " ");
}

export function formatAnswerForClipboard(run: QuestionRun): string {
  const sections = [run.answer_text.trim()].filter(Boolean);
  if (run.answer.result_type === "metric" && run.answer.metric) {
    const metric = run.answer.metric;
    sections.push(
      [
        `${metric.metric_name}：${metric.value ?? "无数据"}${metric.unit ?? ""}`,
        `已核对 ${metric.record_count} 条记录 · ${metric.source_file_count} 个来源文件`,
      ].join("\n"),
    );
  }
  if (
    run.answer.result_type === "table"
    && run.answer.columns
    && run.answer.rows
  ) {
    const columns = run.answer.columns.filter(
      (column) => !["record_id", "item_id"].includes(column),
    );
    if (columns.length) {
      sections.push([
        `| ${columns.join(" | ")} |`,
        `| ${columns.map(() => "---").join(" | ")} |`,
        ...run.answer.rows.map(
          (row) => `| ${columns.map((column) => markdownCell(row[column])).join(" | ")} |`,
        ),
        `返回 ${run.answer.row_count ?? run.answer.rows.length} 行${
          run.answer.truncated ? " · 已达到本次显示上限" : ""
        }`,
      ].join("\n"));
    }
  }
  return sections.join("\n\n");
}

function AnswerResult({ answer }: { answer: QuestionAnswer }) {
  if (answer.result_type === "metric" && answer.metric) {
    return (
      <section className="question-result question-result--metric">
        <span>{answer.metric.metric_name}</span>
        <strong>
          {answer.metric.value ?? "无数据"}
          <small>{answer.metric.unit ?? ""}</small>
        </strong>
        <p>
          已核对 {answer.metric.record_count} 条记录 · {answer.metric.source_file_count} 个来源文件
        </p>
      </section>
    );
  }
  if (answer.result_type === "metric" && "value" in answer) {
    return (
      <section className="question-result question-result--metric">
        <span>{answer.aggregation === "count" ? "计数结果" : "聚合结果"}</span>
        <strong>{answer.value ?? "无数据"}</strong>
        <p>
          已核对 {answer.evidence_summary?.record_count ?? 0} 条记录 ·{" "}
          {answer.evidence_summary?.source_file_count ?? 0} 个来源文件
        </p>
      </section>
    );
  }
  if (
    (answer.result_type === "table" || answer.result_type === "record")
    && answer.rows
  ) {
    const columns = answer.columns
      ?? Object.keys(answer.rows[0] ?? {});
    const visibleColumns = columns.filter(
      (column) => !["record_id", "item_id"].includes(column),
    );
    return (
      <div className="question-table-wrap">
        <table className="question-table">
          <thead>
            <tr>
              {visibleColumns.map((column) => <th key={column}>{column}</th>)}
            </tr>
          </thead>
          <tbody>
            {answer.rows.map((row, index) => (
              <tr key={`${index}-${String(row.record_id ?? "")}`}>
                {visibleColumns.map((column) => (
                  <td key={column}>{renderCell(row[column])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        <small>
          返回 {answer.row_count ?? answer.rows.length} 行
          {answer.truncated ? " · 已达到本次显示上限" : ""}
        </small>
      </div>
    );
  }
  return null;
}

function QueryReceipt({
  trace,
  active,
}: {
  trace: QuestionToolTrace;
  active: boolean;
}) {
  const facts = [
    trace.record_count != null ? `${trace.record_count} 条记录` : "",
    trace.source_file_count != null ? `${trace.source_file_count} 个来源` : "",
    trace.row_count != null ? `${trace.row_count} 行结果` : "",
    trace.duration_ms != null ? `耗时 ${formatQueryDuration(trace.duration_ms)}` : "",
  ].filter(Boolean);
  return (
    <li className={`query-receipt query-receipt--${trace.status}`}>
      <span className="query-receipt__mark" aria-hidden="true">
        {trace.status === "completed" ? "✓" : trace.status === "error" ? "!" : ""}
      </span>
      <div>
        <strong>{trace.label}</strong>
        <small>
          {trace.status === "running" && active ? "正在核对…" : facts.join(" · ") || trace.message || "已完成"}
        </small>
      </div>
    </li>
  );
}

function DataCheckProcess({
  traces,
  active,
  failed = false,
  reasoningActive = false,
  durationLabel = "",
}: {
  traces: QuestionToolTrace[];
  active: boolean;
  failed?: boolean;
  reasoningActive?: boolean;
  durationLabel?: string;
}) {
  const [expanded, setExpanded] = useState(active || failed);

  useEffect(() => {
    if (active || failed) setExpanded(true);
  }, [active, failed]);

  const status = failed
    ? `未完成${durationLabel ? ` · 耗时 ${durationLabel}` : ""}`
    : active
      ? `正在核对${durationLabel ? ` · 已用 ${durationLabel}` : ""}`
      : `${traces.length} 步 · 已完成${
        durationLabel ? ` · 耗时 ${durationLabel}` : ""
      }`;
  return (
    <details
      className="question-response-part question-response-part--process"
      onToggle={(event) => setExpanded(event.currentTarget.open)}
      open={expanded}
    >
      <summary>
        <span>数据核对</span>
        <small>{status}</small>
        <i aria-hidden="true">⌄</i>
      </summary>
      {traces.length ? (
        <ul className="query-receipts">
          {traces.map((trace, index) => (
            <QueryReceipt
              active={active}
              key={`${trace.tool_call_id ?? trace.tool_name}-${index}`}
              trace={trace}
            />
          ))}
        </ul>
      ) : (
        <div className="question-thinking">
          <i aria-hidden="true" />
          <span>{reasoningActive ? "正在分析查询口径" : "正在理解问题"}</span>
        </div>
      )}
    </details>
  );
}

function StoredTurn({
  run,
  selected,
  onInspect,
  onCopyAnswer,
  onCopyQuestion,
  onRetry,
  copiedAction,
  canRetry,
}: {
  run: QuestionRun;
  selected: boolean;
  onInspect: () => void;
  onCopyAnswer: () => void;
  onCopyQuestion: () => void;
  onRetry: () => void;
  copiedAction: "question" | "answer" | "";
  canRetry: boolean;
}) {
  const hasAnswer = Boolean(
    run.answer_text || Object.keys(run.answer).length,
  );
  return (
    <article className="question-turn">
      <div className="question-bubble question-bubble--user">{run.question}</div>
      <div className="question-message-actions question-message-actions--user">
        <button onClick={onCopyQuestion} type="button">
          {copiedAction === "question" ? "已复制" : "复制问题"}
        </button>
      </div>
      <div className="question-bubble question-bubble--assistant">
        {run.tool_trace.length ? (
          <DataCheckProcess
            active={false}
            durationLabel={storedRunDuration(run)}
            failed={run.status === "failed"}
            traces={run.tool_trace}
          />
        ) : null}
        {hasAnswer ? (
          <section className="question-response-part question-response-part--answer">
            <span>回答</span>
            {run.answer_text ? <p className="question-answer-copy">{run.answer_text}</p> : null}
            <AnswerResult answer={run.answer} />
          </section>
        ) : null}
        {run.status === "failed" || run.status === "stopped" ? (
          <p className="question-run-note">
            {run.status === "stopped" ? "本次查询已停止。" : "本次查询没有完成，可以重新提问。"}
          </p>
        ) : null}
        {hasAnswer ? (
          <button
            className={`evidence-link${selected ? " active" : ""}`}
            onClick={onInspect}
            type="button"
          >
            {selected ? "正在查看口径" : "查看口径与证据"}
          </button>
        ) : null}
      </div>
      <div className="question-message-actions question-message-actions--assistant">
        {hasAnswer ? (
          <button onClick={onCopyAnswer} type="button">
            {copiedAction === "answer" ? "已复制" : "复制答案"}
          </button>
        ) : null}
        {canRetry ? (
          <button
            onClick={onRetry}
            title="基于当前已审核数据重新查询，并保留原回答"
            type="button"
          >
            {run.status === "failed" || run.status === "stopped"
              ? "重试查询"
              : "重新查询"}
          </button>
        ) : null}
      </div>
    </article>
  );
}

function LiveTurn({ run }: { run: LiveRun }) {
  const active = run.status === "running";
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);

  return (
    <article className="question-turn" aria-live="polite">
      <div className="question-bubble question-bubble--user">{run.question}</div>
      <div className="question-bubble question-bubble--assistant">
        {run.toolTrace.length || active ? (
          <DataCheckProcess
            active={active}
            durationLabel={formatQueryDuration(now - run.startedAt)}
            failed={run.status === "failed"}
            reasoningActive={run.reasoningActive}
            traces={run.toolTrace}
          />
        ) : null}
        {run.answerText || Object.keys(run.answer).length ? (
          <section className="question-response-part question-response-part--answer">
            <span>回答</span>
            {run.answerText ? <p className="question-answer-copy">{run.answerText}</p> : null}
            <AnswerResult answer={run.answer} />
          </section>
        ) : null}
        {run.clarification ? (
          <div className="question-clarification">
            <span>需要确认</span>
            <strong>{run.clarification.question}</strong>
            {run.clarification.choices.length ? (
              <p>{run.clarification.choices.join(" / ")}</p>
            ) : null}
          </div>
        ) : null}
        {run.status === "failed" ? (
          <p className="question-run-note">查询没有完成，请稍后重试。</p>
        ) : null}
      </div>
    </article>
  );
}

function QuestionScopeRail({
  run,
  scopeName,
  sourceItemId,
  selectedSourceName,
  sources,
  loadingSources,
  activeTab,
  onTabChange,
  onSelectSource,
  onPageChange,
  onSearch,
  onCollapse,
}: {
  run: QuestionRun | null;
  scopeName: string;
  sourceItemId: string;
  selectedSourceName: string;
  sources: QuestionSourcePage;
  loadingSources: boolean;
  activeTab: "sources" | "evidence";
  onTabChange: (tab: "sources" | "evidence") => void;
  onSelectSource: (itemId: string) => void;
  onPageChange: (page: number) => void;
  onSearch: (value: string) => void;
  onCollapse: () => void;
}) {
  const evidence = run?.evidence.at(-1);
  return (
    <aside className="question-evidence" aria-label="问数文件范围与查询凭据">
      <header>
        <button
          aria-pressed={activeTab === "sources"}
          className={activeTab === "sources" ? "active" : undefined}
          onClick={() => onTabChange("sources")}
          type="button"
        >
          文件范围
        </button>
        <button
          aria-pressed={activeTab === "evidence"}
          className={activeTab === "evidence" ? "active" : undefined}
          onClick={() => onTabChange("evidence")}
          type="button"
        >
          本次凭据
        </button>
        <button
          aria-label="收起文件与凭据栏"
          className="question-evidence__collapse"
          onClick={onCollapse}
          title="收起文件与凭据栏"
          type="button"
        >
          ›
        </button>
      </header>
      {activeTab === "sources" ? (
        <section className="question-source-panel">
          <div className="question-source-panel__scope">
            <span>当前行政范围</span>
            <strong>{scopeName || "当前授权范围"}</strong>
            <small>仅列出存在已审核正式记录的文件</small>
          </div>
          <form
            className="question-source-search"
            onSubmit={(event) => {
              event.preventDefault();
              const data = new FormData(event.currentTarget);
              onSearch(String(data.get("source-search") ?? "").trim());
            }}
          >
            <input
              aria-label="搜索问数文件"
              name="source-search"
              placeholder="搜索文件名"
              type="search"
            />
            <button type="submit">查找</button>
          </form>
          <div className="question-source-list" role="radiogroup" aria-label="问数文件范围">
            <button
              aria-checked={!sourceItemId}
              className={!sourceItemId ? "active" : undefined}
              onClick={() => onSelectSource("")}
              role="radio"
              type="button"
            >
              <i aria-hidden="true" />
              <span>
                <strong>默认有效文件</strong>
                <small>
                  {sources.default_total} 个默认文件
                  {sources.total > sources.default_total
                    ? ` · ${sources.total - sources.default_total} 个历史版本`
                    : ""}
                </small>
              </span>
            </button>
            {loadingSources ? <p>正在核对文件目录…</p> : null}
            {!loadingSources && !sources.items.length ? (
              <p>当前范围暂无可问数文件。</p>
            ) : null}
            {sources.items.map((source) => (
              <button
                aria-checked={source.id === sourceItemId}
                className={source.id === sourceItemId ? "active" : undefined}
                key={source.id}
                onClick={() => onSelectSource(source.id)}
                role="radio"
                type="button"
              >
                <i aria-hidden="true" />
                <span>
                  <strong title={source.file_name}>{source.file_name}</strong>
                  <small>
                    {source.administrative_unit_name} · {source.record_count} 条正式记录
                    {!source.is_default ? " · 历史版本（可单独查询）" : ""}
                  </small>
                </span>
              </button>
            ))}
          </div>
          {sourceItemId && !sources.items.some((source) => source.id === sourceItemId) ? (
            <p className="question-source-panel__selected">
              已限定：{selectedSourceName || "所选文件"}
            </p>
          ) : null}
          <nav className="question-source-pagination" aria-label="文件分页">
            <button
              disabled={sources.page <= 1}
              onClick={() => onPageChange(sources.page - 1)}
              type="button"
            >
              上一页
            </button>
            <span>{sources.page} / {sources.total_pages}</span>
            <button
              disabled={sources.page >= sources.total_pages}
              onClick={() => onPageChange(sources.page + 1)}
              type="button"
            >
              下一页
            </button>
          </nav>
        </section>
      ) : (
        <>
          <dl>
            <div>
              <dt>查询范围</dt>
              <dd>{selectedSourceName || scopeName || "当前授权范围"}</dd>
            </div>
            <div>
              <dt>数据状态</dt>
              <dd>仅已审核记录</dd>
            </div>
            <div>
              <dt>核对记录</dt>
              <dd>{evidence?.record_count ?? "—"}</dd>
            </div>
            <div>
              <dt>来源文件</dt>
              <dd>{evidence?.source_file_count ?? "—"}</dd>
            </div>
            <div>
              <dt>有数据的村</dt>
              <dd>{evidence?.data_village_count ?? "—"}</dd>
            </div>
            <div>
              <dt>查询口径</dt>
              <dd>{run?.tool_trace.length ? "Hermes 工具链" : "—"}</dd>
            </div>
          </dl>
          {run?.tool_trace.length ? (
            <section>
              <span>数据核对</span>
              <ol>
                {run.tool_trace.map((trace, index) => (
                  <li key={`${trace.tool_call_id ?? trace.tool_name}-${index}`}>
                    <i>{String(index + 1).padStart(2, "0")}</i>
                    <div>
                      <strong>{trace.label}</strong>
                      <small>{trace.status === "error" ? "未完成" : "已完成"}</small>
                    </div>
                  </li>
                ))}
              </ol>
            </section>
          ) : (
            <p className="question-evidence__empty">
              完成一次查询后，可在这里核对范围、记录数和来源。
            </p>
          )}
        </>
      )}
      <footer>
        单一事实源
        <small>
          {sourceItemId ? "查询已由后端固定到一个文件" : "查询当前范围全部已审核文件"}
        </small>
      </footer>
    </aside>
  );
}

function applyStreamEvent(current: LiveRun, event: QuestionStreamEvent): LiveRun {
  if (event.event === "run.started") {
    return {
      ...current,
      id: event.run_id,
      startedAt: event.started_at
        ? Date.parse(event.started_at)
        : current.startedAt,
      status: "running",
    };
  }
  if (event.event === "assistant.delta") {
    return {
      ...current,
      reasoningActive: true,
    };
  }
  if (event.event === "reasoning.status") {
    return { ...current, reasoningActive: event.active ?? true };
  }
  if (event.event === "tool.started") {
    return {
      ...current,
      toolTrace: [
        ...current.toolTrace,
        {
          tool_call_id: event.tool_call_id,
          tool_name: "",
          label: event.label ?? "查询数据",
          status: "running",
        },
      ],
    };
  }
  if (event.event === "tool.completed" || event.event === "tool.failed") {
    return {
      ...current,
      toolTrace: current.toolTrace.map((trace) =>
        trace.tool_call_id === event.tool_call_id
          ? {
              ...trace,
              status: event.status === "error" ? "error" : "completed",
              result_type: event.result_type,
              row_count: event.row_count,
              record_count: event.record_count,
              source_file_count: event.source_file_count,
              data_village_count: event.data_village_count,
              duration_ms: event.duration_ms,
              message: event.message,
            }
          : trace,
      ),
    };
  }
  if (event.event === "clarify.requested") {
    return {
      ...current,
      clarification: {
        question: event.question ?? "请确认查询口径",
        choices: event.choices ?? [],
      },
    };
  }
  if (event.event === "answer.completed") {
    return {
      ...current,
      answerText: event.content ?? current.answerText,
      answer: event.answer ?? {},
      status: "completed",
      reasoningActive: false,
    };
  }
  if (event.event === "run.completed") {
    return {
      ...current,
      status: event.status ?? "completed",
      reasoningActive: false,
    };
  }
  if (event.event === "run.failed") {
    return { ...current, status: "failed", answerText: event.message ?? "" };
  }
  if (event.event === "run.stopped") {
    return { ...current, status: "stopped" };
  }
  return current;
}

export function QuestionPage({ currentUser }: { currentUser: CurrentUser }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const linkedConversationId = searchParams.get("conversation") ?? "";
  const defaultScopeUnitId = currentUser.scope_unit_id ?? currentUser.upload_units[0]?.id ?? "";
  const [conversations, setConversations] = useState<QuestionConversation[]>([]);
  const [conversationPageNumber, setConversationPageNumber] = useState(1);
  const [conversationPageCount, setConversationPageCount] = useState(1);
  const [conversationTotal, setConversationTotal] = useState(0);
  const [conversationSearch, setConversationSearch] = useState("");
  const [organizingConversations, setOrganizingConversations] = useState(false);
  const [selectedConversationIds, setSelectedConversationIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [pendingDeleteIds, setPendingDeleteIds] = useState<string[]>([]);
  const [deletingConversations, setDeletingConversations] = useState(false);
  const [openConversationMenuId, setOpenConversationMenuId] = useState("");
  const [renamingConversationId, setRenamingConversationId] = useState("");
  const [renamingConversation, setRenamingConversation] = useState(false);
  const [ledgerCollapsed, setLedgerCollapsed] = useState(false);
  const [evidenceCollapsed, setEvidenceCollapsed] = useState(false);
  const [resolvingLinkedConversation, setResolvingLinkedConversation] = useState(
    Boolean(linkedConversationId),
  );
  const [scopeUnitId, setScopeUnitId] = useState(defaultScopeUnitId);
  const [sourceItemId, setSourceItemId] = useState("");
  const [sourcePageNumber, setSourcePageNumber] = useState(1);
  const [sourceSearch, setSourceSearch] = useState("");
  const [sources, setSources] = useState<QuestionSourcePage>({
    items: [],
    page: 1,
    page_size: 12,
    total: 0,
    default_total: 0,
    total_pages: 1,
  });
  const [loadingSources, setLoadingSources] = useState(true);
  const [railTab, setRailTab] = useState<"sources" | "evidence">("sources");
  const [activeId, setActiveId] = useState("");
  const [detail, setDetail] = useState<QuestionConversationDetail | null>(null);
  const [liveRun, setLiveRun] = useState<LiveRun | null>(null);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [showScrollBottom, setShowScrollBottom] = useState(false);
  const [copiedMessageKey, setCopiedMessageKey] = useState("");
  const streamController = useRef<AbortController | null>(null);
  const copyFeedbackTimer = useRef<number | null>(null);
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const questionInputRef = useRef<HTMLTextAreaElement | null>(null);
  const deleteCancelRef = useRef<HTMLButtonElement | null>(null);
  const renameInputRef = useRef<HTMLInputElement | null>(null);
  const followLatestRef = useRef(true);
  const messagePositions = useRef(new Map<string, { scrollTop: number; wasNearBottom: boolean }>());
  const isTenantAdmin = currentUser.role === "tenant_admin";
  const scopeOptions = useMemo(
    () => [
      ...(isTenantAdmin && currentUser.scope_unit_id
        ? [{
            id: currentUser.scope_unit_id,
            name: `全部村（${currentUser.upload_units.length}个）`,
          }]
        : []),
      ...currentUser.upload_units.filter((unit) => unit.id !== currentUser.scope_unit_id),
    ],
    [currentUser.scope_unit_id, currentUser.upload_units, isTenantAdmin],
  );
  const selectedScope = scopeOptions.find((option) => option.id === scopeUnitId);
  const suggestedQuestions = isTenantAdmin && scopeUnitId === currentUser.scope_unit_id
    ? tenantSuggestedQuestions
    : villageSuggestedQuestions;

  function updateConversationLocation(
    conversationId: string,
    replace = false,
  ) {
    const next = new URLSearchParams(searchParams);
    if (conversationId) next.set("conversation", conversationId);
    else next.delete("conversation");
    setSearchParams(next, { replace });
  }

  async function refreshConversations(signal?: AbortSignal) {
    const next = await getQuestionConversations(
      scopeUnitId,
      sourceItemId,
      conversationPageNumber,
      conversationSearch,
      signal,
    );
    setConversations(next.items);
    setConversationTotal(next.total);
    setConversationPageCount(next.total_pages);
    return next;
  }

  useEffect(() => {
    if (!linkedConversationId) {
      setResolvingLinkedConversation(false);
      return;
    }
    if (linkedConversationId === activeId) {
      setResolvingLinkedConversation(false);
      return;
    }
    const controller = new AbortController();
    setResolvingLinkedConversation(true);
    getQuestionConversation(linkedConversationId, 0, controller.signal)
      .then((next) => {
        setScopeUnitId(next.conversation.scope_unit_id);
        setSourceItemId(next.conversation.source_item_id ?? "");
        setConversationPageNumber(1);
        setConversationSearch("");
        setActiveId(next.conversation.id);
        setDetail(next);
        const latest = next.runs.at(-1);
        setSelectedRunId(latest?.id ?? "");
      })
      .catch((cause) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(cause instanceof Error ? cause.message : "链接中的问数会话无法读取");
        setActiveId("");
        setDetail(null);
        updateConversationLocation("", true);
      })
      .finally(() => setResolvingLinkedConversation(false));
    return () => controller.abort();
  }, [linkedConversationId]);

  useEffect(() => {
    if (!scopeUnitId || resolvingLinkedConversation) return;
    const controller = new AbortController();
    setLoading(true);
    refreshConversations(controller.signal)
      .then((next) => {
        if (!activeId) {
          const nextId = next.items[0]?.id ?? "";
          setActiveId(nextId);
          if (nextId) updateConversationLocation(nextId, true);
        }
      })
      .catch((cause) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(cause instanceof Error ? cause.message : "会话加载失败");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [
    conversationPageNumber,
    conversationSearch,
    defaultScopeUnitId,
    scopeUnitId,
    sourceItemId,
    resolvingLinkedConversation,
  ]);

  useEffect(() => {
    if (!scopeUnitId) return;
    const controller = new AbortController();
    setLoadingSources(true);
    getQuestionSources(
      scopeUnitId,
      sourcePageNumber,
      sourceSearch,
      controller.signal,
    )
      .then(setSources)
      .catch((cause) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(cause instanceof Error ? cause.message : "文件范围加载失败");
      })
      .finally(() => setLoadingSources(false));
    return () => controller.abort();
  }, [scopeUnitId, sourcePageNumber, sourceSearch]);

  useEffect(() => {
    if (!activeId) {
      setDetail(null);
      return;
    }
    if (detail?.conversation.id === activeId) return;
    const controller = new AbortController();
    getQuestionConversation(activeId, 0, controller.signal)
      .then((next) => {
        setDetail(next);
        const latest = next.runs.at(-1);
        if (latest) setSelectedRunId(latest.id);
      })
      .catch((cause) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(cause instanceof Error ? cause.message : "问答记录加载失败");
      });
    return () => controller.abort();
  }, [activeId]);

  useEffect(() => {
    const conversationId = detail?.conversation.id;
    const container = messagesRef.current;
    if (!conversationId || !container) return;
    const saved = messagePositions.current.get(conversationId);
    window.requestAnimationFrame(() => {
      if (saved && !saved.wasNearBottom) {
        container.scrollTop = saved.scrollTop;
        followLatestRef.current = false;
        setShowScrollBottom(true);
      } else {
        container.scrollTop = container.scrollHeight;
        followLatestRef.current = true;
        setShowScrollBottom(false);
      }
    });
  }, [detail?.conversation.id]);

  useEffect(() => {
    if (!liveRun || !followLatestRef.current) return;
    const frame = window.requestAnimationFrame(() => {
      const container = messagesRef.current;
      if (container) container.scrollTop = container.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [liveRun]);

  useEffect(() => () => {
    streamController.current?.abort();
    if (copyFeedbackTimer.current != null) {
      window.clearTimeout(copyFeedbackTimer.current);
    }
  }, []);

  useEffect(() => {
    if (pendingDeleteIds.length) deleteCancelRef.current?.focus();
  }, [pendingDeleteIds.length]);

  useEffect(() => {
    if (renamingConversationId) {
      window.requestAnimationFrame(() => {
        renameInputRef.current?.focus();
        renameInputRef.current?.select();
      });
    }
  }, [renamingConversationId]);

  const selectedRun = useMemo(
    () => detail?.runs.find((run) => run.id === selectedRunId) ?? detail?.runs.at(-1) ?? null,
    [detail, selectedRunId],
  );

  async function createConversation() {
    setError("");
    if (!scopeUnitId) throw new Error("当前没有可查询的村");
    const conversation = await createQuestionConversation(
      scopeUnitId,
      sourceItemId || undefined,
    );
    setConversationPageNumber(1);
    setConversationSearch("");
    setConversations((current) => [
      conversation,
      ...current.filter((item) => item.id !== conversation.id),
    ]);
    setConversationTotal((current) => current + 1);
    setActiveId(conversation.id);
    updateConversationLocation(conversation.id);
    setDetail({
      conversation,
      runs: [],
      run_total: 0,
      has_more_before: false,
    });
    setSelectedRunId("");
    return conversation;
  }

  async function submitQuestion(question: string, retryOfRunId?: string) {
    const normalized = question.trim();
    if (!normalized || busy) return;
    setBusy(true);
    setError("");
    let conversationId = activeId;
    try {
      if (!conversationId) {
        const conversation = await createConversation();
        conversationId = conversation.id;
      }
      const controller = new AbortController();
      streamController.current = controller;
      setLiveRun({
        id: "",
        question: normalized,
        startedAt: Date.now(),
        answerText: "",
        answer: {},
        status: "running",
        toolTrace: [],
        reasoningActive: false,
      });
      followLatestRef.current = true;
      setShowScrollBottom(false);
      await streamQuestionRun(
        conversationId,
        normalized,
        (event) => setLiveRun((current) => (
          current ? applyStreamEvent(current, event) : current
        )),
        controller.signal,
        retryOfRunId,
      );
      const [nextDetail] = await Promise.all([
        getQuestionConversation(conversationId, 0),
        refreshConversations(),
      ]);
      setDetail(nextDetail);
      const latest = nextDetail.runs.at(-1);
      if (latest) setSelectedRunId(latest.id);
      setLiveRun(null);
      if (followLatestRef.current) {
        window.requestAnimationFrame(() => {
          const container = messagesRef.current;
          if (container) container.scrollTop = container.scrollHeight;
        });
      }
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") return;
      setLiveRun((current) => current ? { ...current, status: "failed" } : current);
      setError(cause instanceof Error ? cause.message : "问题没有完成");
    } finally {
      streamController.current = null;
      setBusy(false);
    }
  }

  async function copyMessage(key: string, value: string) {
    try {
      if (!navigator.clipboard) throw new Error("当前浏览器不支持复制");
      await navigator.clipboard.writeText(value);
      setCopiedMessageKey(key);
      if (copyFeedbackTimer.current != null) {
        window.clearTimeout(copyFeedbackTimer.current);
      }
      copyFeedbackTimer.current = window.setTimeout(() => {
        setCopiedMessageKey("");
        copyFeedbackTimer.current = null;
      }, 1600);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "复制失败");
    }
  }

  async function stopCurrentRun() {
    if (!activeId || !busy) return;
    try {
      await stopQuestionRun(activeId);
      streamController.current?.abort();
      setLiveRun((current) => current ? { ...current, status: "stopped" } : current);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "停止查询失败");
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const question = String(data.get("question") ?? "");
    form.reset();
    resetQuestionInputHeight();
    await submitQuestion(question);
  }

  function resizeQuestionInput(textarea: HTMLTextAreaElement) {
    const maxHeight = 180;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, maxHeight)}px`;
    textarea.style.overflowY = textarea.scrollHeight > maxHeight ? "auto" : "hidden";
  }

  function resetQuestionInputHeight() {
    const textarea = questionInputRef.current;
    if (!textarea) return;
    textarea.style.height = "";
    textarea.style.overflowY = "hidden";
  }

  function handleQuestionInputKeyDown(
    event: ReactKeyboardEvent<HTMLTextAreaElement>,
  ) {
    const action = questionInputKeyAction({
      key: event.key,
      shiftKey: event.shiftKey,
      ctrlKey: event.ctrlKey,
      metaKey: event.metaKey,
      altKey: event.altKey,
      isComposing: event.nativeEvent.isComposing,
      keyCode: event.keyCode,
    });
    if (action === "submit") {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
      return;
    }
    if (action === "newline" && event.ctrlKey) {
      event.preventDefault();
      const textarea = event.currentTarget;
      textarea.setRangeText(
        "\n",
        textarea.selectionStart,
        textarea.selectionEnd,
        "end",
      );
      resizeQuestionInput(textarea);
    }
  }

  function saveMessagePosition() {
    const container = messagesRef.current;
    if (!activeId || !container) return;
    const distanceFromBottom = (
      container.scrollHeight - container.scrollTop - container.clientHeight
    );
    messagePositions.current.set(activeId, {
      scrollTop: container.scrollTop,
      wasNearBottom: distanceFromBottom < 120,
    });
  }

  function scrollMessagesToBottom() {
    const container = messagesRef.current;
    if (!container) return;
    const prefersReducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    container.scrollTo({
      top: container.scrollHeight,
      behavior: prefersReducedMotion ? "auto" : "smooth",
    });
    followLatestRef.current = true;
    setShowScrollBottom(false);
  }

  async function loadOlderRuns() {
    if (!activeId || !detail?.has_more_before || loadingOlder) return;
    const container = messagesRef.current;
    const previousHeight = container?.scrollHeight ?? 0;
    setLoadingOlder(true);
    try {
      const older = await getQuestionConversation(activeId, detail.runs.length);
      setDetail((current) => {
        if (!current || current.conversation.id !== activeId) return current;
        const knownIds = new Set(current.runs.map((run) => run.id));
        const prepended = older.runs.filter((run) => !knownIds.has(run.id));
        return {
          ...current,
          runs: [...prepended, ...current.runs],
          run_total: older.run_total,
          has_more_before: older.has_more_before,
        };
      });
      window.requestAnimationFrame(() => {
        if (!container) return;
        container.scrollTop += container.scrollHeight - previousHeight;
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "更早的问答记录加载失败");
    } finally {
      setLoadingOlder(false);
    }
  }

  function handleMessagesScroll() {
    const container = messagesRef.current;
    if (!container) return;
    const nearBottom = (
      container.scrollHeight - container.scrollTop - container.clientHeight
    ) < 120;
    followLatestRef.current = nearBottom;
    setShowScrollBottom(!nearBottom);
    if (container.scrollTop < 40 && detail?.has_more_before && !loadingOlder) {
      void loadOlderRuns();
    }
  }

  function requestConversationDelete(ids: string[]) {
    if (!ids.length || busy) return;
    setPendingDeleteIds(ids);
  }

  async function confirmConversationDelete() {
    if (!pendingDeleteIds.length || deletingConversations) return;
    setDeletingConversations(true);
    setError("");
    try {
      const deletingActive = pendingDeleteIds.includes(activeId);
      await deleteQuestionConversations(pendingDeleteIds);
      setPendingDeleteIds([]);
      setSelectedConversationIds(new Set());
      setOrganizingConversations(false);
      if (deletingActive) {
        setActiveId("");
        setDetail(null);
        setSelectedRunId("");
        updateConversationLocation("", true);
      }
      if (
        pendingDeleteIds.length >= conversations.length
        && conversationPageNumber > 1
      ) {
        setConversationPageNumber((current) => current - 1);
      } else {
        const next = await refreshConversations();
        if (deletingActive) {
          const nextId = next.items[0]?.id ?? "";
          setActiveId(nextId);
          updateConversationLocation(nextId, true);
        }
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "清理会话失败");
    } finally {
      setDeletingConversations(false);
    }
  }

  function toggleConversationSelection(conversationId: string) {
    setSelectedConversationIds((current) => {
      const next = new Set(current);
      if (next.has(conversationId)) next.delete(conversationId);
      else next.add(conversationId);
      return next;
    });
  }

  async function submitConversationRename(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!renamingConversationId || renamingConversation) return;
    const data = new FormData(event.currentTarget);
    const title = String(data.get("conversation_title") ?? "").trim();
    if (!title) {
      setError("会话标题不能为空");
      renameInputRef.current?.focus();
      return;
    }
    setRenamingConversation(true);
    setError("");
    try {
      const renamed = await renameQuestionConversation(
        renamingConversationId,
        title,
      );
      setConversations((current) => current.map((conversation) => (
        conversation.id === renamed.id ? renamed : conversation
      )));
      setDetail((current) => (
        current?.conversation.id === renamed.id
          ? { ...current, conversation: renamed }
          : current
      ));
      setRenamingConversationId("");
      setOpenConversationMenuId("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "重命名会话失败");
    } finally {
      setRenamingConversation(false);
    }
  }

  function changeScope(nextScopeUnitId: string) {
    if (busy || nextScopeUnitId === scopeUnitId) return;
    setScopeUnitId(nextScopeUnitId);
    setSourceItemId("");
    setSourcePageNumber(1);
    setSourceSearch("");
    setConversationPageNumber(1);
    setConversationSearch("");
    setSelectedConversationIds(new Set());
    setOrganizingConversations(false);
    setRailTab("sources");
    setLiveRun(null);
    setSelectedRunId("");
    setActiveId("");
    updateConversationLocation("", true);
    setDetail(null);
  }

  function changeSource(nextSourceItemId: string) {
    if (busy || nextSourceItemId === sourceItemId) return;
    setSourceItemId(nextSourceItemId);
    setConversationPageNumber(1);
    setConversationSearch("");
    setSelectedConversationIds(new Set());
    setOrganizingConversations(false);
    setLiveRun(null);
    setSelectedRunId("");
    setActiveId("");
    updateConversationLocation("", true);
    setDetail(null);
  }

  const selectedSourceName = (
    sources.items.find((source) => source.id === sourceItemId)?.file_name
    ?? detail?.conversation.source_name
    ?? ""
  );
  const renameTarget = conversations.find(
    (conversation) => conversation.id === renamingConversationId,
  );
  const workspaceClassName = [
    "question-workspace",
    ledgerCollapsed ? "question-workspace--ledger-collapsed" : "",
    evidenceCollapsed ? "question-workspace--evidence-collapsed" : "",
  ].filter(Boolean).join(" ");

  return (
    <section className={workspaceClassName}>
      {!ledgerCollapsed ? (
      <aside className="question-ledger" aria-label="问数会话">
        <header>
          <div>
            <span>问数账簿</span>
            <strong>{conversationTotal} 个会话</strong>
          </div>
          <div className="question-ledger__header-actions">
            <button
              aria-label="收起会话栏"
              className="question-panel-collapse"
              onClick={() => setLedgerCollapsed(true)}
              title="收起会话栏"
              type="button"
            >
              ‹
            </button>
            <button
              aria-label="新建问数会话"
              disabled={busy}
              onClick={() => void createConversation().catch((cause: unknown) => {
                setError(cause instanceof Error ? cause.message : "新建会话失败");
              })}
              type="button"
            >
              ＋
            </button>
          </div>
        </header>
        <div className="question-ledger__tools">
          <form
            key={conversationSearch}
            onSubmit={(event) => {
              event.preventDefault();
              const data = new FormData(event.currentTarget);
              setConversationSearch(String(data.get("conversation_search") ?? "").trim());
              setConversationPageNumber(1);
            }}
          >
            <label className="sr-only" htmlFor="conversation-search">搜索会话</label>
            <input
              autoComplete="off"
              defaultValue={conversationSearch}
              id="conversation-search"
              name="conversation_search"
              placeholder="搜索会话…"
              type="search"
            />
            <button type="submit">查找</button>
          </form>
          <button
            aria-pressed={organizingConversations}
            disabled={busy || !conversations.length}
            onClick={() => {
              setOrganizingConversations((current) => !current);
              setSelectedConversationIds(new Set());
            }}
            type="button"
          >
            {organizingConversations ? "完成" : "整理"}
          </button>
        </div>
        {organizingConversations ? (
          <div className="question-ledger__batch">
            <button
              disabled={!selectedConversationIds.size}
              onClick={() => requestConversationDelete([...selectedConversationIds])}
              type="button"
            >
              清理所选（{selectedConversationIds.size}）
            </button>
            <button
              onClick={() => {
                setSelectedConversationIds(
                  selectedConversationIds.size === conversations.length
                    ? new Set()
                    : new Set(conversations.map((conversation) => conversation.id)),
                );
              }}
              type="button"
            >
              {selectedConversationIds.size === conversations.length ? "取消全选" : "选择本页"}
            </button>
          </div>
        ) : null}
        {renamingConversationId && renameTarget ? (
          <form
            className="question-rename-form"
            onSubmit={submitConversationRename}
          >
            <label htmlFor="conversation-title">重命名会话</label>
            <input
              autoComplete="off"
              defaultValue={renameTarget.title}
              id="conversation-title"
              maxLength={240}
              name="conversation_title"
              ref={renameInputRef}
              required
            />
            <div>
              <button
                disabled={renamingConversation}
                onClick={() => setRenamingConversationId("")}
                type="button"
              >
                取消
              </button>
              <button disabled={renamingConversation} type="submit">
                {renamingConversation ? "正在保存…" : "保存"}
              </button>
            </div>
          </form>
        ) : null}
        {pendingDeleteIds.length ? (
          <div
            aria-labelledby="conversation-delete-title"
            className="question-delete-confirm"
            role="alertdialog"
          >
            <strong id="conversation-delete-title">
              清理 {pendingDeleteIds.length} 个会话？
            </strong>
            <p>会话将从账簿隐藏，查询凭据仍保留用于审计。</p>
            <div>
              <button
                disabled={deletingConversations}
                onClick={() => setPendingDeleteIds([])}
                ref={deleteCancelRef}
                type="button"
              >
                取消
              </button>
              <button
                disabled={deletingConversations}
                onClick={() => void confirmConversationDelete()}
                type="button"
              >
                {deletingConversations ? "正在清理…" : "确认清理"}
              </button>
            </div>
          </div>
        ) : null}
        {loading ? <p className="question-ledger__empty">正在载入会话…</p> : null}
        {!loading && !conversations.length ? (
          <p className="question-ledger__empty">还没有问数记录。直接在右侧输入问题即可开始。</p>
        ) : null}
        <ol>
          {conversations.map((conversation) => (
            <li key={conversation.id}>
              <div className={conversation.id === activeId ? "active" : undefined}>
                {organizingConversations ? (
                  <label>
                    <input
                      checked={selectedConversationIds.has(conversation.id)}
                      onChange={() => toggleConversationSelection(conversation.id)}
                      type="checkbox"
                    />
                    <span className="sr-only">选择{conversation.title}</span>
                  </label>
                ) : null}
                <button
                  className="question-ledger__select"
                  disabled={busy}
                  onClick={() => {
                    saveMessagePosition();
                    setActiveId(conversation.id);
                    updateConversationLocation(conversation.id);
                    setOpenConversationMenuId("");
                    setLiveRun(null);
                  }}
                  type="button"
                >
                  <span>{conversation.title}</span>
                  <small>
                    {conversation.scope_name} · {conversation.run_count} 次
                  </small>
                  <time>{formatConversationTime(conversation.updated_at)}</time>
                </button>
                {!organizingConversations ? (
                  <div className="question-conversation-menu">
                    <button
                      aria-expanded={openConversationMenuId === conversation.id}
                      aria-haspopup="menu"
                      aria-label={`更多操作：${conversation.title}`}
                      className="question-conversation-menu__trigger"
                      disabled={busy}
                      onClick={() => setOpenConversationMenuId((current) => (
                        current === conversation.id ? "" : conversation.id
                      ))}
                      title="更多操作"
                      type="button"
                    >
                      ⋯
                    </button>
                    {openConversationMenuId === conversation.id ? (
                      <div
                        aria-label={`${conversation.title}的操作`}
                        className="question-conversation-menu__items"
                        role="menu"
                      >
                        <button
                          onClick={() => {
                            setRenamingConversationId(conversation.id);
                            setOpenConversationMenuId("");
                          }}
                          role="menuitem"
                          type="button"
                        >
                          重命名
                        </button>
                        <button
                          onClick={() => {
                            requestConversationDelete([conversation.id]);
                            setOpenConversationMenuId("");
                          }}
                          role="menuitem"
                          type="button"
                        >
                          清理
                        </button>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
        <nav aria-label="会话分页" className="question-ledger__pagination">
          <button
            disabled={loading || conversationPageNumber <= 1}
            onClick={() => setConversationPageNumber((current) => current - 1)}
            type="button"
          >
            上一页
          </button>
          <span>{conversationPageNumber} / {conversationPageCount}</span>
          <button
            disabled={loading || conversationPageNumber >= conversationPageCount}
            onClick={() => setConversationPageNumber((current) => current + 1)}
            type="button"
          >
            下一页
          </button>
        </nav>
        <footer>
          <i aria-hidden="true" />
          会话只读取当前授权村情
        </footer>
      </aside>
      ) : null}

      <section className="question-dialogue">
        <header>
          <div>
            <span>可信问数</span>
            <h2>{detail?.conversation.title ?? "从已审核的村情数据里问"}</h2>
          </div>
          {ledgerCollapsed || evidenceCollapsed ? (
            <div className="question-panel-toggles">
              {ledgerCollapsed ? (
                <button onClick={() => setLedgerCollapsed(false)} type="button">
                  展开会话
                </button>
              ) : null}
              {evidenceCollapsed ? (
                <button onClick={() => setEvidenceCollapsed(false)} type="button">
                  展开文件范围
                </button>
              ) : null}
            </div>
          ) : null}
          <div className="question-scope">
            <label htmlFor="question-scope-select">查询范围</label>
            {isTenantAdmin ? (
              <select
                disabled={busy}
                id="question-scope-select"
                onChange={(event) => changeScope(event.target.value)}
                value={scopeUnitId}
              >
                {scopeOptions.map((option) => (
                  <option key={option.id} value={option.id}>{option.name}</option>
                ))}
              </select>
            ) : (
              <strong>{selectedScope?.name ?? currentUser.scope_unit_name ?? "当前村"}</strong>
            )}
            <small>
              {sourceItemId
                ? `已限定文件：${selectedSourceName || "所选文件"}`
                : "智能理解问题 · 按当前范围作答"}
            </small>
          </div>
        </header>

        <div
          className="question-messages"
          onScroll={handleMessagesScroll}
          ref={messagesRef}
        >
          {loadingOlder ? (
            <p aria-live="polite" className="question-history-status">正在载入更早记录…</p>
          ) : null}
          {!loadingOlder && detail?.has_more_before ? (
            <button
              className="question-history-more"
              onClick={() => void loadOlderRuns()}
              type="button"
            >
              载入更早记录
            </button>
          ) : null}
          {!detail?.runs.length && !liveRun ? (
            <div className="question-welcome">
              <span className="question-welcome__seal">问</span>
              <h3>你想从村情台账里知道什么？</h3>
              <p>可以问人数、名单、分组、比较或某条记录的来源。</p>
              <div>
                {suggestedQuestions.map((question) => (
                  <button
                    disabled={busy}
                    key={question}
                    onClick={() => void submitQuestion(question)}
                    type="button"
                  >
                    {question}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
          {detail?.runs.map((run, index) => (
            <StoredTurn
              canRetry={
                !busy
                && index === detail.runs.length - 1
                && run.status !== "running"
              }
              copiedAction={
                copiedMessageKey === `${run.id}:question`
                  ? "question"
                  : copiedMessageKey === `${run.id}:answer`
                    ? "answer"
                    : ""
              }
              key={run.id}
              onCopyAnswer={() => void copyMessage(
                `${run.id}:answer`,
                formatAnswerForClipboard(run),
              )}
              onCopyQuestion={() => void copyMessage(
                `${run.id}:question`,
                run.question,
              )}
              onInspect={() => {
                setSelectedRunId(run.id);
                setRailTab("evidence");
              }}
              onRetry={() => void submitQuestion(run.question, run.id)}
              run={run}
              selected={selectedRun?.id === run.id}
            />
          ))}
          {liveRun ? <LiveTurn run={liveRun} /> : null}
        </div>
        {showScrollBottom ? (
          <button
            aria-label="回到最新回答"
            className="question-scroll-bottom"
            onClick={scrollMessagesToBottom}
            title="回到最新回答"
            type="button"
          >
            ↓
            <span>回到最新</span>
          </button>
        ) : null}

        <form className="question-composer" onSubmit={submit}>
          {error ? <p className="alert" role="alert">{error}</p> : null}
          <div className="question-composer__surface">
            <label htmlFor="question-input">继续提问</label>
            <textarea
              disabled={busy}
              id="question-input"
              minLength={2}
              name="question"
              onInput={(event) => resizeQuestionInput(event.currentTarget)}
              onKeyDown={handleQuestionInputKeyDown}
              placeholder="例如：去年各村组的低保人数分别是多少？"
              ref={questionInputRef}
              required
              rows={1}
            />
            <div className="question-composer__actions">
              <small>Enter 查询 · Shift/Ctrl+Enter 换行</small>
              {busy ? (
                <button
                  aria-label="停止查询"
                  className="question-composer__submit question-composer__submit--stop"
                  onClick={() => void stopCurrentRun()}
                  title="停止查询"
                  type="button"
                >
                  <i aria-hidden="true" />
                </button>
              ) : (
                <button
                  aria-label="查询村情"
                  className="question-composer__submit"
                  title="查询村情"
                  type="submit"
                >
                  <span aria-hidden="true">↑</span>
                </button>
              )}
            </div>
          </div>
        </form>
      </section>

      {!evidenceCollapsed ? (
      <QuestionScopeRail
        activeTab={railTab}
        loadingSources={loadingSources}
        onPageChange={setSourcePageNumber}
        onSearch={(value) => {
          setSourceSearch(value);
          setSourcePageNumber(1);
        }}
        onCollapse={() => setEvidenceCollapsed(true)}
        onSelectSource={changeSource}
        onTabChange={setRailTab}
        run={selectedRun}
        selectedSourceName={selectedSourceName}
        sourceItemId={sourceItemId}
        sources={sources}
        scopeName={detail?.conversation.scope_name ?? selectedScope?.name ?? ""}
      />
      ) : null}
    </section>
  );
}
