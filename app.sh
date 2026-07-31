#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_HOST="${HOST:-0.0.0.0}"
APP_PORT="${PORT:-9137}"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-9138}"
API_TARGET="http://${API_HOST}:${API_PORT}"

cd "$PROJECT_ROOT"

for command_name in curl docker uv node npm; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "缺少启动依赖：${command_name}" >&2
    exit 1
  fi
done

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "已从 .env.example 创建 .env，请按需补充模型密钥。"
fi

set -a
# shellcheck disable=SC1091
source "$PROJECT_ROOT/.env"
set +a

if [[ ! -d frontend/node_modules ]]; then
  echo "安装前端依赖..."
  npm --prefix frontend ci
fi

echo "同步 Python 依赖..."
uv sync --all-extras --frozen

postgres_container_id="$(docker compose --env-file docker/.env ps -q postgres)"
if [[ -n "$postgres_container_id" ]] \
  && [[ "$(docker inspect --format '{{.State.Running}}' "$postgres_container_id")" == "true" ]]; then
  echo "PostgreSQL 已运行，复用现有容器并等待健康检查..."
else
  echo "启动 PostgreSQL..."
fi
docker compose --env-file docker/.env up -d --wait postgres

echo "执行数据库迁移..."
uv run alembic upgrade head

# 重复执行时先清理旧进程，避免端口占用和 worker 叠加
kill_port() {
  local port="$1"
  local pids=""
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  elif command -v fuser >/dev/null 2>&1; then
    pids="$(fuser "$port"/tcp 2>/dev/null || true)"
  fi
  if [[ -z "$pids" ]]; then
    return
  fi
  echo "停止占用端口 ${port} 的旧进程: ${pids}"
  kill $pids 2>/dev/null || true
  for _ in $(seq 1 10); do
    if command -v lsof >/dev/null 2>&1; then
      pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    else
      pids="$(fuser "$port"/tcp 2>/dev/null || true)"
    fi
    [[ -z "$pids" ]] && return
    sleep 1
  done
  kill -9 $pids 2>/dev/null || true
}

kill_matching() {
  local pattern="$1"
  local pids
  pids="$(pgrep -f "$pattern" 2>/dev/null | grep -v "^$$\$" || true)"
  if [[ -z "$pids" ]]; then
    return
  fi
  echo "停止残留进程 (${pattern}): ${pids}"
  kill $pids 2>/dev/null || true
}

kill_port "$APP_PORT"
kill_port "$API_PORT"
kill_matching "uvicorn village_insight.api.app:app"
kill_matching "village-insight-worker"
kill_matching "vite.*--port ${APP_PORT}"

child_pids=()
cleaned_up=0

cleanup() {
  if (( cleaned_up )); then
    return
  fi
  cleaned_up=1
  trap - INT TERM EXIT
  if (( ${#child_pids[@]} )); then
    kill "${child_pids[@]}" 2>/dev/null || true
    wait "${child_pids[@]}" 2>/dev/null || true
  fi
}

trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
trap cleanup EXIT

export PARSE_WORKERS="${PARSE_WORKERS:-2}"
export HERMES_WORKERS="${HERMES_WORKERS:-1}"
export MATERIALIZE_WORKERS="${MATERIALIZE_WORKERS:-1}"
export VITE_UPLOAD_CONCURRENCY="${VITE_UPLOAD_CONCURRENCY:-2}"
API_READY_TIMEOUT_SECONDS="${API_READY_TIMEOUT_SECONDS:-60}"
if [[ ! "$API_READY_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "API_READY_TIMEOUT_SECONDS 必须是正整数。" >&2
  exit 1
fi

echo "启动 API：${API_TARGET}"
uv run uvicorn village_insight.api.app:app \
  --host "$API_HOST" \
  --port "$API_PORT" &
child_pids+=("$!")

echo "启动确定性解析 Worker（并发 ${PARSE_WORKERS}）..."
uv run village-insight-worker --lane parse --concurrency "$PARSE_WORKERS" &
child_pids+=("$!")

echo "启动 Hermes Worker（并发 ${HERMES_WORKERS}）..."
uv run village-insight-worker --lane hermes --concurrency "$HERMES_WORKERS" &
child_pids+=("$!")

echo "启动 JSONB 物化 Worker（并发 ${MATERIALIZE_WORKERS}）..."
uv run village-insight-worker --lane materialize --concurrency "$MATERIALIZE_WORKERS" &
child_pids+=("$!")

API_READY_URL="${API_TARGET}/api/health/ready"
echo "等待 API 就绪：${API_READY_URL}"
api_ready=0
api_ready_deadline=$((SECONDS + API_READY_TIMEOUT_SECONDS))
while (( SECONDS < api_ready_deadline )); do
  if curl --fail --silent --max-time 1 "$API_READY_URL" >/dev/null; then
    api_ready=1
    break
  fi
  if ! kill -0 "${child_pids[0]}" 2>/dev/null; then
    echo "API 在就绪前退出。" >&2
    wait "${child_pids[0]}" || true
    exit 1
  fi
  sleep 1
done
if (( ! api_ready )); then
  echo "等待 API 就绪超时（${API_READY_TIMEOUT_SECONDS} 秒）。" >&2
  exit 1
fi
echo "API 已就绪。"

echo "启动 VillageInsight：http://${APP_HOST}:${APP_PORT}"
VITE_API_TARGET="$API_TARGET" npm --prefix frontend run dev -- \
  --host "$APP_HOST" \
  --port "$APP_PORT" \
  --strictPort &
child_pids+=("$!")

set +e
wait -n "${child_pids[@]}"
exit_status=$?
set -e

if (( exit_status != 0 )); then
  echo "应用进程异常退出（状态码：${exit_status}）。" >&2
fi
exit "$exit_status"
