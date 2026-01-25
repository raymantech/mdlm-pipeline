#!/usr/bin/env bash
set -euo pipefail

# Resolve directories regardless of where you run this script from
BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$BACKEND_DIR/.." && pwd)"

LOG_DIR="$BACKEND_DIR/logs"
LOCK_DIR="$BACKEND_DIR/.lock"
mkdir -p "$LOG_DIR"

# Pick python interpreter: **force** project-local venv to avoid system Python surprises.
# (macOS often has multiple python3s; the venv is the only stable runtime here.)
if [[ -x "$ROOT_DIR/.venv/bin/python3.9" ]]; then
  PY="$ROOT_DIR/.venv/bin/python3.9"
elif [[ -x "$ROOT_DIR/.venv/bin/python3" ]]; then
  PY="$ROOT_DIR/.venv/bin/python3"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PY="$ROOT_DIR/.venv/bin/python"
else
  echo "[FATAL] No venv python found under $ROOT_DIR/.venv/bin" >&2
  echo "Create it first:" >&2
  echo "  cd $ROOT_DIR" >&2
  echo "  /usr/bin/python3 -m venv .venv" >&2
  echo "  ./.venv/bin/python -m pip install -U pip" >&2
  echo "  ./.venv/bin/python -m pip install -r backend/requirements.txt" >&2
  exit 1
fi

# Prevent overlapping runs
if mkdir "$LOCK_DIR" 2>/dev/null; then
  trap 'rmdir "$LOCK_DIR"' EXIT
else
  echo "$(date '+%F %T') [SKIP] pipeline already running" >> "$LOG_DIR/pipeline.log"
  exit 0
fi

cd "$BACKEND_DIR"

log(){
  # write both to stdout and file
  local msg="$1"
  echo "$msg"
  echo "$msg" >> "$LOG_DIR/pipeline.log"
}

log "==================== $(date '+%F %T') START ===================="
log "$(date '+%F %T') [INFO] PY=$PY"
"$PY" -V | tee -a "$LOG_DIR/pipeline.log" >/dev/null || true

run_step () {
  local name="$1"
  local cmd="$2"
  local logfile="$3"

  log "$(date '+%F %T') [STEP] $name"
  # shellcheck disable=SC2086
  eval "$cmd" >> "$logfile" 2>&1 || {
    log "$(date '+%F %T') [FAIL] $name (see $logfile)"
    log "$(date '+%F %T') [FAIL] last 60 lines of $logfile:"
    tail -n 60 "$logfile" || true
    exit 1
  }
  log "$(date '+%F %T') [OK]   $name"
}

# 1) ingest: 抓取入库（榜单抓取）
run_step "ingest charts (run_daily.py)" "\"$PY\" run_daily.py" "$LOG_DIR/ingest.log"

# 2) backfill: 自动补齐缺失区间（逐天分析 + 合并 + 导出最新 json）
run_step "backfill missing days (backfill_missing_days.py)" "\"$PY\" backfill_missing_days.py" "$LOG_DIR/backfill.log"

log "$(date '+%F %T') [OK] all done"
