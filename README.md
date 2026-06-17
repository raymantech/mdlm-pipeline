# MDLM 1.0 (Merged: Frontend + Backend)

This folder contains BOTH:
- `frontend/` : static dashboard served at https://mdlm.hawnlink.cn/
- `backend/`  : data pipeline that outputs `frontend/data/merged_events_latest.json`

## One-command run (local)

```bash
bash run.sh
```

It will:
1) fetch charts into SQLite (`backend/charts.db`)
2) analyze events
3) merge events
4) export JSON to: `frontend/data/merged_events_latest.json`

Then you only upload/replace that JSON on your server.

## Requirements

- Python 3.10+
- Dependencies:
  ```bash
  pip install -r backend/requirements.txt
  ```

Optional:
- `.env` can be placed at project root (`./.env`) or `backend/.env`.
- `python-dotenv` is NOT required; we parse `.env` ourselves.

## Move the folder anywhere

All scripts resolve paths from their own location, so you can move/rename this whole folder freely and it will still work.

## 每日数据管道 (run_daily.py)

### 运行完整管道

```bash
# 基本运行（包含 QQ、酷狗、网易云）
python backend/run_daily.py

# 指定日期
python backend/run_daily.py --date 2026-01-28

# 启用抖音汽水热歌榜抓取（CLI 参数）
python backend/run_daily.py --enable-douyin-qishui

# 启用抖音汽水热歌榜抓取（环境变量）
ENABLE_DOUYIN_QISHUI=1 python backend/run_daily.py

# 同时指定日期和启用汽水抓取
python backend/run_daily.py --date 2026-01-28 --enable-douyin-qishui

# 仅执行抖音汽水抓取+分析导出（用于本地调试，防止 DB 被刷爆）
python backend/run_daily.py --only qishui
```

**参数说明**：
- `--date YYYY-MM-DD`：指定分析日期（默认：今天）
- `--enable-douyin-qishui`：启用抖音汽水热歌榜抓取（默认关闭）
- `--skip-kugou`：跳过酷狗音乐抓取（临时开关，用于解决数据库 schema 问题）
- `--only qishui`：仅执行抖音汽水抓取+分析导出，跳过 QQ/酷狗/网易云（用于本地调试）

**环境变量**：
- `ENABLE_DOUYIN_QISHUI=1`：启用抖音汽水热歌榜抓取（等同于 `--enable-douyin-qishui`）
- `SKIP_KUGOU=1`：跳过酷狗音乐抓取（等同于 `--skip-kugou`）

**注意**：
- 抖音汽水热歌榜抓取默认关闭，需要手动开启。启用时会自动跳过原有的 `ingest_douyin.py` 步骤，避免同平台重复写入。
- TopN 语义一致性：启用 qishui 时，`run_daily.py` 会显式传递 `--top 100` 给 `ingest_douyin_qishui.py`，保证抓取目标为 Top 100。如果实际抓取数量不足 100，会在日志中说明（不视为错误）。
- 数据库路径：
  - 主数据库路径：`backend/charts.db`（由环境变量 `MDLM_DB` 或默认 `ROOT/charts.db` 决定）
  - `data/charts.db`：用于 GitHub Actions 的 data 分支备份，若存在且为 0B，应视为误生成文件，可安全删除
- 数据库迁移：`db_init.py` 会自动为 `chart_entry` 表添加 `score` 列（REAL 类型，允许 NULL），用于酷狗音乐数据。迁移是幂等的，重复运行不会报错。
- 其他平台（QQ、酷狗、网易云）不受影响。

### merged_events_latest.json 关键实现说明

- **关键实现思路**
  - **前端隐藏事件类型（static_site/app.js）**：默认展示全部事件类型；通过常量 `HIDDEN_EVENT_TYPES = new Set(["掉出榜"])` 做黑名单过滤。在「事件类型分布」图表的 labels 与「事件类型」筛选下拉的 options 生成处，用 `filter(l => !HIDDEN_EVENT_TYPES.has(l))` 排除黑名单类型；图表点击筛选时使用的也是过滤后的 labels，故不会出现黑名单类型。以后新增要隐藏的类型，直接往 Set 里加中文名即可。
  - **后端 7 天裁剪（backend/export_dashboard_data.py）**：在写入 `merged_events_latest.json` 前调用 `prune_events_last_n_days(events, n=7, tz="Asia/Shanghai")`，以北京时间 today 为基准，只保留 [today-6, today] 共 7 天事件。日期字段兼容 `event_date` / `date` / `day`；若某条缺失或解析失败则保留该条并打 `[WARN]`，不丢弃。不改动上游抓取与 merge，仅在最终导出前做一次裁剪。
  - **导出末尾统计（同上脚本）**：写入完成后用 `out_path.stat().st_size` 取文件大小，按 ≥1MB 显示 `x.xMB`、否则 `x.xKB`，并打印 `[INFO] merged_events_latest.json: events=<count> size=<size>`，无新依赖。

- **关键代码 Diff（节选）**
  - **static_site/app.js**  
    常量与图表/下拉过滤：
    ```js
    /** 事件类型展示配置：默认显示全部类型，此处列出的类型不会出现在「事件类型分布」图表和筛选下拉中（仅前端过滤，不改数据源）。以后新增要隐藏的类型，直接往 Set 里加中文名即可。 */
    const HIDDEN_EVENT_TYPES = new Set(["掉出榜"]);
    // ...
    const labels = Object.keys(tMap).filter(l => !HIDDEN_EVENT_TYPES.has(l));
    // datasets[0].data: labels.map(l => tMap[l])
    // 下拉: types = [...new Set(RAW.map(x => x._mTag))].filter(t => !HIDDEN_EVENT_TYPES.has(t));
    ```
  - **backend/export_dashboard_data.py**  
    裁剪函数与写入前调用、末尾统计：
    ```python
    def _event_date_str(ev): ...
    def prune_events_last_n_days(events, n=7, tz="Asia/Shanghai") -> Tuple[List, int]:
        today = beijing_today()
        start = today - timedelta(days=n - 1)
        # 遍历 events：有日期且在 [start,today] 保留，缺日期/解析失败则保留并 warn
        return kept, dropped_count

    # main 中写入前：
    final_events, dropped_count = prune_events_last_n_days(final_events, n=7, tz="Asia/Shanghai")
    print(f"[INFO] prune_last_days: keep={len(final_events)} drop={dropped_count} n=7")
    # 写入后：
    size_bytes = out_path.stat().st_size
    size_str = f"{size_bytes / (1024 * 1024):.1f}MB" if size_bytes >= 1024 * 1024 else f"{size_bytes / 1024:.1f}KB"
    print(f"[INFO] {out_path.name}: events={len(final_events)} size={size_str}")
    ```

## 汽水 App（抖音侧）榜单合并

将汽水 App 截图 OCR 结果转为标准 events 并可选并入 `merged_events_latest.json`。**抖音（DOUYIN_QISHUI）为可缺席源**：`data/events_qishui_latest.json` 不存在时导出流程照常运行并打 `[SKIP]`，GitHub Actions 不依赖、不调用、不检查抖音相关文件；抖音数据仅通过**手动运行**脚本合并。

**新增/修改文件**：
- **新增** `backend/sources/qishui_app_to_events.py`：读 `data/qishui_app_ocr_latest.json`，写 `data/events_qishui_latest.json`（events 数组；含 date, platform=DOUYIN_QISHUI, source, chart=hot|new, rank, track_name, artist_name, event_type=DOMINANT|NEW, fingerprint, ts）。
- **修改** `backend/export_dashboard_data.py`：可选加载 `data/events_qishui_latest.json`，按 (date, platform, chart, rank) 硬去重并入后继续 7 天裁剪与写入。
- **修改** `static_site/app.js`：`platform=DOUYIN_QISHUI` 显示为「抖音/汽水」；事件类型 DOMINANT/NEW 沿用现有 TAG_MAP（持续霸榜/首发新歌）。

**本地运行命令（从 OCR → events → merged）**：

```bash
# 1) OCR 榜单 → 标准 events（需先有 data/qishui_app_ocr_latest.json，由 ocr_qishui_app_screenshots_tencent.py 生成）
python3 backend/sources/qishui_app_to_events.py

# 2) 生成 merged_events_latest.json（会读 DB + 可选读 data/events_qishui_latest.json 并入；输出到 frontend/data/ 或 --out 指定路径）
python3 backend/export_dashboard_data.py --out static_site/data/merged_events_latest.json
# 若未生成 events_qishui_latest.json，会打印 [SKIP] 汽水 events 不存在，跳过，不影响合并结果
```

一条龙（先 OCR 再转 events 再导出，按需执行）：

```bash
python3 backend/ocr_qishui_app_screenshots_tencent.py   # 可选：生成 qishui_app_ocr_latest.json
python3 backend/sources/qishui_app_to_events.py         # 生成 events_qishui_latest.json
python3 backend/export_dashboard_data.py --out static_site/data/merged_events_latest.json
```

## 汽水热歌榜抓取

### 运行脚本

```bash
# 导入到数据库（默认抓取 Top 100，输出到 data/douyin_qishui_latest.json）
python backend/ingest_douyin_qishui.py

# 指定日期并归档（幂等入库，同一天重复运行不会产生重复数据）
python backend/ingest_douyin_qishui.py --date 2026-01-28 --archive

# 指定 URL 和数量，自定义输出路径
python backend/ingest_douyin_qishui.py --url https://www.douyin.com/qishui/playlist/7456953055192696858 --top 50 --out custom.json

# 只输出 JSON，不写入数据库
python backend/ingest_douyin_qishui.py --no-import
```

**参数说明**：
- `--date YYYY-MM-DD`：指定目标日期，用于幂等入库（默认：今天）
- `--archive`：归档到日期文件 `data/douyin_qishui_YYYY-MM-DD.json`
- `--out PATH`：自定义输出路径（默认：`data/douyin_qishui_latest.json`，相对项目根目录）
- `--no-import`：跳过数据库导入，仅输出 JSON
- `--top N`：抓取前 N 首（默认：100）

**输出文件**：
- 默认输出：`data/douyin_qishui_latest.json`（每次运行覆盖）
- 归档文件：`data/douyin_qishui_YYYY-MM-DD.json`（使用 `--archive` 时生成）

**幂等性**：同一天同榜单重复运行不会产生重复 snapshot/entries，会自动删除当天已有数据后重新插入。

### JSON Schema

输出 JSON 格式（格式 A）：
```json
{
  "source": "https://www.douyin.com/qishui/playlist/7456953055192696858",
  "fetched_at": "2026-01-28T10:30:00",
  "playlist_id": "7456953055192696858",
  "tracks": [
    {
      "rank": 1,
      "track_platform_id": "qishui:abc123...",
      "track_name": "歌曲名",
      "artist_name_raw": "艺人名",
      "heat": 0,
      "extra_metrics": {
        "playlist_id": "7456953055192696858",
        "source": "qishui_playlist"
      }
    }
  ]
}
```

### 验收

1. **验证文件输出**：检查默认输出文件是否存在
   ```bash
   ls -lh data/douyin_qishui_latest.json
   ```

2. **验证数据库写入**：检查 `backend/charts.db` 中是否有新数据
   ```bash
   sqlite3 backend/charts.db "SELECT COUNT(*) FROM chart_entry WHERE snapshot_id IN (SELECT id FROM chart_snapshot WHERE chart_id IN (SELECT id FROM chart WHERE name='热歌榜' AND platform_id IN (SELECT id FROM platform WHERE name='抖音(汽水)')))"
   ```

3. **验证幂等性**：同一天重复运行不应产生重复数据
   ```bash
   python backend/ingest_douyin_qishui.py --date 2026-01-28
   python backend/ingest_douyin_qishui.py --date 2026-01-28  # 再次运行
   sqlite3 backend/charts.db "SELECT date(captured_at) as day, COUNT(*) as snapshot_count FROM chart_snapshot WHERE chart_id IN (SELECT id FROM chart WHERE name='热歌榜' AND platform_id IN (SELECT id FROM platform WHERE name='抖音(汽水)')) GROUP BY day ORDER BY day DESC LIMIT 5"
   ```
   每个日期应该只有 1 个 snapshot。

4. **验证黑马读取**：运行黑马分析脚本
   ```bash
   python backend/dark_horse.py
   ```
   脚本会从数据库读取最新数据并输出分析结果。

5. **验证数据库迁移**：检查 `chart_entry` 表是否包含 `score` 列
   ```bash
   sqlite3 backend/charts.db "PRAGMA table_info(chart_entry);" | grep score
   ```
   应该能看到 `score` 列（类型为 REAL）。

## 汽水 App 截图 OCR（腾讯云）

从汽水音乐 App 截图（热歌榜/新歌榜 Top20）中识别文字并输出结构化 JSON，不入库、不合并主站。

### 安装依赖

```bash
pip install tencentcloud-sdk-python-ocr
```

### 环境变量（必填）

| 变量 | 说明 |
|------|------|
| `TENCENT_SECRET_ID` | 腾讯云 SecretId（控制台获取） |
| `TENCENT_SECRET_KEY` | 腾讯云 SecretKey |
| `TENCENT_REGION` | 地域，可选，默认 `ap-guangzhou` |

### 命令

```bash
# 默认：读取 /Users/ray/Desktop/qishui_inbox/ 下 qishui_hot.png、qishui_new.png，输出 data/qishui_app_ocr_latest.json
python backend/ocr_qishui_app_screenshots_tencent.py

# 指定截图目录与输出路径
python backend/ocr_qishui_app_screenshots_tencent.py --dir /path/to/screenshots --out data/qishui_app_ocr_latest.json

# 指定日期（默认北京时间当天）
python backend/ocr_qishui_app_screenshots_tencent.py --date 2026-01-31
```

### 输出

- 默认输出：`data/qishui_app_ocr_latest.json`
- Schema：`generated_at`、`date`、`source`、`charts.hot` / `charts.new`（每条含 `rank`、`track_name`、`artist_name`）、`debug.hot_raw_lines` / `debug.new_raw_lines`

## 本地构建线上 merged JSON（一键净化+合并汽水）

从 GitHub 下载 Action 生成的 `merged_events_latest.json` 后，用本脚本做本地净化、合并汽水、输出可直接推送到线上的 JSON。

### 目录约定

| 路径 | 说明 |
|------|------|
| `inbox/github_site/merged_events_latest.json` | 从 GitHub 下载的 base JSON（输入） |
| `data/qishui_app_ocr_latest.json` | 汽水 OCR 输出（可选，当天不更新可不提供） |
| `data/qishui_events_history.json` | 汽水历史归档（长期保留） |
| `out/merged_events_latest.json` | 最终输出（线上用，覆盖推送） |

### 示例命令

```bash
# 1) 只净化 + 裁剪（不合并汽水）
python backend/build_site_merged_with_qishui.py --no-qishui

# 2) 汽水更新当天：先 OCR，再构建
python backend/ocr_qishui_app_screenshots_tencent.py
python backend/build_site_merged_with_qishui.py

# 3) 汽水不更新当天（仍合并历史窗口）
python backend/build_site_merged_with_qishui.py
```

### 验收

1. 把 GitHub 下载的 `merged_events_latest.json` 放到 `inbox/github_site/`
2. 运行：`python backend/build_site_merged_with_qishui.py --keep-days 7`
3. 检查 `out/merged_events_latest.json`：
   - 不存在 `charts` 含「掉出榜」
   - 不存在 `charts` 含「每日快照」
   - 只有一个平台名「抖音(汽水)」（无「抖音/汽水」等）
   - 汽水事件 `tags` 仅为 `["汽水OCR"]`（无 hot_song/search_term/new_song/artist_rank）
   - `total_days` <= 7，`date_distribution` key 数 <= 7
