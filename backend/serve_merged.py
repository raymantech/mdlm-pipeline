"""
极简 FastAPI 服务：从 backend/test_merged_result.json 读取并暴露 /api/merged。
仅在用户选择启用时使用；默认前端通过 public 静态文件加载 JSON。

启动：
  pip install fastapi uvicorn
  uvicorn backend.serve_merged:app --reload --port 8000

  # 或在项目根目录
  python -m uvicorn backend.serve_merged:app --reload --port 8000
"""
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

APP = FastAPI(title="MDLM Merged API", version="0.1.0")
ROOT = Path(__file__).resolve().parent
DEFAULT_JSON = ROOT / "test_merged_result.json"

APP.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@APP.get("/api/merged")
def get_merged():
    """返回 test_merged_result.json 内容。"""
    if not DEFAULT_JSON.exists():
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "path": str(DEFAULT_JSON)},
        )
    with open(DEFAULT_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data
