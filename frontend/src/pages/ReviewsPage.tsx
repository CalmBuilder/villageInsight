import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  acceptReviewProposal,
  getFields,
  getReview,
  getReviewQueue,
  rejectReviewProposal,
  type GovernanceFieldResolution,
  type ReviewFieldEvidence,
  type ReviewQueueItem,
  type SemanticField,
} from "../lib/api";
import { initialResolution } from "./reviewResolution";

const reasonLabels: Record<string, string> = {
  NO_TEMPLATE: "未命中模板",
  MISSING_HEADERS: "模板字段缺失",
  SEMANTIC_REVIEW: "包含新语义或歧义",
  MODEL_REVIEW_REQUIRED: "识别结果要求复核",
  POLICY_REVIEW: "未满足自动通行策略",
  HERMES_LOW_CONFIDENCE: "二次判定后仍为低置信",
  HERMES_SEMANTIC_CONFLICT: "二次判定后仍有语义冲突",
};

const domainOptions = [
  { code: "population", label: "人口户籍" },
  { code: "governance", label: "村务治理" },
  { code: "social_assistance", label: "民政救助" },
  { code: "agriculture", label: "农业生产" },
  { code: "party_affairs", label: "党建" },
  { code: "land", label: "土地资产" },
  { code: "employment", label: "就业务工" },
  { code: "finance", label: "财务补贴" },
  { code: "other", label: "其他台账" },
] as const;

type DomainCode = (typeof domainOptions)[number]["code"];

const recordTypeOptions: Record<
  DomainCode,
  Array<{ code: string; label: string }>
> = {
  population: [
    { code: "person", label: "人员" },
    { code: "household", label: "家庭/户" },
  ],
  governance: [
    { code: "village_staff", label: "村干部" },
    { code: "dispute", label: "矛盾纠纷" },
    { code: "event", label: "村务事件" },
  ],
  social_assistance: [
    { code: "beneficiary", label: "救助对象" },
    { code: "benefit_adjustment", label: "待遇调整" },
  ],
  agriculture: [
    { code: "crop_registration", label: "农作物登记" },
    { code: "subsidy", label: "农业补贴" },
    { code: "land_record", label: "耕地记录" },
  ],
  party_affairs: [
    { code: "party_member", label: "党员" },
    { code: "party_activity", label: "党建活动" },
  ],
  land: [
    { code: "parcel", label: "地块" },
    { code: "asset", label: "集体资产" },
  ],
  employment: [
    { code: "worker", label: "务工人员" },
    { code: "employment_record", label: "就业记录" },
  ],
  finance: [
    { code: "payment", label: "发放明细" },
    { code: "income_expense", label: "收支记录" },
  ],
  other: [{ code: "generic_record", label: "通用记录" }],
};

const grainOptions = [
  { code: "one_row_per_person", label: "每行一人" },
  { code: "one_row_per_household", label: "每行一户" },
  { code: "one_row_per_payment", label: "每行一笔发放" },
  { code: "one_row_per_parcel", label: "每行一块地" },
  { code: "one_row_per_record", label: "每行一条记录" },
] as const;

const typeLabels: Record<string, string> = {
  text: "文本",
  integer: "整数",
  decimal: "小数",
  boolean: "是/否",
  date: "日期",
  datetime: "日期时间",
};

const candidateReasonLabels: Record<string, string> = {
  full_header_path: "完整表头一致",
  published_alias: "已发布别名一致",
  normalized_base_alias: "基础名称一致",
  semantic_label_overlap: "名称语义接近",
  data_type: "数据类型兼容",
  region_context: "业务上下文一致",
  role: "字段角色一致",
  role_variant_candidate: "可能是角色变体",
};

const FIELD_PAGE_SIZE = 30;
const REVIEW_PAGE_SIZE = 20;

function GovernanceHelpDialog({ onClose }: { onClose: () => void }) {
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    closeButtonRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  return (
    <div
      className="governance-help-layer"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      role="presentation"
    >
      <section
        aria-labelledby="governance-help-title"
        aria-modal="true"
        className="governance-help"
        role="dialog"
      >
        <header>
          <div>
            <span>治理说明</span>
            <h2 id="governance-help-title">把来源列确认成可复用的标准含义</h2>
          </div>
          <button
            aria-label="关闭数据治理说明"
            onClick={onClose}
            ref={closeButtonRef}
            type="button"
          >
            ×
          </button>
        </header>
        <p className="governance-help__lead">
          待治理不代表导入失败。系统已经保留原始文件；你要确认的是这列以后如何参与查询，
          以及同类文件能否直接复用。
        </p>
        <ol className="governance-help__steps">
          <li><span>1</span><p><strong>先认来源</strong>核对文件、Sheet、列坐标和完整表头。</p></li>
          <li><span>2</span><p><strong>再定含义</strong>不要只看 AI 百分比，要以原表内容为准。</p></li>
          <li><span>3</span><p><strong>最后发布</strong>全部列有方案后，发布并重新构建当前文件。</p></li>
        </ol>
        <div className="governance-help__receipts">
          <article>
            <span>复用已有字段</span>
            <strong>例：“户别” → 户别类型</strong>
            <p>含义已经存在。选中正确标准字段，可记住别名和完整表头路径。</p>
            <small>结果：更新字段版本；以后同类表可直接匹配。</small>
          </article>
          <article>
            <span>发布新字段</span>
            <strong>例：首次出现的本地业务分类</strong>
            <p>目录中确实没有，但该列需要长期查询时，填写稳定编码、名称和数据类型。</p>
            <small>结果：新增不可变字段版本；不是给临时备注随手建字段。</small>
          </article>
          <article>
            <span>忽略该列</span>
            <strong>例：“序号”或脱敏展示辅助列</strong>
            <p>不建立标准语义映射；必须说明原因，可选择仅当前文件或同类表都记住。</p>
            <small>结果：原始单元格仍完整保留，不会被删除。</small>
          </article>
        </div>
        <aside className="governance-help__warning">
          <strong>看到低置信候选时怎么办？</strong>
          <p>
            10% 的地址候选只说明数据类型相容，不说明它真的是地址。无法从原表确认时，
            不要发布错误字段；无表头列一律不进入正式语义映射，结构本身不可靠时应驳回建议。
          </p>
        </aside>
        <footer>
          <p>
            “已有处理方案”包括系统预填方案，只有点击“发布字段并重新入库”才会正式生效。
          </p>
          <button className="primary-button" onClick={onClose} type="button">
            我知道怎么处理了
          </button>
        </footer>
      </section>
    </div>
  );
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function isComplete(resolution: GovernanceFieldResolution | undefined) {
  if (!resolution) return false;
  if (resolution.mode === "reuse_existing") {
    return Boolean(resolution.semantic_field_code && resolution.expected_field_version);
  }
  if (resolution.mode === "create_new") {
    return Boolean(
      resolution.new_field_code
      && resolution.new_field_name
      && resolution.new_field_layer
      && resolution.new_field_data_type,
    );
  }
  return Boolean(resolution.ignore_scope && resolution.ignore_reason?.trim());
}

function resolutionSummary(
  evidence: ReviewFieldEvidence,
  resolution: GovernanceFieldResolution,
  fields: SemanticField[],
) {
  if (resolution.mode === "ignore") {
    return resolution.ignore_scope === "context"
      ? `同类表遇到“${evidence.header_path.join(" / ")}”时自动忽略语义映射，原始值仍保留`
      : "仅在本文件中忽略语义映射，原始值仍保留";
  }
  const code = resolution.mode === "create_new"
    ? resolution.new_field_code
    : resolution.semantic_field_code;
  const field = fields.find((item) => item.code === code);
  const additions = [
    resolution.learn_path ? `表头路径“${evidence.header_path.join(" / ")}”` : null,
    resolution.learn_alias ? `别名“${resolution.learn_alias}”` : null,
    resolution.role ? `角色“${resolution.role}”` : null,
  ].filter(Boolean);
  return `${resolution.mode === "create_new" ? "发布新字段" : "复用并更新"} `
    + `${code ?? "尚未选择"}${field ? `（${field.name}）` : ""}`
    + `${additions.length ? `；沉淀${additions.join("、")}` : ""}`;
}

function FieldResolutionEditor({
  evidence,
  fileName,
  fields,
  resolution,
  onChange,
}: {
  evidence: ReviewFieldEvidence;
  fileName: string;
  fields: SemanticField[];
  resolution: GovernanceFieldResolution;
  onChange: (next: GovernanceFieldResolution) => void;
}) {
  const update = (values: Partial<GovernanceFieldResolution>) => {
    onChange({ ...resolution, ...values });
  };
  return (
    <section className="field-resolution-editor" aria-label={`${evidence.leaf_header}治理`}>
      <nav className="field-evidence-path" aria-label="字段来源">
        <span title={fileName}>{fileName}</span>
        <i aria-hidden="true">›</i>
        <span>Sheet：{evidence.sheet_name}</span>
        <i aria-hidden="true">›</i>
        <strong>{evidence.column_coordinate} 列</strong>
      </nav>
      <header>
        <div>
          <span>来源表头</span>
          <h3>{evidence.header_path.join(" / ")}</h3>
          <p>
            父级表头：{evidence.parent_path.join(" / ") || "无"}
            {" · "}
            观测类型：{typeLabels[evidence.observed_data_type ?? ""] ?? "未识别"}
          </p>
        </div>
        <em>
          AI 建议
          {" "}
          {Math.round((evidence.hermes_suggestion.confidence ?? 0) * 100)}%
        </em>
      </header>

      {evidence.candidates.length ? (
        <section className="candidate-evidence">
          <span>已发布候选 · 点击即可选为沉淀目标</span>
          {evidence.candidates.slice(0, 3).map((candidate) => (
            <button
              aria-pressed={
                resolution.mode === "reuse_existing"
                && resolution.semantic_field_code === candidate.semantic_field_code
              }
              key={candidate.semantic_field_code}
              onClick={() => update({
                mode: "reuse_existing",
                semantic_field_code: candidate.semantic_field_code,
                expected_field_version: candidate.semantic_field_version,
                ignore_scope: null,
                ignore_reason: null,
              })}
              type="button"
            >
              <strong>{candidate.semantic_field_code}</strong>
              <small>
                v{candidate.semantic_field_version}
                {" · "}
                {Math.round(candidate.score_basis_points / 100)}%
                {" · "}
                {candidate.reasons.map((reason) => (
                  candidateReasonLabels[reason] ?? reason
                )).join("、")}
              </small>
              <em>选用</em>
            </button>
          ))}
        </section>
      ) : (
        <p className="field-no-candidate">没有可直接复用的已发布字段。</p>
      )}

      <fieldset className="resolution-mode">
        <legend>这列最终怎么处理？</legend>
        {([
          ["reuse_existing", "复用已有字段"],
          ["create_new", "发布新字段"],
          ["ignore", "忽略该列"],
        ] as const).map(([mode, label]) => (
          <label key={mode}>
            <input
              checked={resolution.mode === mode}
              name={`mode-${evidence.source_column_id}`}
              onChange={() => update({
                mode,
                ignore_scope: mode === "ignore" ? "file" : null,
                ignore_reason: mode === "ignore" ? "" : null,
              })}
              type="radio"
            />
            <span>{label}</span>
          </label>
        ))}
      </fieldset>

      {resolution.mode === "reuse_existing" ? (
        <div className="resolution-fields">
          <label>
            沉淀为标准字段
            <select
              value={resolution.semantic_field_code ?? ""}
              onChange={(event) => {
                const field = fields.find((item) => item.code === event.target.value);
                update({
                  semantic_field_code: field?.code ?? null,
                  expected_field_version: field?.published_version ?? null,
                });
              }}
            >
              <option value="">请选择已发布字段</option>
              {fields.filter((field) => field.published_version).map((field) => (
                <option key={field.code} value={field.code}>
                  {field.name} · {field.code} · v{field.published_version}
                </option>
              ))}
            </select>
          </label>
          <details className="resolution-more">
            <summary>更多沉淀规则：别名、字段角色和完整表头路径</summary>
            <div>
              <label>
                记住为别名（可选）
                <input
                  value={resolution.learn_alias ?? ""}
                  onChange={(event) => update({
                    learn_alias: event.target.value || null,
                  })}
                  placeholder={`例如：${evidence.leaf_header}`}
                />
              </label>
              <label>
                字段角色（可选）
                <input
                  value={resolution.role ?? ""}
                  onChange={(event) => update({ role: event.target.value || null })}
                  placeholder="例如：户主、配偶、联系人"
                />
              </label>
              <label className="check-line">
                <input
                  checked={resolution.learn_path}
                  onChange={(event) => update({ learn_path: event.target.checked })}
                  type="checkbox"
                />
                记住完整表头路径及父级表头
              </label>
            </div>
          </details>
        </div>
      ) : null}

      {resolution.mode === "create_new" ? (
        <div className="resolution-fields resolution-fields--new">
          <label>
            新标准字段编码
            <input
              value={resolution.new_field_code ?? ""}
              onChange={(event) => update({ new_field_code: event.target.value || null })}
              placeholder="例如：population.household_head_name"
              spellCheck={false}
            />
          </label>
          <label>
            标准字段名称
            <input
              value={resolution.new_field_name ?? ""}
              onChange={(event) => update({ new_field_name: event.target.value || null })}
              placeholder={`例如：${evidence.leaf_header}`}
            />
          </label>
          <label>
            字段层级
            <select
              value={resolution.new_field_layer ?? "domain"}
              onChange={(event) => update({
                new_field_layer: event.target.value as "base" | "domain",
              })}
            >
              <option value="base">基础字段</option>
              <option value="domain">业务域字段</option>
            </select>
          </label>
          <label>
            数据类型
            <select
              value={resolution.new_field_data_type ?? "text"}
              onChange={(event) => update({
                new_field_data_type: event.target.value,
              })}
            >
              {Object.entries(typeLabels).map(([code, label]) => (
                <option key={code} value={code}>{label}</option>
              ))}
            </select>
          </label>
          <label>
            单位（可选）
            <input
              value={resolution.unit ?? ""}
              onChange={(event) => update({ unit: event.target.value || null })}
              placeholder="例如：元、亩、人"
            />
          </label>
          <label>
            字段角色（可选）
            <input
              value={resolution.role ?? ""}
              onChange={(event) => update({ role: event.target.value || null })}
              placeholder="例如：户主、联系人"
            />
          </label>
        </div>
      ) : null}

      {resolution.mode === "ignore" ? (
        <div className="resolution-fields">
          <label>
            忽略范围
            <select
              value={resolution.ignore_scope ?? "file"}
              onChange={(event) => update({
                ignore_scope: event.target.value as "file" | "context",
              })}
            >
              <option value="file">仅当前文件</option>
              <option value="context">同类业务表也记住</option>
            </select>
          </label>
          <label>
            忽略原因
            <input
              value={resolution.ignore_reason ?? ""}
              onChange={(event) => update({
                ignore_reason: event.target.value || null,
              })}
              placeholder="例如：说明文字、重复展示列、临时计算列"
            />
          </label>
          <p>忽略只影响标准字段映射，原始单元格仍完整保留。</p>
        </div>
      ) : null}

      <aside className="resolution-result">
        <span>确认后将沉淀</span>
        <strong>{resolutionSummary(evidence, resolution, fields)}</strong>
      </aside>
    </section>
  );
}

export function ReviewsPage() {
  const [reviews, setReviews] = useState<ReviewQueueItem[]>([]);
  const [reviewTotal, setReviewTotal] = useState(0);
  const [reviewOffset, setReviewOffset] = useState(0);
  const [selectedDetail, setSelectedDetail] = useState<ReviewQueueItem | null>(null);
  const [fields, setFields] = useState<SemanticField[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [activeFieldId, setActiveFieldId] = useState("");
  const [fieldPage, setFieldPage] = useState(0);
  const [helpOpen, setHelpOpen] = useState(false);
  const [fieldSearch, setFieldSearch] = useState("");
  const [tenantFilter, setTenantFilter] = useState("");
  const [villageFilter, setVillageFilter] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [queueOpen, setQueueOpen] = useState(false);
  const [domain, setDomain] = useState<DomainCode>("population");
  const [recordType, setRecordType] = useState("person");
  const [recordGrain, setRecordGrain] = useState("one_row_per_person");
  const [resolutions, setResolutions] = useState<
    Record<string, GovernanceFieldResolution>
  >({});

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const [nextReviews, nextFields] = await Promise.all([
        getReviewQueue({
          tenantId: tenantFilter || undefined,
          administrativeUnitId: villageFilter || undefined,
          limit: REVIEW_PAGE_SIZE,
          offset: reviewOffset,
        }, signal),
        getFields(signal),
      ]);
      setReviews(nextReviews.items);
      setReviewTotal(nextReviews.total);
      setFields(nextFields);
      setSelectedId((current) =>
        current && nextReviews.items.some((review) => review.proposal_id === current)
          ? current
          : (nextReviews.items[0]?.proposal_id ?? ""),
      );
      setError("");
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") return;
      setError(cause instanceof Error ? cause.message : "治理队列加载失败");
    }
  }, [reviewOffset, tenantFilter, villageFilter]);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  const sourceOptions = useMemo(() => {
    const tenants = new Map<string, string>();
    const villages = new Map<string, { name: string; tenantId: string }>();
    for (const review of reviews) {
      tenants.set(review.tenant_id, review.tenant_name);
      villages.set(review.administrative_unit_id, {
        name: review.administrative_unit_name,
        tenantId: review.tenant_id,
      });
    }
    return {
      tenants: Array.from(tenants, ([id, name]) => ({ id, name })),
      villages: Array.from(villages, ([id, value]) => ({ id, ...value })),
    };
  }, [reviews]);
  const visibleReviews = reviews;
  const selectedSummary =
    visibleReviews.find((review) => review.proposal_id === selectedId)
    ?? visibleReviews[0];
  const selectedProposalId = selectedSummary?.proposal_id;
  const selected = selectedDetail?.proposal_id === selectedSummary?.proposal_id
    ? selectedDetail
    : undefined;

  useEffect(() => {
    if (!selectedProposalId) {
      setSelectedDetail(null);
      return;
    }
    const controller = new AbortController();
    setSelectedDetail(null);
    void getReview(selectedProposalId, controller.signal)
      .then(setSelectedDetail)
      .catch((cause) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(cause instanceof Error ? cause.message : "治理详情加载失败");
      });
    return () => controller.abort();
  }, [selectedProposalId]);

  useEffect(() => {
    if (!selected) return;
    const suggestedDomain = selected.matched_domain
      ?? selected.proposal.template_suggestion?.domain
      ?? selected.proposal.field_decisions
        ?.map((decision) => (
          decision.proposed_field_code ?? decision.semantic_field_code ?? ""
        ).split(".")[0])
        .find((code): code is DomainCode =>
          domainOptions.some((option) => option.code === code))
      ?? "population";
    const nextDomain = domainOptions.some((option) => option.code === suggestedDomain)
      ? suggestedDomain as DomainCode
      : "other";
    setDomain(nextDomain);
    setRecordType(
      selected.matched_record_type
      ?? selected.proposal.template_suggestion?.record_type
      ?? recordTypeOptions[nextDomain][0].code,
    );
    setRecordGrain(
      selected.matched_record_grain
      ?? selected.proposal.record_grain?.value
      ?? "one_row_per_record",
    );
    setResolutions(Object.fromEntries(
      selected.field_evidence.map((evidence) => [
        evidence.source_column_id,
        initialResolution(evidence, fields),
      ]),
    ));
    setActiveFieldId(selected.field_evidence[0]?.source_column_id ?? "");
    setFieldPage(0);
    setFieldSearch("");
  }, [fields, selected]);

  const searchedFields = useMemo(() => {
    if (!selected) return [];
    const needle = fieldSearch.trim().toLocaleLowerCase("zh-CN");
    if (!needle) return selected.field_evidence;
    return selected.field_evidence.filter((evidence) =>
      `${evidence.sheet_name} ${evidence.column_coordinate} ${evidence.header_path.join(" ")}`
        .toLocaleLowerCase("zh-CN")
        .includes(needle));
  }, [fieldSearch, selected]);
  const pageCount = Math.max(1, Math.ceil(searchedFields.length / FIELD_PAGE_SIZE));
  const pagedFields = searchedFields.slice(
    fieldPage * FIELD_PAGE_SIZE,
    (fieldPage + 1) * FIELD_PAGE_SIZE,
  );
  const activeEvidence = selected?.field_evidence.find(
    (evidence) => evidence.source_column_id === activeFieldId,
  ) ?? selected?.field_evidence[0];
  const completedCount = selected?.field_evidence.filter(
    (evidence) => isComplete(resolutions[evidence.source_column_id]),
  ).length ?? 0;
  const totalCount = selected?.field_evidence.length ?? 0;
  const allComplete = totalCount === completedCount;
  const activeFieldIndex = activeEvidence
    ? searchedFields.findIndex(
      (evidence) => evidence.source_column_id === activeEvidence.source_column_id,
    )
    : -1;

  function moveField(direction: -1 | 1) {
    const nextIndex = activeFieldIndex + direction;
    const next = searchedFields[nextIndex];
    if (!next) return;
    setActiveFieldId(next.source_column_id);
    setFieldPage(Math.floor(nextIndex / FIELD_PAGE_SIZE));
  }

  return (
    <section className="review-workspace review-workspace--field-governance">
      <header className="review-summary">
        <div>
          <span className="eyebrow">FIELD GOVERNANCE</span>
          <div className="review-summary__title">
            <h2>逐列确认，下一份表直接复用</h2>
            <button
              aria-label="查看数据治理说明"
              className="governance-help-trigger"
              onClick={() => setHelpOpen(true)}
              title="数据治理是什么意思？"
              type="button"
            >
              ?
            </button>
          </div>
          <p>先看清文件、Sheet、列坐标和完整表头，再决定沉淀成哪个标准字段。</p>
        </div>
        <div className="review-summary__status">
          <span>{reviewTotal} 份文件待治理</span>
          <strong>{completedCount}/{totalCount || "—"} 列已有处理方案</strong>
        </div>
      </header>
      {helpOpen ? <GovernanceHelpDialog onClose={() => setHelpOpen(false)} /> : null}
      {error ? <p className="alert" role="alert">{error}</p> : null}
      <div className="review-scope-filter" aria-label="治理来源筛选">
        <span>治理范围</span>
        <label>
          <span>租户</span>
          <select
            value={tenantFilter}
            onChange={(event) => {
              setReviewOffset(0);
              setTenantFilter(event.target.value);
              setVillageFilter("");
            }}
          >
            <option value="">全部业务租户</option>
            {sourceOptions.tenants.map((tenant) => (
              <option key={tenant.id} value={tenant.id}>{tenant.name}</option>
            ))}
          </select>
        </label>
        <label>
          <span>村</span>
          <select
            value={villageFilter}
            onChange={(event) => {
              setReviewOffset(0);
              setVillageFilter(event.target.value);
            }}
          >
            <option value="">全部村</option>
            {sourceOptions.villages
              .filter((village) => !tenantFilter || village.tenantId === tenantFilter)
              .map((village) => (
                <option key={village.id} value={village.id}>{village.name}</option>
              ))}
          </select>
        </label>
        <small>当前页 {visibleReviews.length} / 共 {reviewTotal} 项待治理</small>
      </div>

      <div className="review-split" data-queue-open={queueOpen}>
        {queueOpen ? (
          <button
            aria-label="关闭待治理文件"
            className="review-queue-backdrop"
            onClick={() => setQueueOpen(false)}
            type="button"
          />
        ) : null}
        <aside className="review-queue" aria-label="待治理项目">
          <header>
            <div>
              <strong>待治理文件</strong>
              <span>{reviewTotal} 份</span>
            </div>
            <button onClick={() => setQueueOpen(false)} type="button">关闭</button>
          </header>
          {visibleReviews.length ? visibleReviews.map((review) => (
            <button
              data-selected={review.proposal_id === selectedSummary?.proposal_id}
              key={review.proposal_id}
              onClick={() => {
                setSelectedId(review.proposal_id);
                setQueueOpen(false);
              }}
              type="button"
            >
              <span>
                <strong>{review.relative_path || review.file_name}</strong>
                <small>
                  {review.tenant_name} / {review.administrative_unit_name}
                  {" · "}
                  {formatDate(review.created_at)}
                </small>
              </span>
              <span>
                <em>{review.field_count} 列待确认</em>
                <small>{review.match_type === "none" ? "无模板" : "部分匹配"}</small>
              </span>
            </button>
          )) : (
            <div className="catalog-empty">
              <strong>待治理队列为空</strong>
              <p>已发布字段和 Region 模板会让同类文件直接通过。</p>
            </div>
          )}
          <nav className="review-queue-pagination" aria-label="治理文件分页">
            <button
              type="button"
              disabled={reviewOffset === 0}
              onClick={() => setReviewOffset(Math.max(0, reviewOffset - REVIEW_PAGE_SIZE))}
            >
              上一页
            </button>
            <span>
              {Math.floor(reviewOffset / REVIEW_PAGE_SIZE) + 1}
              {" / "}
              {Math.max(1, Math.ceil(reviewTotal / REVIEW_PAGE_SIZE))}
            </span>
            <button
              type="button"
              disabled={reviewOffset + REVIEW_PAGE_SIZE >= reviewTotal}
              onClick={() => setReviewOffset(reviewOffset + REVIEW_PAGE_SIZE)}
            >
              下一页
            </button>
          </nav>
        </aside>

        <div className="review-detail">
          {!selected ? (
            <p>选择一份待治理文件，逐列确认将沉淀的标准语义。</p>
          ) : (
            <form
              onSubmit={async (event) => {
                event.preventDefault();
                if (!allComplete) {
                  setError(`仍有 ${totalCount - completedCount} 列未完成确认。`);
                  return;
                }
                setBusy(true);
                setError("");
                try {
                  const suggestion = selected.proposal.template_suggestion;
                  await acceptReviewProposal(selected, {
                    template_code: selected.matched_template_code
                      ? null
                      : (suggestion?.template_code ?? `${domain}.${recordType}`),
                    template_name: selected.matched_template_name
                      ?? suggestion?.template_name
                      ?? `${domainOptions.find((item) => item.code === domain)?.label}·`
                        + `${recordTypeOptions[domain].find((item) => item.code === recordType)?.label}`,
                    domain,
                    record_type: recordType,
                    record_grain: recordGrain,
                    field_resolutions: selected.field_evidence.map(
                      (evidence) => resolutions[evidence.source_column_id],
                    ),
                  });
                  await refresh();
                } catch (cause) {
                  setError(cause instanceof Error ? cause.message : "治理提交失败");
                } finally {
                  setBusy(false);
                }
              }}
            >
              <header className="governance-file-header">
                <div>
                  <span className="coordinate">来源文件</span>
                  <h3>{selected.relative_path || selected.file_name}</h3>
                  <p>
                    {selected.tenant_name} / {selected.administrative_unit_name}
                    {" · 上传人 "}
                    {selected.created_by_display_name}
                  </p>
                </div>
                <button
                  className="governance-file-switch"
                  onClick={() => setQueueOpen(true)}
                  type="button"
                >
                  切换文件
                  <span>{reviewTotal}</span>
                </button>
                <dl>
                  <div><dt>待确认</dt><dd>{totalCount}</dd></div>
                  <div><dt>已完成</dt><dd>{completedCount}</dd></div>
                </dl>
              </header>
              <div className="review-reasons">
                {selected.reason_codes.map((reason) => (
                  <span key={reason}>{reasonLabels[reason] ?? reason}</span>
                ))}
              </div>
              {selected.review_kind === "structure" ? (
                <aside className="governance-structure-notice">
                  <strong>这份文件首先是结构问题</strong>
                  <p>
                    当前表格包含跨栏表单、多级合并表头或其他布局歧义。逐列选择不能证明
                    Region 结构正确；请只在核对数据区和记录粒度后发布，否则应驳回建议。
                  </p>
                </aside>
              ) : null}

              <details className="governance-context-disclosure">
                <summary>
                  <span>整份文件设置</span>
                  <strong>
                    {domainOptions.find((item) => item.code === domain)?.label}
                    {" · "}
                    {recordTypeOptions[domain].find(
                      (item) => item.code === recordType,
                    )?.label}
                    {" · "}
                    {grainOptions.find((item) => item.code === recordGrain)?.label
                      ?? "采用当前建议"}
                  </strong>
                  <em>展开修改</em>
                </summary>
                <section className="governance-context">
                  <label>
                    业务域
                    <select
                      value={domain}
                      onChange={(event) => {
                        const next = event.target.value as DomainCode;
                        setDomain(next);
                        setRecordType(recordTypeOptions[next][0].code);
                      }}
                    >
                      {domainOptions.map((option) => (
                        <option key={option.code} value={option.code}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    每行记录什么
                    <select
                      value={recordType}
                      onChange={(event) => setRecordType(event.target.value)}
                    >
                      {recordTypeOptions[domain].map((option) => (
                        <option key={option.code} value={option.code}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    记录粒度
                    <select
                      value={recordGrain}
                      onChange={(event) => setRecordGrain(event.target.value)}
                    >
                      {[...grainOptions, {
                        code: recordGrain,
                        label: "采用当前建议",
                      }].filter((option, index, values) =>
                        values.findIndex((item) => item.code === option.code) === index)
                        .map((option) => (
                          <option key={option.code} value={option.code}>
                            {option.label}
                          </option>
                        ))}
                    </select>
                  </label>
                </section>
              </details>

              <div className="field-governance-grid">
                <aside className="field-governance-nav" aria-label="待确认字段">
                  <label>
                    搜索列
                    <input
                      value={fieldSearch}
                      onChange={(event) => {
                        setFieldSearch(event.target.value);
                        setFieldPage(0);
                      }}
                      placeholder="表头、Sheet 或列坐标…"
                      type="search"
                    />
                  </label>
                  <div>
                    {pagedFields.map((evidence) => (
                      <button
                        data-selected={evidence.source_column_id === activeEvidence?.source_column_id}
                        key={evidence.source_column_id}
                        onClick={() => setActiveFieldId(evidence.source_column_id)}
                        type="button"
                      >
                        <span>
                          <strong>{evidence.column_coordinate} 列 · {evidence.leaf_header}</strong>
                          <small>{evidence.sheet_name} · {evidence.header_path.join(" / ")}</small>
                        </span>
                        <em>
                          {isComplete(resolutions[evidence.source_column_id])
                            ? "方案已填"
                            : "待处理"}
                        </em>
                      </button>
                    ))}
                  </div>
                  {pageCount > 1 ? (
                    <nav aria-label="字段分页">
                      <button
                        disabled={fieldPage === 0}
                        onClick={() => setFieldPage((page) => page - 1)}
                        type="button"
                      >
                        上一页
                      </button>
                      <span>{fieldPage + 1} / {pageCount}</span>
                      <button
                        disabled={fieldPage + 1 >= pageCount}
                        onClick={() => setFieldPage((page) => page + 1)}
                        type="button"
                      >
                        下一页
                      </button>
                    </nav>
                  ) : null}
                </aside>

                {activeEvidence && resolutions[activeEvidence.source_column_id] ? (
                  <FieldResolutionEditor
                    evidence={activeEvidence}
                    fileName={selected.relative_path || selected.file_name}
                    fields={fields}
                    resolution={resolutions[activeEvidence.source_column_id]}
                    onChange={(next) => setResolutions((current) => ({
                      ...current,
                      [activeEvidence.source_column_id]: next,
                    }))}
                  />
                ) : (
                  <p className="field-resolution-empty">当前筛选下没有待确认字段。</p>
                )}
              </div>

              <footer className="governance-submit">
                <div>
                  <strong>{completedCount} / {totalCount} 列已有处理方案</strong>
                  <span>
                    {allComplete
                      ? "提交后立即发布字段与 Region 模板，并重新构建当前文件。"
                      : `还需确认 ${totalCount - completedCount} 列，未完成前不能发布。`}
                  </span>
                </div>
                <nav aria-label="字段前后切换">
                  <button
                    disabled={activeFieldIndex <= 0}
                    onClick={() => moveField(-1)}
                    type="button"
                  >
                    上一列
                  </button>
                  <button
                    disabled={
                      activeFieldIndex < 0
                      || activeFieldIndex + 1 >= searchedFields.length
                    }
                    onClick={() => moveField(1)}
                    type="button"
                  >
                    下一列
                  </button>
                </nav>
                <button
                  className="text-button"
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    try {
                      await rejectReviewProposal(selected, "管理员判定该建议不可用");
                      await refresh();
                    } catch (cause) {
                      setError(cause instanceof Error ? cause.message : "驳回失败");
                    } finally {
                      setBusy(false);
                    }
                  }}
                  type="button"
                >
                  驳回整份建议
                </button>
                <button
                  className="primary-button"
                  disabled={busy || !allComplete}
                  type="submit"
                >
                  {busy ? "正在发布并重建…" : "发布字段并重新入库"}
                </button>
              </footer>
            </form>
          )}
        </div>
      </div>
    </section>
  );
}
