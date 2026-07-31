import { useDeferredValue, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  getFiles,
  getItemFieldMatches,
  getItemMatch,
  getItemProfile,
  getItemProposals,
  getItemRegionMatches,
  getWorkerCapacity,
  importDirectory,
  reimportFile,
  uploadBatch,
  type Batch,
  type CurrentUser,
  type FileLedgerItem,
  type FieldMatch,
  type RegionTemplateMatch,
  type TemplateMatch,
  type TemplateProposal,
  type WorkbookProfile,
  type WorkerCapacity,
} from "../lib/api";
import { IngestionStageRail } from "../components/IngestionStageRail";
import { StatusBadge } from "../components/StatusBadge";

type Filter = "all" | "imported" | "processing" | "hermes" | "review" | "failed";
type FilterTone = "neutral" | "success" | "progress" | "assist" | "review" | "danger";
type IntakeMode = "upload" | "folder" | "directory";

const DEFAULT_FILE_PAGE_SIZE = 20;
const FILE_PAGE_SIZES = [10, 20, 50] as const;
const fileFilters = new Set<Filter>([
  "all",
  "imported",
  "processing",
  "hermes",
  "review",
  "failed",
]);
const processingStatuses = new Set([
  "pending",
  "profiling",
  "matching",
  "recognizing",
  "ready",
  "materializing",
]);
const filterOptions: Array<{
  key: Filter;
  label: string;
  hint: string;
  tone: FilterTone;
}> = [
  { key: "all", label: "全部文件", hint: "已接收", tone: "neutral" },
  { key: "imported", label: "已正式入库", hint: "可用于问数", tone: "success" },
  { key: "processing", label: "自动处理中", hint: "后台执行", tone: "progress" },
  { key: "hermes", label: "AI 辅助", hint: "仅在必要时", tone: "assist" },
  { key: "review", label: "待治理", hint: "不阻断入库", tone: "review" },
  { key: "failed", label: "处理失败", hint: "可以重试", tone: "danger" },
];

const fieldRoleLabels: Record<string, string> = {
  household_head: "户主",
  applicant: "申请人",
  beneficiary: "受益人",
  guardian: "监护人",
  spouse: "配偶",
  father: "父亲",
  mother: "母亲",
  child: "子女",
  contact: "联系人",
  responsible_person: "负责人",
  account_holder: "账户持有人",
  payer: "缴费人",
  member: "家庭成员",
  subject: "本人",
};

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function matchLabel(item: FileLedgerItem) {
  if (!item.match_type) return "等待匹配";
  if (item.match_type === "exact") return "精确命中";
  if (item.match_type === "partial") return "部分命中";
  return "未命中";
}

export function BatchPage({ currentUser }: { currentUser: CurrentUser }) {
  const isReadOnly = currentUser.role === "platform_admin";
  const [searchParams, setSearchParams] = useSearchParams();
  const rawFilter = searchParams.get("status") as Filter | null;
  const filter = rawFilter && fileFilters.has(rawFilter) ? rawFilter : "all";
  const villageFilter = searchParams.get("village") ?? "";
  const query = searchParams.get("q") ?? "";
  const selectedFileId = searchParams.get("file") ?? "";
  const requestedPage = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);
  const requestedPageSize = Number(searchParams.get("per_page") ?? DEFAULT_FILE_PAGE_SIZE);
  const pageSize = FILE_PAGE_SIZES.includes(
    requestedPageSize as (typeof FILE_PAGE_SIZES)[number],
  )
    ? requestedPageSize
    : DEFAULT_FILE_PAGE_SIZE;
  const offset = (requestedPage - 1) * pageSize;
  const [files, setFiles] = useState<FileLedgerItem[]>([]);
  const [capacity, setCapacity] = useState<WorkerCapacity | null>(null);
  const deferredQuery = useDeferredValue(query);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<Record<Filter, number>>({
    all: 0,
    imported: 0,
    processing: 0,
    hermes: 0,
    review: 0,
    failed: 0,
  });
  const [intakeOpen, setIntakeOpen] = useState(false);
  const [capacityOpen, setCapacityOpen] = useState(false);
  const [mode, setMode] = useState<IntakeMode>("upload");
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [selectedItem, setSelectedItem] = useState<FileLedgerItem | null>(null);
  const [profile, setProfile] = useState<WorkbookProfile | null>(null);
  const [match, setMatch] = useState<TemplateMatch | null>(null);
  const [regionMatches, setRegionMatches] = useState<RegionTemplateMatch[]>([]);
  const [fieldMatches, setFieldMatches] = useState<FieldMatch[]>([]);
  const [proposals, setProposals] = useState<TemplateProposal[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [reimporting, setReimporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const tableViewportRef = useRef<HTMLDivElement>(null);
  const directoryRequestId = useRef(0);
  const detailRequestController = useRef<AbortController | null>(null);
  const closedDetailId = useRef<string | null>(null);

  function updateDirectoryQuery(
    patch: Record<string, string | null>,
    options: { resetPage?: boolean; replace?: boolean } = {},
  ) {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(patch)) {
      if (!value || value === "all") next.delete(key);
      else next.set(key, value);
    }
    if (options.resetPage) next.delete("page");
    setSearchParams(next, { replace: options.replace });
  }

  function goToPage(page: number) {
    updateDirectoryQuery({ page: page > 1 ? String(page) : null });
    tableViewportRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  }

  function closeDetail() {
    detailRequestController.current?.abort();
    detailRequestController.current = null;
    closedDetailId.current = selectedItem?.id ?? selectedFileId ?? null;
    setSelectedItem(null);
    updateDirectoryQuery({ file: null }, { replace: true });
  }

  async function refresh(signal?: AbortSignal) {
    const requestId = ++directoryRequestId.current;
    setLoading(true);
    try {
      const [page, nextCapacity] = await Promise.all([
        getFiles({
          search: deferredQuery.trim(),
          status: filter,
          administrativeUnitId: villageFilter || undefined,
          limit: pageSize,
          offset,
        }, signal),
        getWorkerCapacity(signal),
      ]);
      if (requestId !== directoryRequestId.current) return;
      setFiles(page.items);
      setTotal(page.total);
      setCounts(page.counts);
      setCapacity(nextCapacity);
      setError("");
    } catch (cause) {
      if (requestId !== directoryRequestId.current) return;
      if (cause instanceof DOMException && cause.name === "AbortError") return;
      setError(cause instanceof Error ? cause.message : "文件台账加载失败");
    } finally {
      if (requestId === directoryRequestId.current) setLoading(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [deferredQuery, filter, offset, pageSize, villageFilter]);

  useEffect(() => {
    if (counts.processing === 0) return;
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [counts.processing, deferredQuery, filter, offset, pageSize, villageFilter]);

  useEffect(() => {
    if (!fileRef.current) return;
    if (mode === "folder") fileRef.current.setAttribute("webkitdirectory", "");
    else fileRef.current.removeAttribute("webkitdirectory");
  }, [mode, intakeOpen]);

  useEffect(() => {
    if (!total) return;
    const lastPage = Math.max(1, Math.ceil(total / pageSize));
    if (requestedPage > lastPage) goToPage(lastPage);
  }, [pageSize, requestedPage, total]);

  useEffect(() => {
    if (!selectedFileId) {
      closedDetailId.current = null;
      if (selectedItem) setSelectedItem(null);
      return;
    }
    if (closedDetailId.current === selectedFileId) return;
    if (selectedItem?.id === selectedFileId) return;
    const item = files.find((file) => file.id === selectedFileId);
    if (item) void openDetail(item, false);
  }, [files, selectedFileId, selectedItem]);

  useEffect(() => {
    if (!intakeOpen && !selectedItem) return;
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (selectedItem) closeDetail();
      else setIntakeOpen(false);
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [intakeOpen, selectedItem, searchParams]);

  async function openDetail(item: FileLedgerItem, syncUrl = true) {
    detailRequestController.current?.abort();
    const controller = new AbortController();
    detailRequestController.current = controller;
    closedDetailId.current = null;
    if (syncUrl) updateDirectoryQuery({ file: item.id }, { replace: true });
    setSelectedItem(item);
    setProfile(null);
    setMatch(null);
    setRegionMatches([]);
    setFieldMatches([]);
    setProposals([]);
    setDetailError("");
    if (!["ready", "needs_review", "materializing", "imported"].includes(item.status)) return;
    const results = await Promise.allSettled([
      getItemProfile(item.batch_id, item.id, controller.signal),
      getItemMatch(item.batch_id, item.id, controller.signal),
      getItemProposals(item.batch_id, item.id, controller.signal),
      getItemRegionMatches(item.batch_id, item.id, controller.signal),
      getItemFieldMatches(item.batch_id, item.id, controller.signal),
    ]);
    if (controller.signal.aborted) return;
    if (detailRequestController.current === controller) {
      detailRequestController.current = null;
    }
    if (results[0].status === "fulfilled") setProfile(results[0].value);
    if (results[1].status === "fulfilled") setMatch(results[1].value);
    if (results[2].status === "fulfilled") setProposals(results[2].value);
    if (results[3].status === "fulfilled") setRegionMatches(results[3].value);
    if (results[4].status === "fulfilled") setFieldMatches(results[4].value);
    if (results.every((result) => result.status === "rejected")) {
      setDetailError("处理详情尚未就绪，请稍后刷新。");
    }
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const name = String(data.get("name") ?? "").trim();
    const administrativeUnitId =
      String(data.get("administrative_unit_id") ?? "").trim() || undefined;
    if (currentUser.role === "tenant_admin" && !administrativeUnitId) {
      setError("租户管理员入库前必须选择所属村");
      return;
    }
    setBusy(true);
    setError("");
    try {
      let batch: Batch;
      if (mode === "directory") {
        const directory = String(data.get("directory") ?? "").trim();
        if (!directory) throw new Error("请输入允许读取的服务端目录");
        batch = await importDirectory(
          name || "目录导入",
          directory,
          administrativeUnitId,
        );
      } else {
        const picked = fileRef.current?.files;
        if (!picked?.length) throw new Error("请选择至少一个结构化文件");
        batch = await uploadBatch(
          name || "文件导入",
          picked,
          administrativeUnitId,
        );
      }
      form.reset();
      setSelectedFiles([]);
      setIntakeOpen(false);
      await refresh();
      if (batch.upload_failures?.length) {
        setError(`${batch.upload_failures.length} 个文件上传失败，其余文件已进入后台处理。`);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "提交失败");
    } finally {
      setBusy(false);
    }
  }

  async function reimportSelectedItem() {
    if (!selectedItem || reimporting) return;
    detailRequestController.current?.abort();
    detailRequestController.current = null;
    setReimporting(true);
    setDetailError("");
    try {
      const reset = await reimportFile(selectedItem.batch_id, selectedItem.id);
      setProfile(null);
      setMatch(null);
      setRegionMatches([]);
      setFieldMatches([]);
      setProposals([]);
      setSelectedItem((current) => current ? {
        ...current,
        ...reset,
        match_type: null,
        score_basis_points: null,
        requires_hermes: null,
        total_regions: null,
        matched_regions: null,
        coverage_basis_points: null,
        hermes_call_count: 0,
        record_count: 0,
        sheet_count: null,
      } : current);
      await refresh();
    } catch (cause) {
      setDetailError(cause instanceof Error ? cause.message : "重新入库失败");
    } finally {
      setReimporting(false);
    }
  }

  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const page = Math.min(requestedPage, pageCount);
  const rangeStart = total ? offset + 1 : 0;
  const rangeEnd = Math.min(offset + files.length, total);

  return (
    <section className="file-workspace">
      <header className="workspace-heading" data-read-only={isReadOnly || undefined}>
        <div className="workspace-heading__title">
          <div className="workspace-heading__title-row">
            <h1>{isReadOnly ? "业务文件台账" : "文件入库"}</h1>
            <div className="workspace-scope" aria-label="当前数据范围">
              <span>当前范围</span>
              <strong>{isReadOnly ? "全部业务租户" : currentUser.tenant_name}</strong>
              <i aria-hidden="true">/</i>
              <strong>
                {isReadOnly
                  ? "只读"
                  : currentUser.role === "tenant_admin"
                  ? "全部下属村"
                  : currentUser.scope_unit_name}
              </strong>
            </div>
          </div>
          {!isReadOnly ? (
            <p>批量解析复杂 Excel，确认数据是否已正确入库。</p>
          ) : null}
        </div>
        <div className="workspace-heading__actions">
          <button className="button button--ghost" type="button" onClick={() => setCapacityOpen((open) => !open)}>
            处理能力
            {capacity ? <span>{capacity.running.parse + capacity.running.hermes + capacity.running.materialize} 运行中</span> : null}
          </button>
          <button className="button button--ghost" type="button" onClick={() => void refresh()}>
            刷新
          </button>
          {!isReadOnly ? (
            <button className="button button--primary" type="button" onClick={() => setIntakeOpen(true)}>
              ＋ 导入文件
            </button>
          ) : null}
        </div>
      </header>

      {capacityOpen && capacity ? (
        <div className="capacity-panel" aria-label="后台处理能力">
          <strong>并发处理通道</strong>
          <p data-paused={capacity.resources.admission_paused}>
            {capacity.resources.admission_paused ? "内存保护已暂停领取新文件" : "内存状态正常"}
            {capacity.resources.available_memory_mb === null
              ? null
              : ` · 可用 ${capacity.resources.available_memory_mb} MB`}
          </p>
          {(["parse", "hermes", "materialize"] as const).map((lane) => (
            <div key={lane}>
              <span>{lane === "parse" ? "结构解析" : lane === "hermes" ? "AI 辅助" : "正式入库"}</span>
              <b>{capacity.running[lane]} / {capacity.lanes[lane]}</b>
              <small>排队 {capacity.queued[lane]}</small>
            </div>
          ))}
        </div>
      ) : null}

      <div className="status-filters" aria-label="文件状态筛选">
        {filterOptions.map((option) => (
          <button
            key={option.key}
            type="button"
            data-tone={option.tone}
            aria-pressed={filter === option.key}
            onClick={() => updateDirectoryQuery(
              { status: option.key },
              { resetPage: true },
            )}
          >
            <span>{option.label}</span>
            <strong>{counts[option.key]}</strong>
            <small>{option.hint}</small>
          </button>
        ))}
      </div>

      <section className="file-panel">
        <header className="file-toolbar">
          <div>
            <h2>文件台账</h2>
            <span>以文件为单位隔离处理；单个失败不会影响同批次其他文件。</span>
          </div>
          <div className="file-toolbar__controls">
            {currentUser.role === "tenant_admin" ? (
              <label className="village-filter">
                <span>所属村</span>
                <select
                  aria-label="按所属村筛选文件"
                  value={villageFilter}
                  onChange={(event) => updateDirectoryQuery(
                    { village: event.target.value },
                    { resetPage: true },
                  )}
                >
                  <option value="">全部村</option>
                  {currentUser.upload_units.map((unit) => (
                    <option key={unit.id} value={unit.id}>{unit.name}</option>
                  ))}
                </select>
              </label>
            ) : null}
            <label className="file-search">
              <span aria-hidden="true">⌕</span>
              <input
                aria-label="搜索文件、批次或村"
                value={query}
                name="file_search"
                autoComplete="off"
                onChange={(event) => updateDirectoryQuery(
                  { q: event.target.value },
                  { resetPage: true, replace: true },
                )}
                placeholder="搜索文件、批次或村…"
              />
            </label>
            <label className="file-page-size">
              <span>每页</span>
              <select
                aria-label="每页文件数量"
                value={pageSize}
                onChange={(event) => updateDirectoryQuery(
                  { per_page: event.target.value },
                  { resetPage: true },
                )}
              >
                {FILE_PAGE_SIZES.map((size) => (
                  <option key={size} value={size}>{size} 条</option>
                ))}
              </select>
            </label>
            <div className="file-toolbar__pagination" aria-label="文件快速分页">
              <span>{rangeStart}–{rangeEnd} / {total}</span>
              <button
                type="button"
                aria-label="上一页文件"
                disabled={page <= 1}
                onClick={() => goToPage(page - 1)}
              >
                ‹
              </button>
              <button
                type="button"
                aria-label="下一页文件"
                disabled={page >= pageCount}
                onClick={() => goToPage(page + 1)}
              >
                ›
              </button>
            </div>
          </div>
        </header>

        {error ? <p className="alert" role="alert">{error}</p> : null}
        <div
          className="file-table-viewport"
          data-loading={loading || undefined}
          ref={tableViewportRef}
        >
          <table className="file-table" aria-label="文件处理台账">
            <thead>
              <tr>
                <th scope="col">文件</th>
                <th scope="col">数据归属</th>
                <th scope="col">处理状态</th>
                <th scope="col">模板与 AI</th>
                <th scope="col">正式记录</th>
                <th scope="col">更新时间</th>
                <th scope="col"><span className="sr-only">查看详情</span></th>
              </tr>
            </thead>
            <tbody>
              {files.map((item) => (
                <tr key={item.id} data-selected={item.id === selectedItem?.id || undefined}>
                  <td>
                    <button
                      className="file-identity file-row-primary"
                      type="button"
                      title={item.relative_path || item.original_name}
                      onClick={() => void openDetail(item)}
                    >
                      <i aria-hidden="true">XL</i>
                      <span>
                        <strong>{item.relative_path || item.original_name}</strong>
                        <small>{formatBytes(item.size_bytes)}{item.sheet_count !== null ? ` · ${item.sheet_count} 个 Sheet` : ""}</small>
                      </span>
                    </button>
                  </td>
                  <td>
                    <strong>{item.administrative_unit_name}</strong>
                    <small>{item.batch_name}</small>
                  </td>
                  <td><StatusBadge status={item.status} /></td>
                  <td>
                    <span
                      className="file-match-status"
                      data-match={item.match_type ?? "pending"}
                    >
                      <i aria-hidden="true" />
                      {matchLabel(item)}
                    </span>
                    <small>
                      {item.total_regions !== null
                        ? `${item.matched_regions ?? 0}/${item.total_regions} 个区域复用`
                        : item.score_basis_points !== null
                          ? `${Math.round(item.score_basis_points / 100)}%`
                          : "—"}
                      {item.hermes_call_count ? ` · AI ${item.hermes_call_count} 次` : ""}
                    </small>
                  </td>
                  <td>
                    <strong className="file-record-count">{item.record_count}</strong>
                    <small>{item.record_count > 0 ? "正式记录" : "尚未入库"}</small>
                  </td>
                  <td><time dateTime={item.updated_at}>{formatDate(item.updated_at)}</time></td>
                  <td>
                    <button
                      className="file-row-open"
                      type="button"
                      aria-label={`查看 ${item.original_name} 处理详情`}
                      onClick={() => void openDetail(item)}
                    >
                      ›
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {files.length === 0 ? (
            <div className="file-empty">
              <span>暂无符合条件的文件</span>
              <p>{counts.all ? "调整筛选条件查看其他文件。" : "导入第一批 Excel 后，处理状态会显示在这里。"}</p>
              {!counts.all && !isReadOnly ? (
                <button className="button button--primary" type="button" onClick={() => setIntakeOpen(true)}>
                  导入文件
                </button>
              ) : null}
            </div>
          ) : null}
          {loading ? <span className="file-table-loading" role="status">正在更新文件台账…</span> : null}
        </div>
        <nav className="directory-pagination file-pagination" aria-label="文件分页">
          <span>{rangeStart}–{rangeEnd} / {total} 个文件</span>
          <div>
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => goToPage(page - 1)}
            >
              上一页
            </button>
            <span>第 {page} / {pageCount} 页</span>
            <button
              type="button"
              disabled={page >= pageCount}
              onClick={() => goToPage(page + 1)}
            >
              下一页
            </button>
          </div>
        </nav>
      </section>

      {intakeOpen && !isReadOnly ? (
        <div className="drawer-layer" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setIntakeOpen(false);
        }}>
          <aside className="side-drawer intake-drawer" aria-label="导入结构化文件">
            <header>
              <div><span>新建导入</span><h2>导入结构化文件</h2></div>
              <button type="button" aria-label="关闭导入面板" onClick={() => setIntakeOpen(false)}>×</button>
            </header>
            <div className="drawer-tabs" aria-label="导入方式">
              <button type="button" aria-pressed={mode === "upload"} onClick={() => setMode("upload")}>批量上传</button>
              <button type="button" aria-pressed={mode === "folder"} onClick={() => setMode("folder")}>浏览器目录</button>
              <button type="button" aria-pressed={mode === "directory"} onClick={() => setMode("directory")}>服务端目录</button>
            </div>
            <form onSubmit={submit}>
              <label>批次名称<input name="name" placeholder="例如：东河村 2026 年台账" /></label>
              {currentUser.role === "tenant_admin" ? (
                <label>
                  所属村
                  <select name="administrative_unit_id" defaultValue="">
                    <option value="" disabled>请选择文件所属村</option>
                    {currentUser.upload_units.map((unit) => (
                      <option key={unit.id} value={unit.id}>{unit.name}</option>
                    ))}
                  </select>
                  <small>只能选择 {currentUser.tenant_name} 下属村，操作人记录为 {currentUser.username}。</small>
                </label>
              ) : null}
              {mode === "directory" ? (
                <label>服务端目录<input name="directory" placeholder="/data/import/东河村" /><small>只能读取系统允许的目录。</small></label>
              ) : (
                <label className="drop-field">
                  <input
                    ref={fileRef}
                    type="file"
                    accept=".xlsx,.xls,.csv"
                    multiple
                    aria-label="选择结构化文件"
                    onChange={(event) => setSelectedFiles(Array.from(event.target.files ?? []))}
                  />
                  <strong>{mode === "folder" ? "选择一个文件夹" : "选择 Excel 或 CSV 文件"}</strong>
                  <span>支持批量处理，当前上传最多保持 2 路并发</span>
                </label>
              )}
              {selectedFiles.length ? (
                <div className="selected-manifest">
                  <strong>已选择 {selectedFiles.length} 个文件</strong>
                  {selectedFiles.slice(0, 5).map((file) => (
                    <div key={`${file.webkitRelativePath}-${file.name}-${file.size}`}><span>{file.webkitRelativePath || file.name}</span><small>{formatBytes(file.size)}</small></div>
                  ))}
                  {selectedFiles.length > 5 ? <small>另有 {selectedFiles.length - 5} 个文件</small> : null}
                </div>
              ) : null}
              <footer>
                <button className="button button--ghost" type="button" onClick={() => setIntakeOpen(false)}>取消</button>
                <button className="button button--primary" disabled={busy} type="submit">{busy ? "正在提交…" : "开始自动入库"}</button>
              </footer>
            </form>
          </aside>
        </div>
      ) : null}

      {selectedItem ? (
        <div className="drawer-layer" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) closeDetail();
        }}>
          <aside className="side-drawer detail-drawer" aria-label="文件处理详情">
            <header>
              <div><span>处理详情</span><h2>{selectedItem.original_name}</h2></div>
              <button type="button" aria-label="关闭文件详情" onClick={closeDetail}>×</button>
            </header>
            <div className="detail-summary">
              <StatusBadge status={selectedItem.status} />
              <span>{selectedItem.tenant_name} / {selectedItem.administrative_unit_name}</span>
              <span>上传人 {selectedItem.created_by_display_name}</span>
              <span>{formatBytes(selectedItem.size_bytes)}</span>
            </div>
            <IngestionStageRail item={selectedItem} />
            {detailError ? <p className="alert">{detailError}</p> : null}
            <section className="detail-section">
              <h3>结构证据</h3>
              <dl>
                <div><dt>Reader</dt><dd>{profile?.parser_name || selectedItem.parser_name || "等待解析"}</dd></div>
                <div><dt>Sheet</dt><dd>{profile?.sheets.length ?? selectedItem.sheet_count ?? "—"}</dd></div>
                <div><dt>合并区域</dt><dd>{profile ? profile.sheets.reduce((sum, sheet) => sum + sheet.merges.length, 0) : "—"}</dd></div>
                <div><dt>Region 候选</dt><dd>{profile ? profile.sheets.reduce((sum, sheet) => sum + sheet.region_candidates.length, 0) : "—"}</dd></div>
              </dl>
            </section>
            <section className="detail-section">
              <h3>模板与 AI</h3>
              <dl>
                <div><dt>匹配结果</dt><dd>{match ? matchLabel(selectedItem) : "等待匹配"}</dd></div>
                <div>
                  <dt>Region 复用</dt>
                  <dd>{match ? `${match.matched_regions}/${match.total_regions}` : "—"}</dd>
                </div>
                <div>
                  <dt>字段复用</dt>
                  <dd>
                    {fieldMatches.length
                      ? `${fieldMatches.filter((field) => !field.requires_hermes).length}/${fieldMatches.length}`
                      : "—"}
                  </dd>
                </div>
                <div><dt>AI 调用</dt><dd>{selectedItem.hermes_call_count} 次</dd></div>
                <div><dt>识别建议</dt><dd>{proposals.length} 个</dd></div>
              </dl>
              {regionMatches.length ? (
                <div className="region-match-list" aria-label="Region 模板匹配明细">
                  {regionMatches.map((region, index) => (
                    <div key={region.id}>
                      <span>
                        <strong>业务表 {index + 1}</strong>
                        <small>{region.match_type === "exact" ? "已复用模板" : "需要 AI 辅助"}</small>
                      </span>
                      <em data-state={region.match_type}>
                        {region.match_type === "exact"
                          ? "精确"
                          : `${Math.round(region.score_basis_points / 100)}%`}
                      </em>
                    </div>
                  ))}
                </div>
              ) : null}
              {fieldMatches.length ? (
                <details className="field-match-ledger">
                  <summary>
                    查看字段判断
                    <span>
                      {fieldMatches.filter((field) => field.requires_hermes).length
                        ? `${fieldMatches.filter((field) => field.requires_hermes).length} 个交给 AI`
                        : "全部直接复用"}
                    </span>
                  </summary>
                  <div>
                    {fieldMatches.map((field) => (
                      <p key={field.id}>
                        <span>
                          <strong>{field.header_path.join(" / ")}</strong>
                          <small>
                            {field.semantic_field_code
                              ? `复用 ${field.semantic_field_code}`
                              : field.differences.ambiguous
                                ? "存在多个候选"
                                : "等待语义识别"}
                            {field.context.role
                              ? ` · 角色：${fieldRoleLabels[field.context.role] ?? field.context.role}`
                              : ""}
                          </small>
                        </span>
                        <em data-state={field.match_type}>
                          {field.match_type === "exact"
                            ? "已复用"
                            : field.match_type === "partial"
                              ? "需判断"
                              : "新字段"}
                        </em>
                      </p>
                    ))}
                  </div>
                </details>
              ) : null}
              {match?.differences.new_headers?.length ? <p>新增字段：{match.differences.new_headers.join("、")}</p> : null}
            </section>
            <section className="detail-section">
              <h3>正式入库</h3>
              <div className="record-result"><strong>{selectedItem.record_count}</strong><span>条 JSONB 正式记录</span></div>
              {selectedItem.status === "needs_review" ||
              selectedItem.formal_import_status === "partial" ? (
                <Link to="/admin/reviews">前往管理端治理数据 →</Link>
              ) : null}
              {selectedItem.error_message ? <p className="alert">{selectedItem.error_message}</p> : null}
            </section>
            {!isReadOnly ? (
              <section className="detail-section reimport-action">
                <div>
                  <h3>重新解析入库</h3>
                  <p>仅清理并重建这个文件产生的数据，其他文件和已发布模板不受影响。</p>
                </div>
                <button
                  className="button button--ghost"
                  disabled={reimporting || processingStatuses.has(selectedItem.status)}
                  onClick={() => void reimportSelectedItem()}
                  type="button"
                >
                  {reimporting ? "正在重新排队…" : "重新入库"}
                </button>
              </section>
            ) : null}
          </aside>
        </div>
      ) : null}
    </section>
  );
}
