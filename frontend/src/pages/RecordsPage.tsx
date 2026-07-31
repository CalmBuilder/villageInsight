import { useEffect, useMemo, useState } from "react";
import {
  getDatasetRecords,
  getDatasetRecordTree,
  type DatasetRecord,
  type DatasetRecordFilePage,
  type DatasetRecordGroup,
  type DatasetRecordPage,
} from "../lib/api";
import { StatusBadge } from "../components/StatusBadge";

const recordTypeLabels: Record<string, string> = {
  population_person: "人口信息",
  agriculture_crop: "农作物登记",
  household: "农户信息",
};

const FILE_PAGE_SIZE = 10;
const RECORD_PAGE_SIZE = 25;

function recordLabel(record: Pick<DatasetRecord, "record_type">) {
  return recordTypeLabels[record.record_type]
    ?? record.record_type.replaceAll("_", " ");
}

function groupKey(group: DatasetRecordGroup) {
  return [group.item_id, group.sheet_id, group.region_id, group.record_type].join(":");
}

export function RecordsPage() {
  const [tree, setTree] = useState<DatasetRecordFilePage | null>(null);
  const [records, setRecords] = useState<DatasetRecordPage | null>(null);
  const [selectedGroupKey, setSelectedGroupKey] = useState("");
  const [expandedItems, setExpandedItems] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<DatasetRecord | null>(null);
  const [qualityStatus, setQualityStatus] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [recordOffset, setRecordOffset] = useState(0);
  const [error, setError] = useState("");
  const [detailTab, setDetailTab] = useState<"fields" | "evidence" | "raw">("fields");

  useEffect(() => {
    const controller = new AbortController();
    void getDatasetRecordTree(
      {
        qualityStatus: qualityStatus || undefined,
        limit: FILE_PAGE_SIZE,
        offset,
      },
      controller.signal,
    )
      .then((nextPage) => {
        setTree(nextPage);
        const allChildren = nextPage.items.flatMap((file) => file.children);
        setSelectedGroupKey((current) => (
          allChildren.some((group) => groupKey(group) === current)
            ? current
            : (allChildren[0] ? groupKey(allChildren[0]) : "")
        ));
        setExpandedItems((current) => {
          const next = new Set(
            [...current].filter((itemId) =>
              nextPage.items.some((file) => file.item_id === itemId)),
          );
          if (!next.size && nextPage.items[0]) next.add(nextPage.items[0].item_id);
          return next;
        });
        setRecordOffset(0);
        setError("");
      })
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(cause instanceof Error ? cause.message : "入库文件加载失败");
      });
    return () => controller.abort();
  }, [offset, qualityStatus]);

  const searchedFiles = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase("zh-CN");
    if (!needle) return tree?.items ?? [];
    return (tree?.items ?? []).filter((file) => (
      [
        file.source_file_name,
        file.administrative_unit_name,
        ...file.children.flatMap((group) => [
          group.sheet_name,
          group.record_type,
          recordLabel(group),
        ]),
      ]
        .join(" ")
        .toLocaleLowerCase("zh-CN")
        .includes(needle)
    ));
  }, [search, tree]);
  const visibleChildren = useMemo(
    () => searchedFiles.flatMap((file) => file.children),
    [searchedFiles],
  );
  const selectedGroup = visibleChildren.find(
    (group) => groupKey(group) === selectedGroupKey,
  ) ?? visibleChildren[0] ?? null;

  useEffect(() => {
    if (!selectedGroup) {
      setRecords(null);
      setSelected(null);
      return;
    }
    setSelectedGroupKey(groupKey(selectedGroup));
    const controller = new AbortController();
    void getDatasetRecords(
      {
        itemId: selectedGroup.item_id,
        sheetId: selectedGroup.sheet_id,
        regionId: selectedGroup.region_id,
        recordType: selectedGroup.record_type,
        qualityStatus: qualityStatus || undefined,
        limit: RECORD_PAGE_SIZE,
        offset: recordOffset,
      },
      controller.signal,
    )
      .then((nextPage) => {
        setRecords(nextPage);
        setSelected((current) => (
          nextPage.items.find((record) => record.id === current?.id)
          ?? nextPage.items[0]
          ?? null
        ));
      })
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(cause instanceof Error ? cause.message : "数据集记录加载失败");
      });
    return () => controller.abort();
  }, [qualityStatus, recordOffset, selectedGroup]);

  const currentPage = Math.floor(offset / FILE_PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil((tree?.total ?? 0) / FILE_PAGE_SIZE));
  const recordPage = Math.floor(recordOffset / RECORD_PAGE_SIZE) + 1;
  const recordPageCount = Math.max(
    1,
    Math.ceil((records?.total ?? 0) / RECORD_PAGE_SIZE),
  );
  const pageRecords = tree?.items.reduce(
    (total, file) => total + file.record_count,
    0,
  ) ?? 0;
  const pagePassed = tree?.items.reduce(
    (total, file) => total + file.passed_count,
    0,
  ) ?? 0;
  const pageRebuild = tree?.items.reduce(
    (total, file) => total + file.pending_rebuild_count,
    0,
  ) ?? 0;

  function toggleFile(itemId: string) {
    setExpandedItems((current) => {
      const next = new Set(current);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  }

  function chooseGroup(group: DatasetRecordGroup) {
    setSelectedGroupKey(groupKey(group));
    setRecordOffset(0);
    setDetailTab("fields");
  }

  return (
    <section className="records-stage">
      <header className="records-summary">
        <div>
          <span className="eyebrow">AUTHORITATIVE DATASET RECORDS</span>
          <h2>正式入库记录</h2>
          <p>一份文件一个目录，展开 Sheet 与数据区，再抽查记录和来源证据。</p>
        </div>
        <dl>
          <div><dt>来源文件</dt><dd>{tree?.total ?? "—"}</dd></div>
          <div><dt>本页记录</dt><dd>{pageRecords}</dd></div>
          <div><dt>本页通过</dt><dd>{pagePassed}</dd></div>
        </dl>
      </header>

      <div className="record-toolbar">
        <label>
          搜索当前页
          <input
            onChange={(event) => setSearch(event.target.value)}
            placeholder="文件、Sheet、记录类型…"
            type="search"
            value={search}
          />
        </label>
        <label>
          质量状态
          <select
            aria-label="质量状态"
            value={qualityStatus}
            onChange={(event) => {
              setQualityStatus(event.target.value);
              setOffset(0);
            }}
          >
            <option value="">全部状态</option>
            <option value="passed">通过</option>
            <option value="failed">需处理</option>
            <option value="unknown">迁移后待重建</option>
          </select>
        </label>
        <nav className="record-pagination" aria-label="文件分页">
          <span>第 {currentPage} / {pageCount} 页</span>
          <button
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - FILE_PAGE_SIZE))}
            type="button"
          >
            上一页
          </button>
          <button
            disabled={offset + FILE_PAGE_SIZE >= (tree?.total ?? 0)}
            onClick={() => setOffset(offset + FILE_PAGE_SIZE)}
            type="button"
          >
            下一页
          </button>
        </nav>
      </div>

      {error && <p className="alert" role="alert">{error}</p>}
      <div className="record-explorer">
        <div className="record-tree" role="tree" aria-label="正式入库文件目录">
          <div className="record-tree__head">
            <span>文件 / Sheet / 数据区</span>
            <span>记录</span>
            <span>质量</span>
          </div>
          {searchedFiles.length ? searchedFiles.map((file) => {
            const expanded = expandedItems.has(file.item_id) || Boolean(search.trim());
            return (
              <section className="record-tree__file" key={file.item_id}>
                <button
                  aria-expanded={expanded}
                  className="record-tree__root"
                  onClick={() => toggleFile(file.item_id)}
                  role="treeitem"
                  type="button"
                >
                  <span className="record-tree__chevron" aria-hidden="true">
                    {expanded ? "−" : "+"}
                  </span>
                  <span>
                    <strong title={file.source_file_name}>{file.source_file_name}</strong>
                    <small>
                      {file.administrative_unit_name}
                      {" · "}
                      {file.dataset_count} 个子数据集
                    </small>
                  </span>
                  <code>{file.record_count} 条</code>
                  <span className="record-quality">
                    <StatusBadge
                      status={file.failed_count
                        ? "failed"
                        : file.pending_rebuild_count
                          ? "pending_rebuild"
                          : "passed"}
                    />
                    <code>{file.passed_count}/{file.record_count}</code>
                  </span>
                </button>
                {expanded ? (
                  <div className="record-tree__children" role="group">
                    {file.children.map((group, childIndex) => (
                      <button
                        data-selected={groupKey(group) === selectedGroupKey}
                        key={groupKey(group)}
                        onClick={() => chooseGroup(group)}
                        role="treeitem"
                        type="button"
                      >
                        <span className="record-tree__branch" aria-hidden="true" />
                        <span>
                          <strong>Sheet：{group.sheet_name}</strong>
                          <small>
                            {recordLabel(group)}
                            {" · 数据区 "}
                            {childIndex + 1}
                          </small>
                        </span>
                        <code>
                          {group.record_count} 条
                          {" · 行 "}
                          {group.min_source_row}–{group.max_source_row}
                        </code>
                        <span className="record-quality">
                          <StatusBadge
                            status={group.failed_count
                              ? "failed"
                              : group.pending_rebuild_count
                                ? "pending_rebuild"
                                : "passed"}
                          />
                          <code>{group.passed_count}/{group.record_count}</code>
                        </span>
                      </button>
                    ))}
                  </div>
                ) : null}
              </section>
            );
          }) : tree ? (
            <p className="record-table__empty">当前筛选条件下没有正式记录。</p>
          ) : (
            <p className="record-table__empty">正在加载入库文件目录…</p>
          )}
        </div>

        <aside className="json-inspector" aria-label="JSONB 记录详情">
          {selected && selectedGroup ? (
            <>
              <header>
                <span>DATASET RECORD</span>
                <strong>{recordLabel(selected)}</strong>
                <small>
                  {selectedGroup.source_file_name}
                  {" · Sheet "}
                  {selectedGroup.sheet_name}
                  {" · "}
                  {selectedGroup.record_count} 条
                </small>
              </header>
              <nav className="record-sample-nav" aria-label="当前数据集记录">
                <div className="record-sample-nav__pagination">
                  <span>
                    样本记录 · 第 {recordPage} / {recordPageCount} 页
                  </span>
                  <span>
                    <button
                      disabled={recordOffset === 0}
                      onClick={() => setRecordOffset(Math.max(
                        0,
                        recordOffset - RECORD_PAGE_SIZE,
                      ))}
                      type="button"
                    >
                      上一页
                    </button>
                    <button
                      disabled={recordOffset + RECORD_PAGE_SIZE >= (records?.total ?? 0)}
                      onClick={() => setRecordOffset(recordOffset + RECORD_PAGE_SIZE)}
                      type="button"
                    >
                      下一页
                    </button>
                  </span>
                </div>
                <div>
                  {records?.items.map((record) => (
                    <button
                      aria-pressed={record.id === selected.id}
                      key={record.id}
                      onClick={() => setSelected(record)}
                      type="button"
                    >
                      第 {record.source_row} 行
                    </button>
                  ))}
                </div>
              </nav>
              <nav className="record-detail-tabs" aria-label="记录详情">
                <button
                  aria-pressed={detailTab === "fields"}
                  onClick={() => setDetailTab("fields")}
                  type="button"
                >
                  业务字段
                </button>
                <button
                  aria-pressed={detailTab === "evidence"}
                  onClick={() => setDetailTab("evidence")}
                  type="button"
                >
                  来源证据
                </button>
                <button
                  aria-pressed={detailTab === "raw"}
                  onClick={() => setDetailTab("raw")}
                  type="button"
                >
                  原始 JSON
                </button>
              </nav>
              {detailTab === "fields" ? (
                <section>
                  <h3>标准业务字段</h3>
                  <pre>{JSON.stringify(selected.semantic_data, null, 2)}</pre>
                </section>
              ) : null}
              {detailTab === "evidence" ? (
                <section>
                  <h3>证据轨</h3>
                  <ol className="evidence-rail">
                    <li><span>文件</span><strong>{selected.source_file_name || "未知文件"}</strong></li>
                    <li><span>行政范围</span><strong>{selected.administrative_unit_name || "未知范围"}</strong></li>
                    <li>
                      <span>Sheet</span>
                      <strong title={selected.sheet_id}>{selectedGroup.sheet_name}</strong>
                    </li>
                    <li><span>Region</span><strong title={selected.region_id}>{selected.region_id || "待重建"}</strong></li>
                    <li><span>原表行</span><strong>第 {selected.source_row} 行</strong></li>
                  </ol>
                </section>
              ) : null}
              {detailTab === "raw" ? (
                <section>
                  <h3>原始字段 / raw_data</h3>
                  <pre>{JSON.stringify(selected.raw_data, null, 2)}</pre>
                </section>
              ) : null}
            </>
          ) : (
            <p>展开文件并选择一个 Sheet 数据区查看记录和单元格来源。</p>
          )}
        </aside>
      </div>
      <p className="records-footnote">
        当前页 {tree?.items.length ?? 0} 个文件、{pageRecords} 条记录，
        {pageRebuild} 条待重建；文件分页与样本记录分页相互独立。
      </p>
    </section>
  );
}
