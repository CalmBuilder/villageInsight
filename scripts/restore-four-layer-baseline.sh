#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BASELINE_DIRECTORY="$PROJECT_ROOT/backups/20260730-post-ingestion-four-layer-baseline"
BASELINE_SNAPSHOT="$BASELINE_DIRECTORY/template-catalog-snapshot.json"

cd "$PROJECT_ROOT"

if [[ ! -f "$BASELINE_SNAPSHOT" ]]; then
  echo "找不到四层模板基线：$BASELINE_SNAPSHOT" >&2
  exit 1
fi

if [[ "${1:-}" == "--dry-run" ]]; then
  uv run python -m village_insight.templates.catalog_snapshot restore \
    --input "$BASELINE_SNAPSHOT" \
    --dry-run
  exit 0
fi

if [[ "${1:-}" != "--yes" ]]; then
  echo "即将把四层模板的发布状态恢复到 2026-07-30 全村入库验收基线。"
  echo "入库文件和 JSONB 记录不会删除；基线之后新增的模板会停止参与匹配。"
  read -r -p "输入 RESTORE 确认恢复：" answer
  if [[ "$answer" != "RESTORE" ]]; then
    echo "已取消。"
    exit 0
  fi
fi

timestamp="$(date +%Y%m%d-%H%M%S)"
pre_restore_snapshot="$BASELINE_DIRECTORY/pre-restore-$timestamp.json"

uv run python -m village_insight.templates.catalog_snapshot create \
  --output "$pre_restore_snapshot"
uv run python -m village_insight.templates.catalog_snapshot restore \
  --input "$BASELINE_SNAPSHOT" \
  --confirm

echo "四层模板已恢复到验收基线。"
echo "恢复前的模板状态已保存到：$pre_restore_snapshot"
