#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$PROJECT_ROOT/data/run"
LOG_DIR="$PROJECT_ROOT/logs"
SUPERVISOR_PID_FILE="$RUNTIME_DIR/app.pid"
APP_HOST="${HOST:-0.0.0.0}"
APP_PORT="${PORT:-9137}"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-9138}"
API_TARGET="http://${API_HOST}:${API_PORT}"
API_READY_URL="${API_TARGET}/api/health/ready"
API_LIVE_URL="${API_TARGET}/api/health/live"
START_TIMEOUT_SECONDS="${START_TIMEOUT_SECONDS:-180}"
STOP_TIMEOUT_SECONDS="${STOP_TIMEOUT_SECONDS:-15}"
LOG_MAX_BYTES="${LOG_MAX_BYTES:-20971520}"
LOG_BACKUP_COUNT="${LOG_BACKUP_COUNT:-5}"

COMPONENTS=(api worker-parse worker-hermes worker-materialize frontend)
child_pids=()
cleaned_up=0
log_to_files=0

cd "$PROJECT_ROOT"

usage() {
  cat <<'EOF'
用法：
  ./app.sh [start]              后台启动并等待服务就绪（默认）
  ./app.sh foreground           前台启动，便于调试
  ./app.sh stop                 停止应用进程，保留 PostgreSQL
  ./app.sh restart              重启应用进程
  ./app.sh status               查看进程和健康状态
  ./app.sh logs [组件]          跟踪日志，组件默认 all

日志组件：all、supervisor、api、worker-parse、worker-hermes、worker-materialize、frontend
EOF
}

is_positive_integer() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]]
}

validate_runtime_settings() {
  local name value
  for name in START_TIMEOUT_SECONDS STOP_TIMEOUT_SECONDS LOG_MAX_BYTES LOG_BACKUP_COUNT; do
    value="${!name}"
    if ! is_positive_integer "$value"; then
      echo "${name} 必须是正整数。" >&2
      exit 1
    fi
  done
}

read_pid_file() {
  local path="$1"
  local pid=""
  [[ -f "$path" ]] || return 1
  IFS= read -r pid < "$path" || true
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  printf '%s\n' "$pid"
}

pid_is_running() {
  local pid="$1"
  kill -0 "$pid" 2>/dev/null
}

pid_matches_supervisor() {
  local pid="$1"
  local arguments=""
  if [[ -r "/proc/${pid}/cmdline" ]]; then
    arguments="$(tr '\0' ' ' < "/proc/${pid}/cmdline")"
  else
    arguments="$(ps -o args= -p "$pid" 2>/dev/null || true)"
  fi
  [[ "$arguments" == *"$PROJECT_ROOT/app.sh _supervise"* ]]
}

supervisor_pid() {
  local pid
  pid="$(read_pid_file "$SUPERVISOR_PID_FILE")" || return 1
  pid_is_running "$pid" || return 1
  pid_matches_supervisor "$pid" || return 1
  printf '%s\n' "$pid"
}

rotate_log() {
  local path="$1"
  local size=0
  local index
  [[ -f "$path" ]] || return 0
  size="$(wc -c < "$path")"
  (( size >= LOG_MAX_BYTES )) || return 0
  for ((index = LOG_BACKUP_COUNT - 1; index >= 1; index--)); do
    if [[ -f "${path}.${index}" ]]; then
      mv -f -- "${path}.${index}" "${path}.$((index + 1))"
    fi
  done
  mv -f -- "$path" "${path}.1"
}

prepare_runtime_directories() {
  mkdir -p "$RUNTIME_DIR" "$LOG_DIR"
}

rotate_component_logs() {
  local component
  rotate_log "$LOG_DIR/supervisor.log"
  for component in "${COMPONENTS[@]}"; do
    rotate_log "$LOG_DIR/${component}.log"
  done
}

component_pid_file() {
  printf '%s/%s.pid\n' "$RUNTIME_DIR" "$1"
}

port_listener_pids() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
  elif command -v fuser >/dev/null 2>&1; then
    fuser "$port"/tcp 2>/dev/null || true
  fi
}

ensure_port_available() {
  local port="$1"
  local label="$2"
  local pids
  pids="$(port_listener_pids "$port")"
  if [[ -n "$pids" ]]; then
    echo "${label} 端口 ${port} 已被未登记进程占用（PID: ${pids//$'\n'/,}）。" >&2
    echo "请先确认并停止占用者；app.sh 不会自动终止其他进程。" >&2
    return 1
  fi
}

check_commands() {
  local command_name
  for command_name in curl docker uv node npm; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
      echo "缺少启动依赖：${command_name}" >&2
      exit 1
    fi
  done
}

load_environment() {
  if [[ ! -f .env ]]; then
    cp .env.example .env
    echo "已从 .env.example 创建 .env，请按需补充模型密钥。"
  fi
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  set +a

  export PARSE_WORKERS="${PARSE_WORKERS:-2}"
  export HERMES_WORKERS="${HERMES_WORKERS:-1}"
  export MATERIALIZE_WORKERS="${MATERIALIZE_WORKERS:-1}"
  export VITE_UPLOAD_CONCURRENCY="${VITE_UPLOAD_CONCURRENCY:-2}"
  API_READY_TIMEOUT_SECONDS="${API_READY_TIMEOUT_SECONDS:-60}"
  if ! is_positive_integer "$API_READY_TIMEOUT_SECONDS"; then
    echo "API_READY_TIMEOUT_SECONDS 必须是正整数。" >&2
    exit 1
  fi
}

prepare_application() {
  check_commands
  load_environment
  ensure_port_available "$APP_PORT" "Web"
  ensure_port_available "$API_PORT" "API"

  if [[ ! -d frontend/node_modules ]]; then
    echo "安装前端依赖..."
    npm --prefix frontend ci
  fi

  echo "构建前端静态资源..."
  VITE_API_TARGET="$API_TARGET" npm --prefix frontend run build

  echo "同步 Python 依赖..."
  uv sync --all-extras --frozen

  local postgres_container_id
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
}

start_component() {
  local component="$1"
  shift
  if (( log_to_files )); then
    "$@" >> "$LOG_DIR/${component}.log" 2>&1 &
  else
    "$@" &
  fi
  local pid=$!
  child_pids+=("$pid")
  printf '%s\n' "$pid" > "$(component_pid_file "$component")"
}

cleanup() {
  if (( cleaned_up )); then
    return
  fi
  cleaned_up=1
  trap - INT TERM EXIT

  local pid remaining=()
  if (( ${#child_pids[@]} )); then
    kill "${child_pids[@]}" 2>/dev/null || true
    local deadline=$((SECONDS + STOP_TIMEOUT_SECONDS))
    while (( SECONDS < deadline )); do
      remaining=()
      for pid in "${child_pids[@]}"; do
        if pid_is_running "$pid"; then
          remaining+=("$pid")
        fi
      done
      (( ${#remaining[@]} == 0 )) && break
      sleep 1
    done
    if (( ${#remaining[@]} )); then
      echo "部分应用进程未在 ${STOP_TIMEOUT_SECONDS} 秒内退出，执行强制清理：${remaining[*]}" >&2
      kill -9 "${remaining[@]}" 2>/dev/null || true
    fi
    wait "${child_pids[@]}" 2>/dev/null || true
  fi

  local component
  for component in "${COMPONENTS[@]}"; do
    rm -f -- "$(component_pid_file "$component")"
  done
  local recorded_pid=""
  recorded_pid="$(read_pid_file "$SUPERVISOR_PID_FILE")" || true
  if [[ "$recorded_pid" == "$$" ]]; then
    rm -f -- "$SUPERVISOR_PID_FILE"
  fi
}

run_stack() {
  prepare_runtime_directories
  trap 'cleanup; exit 130' INT
  trap 'cleanup; exit 143' TERM
  trap cleanup EXIT
  prepare_application

  echo "启动 API：${API_TARGET}"
  start_component api uv run uvicorn village_insight.api.app:app \
    --host "$API_HOST" --port "$API_PORT"

  echo "启动确定性解析 Worker（并发 ${PARSE_WORKERS}）..."
  start_component worker-parse uv run village-insight-worker \
    --lane parse --concurrency "$PARSE_WORKERS"

  echo "启动 Hermes Worker（并发 ${HERMES_WORKERS}）..."
  start_component worker-hermes uv run village-insight-worker \
    --lane hermes --concurrency "$HERMES_WORKERS"

  echo "启动 JSONB 物化 Worker（并发 ${MATERIALIZE_WORKERS}）..."
  start_component worker-materialize uv run village-insight-worker \
    --lane materialize --concurrency "$MATERIALIZE_WORKERS"

  echo "等待 API 就绪：${API_READY_URL}"
  local api_ready=0
  local api_ready_deadline=$((SECONDS + API_READY_TIMEOUT_SECONDS))
  while (( SECONDS < api_ready_deadline )); do
    if curl --fail --silent --max-time 1 "$API_READY_URL" >/dev/null; then
      api_ready=1
      break
    fi
    if ! pid_is_running "${child_pids[0]}"; then
      echo "API 在就绪前退出，请查看 $LOG_DIR/api.log。" >&2
      return 1
    fi
    sleep 1
  done
  if (( ! api_ready )); then
    echo "等待 API 就绪超时（${API_READY_TIMEOUT_SECONDS} 秒），请查看 $LOG_DIR/api.log。" >&2
    return 1
  fi
  echo "API 已就绪。"

  echo "启动构建后的 VillageInsight 前端：http://${APP_HOST}:${APP_PORT}"
  start_component frontend env VITE_API_TARGET="$API_TARGET" npm --prefix frontend run preview -- \
    --host "$APP_HOST" --port "$APP_PORT" --strictPort

  set +e
  wait -n "${child_pids[@]}"
  local exit_status=$?
  set -e
  if (( exit_status != 0 )); then
    echo "应用进程异常退出（状态码：${exit_status}）。" >&2
  else
    echo "应用组件已退出，停止其余组件。" >&2
  fi
  return "$exit_status"
}

start_background() {
  prepare_runtime_directories
  local pid component component_pid
  if pid="$(supervisor_pid)"; then
    echo "VillageInsight 已在后台运行（supervisor PID: ${pid}）。"
    show_status
    return 0
  fi
  rm -f -- "$SUPERVISOR_PID_FILE"
  for component in "${COMPONENTS[@]}"; do
    rm -f -- "$(component_pid_file "$component")"
  done
  rotate_component_logs

  nohup "$PROJECT_ROOT/app.sh" _supervise >> "$LOG_DIR/supervisor.log" 2>&1 &
  pid=$!
  printf '%s\n' "$pid" > "$SUPERVISOR_PID_FILE"
  echo "正在后台启动 VillageInsight（supervisor PID: ${pid}）..."

  local deadline=$((SECONDS + START_TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    component_pid="$(read_pid_file "$(component_pid_file frontend)")" || true
    if [[ -n "$component_pid" ]] \
      && pid_is_running "$component_pid" \
      && curl --fail --silent --max-time 1 "$API_READY_URL" >/dev/null; then
      echo "VillageInsight 已启动：http://${APP_HOST}:${APP_PORT}"
      echo "日志目录：${LOG_DIR}"
      return 0
    fi
    if ! pid_is_running "$pid"; then
      echo "VillageInsight 启动失败，最近的 supervisor 日志如下：" >&2
      tail -n 40 "$LOG_DIR/supervisor.log" >&2 || true
      return 1
    fi
    sleep 1
  done
  echo "等待后台启动超时（${START_TIMEOUT_SECONDS} 秒）。" >&2
  echo "请运行 ./app.sh status 或 ./app.sh logs supervisor 查看详情。" >&2
  return 1
}

stop_background() {
  local pid
  if ! pid="$(supervisor_pid)"; then
    rm -f -- "$SUPERVISOR_PID_FILE"
    echo "VillageInsight 当前没有已登记的后台进程。"
    return 0
  fi

  echo "正在停止 VillageInsight（supervisor PID: ${pid}）..."
  kill "$pid"
  local deadline=$((SECONDS + STOP_TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    if ! pid_is_running "$pid"; then
      rm -f -- "$SUPERVISOR_PID_FILE"
      echo "VillageInsight 已停止；PostgreSQL 保持运行。"
      return 0
    fi
    sleep 1
  done
  echo "supervisor 未在 ${STOP_TIMEOUT_SECONDS} 秒内退出，请查看日志后再处理 PID ${pid}。" >&2
  return 1
}

show_status() {
  local pid component component_pid
  if pid="$(supervisor_pid)"; then
    echo "supervisor: 运行中（PID ${pid}）"
  else
    echo "supervisor: 未运行"
  fi
  for component in "${COMPONENTS[@]}"; do
    if component_pid="$(read_pid_file "$(component_pid_file "$component")")" \
      && pid_is_running "$component_pid"; then
      echo "${component}: 运行中（PID ${component_pid}）"
    else
      echo "${component}: 未运行"
    fi
  done
  if curl --fail --silent --max-time 1 "$API_LIVE_URL" >/dev/null; then
    echo "API live: 正常"
  else
    echo "API live: 不可用"
  fi
  if curl --fail --silent --max-time 1 "$API_READY_URL" >/dev/null; then
    echo "API ready: 正常"
  else
    echo "API ready: 不可用"
  fi
  echo "日志目录：${LOG_DIR}"
}

show_logs() {
  local component="${1:-all}"
  local files=()
  prepare_runtime_directories
  case "$component" in
    all)
      files+=("$LOG_DIR/supervisor.log")
      local name
      for name in "${COMPONENTS[@]}"; do
        files+=("$LOG_DIR/${name}.log")
      done
      ;;
    supervisor|api|worker-parse|worker-hermes|worker-materialize|frontend)
      files+=("$LOG_DIR/${component}.log")
      ;;
    *)
      echo "未知日志组件：${component}" >&2
      usage >&2
      return 2
      ;;
  esac
  local file
  for file in "${files[@]}"; do
    touch "$file"
  done
  tail -n 200 -F "${files[@]}"
}

validate_runtime_settings
command="${1:-start}"
case "$command" in
  start)
    start_background
    ;;
  foreground)
    if supervisor_pid >/dev/null; then
      echo "后台实例正在运行，请先执行 ./app.sh stop。" >&2
      exit 1
    fi
    log_to_files=0
    run_stack
    ;;
  stop)
    stop_background
    ;;
  restart)
    stop_background
    start_background
    ;;
  status)
    show_status
    ;;
  logs)
    show_logs "${2:-all}"
    ;;
  _supervise)
    log_to_files=1
    run_stack
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "未知命令：${command}" >&2
    usage >&2
    exit 2
    ;;
esac
