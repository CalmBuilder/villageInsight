const labels: Record<string, string> = {
  pending: "待处理",
  running: "处理中",
  completed: "已完成",
  partial: "已入库 · 部分语义",
  failed: "失败",
  profiling: "提取证据",
  matching: "匹配模板",
  recognizing: "AI 识别",
  materializing: "正式入库",
  imported: "已入库",
  needs_review: "等待治理",
  ready: "画像完成",
  draft: "草稿",
  active: "已启用",
  archived: "已归档",
  user_confirmed: "用户已确认",
  admin_review: "管理员审核",
  published: "已发布",
  deprecated: "已停用",
  disabled: "已停用",
  pending_rebuild: "等待重建",
  passed: "质量通过",
  succeeded: "已完成",
  rejected: "已驳回",
};

type StatusTone = "success" | "progress" | "review" | "danger" | "inactive" | "neutral";

function toneForStatus(status: string): StatusTone {
  if (["completed", "active", "ready", "published", "imported", "passed", "succeeded"].includes(status)) {
    return "success";
  }
  if (["running", "profiling", "matching", "recognizing", "materializing"].includes(status)) {
    return "progress";
  }
  if (["pending", "needs_review", "admin_review", "pending_rebuild", "draft", "user_confirmed"].includes(status)) {
    return "review";
  }
  if (["failed", "rejected"].includes(status)) return "danger";
  if (["deprecated", "disabled", "archived"].includes(status)) return "inactive";
  return "neutral";
}

function StatusIcon({ tone }: { tone: StatusTone }) {
  if (tone === "success") {
    return (
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="8" cy="8" r="6" />
        <path d="m5 8 2 2 4-5" />
      </svg>
    );
  }
  if (tone === "progress") {
    return (
      <svg className="status__icon--progress" viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="8" cy="8" r="6" />
        <path d="M8 2a6 6 0 0 1 6 6" />
      </svg>
    );
  }
  if (tone === "review") {
    return (
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M8 1.8 14.2 8 8 14.2 1.8 8Z" />
        <path d="M8 4.8v4.1M8 11.2v.1" />
      </svg>
    );
  }
  if (tone === "danger") {
    return (
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M8 2 14 13H2Z" />
        <path d="M8 5.5v3.8M8 11.3v.1" />
      </svg>
    );
  }
  if (tone === "inactive") {
    return (
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="8" cy="8" r="6" />
        <path d="M6.2 5.3v5.4M9.8 5.3v5.4" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="8" r="3.5" />
    </svg>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const tone = toneForStatus(status);
  return (
    <span className={`status status--${status} status--tone-${tone}`}>
      <span className="status__icon"><StatusIcon tone={tone} /></span>
      {labels[status] ?? status}
    </span>
  );
}
