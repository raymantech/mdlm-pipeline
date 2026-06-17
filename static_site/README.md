# 主站静态站（mdlm.hawnlink.cn 基准）

本目录为 **mdlm.hawnlink.cn** 的现有静态站代码基准：`index.html`、`app.js`、`style.css`，以及 `data/merged_events_latest.json`。

## 结构

- `index.html` - 页面结构（图表、筛选、表格、分页）
- `app.js` - 数据加载、筛选、排序、表格渲染（平台含「抖音(汽水)」）
- `style.css` - 样式（filters / table 等复用）
- `data/merged_events_latest.json` - 主站读取的榜单数据（events 格式）
- `api.php` - 可选：通过 PHP 输出 JSON（隐藏路径）

## 数据生成

主站数据由后端合并脚本生成，包含汽水热歌榜（平台标识：**抖音(汽水)**）：

```bash
# 仅汽水（无现有 events 时）
python3 backend/merge_qishui_to_events.py \
  --qishui data/douyin_qishui_latest.json \
  --out static_site/data/merged_events_latest.json

# 在现有 events 上追加汽水（如有 export 出的 merged_events）
python3 backend/merge_qishui_to_events.py \
  --events /path/to/merged_events_latest.json \
  --qishui data/douyin_qishui_latest.json \
  --out static_site/data/merged_events_latest.json
```

## 本地验收

1. 用任意静态服务器打开本目录，例如：
   ```bash
   cd static_site && python3 -m http.server 8080
   ```
2. 浏览器访问 `http://localhost:8080`
3. 在「平台」下拉中选择 **抖音(汽水)**，应只显示汽水热歌榜条目
