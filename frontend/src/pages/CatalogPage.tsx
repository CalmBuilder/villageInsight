import {
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { Link, useSearchParams } from "react-router-dom";
import { StatusBadge } from "../components/StatusBadge";
import {
  createField,
  createMetric,
  getCatalogDirectory,
  getFieldDetails,
  getRegionTemplateSourcePreview,
  getWorkbookRouteSourcePreview,
  publishField,
  runTemplateAction,
  type MetricDefinition,
  type RegionTemplate,
  type RegionSourcePreview,
  type SemanticField,
  type SemanticFieldDetail,
  type SheetComposition,
  type Template,
  type WorkbookRoute,
  type WorkbookRouteSourcePreview,
} from "../lib/api";

type CatalogSection =
  | "fields"
  | "metrics"
  | "regions"
  | "compositions"
  | "routes"
  | "legacy";

type DetailView = "definition" | "variants" | "references" | "versions";

type CatalogRow = {
  id: string;
  code: string;
  name: string;
  description: string;
  status: string;
  version: number;
  kind: CatalogSection;
  meta: string;
  reuse: string;
  dataType?: string;
  layer?: string;
};

const PAGE_SIZE = 20;
const SECTION_IDS = new Set<CatalogSection>([
  "fields",
  "metrics",
  "regions",
  "compositions",
  "routes",
  "legacy",
]);
const DETAIL_VIEWS = new Set<DetailView>([
  "definition",
  "variants",
  "references",
  "versions",
]);

const SECTION_LABELS: Record<CatalogSection, string> = {
  fields: "入库字段",
  metrics: "问数指标",
  regions: "表头模板",
  compositions: "Sheet 模板",
  routes: "文件模板",
  legacy: "历史记录",
};

const SECTION_GUIDANCE: Record<CatalogSection, string> = {
  fields: "统一保存姓名、身份证号、补贴金额等字段，同一字段可跨文件复用。",
  metrics: "引用已发布字段，通过确定性查询回答数量和汇总问题。",
  regions: "按真实文件、Sheet、列和表头查看每一列最终写入哪个字段。",
  compositions: "保存一张 Sheet 中有哪些表格；新增一块表格不会让整份文件失效。",
  routes: "保存一个 Excel 中的 Sheet 组合，相同结构再次上传可直接复用。",
  legacy: "保留旧版本状态和操作记录，供问题追溯。",
};

const nextTemplateAction: Record<
  string,
  { action: "confirm" | "submit-review" | "approve" | "deprecate"; label: string }
> = {
  draft: { action: "confirm", label: "用户确认" },
  user_confirmed: { action: "submit-review", label: "提交审核" },
  admin_review: { action: "approve", label: "批准发布" },
  published: { action: "deprecate", label: "停止匹配" },
};

function formatVariant(field: SemanticField, variant: SemanticField["variants"][number]) {
  if (variant.kind === "header_path") {
    return variant.header_path.length ? variant.header_path.join(" / ") : "未记录表头路径";
  }
  if (variant.kind === "role_context") {
    return [variant.role, variant.domain, variant.record_type].filter(Boolean).join(" · ");
  }
  return variant.alias || field.name;
}

function kindLabel(kind: SemanticField["variants"][number]["kind"]) {
  return {
    alias: "字段别名",
    header_path: "完整表头路径",
    role_context: "角色与上下文",
  }[kind];
}

function templateSourceLabel(source: string) {
  return ({
    validated_baseline: "已验收基线",
    validated_corpus: "真实语料验证稳定态",
    auto_governance: "自动治理",
    manual_governance: "管理员治理",
  } as Record<string, string>)[source] ?? `历史来源（${source}）`;
}

function routeSourceFileCount(route: WorkbookRoute) {
  const paths = new Set<string>();
  for (const member of route.source_metadata.members ?? []) {
    if (member.representative_path) paths.add(member.representative_path);
    for (const path of member.source_paths ?? []) paths.add(path);
  }
  return paths.size;
}

export function CatalogPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const rawSection = searchParams.get("type") as CatalogSection | null;
  const section = rawSection && SECTION_IDS.has(rawSection) ? rawSection : "fields";
  const rawView = searchParams.get("view") as DetailView | null;
  const detailView =
    rawView && DETAIL_VIEWS.has(rawView) ? rawView : "definition";
  const query = searchParams.get("q") ?? "";
  const statusFilter = searchParams.get("status") ?? "all";
  const layerFilter = searchParams.get("layer") ?? "all";
  const dataTypeFilter = searchParams.get("data_type") ?? "all";
  const requestedPage = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);
  const selectedId = searchParams.get("selected") ?? "";
  const deferredQuery = useDeferredValue(query);

  const [fields, setFields] = useState<SemanticField[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [regionTemplates, setRegionTemplates] = useState<RegionTemplate[]>([]);
  const [sheetCompositions, setSheetCompositions] = useState<SheetComposition[]>([]);
  const [workbookRoutes, setWorkbookRoutes] = useState<WorkbookRoute[]>([]);
  const [metrics, setMetrics] = useState<MetricDefinition[]>([]);
  const [fieldDetail, setFieldDetail] = useState<SemanticFieldDetail | null>(null);
  const [regionPreview, setRegionPreview] = useState<RegionSourcePreview | null>(null);
  const [regionPreviewLoading, setRegionPreviewLoading] = useState(false);
  const [regionPreviewError, setRegionPreviewError] = useState("");
  const [routePreview, setRoutePreview] =
    useState<WorkbookRouteSourcePreview | null>(null);
  const [routePreviewLoading, setRoutePreviewLoading] = useState(false);
  const [routePreviewError, setRoutePreviewError] = useState("");
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busyId, setBusyId] = useState("");
  const [creating, setCreating] = useState(false);
  const [drawer, setDrawer] = useState<"field" | "metric" | null>(null);
  const [publishTarget, setPublishTarget] = useState<SemanticField | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [directoryTotal, setDirectoryTotal] = useState(0);
  const [directoryCounts, setDirectoryCounts] = useState<Record<string, number>>({
    fields: 0,
    metrics: 0,
    regions: 0,
    compositions: 0,
    routes: 0,
    legacy: 0,
  });
  const directoryRequestId = useRef(0);

  function updateQuery(
    patch: Record<string, string | null>,
    options: { replace?: boolean; resetPage?: boolean } = {},
  ) {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(patch)) {
      if (!value || value === "all") next.delete(key);
      else next.set(key, value);
    }
    if (options.resetPage) {
      next.delete("page");
      next.delete("selected");
    }
    setSearchParams(next, { replace: options.replace });
  }

  const refresh = useCallback(async (signal?: AbortSignal) => {
    const requestId = ++directoryRequestId.current;
    try {
      const filters = {
        section,
        search: deferredQuery.trim(),
        status: statusFilter,
        layer: layerFilter,
        dataType: dataTypeFilter,
        limit: PAGE_SIZE,
        offset: (requestedPage - 1) * PAGE_SIZE,
      };
      let page: { counts: Record<string, number>; total: number };
      if (section === "fields") {
        const result = await getCatalogDirectory<SemanticField>(filters, signal);
        if (requestId !== directoryRequestId.current) return;
        setFields(result.items);
        page = result;
      } else if (section === "metrics") {
        const result = await getCatalogDirectory<MetricDefinition>(filters, signal);
        if (requestId !== directoryRequestId.current) return;
        setMetrics(result.items);
        page = result;
      } else if (section === "regions") {
        const result = await getCatalogDirectory<RegionTemplate>(filters, signal);
        if (requestId !== directoryRequestId.current) return;
        setRegionTemplates(result.items);
        page = result;
      } else if (section === "compositions") {
        const result = await getCatalogDirectory<SheetComposition>(filters, signal);
        if (requestId !== directoryRequestId.current) return;
        setSheetCompositions(result.items);
        page = result;
      } else if (section === "routes") {
        const result = await getCatalogDirectory<WorkbookRoute>(filters, signal);
        if (requestId !== directoryRequestId.current) return;
        setWorkbookRoutes(result.items);
        page = result;
      } else {
        const result = await getCatalogDirectory<Template>(filters, signal);
        if (requestId !== directoryRequestId.current) return;
        setTemplates(result.items);
        page = result;
      }
      setDirectoryTotal(page.total);
      setDirectoryCounts((current) => ({ ...current, ...page.counts }));
      setError("");
    } catch (cause) {
      if (requestId !== directoryRequestId.current) return;
      if (cause instanceof DOMException && cause.name === "AbortError") return;
      setError(cause instanceof Error ? cause.message : "目录加载失败");
    }
  }, [
    dataTypeFilter,
    deferredQuery,
    layerFilter,
    requestedPage,
    section,
    statusFilter,
  ]);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  const counts = directoryCounts;

  const rows = useMemo<CatalogRow[]>(() => {
    if (section === "fields") {
      return fields.map((field) => ({
        id: field.id,
        code: field.code,
        name: field.name,
        description: field.description,
        status: field.status,
        version: field.version,
        kind: section,
        meta: `${field.layer === "base" ? "基础字段" : "领域字段"} · ${field.data_type}${field.unit_dimension ? ` · ${field.unit_dimension}` : ""}`,
        reuse: `${field.variant_count ?? field.variants.length} 个复用变体`,
        dataType: field.data_type,
        layer: field.layer,
      }));
    }
    if (section === "metrics") {
      return metrics.map((metric) => ({
        id: metric.id,
        code: metric.code,
        name: metric.name,
        description: "",
        status: metric.enabled ? "enabled" : "disabled",
        version: metric.semantic_field_version,
        kind: section,
        meta: `${metric.aggregation} · ${metric.unit || "无单位"}`,
        reuse: `引用 ${metric.semantic_field_code}`,
      }));
    }
    if (section === "regions") {
      return regionTemplates.map((template) => ({
        id: template.id,
        code: template.code,
        name: template.name,
        description: template.description,
        status: template.status,
        version: template.version,
        kind: section,
        meta: `${template.definition.domain || "未分类"} · ${template.definition.record_type || "通用记录"}`,
        reuse: `${template.definition.field_bindings.length || template.definition.header_signature.length} 列`,
      }));
    }
    if (section === "compositions") {
      return sheetCompositions.map((composition) => ({
        id: composition.id,
        code: composition.code,
        name: composition.name,
        description: composition.description,
        status: composition.status,
        version: composition.version,
        kind: section,
        meta: composition.source,
        reuse: `${composition.region_slots.length} 个数据区`,
      }));
    }
    if (section === "routes") {
      return workbookRoutes.map((route) => ({
        id: route.id,
        code: route.code,
        name: route.name,
        description: route.description,
        status: route.status,
        version: route.version,
        kind: section,
        meta: route.source,
        reuse: `${routeSourceFileCount(route)} 个真实文件 · ${route.sheet_slots.length} 个 Sheet`,
      }));
    }
    return templates.map((template) => ({
      id: template.id,
      code: template.code,
      name: template.name,
      description: template.description,
      status: template.status,
      version: template.version,
      kind: section,
      meta: template.layout_fingerprint.slice(0, 12),
      reuse: template.published_version
        ? `已发布 v${template.published_version}`
        : "尚未发布",
    }));
  }, [
    fields,
    metrics,
    regionTemplates,
    section,
    sheetCompositions,
    templates,
    workbookRoutes,
  ]);

  const filteredRows = rows;
  const pageCount = Math.max(1, Math.ceil(directoryTotal / PAGE_SIZE));
  const page = Math.min(requestedPage, pageCount);
  const pageRows = filteredRows;
  const selectedRow =
    filteredRows.find((row) => row.id === selectedId) ?? pageRows[0] ?? null;
  const selectedRowId = selectedRow?.id ?? "";

  useEffect(() => {
    setFieldDetail(null);
    setDetailError("");
    if (section !== "fields" || !selectedRowId) return;
    const controller = new AbortController();
    setDetailsLoading(true);
    void getFieldDetails(selectedRowId, controller.signal)
      .then(setFieldDetail)
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setDetailError(
          cause instanceof Error
            ? `版本与引用暂不可用：${cause.message}`
            : "版本与引用暂不可用",
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailsLoading(false);
      });
    return () => controller.abort();
  }, [section, selectedRowId]);

  useEffect(() => {
    setRoutePreview(null);
    setRoutePreviewError("");
    if (section !== "routes" || !selectedRowId) return;
    const controller = new AbortController();
    setRoutePreviewLoading(true);
    void getWorkbookRouteSourcePreview(selectedRowId, controller.signal)
      .then(setRoutePreview)
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setRoutePreviewError(
          cause instanceof Error ? cause.message : "真实文件清单暂时无法读取",
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setRoutePreviewLoading(false);
      });
    return () => controller.abort();
  }, [section, selectedRowId]);

  useEffect(() => {
    setRegionPreview(null);
    setRegionPreviewError("");
    if (section !== "regions" || !selectedRowId) return;
    const controller = new AbortController();
    setRegionPreviewLoading(true);
    void getRegionTemplateSourcePreview(selectedRowId, controller.signal)
      .then(setRegionPreview)
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setRegionPreviewError(
          cause instanceof Error ? cause.message : "真实文件证据暂时无法读取",
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setRegionPreviewLoading(false);
      });
    return () => controller.abort();
  }, [section, selectedRowId]);

  useEffect(() => {
    if (!drawer && !publishTarget) return;
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setDrawer(null);
      setPublishTarget(null);
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [drawer, publishTarget]);

  function selectRow(row: CatalogRow) {
    updateQuery({ selected: row.id, view: "definition" });
    setInspectorOpen(true);
  }

  async function submitField(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setCreating(true);
    setError("");
    try {
      const created = await createField({
        code: String(data.get("code") ?? "").trim(),
        name: String(data.get("name") ?? "").trim(),
        description: String(data.get("description") ?? "").trim(),
        layer: String(data.get("layer")) as "base" | "domain",
        data_type: String(data.get("data_type")),
        unit_dimension: String(data.get("unit_dimension") ?? "").trim() || null,
      });
      await refresh();
      setDrawer(null);
      setNotice(`已建立字段草稿“${created.name}”`);
      updateQuery({ type: "fields", selected: created.id, view: "definition" });
      setInspectorOpen(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "字段创建失败");
    } finally {
      setCreating(false);
    }
  }

  async function submitMetric(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setCreating(true);
    setError("");
    try {
      const field = fields.find((entry) => entry.code === String(data.get("field")));
      if (!field?.published_version) throw new Error("指标必须引用已发布字段");
      const created = await createMetric({
        code: String(data.get("code") ?? "").trim(),
        name: String(data.get("name") ?? "").trim(),
        semantic_field_code: field.code,
        semantic_field_version: field.published_version,
        aggregation: String(data.get("aggregation")),
        unit: String(data.get("unit") ?? "").trim() || null,
      });
      await refresh();
      setDrawer(null);
      setNotice(`已建立问数指标“${created.name}”`);
      updateQuery({ type: "metrics", selected: created.id });
      setInspectorOpen(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "指标创建失败");
    } finally {
      setCreating(false);
    }
  }

  async function confirmPublish() {
    if (!publishTarget) return;
    setBusyId(publishTarget.id);
    setError("");
    try {
      await publishField(publishTarget);
      setNotice(`已发布“${publishTarget.name}”v${publishTarget.version}`);
      setPublishTarget(null);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "字段发布失败");
    } finally {
      setBusyId("");
    }
  }

  async function handleTemplateAction(template: Template) {
    const command = nextTemplateAction[template.status];
    if (!command) return;
    setBusyId(template.id);
    setError("");
    try {
      await runTemplateAction(template, command.action);
      setNotice(`“${template.name}”已完成：${command.label}`);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "模板状态更新失败");
    } finally {
      setBusyId("");
    }
  }

  const selectedField =
    section === "fields"
      ? fields.find((field) => field.id === selectedRow?.id) ?? null
      : null;
  const selectedMetric =
    section === "metrics"
      ? metrics.find((metric) => metric.id === selectedRow?.id) ?? null
      : null;
  const selectedRegion =
    section === "regions"
      ? regionTemplates.find((template) => template.id === selectedRow?.id) ?? null
      : null;
  const selectedComposition =
    section === "compositions"
      ? sheetCompositions.find((item) => item.id === selectedRow?.id) ?? null
      : null;
  const selectedRoute =
    section === "routes"
      ? workbookRoutes.find((item) => item.id === selectedRow?.id) ?? null
      : null;
  const selectedLegacy =
    section === "legacy"
      ? templates.find((item) => item.id === selectedRow?.id) ?? null
      : null;

  return (
    <section className="catalog-workbench">
      <header className="catalog-workbench__header">
        <div>
          <span className="eyebrow">EXCEL TEMPLATE LIBRARY</span>
          <h1>Excel 入库模板</h1>
          <p>{SECTION_GUIDANCE[section]}</p>
        </div>
        <div className="catalog-workbench__actions">
          <span>
            <strong>{counts[section]}</strong>
            <small>{section === "routes" ? "文件结构模板" : `${SECTION_LABELS[section]}总数`}</small>
          </span>
          {section === "routes" && (
            <span>
              <strong>{counts.route_source_files ?? 0}</strong>
              <small>已分析真实文件</small>
            </span>
          )}
          {section === "fields" && (
            <button className="primary-button" type="button" onClick={() => setDrawer("field")}>
              新建业务字段
            </button>
          )}
          {section === "metrics" && (
            <button className="primary-button" type="button" onClick={() => setDrawer("metric")}>
              新建问数指标
            </button>
          )}
        </div>
      </header>

      {error && <p className="alert catalog-workbench__alert" role="alert">{error}</p>}
      {notice && <p className="catalog-notice" role="status">{notice}</p>}

      <div className="catalog-workbench__body">
        <nav className="catalog-kind-nav" aria-label="Excel 入库模板分类">
          <CatalogGroup
            title="字段和问数"
            items={["fields", "metrics"]}
            active={section}
            counts={counts}
            searchParams={searchParams}
          />
          <CatalogGroup
            title="文件结构"
            items={["regions", "compositions", "routes"]}
            active={section}
            counts={counts}
            searchParams={searchParams}
          />
          <CatalogGroup
            title="历史记录"
            items={["legacy"]}
            active={section}
            counts={counts}
            searchParams={searchParams}
          />
          <p>已发布模板保留版本记录。查看表头模板时，可以回看真实文件中的列、表头和样例值。</p>
        </nav>

        <section className="catalog-directory" aria-label={`${SECTION_LABELS[section]}目录`}>
          <header className="catalog-directory__toolbar">
            <label className="catalog-search">
              <span>搜索当前目录</span>
              <input
                type="search"
                value={query}
                onChange={(event) =>
                  updateQuery(
                    { q: event.target.value || null },
                    { replace: true, resetPage: true },
                  )
                }
                placeholder="名称、编码或说明"
              />
            </label>
            <label>
              <span>状态</span>
              <select
                value={statusFilter}
                onChange={(event) =>
                  updateQuery({ status: event.target.value }, { resetPage: true })
                }
              >
                <option value="all">全部状态</option>
                <option value="published">已发布</option>
                <option value="draft">草稿</option>
                <option value="enabled">启用</option>
                <option value="disabled">停用</option>
                <option value="deprecated">已停用匹配</option>
              </select>
            </label>
            {section === "fields" && (
              <>
                <label>
                  <span>层级</span>
                  <select
                    value={layerFilter}
                    onChange={(event) =>
                      updateQuery({ layer: event.target.value }, { resetPage: true })
                    }
                  >
                    <option value="all">全部层级</option>
                    <option value="base">基础字段</option>
                    <option value="domain">领域字段</option>
                  </select>
                </label>
                <label>
                  <span>数据类型</span>
                  <select
                    value={dataTypeFilter}
                    onChange={(event) =>
                      updateQuery(
                        { data_type: event.target.value },
                        { resetPage: true },
                      )
                    }
                  >
                    <option value="all">全部类型</option>
                    <option value="text">文本</option>
                    <option value="integer">整数</option>
                    <option value="decimal">小数</option>
                    <option value="date">日期</option>
                    <option value="boolean">布尔值</option>
                  </select>
                </label>
              </>
            )}
          </header>

          <div className="catalog-directory__summary">
            <div>
              <strong>{SECTION_LABELS[section]}</strong>
              <span>{directoryTotal} 项结果</span>
            </div>
            <span>每页 {PAGE_SIZE} 项</span>
          </div>

          <div className="catalog-table-wrap">
            <table className="catalog-table">
              <thead>
                <tr>
                  <th scope="col">名称与编码</th>
                  <th scope="col">定义</th>
                  <th scope="col">复用</th>
                  <th scope="col">状态</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((row) => (
                  <tr
                    key={row.id}
                    data-selected={selectedRow?.id === row.id || undefined}
                  >
                    <td>
                      <button
                        className="catalog-row-button"
                        type="button"
                        aria-current={selectedRow?.id === row.id ? "true" : undefined}
                        onClick={() => selectRow(row)}
                      >
                        <strong>{row.name}</strong>
                        <code>{row.code} · v{row.version}</code>
                      </button>
                    </td>
                    <td>{row.meta}</td>
                    <td>{row.reuse}</td>
                    <td><StatusBadge status={row.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!pageRows.length && (
              <div className="catalog-directory__empty">
                <strong>没有符合条件的目录项</strong>
                <p>调整关键词或筛选条件后再试。</p>
                <button
                  type="button"
                  onClick={() =>
                    updateQuery(
                      { q: null, status: null, layer: null, data_type: null, page: null },
                    )
                  }
                >
                  清除筛选
                </button>
              </div>
            )}
          </div>

          <footer className="catalog-pagination" aria-label="目录分页">
            <span>第 {page} / {pageCount} 页</span>
            <div>
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => updateQuery({ page: String(page - 1), selected: null })}
              >
                上一页
              </button>
              <button
                type="button"
                disabled={page >= pageCount}
                onClick={() => updateQuery({ page: String(page + 1), selected: null })}
              >
                下一页
              </button>
            </div>
          </footer>
        </section>

        <aside
          className="catalog-inspector"
          aria-label="目录项详情"
          aria-live="polite"
          data-open={inspectorOpen || undefined}
        >
          <button
            className="catalog-inspector__close"
            type="button"
            aria-label="关闭详情"
            onClick={() => setInspectorOpen(false)}
          >
            ×
          </button>
          {selectedRow ? (
            <>
              <header className="catalog-inspector__header">
                <span>{SECTION_LABELS[section]}</span>
                <h2>{selectedRow.name}</h2>
                <code>{selectedRow.code} · v{selectedRow.version}</code>
                <StatusBadge status={selectedRow.status} />
              </header>
              {section === "fields" && selectedField && (
                <FieldInspector
                  field={selectedField}
                  detail={fieldDetail}
                  detailError={detailError}
                  loading={detailsLoading}
                  view={detailView}
                  onView={(view) => updateQuery({ view })}
                  onPublish={() => setPublishTarget(selectedField)}
                />
              )}
              {section === "metrics" && selectedMetric && (
                <MetricInspector metric={selectedMetric} />
              )}
              {section === "regions" && selectedRegion && (
                <RegionInspector
                  template={selectedRegion}
                  preview={regionPreview}
                  loading={regionPreviewLoading}
                  error={regionPreviewError}
                />
              )}
              {section === "compositions" && selectedComposition && (
                <CompositionInspector
                  composition={selectedComposition}
                  regionTemplates={regionTemplates}
                />
              )}
              {section === "routes" && selectedRoute && (
                <RouteInspector
                  route={selectedRoute}
                  preview={routePreview}
                  loading={routePreviewLoading}
                  error={routePreviewError}
                />
              )}
              {section === "legacy" && selectedLegacy && (
                <LegacyInspector
                  template={selectedLegacy}
                  busy={busyId === selectedLegacy.id}
                  onAction={() => void handleTemplateAction(selectedLegacy)}
                />
              )}
            </>
          ) : (
            <div className="catalog-inspector__empty">
              <strong>选择一个目录项</strong>
              <p>定义、复用关系和版本记录会在这里展示。</p>
            </div>
          )}
        </aside>
      </div>

      {drawer && (
        <div className="drawer-layer" role="presentation" onMouseDown={() => setDrawer(null)}>
          <aside
            className="side-drawer catalog-create-drawer"
            aria-label={drawer === "field" ? "新建业务字段" : "新建问数指标"}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header>
              <div>
                <span>CREATE DRAFT</span>
                <h2>{drawer === "field" ? "新建业务字段" : "新建问数指标"}</h2>
              </div>
              <button type="button" aria-label="关闭" onClick={() => setDrawer(null)}>×</button>
            </header>
            {drawer === "field" ? (
              <FieldCreateForm creating={creating} onSubmit={submitField} />
            ) : (
              <MetricCreateForm
                creating={creating}
                fields={fields}
                onSubmit={submitMetric}
              />
            )}
          </aside>
        </div>
      )}

      {publishTarget && (
        <div className="catalog-confirm-layer" role="presentation">
          <section
            className="catalog-confirm"
            role="dialog"
            aria-modal="true"
            aria-labelledby="publish-title"
          >
            <span>IMMUTABLE RELEASE</span>
            <h2 id="publish-title">发布“{publishTarget.name}”v{publishTarget.version}？</h2>
            <p>
              发布后该版本不可原位修改。后续文件可以复用它的编码、别名、完整表头路径和
              上下文；如需调整，应建立新版本。
            </p>
            <dl>
              <div><dt>字段编码</dt><dd>{publishTarget.code}</dd></div>
              <div><dt>复用变体</dt><dd>{publishTarget.variants.length} 个</dd></div>
            </dl>
            <footer>
              <button type="button" onClick={() => setPublishTarget(null)}>取消</button>
              <button
                className="primary-button"
                type="button"
                disabled={busyId === publishTarget.id}
                onClick={() => void confirmPublish()}
              >
                {busyId === publishTarget.id ? "正在发布…" : "确认发布此版本"}
              </button>
            </footer>
          </section>
        </div>
      )}
    </section>
  );
}

function CatalogGroup({
  title,
  items,
  active,
  counts,
  searchParams,
}: {
  title: string;
  items: CatalogSection[];
  active: CatalogSection;
  counts: Record<string, number>;
  searchParams: URLSearchParams;
}) {
  return (
    <section>
      <h2>{title}</h2>
      {items.map((item) => {
        const next = new URLSearchParams(searchParams);
        next.set("type", item);
        next.delete("page");
        next.delete("selected");
        next.delete("view");
        next.delete("status");
        next.delete("layer");
        next.delete("data_type");
        next.delete("q");
        return (
          <Link
            key={item}
            to={{ search: next.toString() }}
            aria-current={active === item ? "page" : undefined}
          >
            <span>{SECTION_LABELS[item]}</span>
            <strong>{counts[item]}</strong>
          </Link>
        );
      })}
    </section>
  );
}

function InspectorTabs({
  view,
  onView,
}: {
  view: DetailView;
  onView: (view: DetailView) => void;
}) {
  const tabs: Array<[DetailView, string]> = [
    ["definition", "定义"],
    ["variants", "复用变体"],
    ["references", "引用"],
    ["versions", "版本"],
  ];
  return (
    <nav className="catalog-detail-tabs" aria-label="字段详情分类">
      {tabs.map(([value, label]) => (
        <button
          key={value}
          type="button"
          aria-pressed={view === value}
          onClick={() => onView(value)}
        >
          {label}
        </button>
      ))}
    </nav>
  );
}

function FieldInspector({
  field,
  detail,
  detailError,
  loading,
  view,
  onView,
  onPublish,
}: {
  field: SemanticField;
  detail: SemanticFieldDetail | null;
  detailError: string;
  loading: boolean;
  view: DetailView;
  onView: (view: DetailView) => void;
  onPublish: () => void;
}) {
  const resolvedField = detail?.field ?? field;
  return (
    <div className="catalog-detail">
      <InspectorTabs view={view} onView={onView} />
      {loading && <p className="catalog-detail__loading">正在读取版本和引用关系…</p>}
      {detailError && (
        <p className="catalog-detail__warning" role="status">
          {detailError}。字段定义和本地复用变体仍可查看。
        </p>
      )}
      {view === "definition" && (
        <>
          <p className="catalog-detail__lead">
            {resolvedField.description || "该字段尚未填写业务说明。"}
          </p>
          <dl className="catalog-definition-grid">
            <div><dt>层级</dt><dd>{resolvedField.layer === "base" ? "基础字段" : "领域字段"}</dd></div>
            <div><dt>数据类型</dt><dd>{resolvedField.data_type}</dd></div>
            <div><dt>单位</dt><dd>{resolvedField.unit_dimension || "无"}</dd></div>
            <div><dt>已发布版本</dt><dd>{resolvedField.published_version ? `v${resolvedField.published_version}` : "无"}</dd></div>
            <div><dt>模板来源</dt><dd>{templateSourceLabel(resolvedField.source)}</dd></div>
          </dl>
          <section className="catalog-evidence-band">
            <span>语义复用边界</span>
            <strong>{resolvedField.code}</strong>
            <p>新文件逐列匹配此编码及其变体，不依赖原文件、Sheet 或数据区。</p>
          </section>
        </>
      )}
      {view === "variants" && (
        <section className="catalog-detail-section">
          <header>
            <h3>可复用证据</h3>
            <span>{resolvedField.variants.length} 项</span>
          </header>
          {resolvedField.variants.length ? (
            <ul className="catalog-variant-list">
              {resolvedField.variants.map((variant) => (
                <li key={variant.id}>
                  <span>{kindLabel(variant.kind)}</span>
                  <strong>{formatVariant(resolvedField, variant)}</strong>
                  <small>
                    {variant.source} · 置信度 {(variant.confidence_basis_points / 100).toFixed(0)}%
                  </small>
                </li>
              ))}
            </ul>
          ) : (
            <p className="catalog-detail__empty">尚未从治理确认中沉淀别名、表头路径或上下文。</p>
          )}
        </section>
      )}
      {view === "references" && (
        <section className="catalog-detail-section">
          <header>
            <h3>业务表模板引用</h3>
            <span>{detail?.referenced_by.length ?? 0} 项</span>
          </header>
          {detail?.referenced_by.length ? (
            <ul className="catalog-reference-list">
              {detail.referenced_by.map((reference) => (
                <li key={`${reference.template_id}-${reference.template_version}`}>
                  <strong>{reference.template_name}</strong>
                  <code>{reference.template_code} · v{reference.template_version}</code>
                  <StatusBadge status={reference.template_status} />
                </li>
              ))}
            </ul>
          ) : (
            <p className="catalog-detail__empty">当前加载的业务表模板尚未引用此字段。</p>
          )}
        </section>
      )}
      {view === "versions" && (
        <section className="catalog-detail-section">
          <header>
            <h3>不可变版本记录</h3>
            <span>{detail?.versions.length ?? 0} 个版本</span>
          </header>
          <ol className="catalog-version-list">
            {detail?.versions.map((version) => (
              <li key={version.version}>
                <span>v{version.version}</span>
                <div>
                  <strong>{version.name}</strong>
                  <small>{version.data_type} · {version.variant_count} 个变体 · {templateSourceLabel(version.source)}</small>
                </div>
                <StatusBadge status={version.status} />
              </li>
            ))}
          </ol>
        </section>
      )}
      {field.status === "draft" && (
        <footer className="catalog-detail__actions">
          <p>发布将固定当前版本，后续变更需要建立新版本。</p>
          <button className="primary-button" type="button" onClick={onPublish}>
            发布当前草稿
          </button>
        </footer>
      )}
    </div>
  );
}

function MetricInspector({ metric }: { metric: MetricDefinition }) {
  return (
    <div className="catalog-detail">
      <p className="catalog-detail__lead">
        指标只执行确定性聚合，不允许模型自行进行数值计算。
      </p>
      <dl className="catalog-definition-grid">
        <div><dt>聚合方式</dt><dd>{metric.aggregation}</dd></div>
        <div><dt>单位</dt><dd>{metric.unit || "无"}</dd></div>
        <div><dt>引用字段</dt><dd>{metric.semantic_field_code}</dd></div>
        <div><dt>字段版本</dt><dd>v{metric.semantic_field_version}</dd></div>
      </dl>
      <DetailList title="允许筛选的字段" values={metric.allowed_filter_fields} />
      <DetailList title="提问别名" values={metric.aliases} />
    </div>
  );
}

function RegionInspector({
  template,
  preview,
  loading,
  error,
}: {
  template: RegionTemplate;
  preview: RegionSourcePreview | null;
  loading: boolean;
  error: string;
}) {
  const statusLabel = (status: string) =>
    ({
      published_reuse: "复用已有字段",
      codex_confirmed: "已分析确认",
      hermes_confirmed: "AI 复核确认",
      confirmed: "已确认",
    })[status] ?? "已确认";
  return (
    <div className="catalog-detail">
      <p className="catalog-detail__lead">
        {template.description || "记录真实 Excel 中一块表格的表头和字段对应关系。"}
      </p>
      {loading && (
        <p className="catalog-source-state" role="status">
          正在从真实 Excel 读取 Sheet、表头和样例值…
        </p>
      )}
      {error && (
        <p className="catalog-detail__warning" role="status">
          {error}
        </p>
      )}
      {preview && (
        <>
          <section className="catalog-source-card" aria-label="真实文件证据">
            <div className="catalog-source-card__file">
              <span>来源文件</span>
              <strong>{preview.source_file}</strong>
              <small>{preview.source_location}</small>
            </div>
            <dl>
              <div>
                <dt>Sheet</dt>
                <dd>{preview.sheet_name}</dd>
              </div>
              <div>
                <dt>数据范围</dt>
                <dd>{preview.source_range}</dd>
              </div>
              <div>
                <dt>模板列数</dt>
                <dd>{preview.columns.length} 列</dd>
              </div>
              <div>
                <dt>来源文件数</dt>
                <dd>{preview.evidence_count} 个</dd>
              </div>
            </dl>
          </section>
          {preview.warning && (
            <p className="catalog-detail__warning" role="status">
              {preview.warning}
            </p>
          )}
          <section className="catalog-detail-section catalog-column-section">
            <header>
              <h3>每一列如何入库</h3>
              <span>原表头、样例值和入库字段可逐列核对</span>
            </header>
            <div className="catalog-column-list">
              {preview.columns.map((column, index) => (
                <article
                  className="catalog-column-card"
                  key={`${column.excel_column}-${column.semantic_field_code}-${index}`}
                >
                  <span className="catalog-column-card__letter" aria-label={`Excel ${column.excel_column} 列`}>
                    {column.excel_column}
                  </span>
                  <div className="catalog-column-card__mapping">
                    <span>原表头</span>
                    <strong>{column.source_header}</strong>
                    {column.header_path.length > 1 && (
                      <small>{column.header_path.join(" → ")}</small>
                    )}
                  </div>
                  <div className="catalog-column-card__samples">
                    <span>源数据样例</span>
                    <p>
                      {column.sample_values.length
                        ? column.sample_values.join("、")
                        : "该源文件此列暂无非空样例"}
                    </p>
                  </div>
                  <div className="catalog-column-card__target">
                    <span>写入字段</span>
                    <strong>{column.semantic_field_name}</strong>
                    <small>{statusLabel(column.match_status)}</small>
                  </div>
                </article>
              ))}
            </div>
          </section>
        </>
      )}
      <details className="catalog-technical-details">
        <summary>查看技术信息</summary>
        <dl className="catalog-definition-grid">
          <div><dt>业务分类</dt><dd>{template.definition.domain || "未分类"}</dd></div>
          <div><dt>记录类型</dt><dd>{template.definition.record_type || "通用记录"}</dd></div>
          <div><dt>一条记录代表</dt><dd>{template.definition.record_grain || "未填写"}</dd></div>
          <div><dt>内部结构类型</dt><dd>{preview?.layout_mode || template.definition.region_kind}</dd></div>
          <div><dt>模板编码</dt><dd>{template.code}</dd></div>
          <div><dt>模板版本</dt><dd>v{template.version}</dd></div>
          <div><dt>模板来源</dt><dd>{templateSourceLabel(template.source)}</dd></div>
        </dl>
      </details>
    </div>
  );
}

function CompositionInspector({
  composition,
  regionTemplates,
}: {
  composition: SheetComposition;
  regionTemplates: RegionTemplate[];
}) {
  const namesById = new Map(regionTemplates.map((item) => [item.id, item.name]));
  return (
    <div className="catalog-detail">
      <p className="catalog-detail__lead">
        {composition.description || "记录一张 Sheet 中包含哪些表格。"}
      </p>
      <section className="catalog-detail-section">
        <header><h3>这张 Sheet 包含的表格</h3><span>{composition.region_slots.length} 项</span></header>
        <ul className="catalog-slot-list">
          {composition.region_slots.map((slot) => (
            <li key={slot.slot_key}>
              <strong>{namesById.get(slot.region_template_id) || "未命名表格"}</strong>
              <small>{slot.required ? "每次应出现" : "允许不出现"}</small>
            </li>
          ))}
        </ul>
      </section>
      <details className="catalog-technical-details">
        <summary>查看技术信息</summary>
        <p>{composition.code} · v{composition.version} · {templateSourceLabel(composition.source)}</p>
      </details>
    </div>
  );
}

function RouteInspector({
  route,
  preview,
  loading,
  error,
}: {
  route: WorkbookRoute;
  preview: WorkbookRouteSourcePreview | null;
  loading: boolean;
  error: string;
}) {
  return (
    <div className="catalog-detail">
      <p className="catalog-detail__lead">
        {route.description || "记录一个 Excel 文件通常包含哪些 Sheet。"}
      </p>
      {loading && (
        <p className="catalog-source-state" role="status">
          正在读取真实文件和 Sheet 名称…
        </p>
      )}
      {error && (
        <p className="catalog-detail__warning" role="status">{error}</p>
      )}
      {preview?.warning && (
        <p className="catalog-detail__warning" role="status">{preview.warning}</p>
      )}
      {preview && preview.source_files.length > 0 && (
        <section className="catalog-detail-section">
          <header>
            <h3>验证过的真实文件</h3>
            <span>{preview.source_file_count} 个文件</span>
          </header>
          <ul className="catalog-source-file-list">
            {preview.source_files.map((file) => (
              <li key={file.location}>
                <strong>{file.name}</strong>
                <small>{file.location}</small>
              </li>
            ))}
          </ul>
        </section>
      )}
      <section className="catalog-detail-section">
        <header>
          <h3>这个文件包含的 Sheet</h3>
          <span>{preview?.sheets.length ?? route.sheet_slots.length} 个 Sheet</span>
        </header>
        <ul className="catalog-slot-list">
          {(preview?.sheets ?? route.sheet_slots.map((slot) => ({
            sheet_index: slot.ordinal,
            sheet_name: `第 ${slot.ordinal + 1} 个 Sheet`,
            table_count: 0,
            required: slot.required,
          }))).map((sheet) => (
            <li key={`${sheet.sheet_index}-${sheet.sheet_name}`}>
              <strong>{sheet.sheet_name}</strong>
              <small>
                {sheet.table_count ? `${sheet.table_count} 块数据表 · ` : ""}
                {sheet.required ? "每次应出现" : "允许不出现"}
              </small>
            </li>
          ))}
        </ul>
      </section>
      <details className="catalog-technical-details">
        <summary>查看技术信息</summary>
        <p>{route.code} · v{route.version} · {templateSourceLabel(route.source)}</p>
      </details>
    </div>
  );
}

function LegacyInspector({
  template,
  busy,
  onAction,
}: {
  template: Template;
  busy: boolean;
  onAction: () => void;
}) {
  const command = nextTemplateAction[template.status];
  return (
    <div className="catalog-detail">
      <p className="catalog-detail__lead">{template.description || "历史兼容模板。"}</p>
      <dl className="catalog-definition-grid">
        <div><dt>布局指纹</dt><dd>{template.layout_fingerprint}</dd></div>
        <div><dt>已发布版本</dt><dd>{template.published_version ? `v${template.published_version}` : "无"}</dd></div>
      </dl>
      {command && (
        <footer className="catalog-detail__actions">
          <p>状态变更保留审计记录，不会修改已发布历史版本。</p>
          <button className="primary-button" type="button" disabled={busy} onClick={onAction}>
            {busy ? "正在处理…" : command.label}
          </button>
        </footer>
      )}
    </div>
  );
}

function DetailList({ title, values }: { title: string; values: string[] }) {
  return (
    <section className="catalog-detail-section">
      <header><h3>{title}</h3><span>{values.length} 项</span></header>
      {values.length ? (
        <ul className="catalog-token-list">
          {values.map((value) => <li key={value}>{value}</li>)}
        </ul>
      ) : (
        <p className="catalog-detail__empty">暂无记录。</p>
      )}
    </section>
  );
}

function FieldCreateForm({
  creating,
  onSubmit,
}: {
  creating: boolean;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <form className="catalog-drawer-form" onSubmit={onSubmit}>
      <p>先建立草稿；确认语义和复用证据后再发布不可变版本。</p>
      <label>
        <span>字段编码</span>
        <input name="code" required pattern="[a-z][a-z0-9_.]{1,159}" placeholder="例如 person.name" />
        <small>以小写字母开头，只使用小写字母、数字、下划线和点。</small>
      </label>
      <label><span>字段名称</span><input name="name" required placeholder="例如 姓名" /></label>
      <label><span>业务说明</span><textarea name="description" rows={4} placeholder="说明字段的业务含义和适用范围" /></label>
      <div className="catalog-drawer-form__pair">
        <label>
          <span>语义层级</span>
          <select name="layer" defaultValue="base">
            <option value="base">基础字段</option>
            <option value="domain">领域字段</option>
          </select>
        </label>
        <label>
          <span>数据类型</span>
          <select name="data_type" defaultValue="text">
            <option value="text">文本</option>
            <option value="integer">整数</option>
            <option value="decimal">小数</option>
            <option value="date">日期</option>
            <option value="boolean">布尔值</option>
          </select>
        </label>
      </div>
      <label><span>单位（可选）</span><input name="unit_dimension" placeholder="例如 人、元、平方米" /></label>
      <button className="primary-button" disabled={creating} type="submit">
        {creating ? "正在建立…" : "建立字段草稿"}
      </button>
    </form>
  );
}

function MetricCreateForm({
  creating,
  fields,
  onSubmit,
}: {
  creating: boolean;
  fields: SemanticField[];
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  const publishedFields = fields.filter((field) => field.published_version);
  return (
    <form className="catalog-drawer-form" onSubmit={onSubmit}>
      <p>指标必须绑定已发布字段，数值通过确定性聚合生成。</p>
      <label><span>指标编码</span><input name="code" required pattern="[a-z][a-z0-9_.]{1,159}" placeholder="例如 population.total" /></label>
      <label><span>指标名称</span><input name="name" required placeholder="例如 户籍人口数" /></label>
      <label>
        <span>引用业务字段</span>
        <select name="field" required defaultValue="">
          <option value="" disabled>请选择已发布字段</option>
          {publishedFields.map((field) => (
            <option key={field.id} value={field.code}>{field.name} · {field.code}</option>
          ))}
        </select>
      </label>
      <label>
        <span>聚合方式</span>
        <select name="aggregation" defaultValue="count">
          <option value="count">计数</option>
          <option value="sum">求和</option>
          <option value="avg">平均值</option>
          <option value="min">最小值</option>
          <option value="max">最大值</option>
        </select>
      </label>
      <label><span>单位（可选）</span><input name="unit" placeholder="例如 人、户、元" /></label>
      <button className="primary-button" disabled={creating || !publishedFields.length} type="submit">
        {creating ? "正在建立…" : "建立问数指标"}
      </button>
    </form>
  );
}
