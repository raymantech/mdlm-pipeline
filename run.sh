#!/usr/bin/env bash
set -euo pipefail

echo ""
echo "▶ MDLM 一键流程启动"
ROOT="$(cd "$(dirname "$0")" && pwd)"
echo "  ROOT: $ROOT"
echo ""

echo "==================== $(date '+%Y-%m-%d %H:%M:%S') START ===================="

# -----------------------------
# 1. 选择 Python 命令
# -----------------------------
if command -v python3 >/dev/null 2>&1; then
  SYS_PY=python3
elif command -v python >/dev/null 2>&1; then
  SYS_PY=python
else
  echo "[FATAL] No python found"
  exit 1
fi

echo "[INFO] system python: $SYS_PY"
$SYS_PY -V || true

# -----------------------------
# 2. 确保 venv 存在（关键修复点）
# -----------------------------
VENV="$ROOT/.venv"
PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
  echo "[BOOT] create venv at $VENV"
  $SYS_PY -m venv "$VENV"
fi

echo "[INFO] venv python: $PY"
"$PY" -V

# -----------------------------
# 3. 安装依赖（幂等）
# -----------------------------
echo "[BOOT] ensure python deps"
"$PY" -m pip install -U pip
"$PY" -m pip install -r "$ROOT/backend/requirements.txt"

# -----------------------------
# 4. 进入 backend
# -----------------------------
cd "$ROOT/backend"

# -----------------------------
# 5. 抓榜单（今日）
# -----------------------------
echo "[STEP] ingest charts (run_daily.py)"
"$PY" run_daily.py
echo "[OK]   ingest charts"

# -----------------------------
# 6. 回补缺失天（核心）
# -----------------------------
echo "[STEP] backfill missing days"
"$PY" backfill_missing_days.py
echo "[OK]   backfill missing days"

# -----------------------------
# 7. 导出前端 JSON
# -----------------------------
echo "[STEP] export dashboard json"
"$PY" export_dashboard_data.py --latest
echo "[OK]   export json"

# -----------------------------
# 8. 校验产物
# -----------------------------
OUT="$ROOT/frontend/data/merged_events_latest.json"
if [ ! -f "$OUT" ]; then
  echo "[FATAL] output not found: $OUT"
  exit 1
fi

ls -lh "$OUT"

echo ""
echo "[OK] all done"
echo ""
echo "✅ 完成：已生成/更新 $OUT"
echo "下一步：你只需要把这个 json 上传到服务器，替换同名文件即可"