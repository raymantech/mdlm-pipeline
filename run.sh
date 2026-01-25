#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "▶ MDLM1.0 一键流程启动"
echo "  ROOT: $ROOT_DIR"
echo ""

bash "$ROOT_DIR/backend/run_pipeline.sh"

OUT="$ROOT_DIR/frontend/data/merged_events_latest.json"
echo ""
if [[ -f "$OUT" ]]; then
  echo "✅ 完成：已生成/更新 $OUT"
  ls -lh "$OUT"
else
  echo "⚠️ 未找到输出文件：$OUT"
  echo "请查看 backend/logs/pipeline.log / backfill.log"
fi

echo ""
echo "下一步：你只需要把这个 json 上传到服务器，替换同名文件即可（无需替换其他前端文件）。"
