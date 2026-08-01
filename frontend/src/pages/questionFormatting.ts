import type { QuestionRun } from "../lib/api";

export function formatQueryDuration(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  if (seconds < 60) return `${seconds}秒`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder ? `${minutes}分 ${remainder}秒` : `${minutes}分`;
}

export function storedRunDuration(run: QuestionRun): string {
  if (!run.completed_at) return "";
  const startedAt = Date.parse(run.started_at);
  const completedAt = Date.parse(run.completed_at);
  if (!Number.isFinite(startedAt) || !Number.isFinite(completedAt)) return "";
  return formatQueryDuration(completedAt - startedAt);
}

export function renderCell(value: unknown): string {
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
