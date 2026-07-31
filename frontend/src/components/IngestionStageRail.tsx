import type { BatchItem } from "../lib/api";

const stages = [
  { key: "received", label: "已接收" },
  { key: "profile", label: "结构探测" },
  { key: "match", label: "模板匹配" },
  { key: "assist", label: "AI 辅助" },
  { key: "gate", label: "自动校验" },
  { key: "import", label: "JSONB 入库" },
] as const;

const progressByStatus: Record<string, number> = {
  pending: 0,
  profiling: 1,
  matching: 2,
  recognizing: 3,
  needs_review: 4,
  ready: 4,
  materializing: 5,
  imported: 6,
};

export function IngestionStageRail({ item }: { item: BatchItem }) {
  const progress = progressByStatus[item.status] ?? 0;
  const skippedAssist =
    item.status !== "recognizing" &&
    item.formal_import_status !== "needs_review" &&
    progress >= 4;

  return (
    <ol className="stage-rail" aria-label="文件处理阶段">
      {stages.map((stage, index) => {
        const completed = progress > index;
        const current = progress === index;
        const skipped = stage.key === "assist" && skippedAssist;
        return (
          <li
            data-state={
              item.status === "failed" && current
                ? "failed"
                : skipped
                  ? "skipped"
                  : completed
                    ? "completed"
                    : current
                      ? "current"
                      : "pending"
            }
            key={stage.key}
          >
            <span aria-hidden="true" />
            <small>{stage.label}</small>
          </li>
        );
      })}
    </ol>
  );
}
