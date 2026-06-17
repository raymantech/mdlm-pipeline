const DATA_URL = "data/merged_events_latest.json";
const PAGE_SIZE = 50;
let RAW = [], filtered = [], filteredForTypeChart = [], currentPage = 1;
let currentSortKey = '_dateObj', currentSortOrder = 'desc';  // 默认按日期降序，最新在前
let chartTypes = null, chartPlatforms = null;
let typeColorMap = {};

/** 平台 ID → 前端显示名（统一为「抖音(汽水)」，避免下拉/饼图出现两个抖音） */
const PLATFORM_DISPLAY = { "DOUYIN_QISHUI": "抖音(汽水)" };

const QISHUI_CHART_WHITELIST = new Set(["热歌榜", "新歌榜", "热门搜索", "音乐人榜"]);
const QISHUI_TAG_TO_CHART = { hot_song: "热歌榜", new_song: "新歌榜", search_term: "热门搜索", artist_rank: "音乐人榜" };

/** 抖音(汽水)快照类型：不应出现在默认“事件类型分布”柱状图中 */
const QISHUI_SNAPSHOT_TYPES = new Set(["热歌榜", "新歌榜", "热门搜索", "音乐人榜"]);

/** 分析事件类型：应出现在“事件类型分布”柱状图中 */
const ANALYSIS_EVENT_TYPES = new Set(["暴跌", "暴涨", "新进榜", "连续3天Top10", "Top10稳定", "回归榜", "多榜爆发"]);

/** 事件类型排序顺序（用于柱状图） */
/** 分析事件白名单（按 tag 是否存在计数，非互斥） */
const ANALYSIS_TYPES = ["暴跌", "暴涨", "新进榜", "连续3天Top10", "Top10稳定", "回归榜", "多榜爆发"];
const TYPE_ORDER = ANALYSIS_TYPES;

/** 类型下拉展示名：多榜爆发 -> 跨平台联动（多榜爆发） */
const TYPE_DISPLAY = { "多榜爆发": "跨平台联动（多榜爆发）" };

/** 抖音四榜固定顺序（白名单 + 数据兜底） */
const QISHUI_CHART_ORDER = ["热歌榜", "新歌榜", "热门搜索", "音乐人榜"];

/** 主流平台（QQ/酷狗/网易云），作为进入页面的默认筛选，避免抖音最新导致页面显空 */
const MAINSTREAM_PLATFORMS = ["QQ音乐", "酷狗音乐", "网易云音乐"];
const PLATFORM_MAINSTREAM = "__MAINSTREAM__";

const $ = (id) => document.getElementById(id);

/**
 * 平台匹配：普通事件 it.platform === 选中平台；多榜爆发事件 it.platform==="多平台" 且 it.platforms 包含选中平台 也应匹配
 * @param {object} it - 事件对象
 * @param {string} selectedPlatform - 选中的平台（空串=全部；PLATFORM_MAINSTREAM=主流平台）
 */
function platformMatch(it, selectedPlatform) {
  if (!selectedPlatform) return true;
  if (selectedPlatform === PLATFORM_MAINSTREAM) {
    // 主流平台(QQ/酷狗/网易云)
    if (it.platform === "多平台" && Array.isArray(it.platforms)) {
      return it.platforms.some(plat => MAINSTREAM_PLATFORMS.includes(plat));
    }
    return MAINSTREAM_PLATFORMS.includes(it.platform);
  }
  // 具体平台
  if (it.platform === "多平台" && Array.isArray(it.platforms)) {
    return it.platforms.includes(selectedPlatform);
  }
  return it.platform === selectedPlatform;
}

/** 平台列显示：多平台事件显示为 多平台（QQ音乐+网易云音乐） */
function formatPlatform(it) {
  if (it.platform === "多平台" && Array.isArray(it.platforms) && it.platforms.length > 0) {
    return `多平台（${it.platforms.join("+")}）`;
  }
  return (it.platform || "").trim() || "-";
}
Chart.register(ChartDataLabels);

const TAG_MAP = { 'DOMINANT': '持续霸榜', 'DROP': '排名下降', 'SURGE': '排名飙升', 'ENTRY': '新进榜单', 'NEW': '首发新歌' };
const COLORS = ['#4f46e5', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#ec4899', '#94a3b8'];

/** 事件类型展示配置：默认显示全部类型，此处列出的类型不会出现在「事件类型分布」图表和筛选下拉中（仅前端过滤，不改数据源）。以后新增要隐藏的类型，直接往 Set 里加中文名即可。 */
const HIDDEN_EVENT_TYPES = new Set(["掉出榜", "每日快照", "汽水OCR", "汽水 OCR", "未知榜单", "hot_song", "new_song", "search_term", "artist_rank"]);

function isQishuiOcrTag(t) {
  return (String(t || "").trim().toLowerCase().replace(/\s+/g, "") === "汽水ocr");
}

/** 汽水 OCR 事件：raw.source 是原始字段，it.platform 已被 PLATFORM_DISPLAY 归一化，故用 source 判断更可靠 */
function isQishuiOcrEvent(raw, it) {
  return raw.source === "douyin_qishui_app_ocr";
}

/** charts/tags/type/mTag 任一命中「掉出榜」则为掉出榜事件，明细表不展示。 */
function isDropEvent(e) {
  const charts = e.charts || [];
  const tags = e.tags || [];
  const typeVal = e.type || "";
  const mTagVal = e._mTag || e.mTag || "";
  return (Array.isArray(charts) && charts.some(c => c === "掉出榜")) ||
    (Array.isArray(tags) && tags.some(t => t === "掉出榜")) ||
    typeVal === "掉出榜" ||
    mTagVal === "掉出榜";
}

function escapeHtml(s) {
  if (s == null || s === '') return '';
  const t = String(s);
  return t
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** charts 字段兼容：string 或 array */
function getChartsOfEvent(e) {
  const c = e.charts;
  if (!c) return [];
  if (Array.isArray(c)) return c.filter(Boolean).map(String);
  return [String(c)];
}

/** 歌手名：多字段兜底，空则返回空串（不计入统计） */
function getArtistName(e) {
  const name = (e.artist || e.singer || e.artists || e.artist_name || e.artist_name_raw || "").trim();
  if (!name || name === "—" || name === "未知") return "";
  return name;
}

/** 事件是否属于某类型：tags 或 event_type 匹配（多榜爆发等可能只有 event_type） */
function eventHasType(e, typeName) {
  if ((e.tags || []).includes(typeName)) return true;
  return (e.event_type || "").trim() === typeName;
}

/**
 * 从事件中提取主类型（优先 event_type，否则从 tags 推导）
 * 优先级：暴跌 > 暴涨 > 新进榜 > 回归榜 > 多榜爆发
 * 返回 null 表示不属于分析事件
 */
function getPrimaryType(e) {
  // 优先使用 event_type（兼容未来）
  if (e.event_type && typeof e.event_type === 'string' && e.event_type.trim()) {
    const et = e.event_type.trim();
    if (ANALYSIS_EVENT_TYPES.has(et)) {
      return et;
    }
  }
  
  // 从 tags 推导
  const tags = e.tags || [];
  if (!Array.isArray(tags)) return null;
  
  // 按优先级查找
  const priority = ["暴跌", "暴涨", "新进榜", "回归榜", "多榜爆发"];
  for (const p of priority) {
    if (tags.includes(p)) {
      return p;
    }
  }
  
  // 检查英文标签映射
  const tagMap = {
    'DROP': '暴跌',
    'SURGE': '暴涨',
    'ENTRY': '新进榜',
  };
  for (const tag of tags) {
    if (tagMap[tag]) {
      return tagMap[tag];
    }
  }
  
  return null;
}

/**
 * 日期字符串加1天（YYYY-MM-DD格式）
 */
function addDay(dateStr) {
  const d = new Date(dateStr);
  d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10);
}

/**
 * 派生Top10相关信号：连续3天Top10、Top10稳定
 */
function deriveTop10Signals(events) {
  const derived = [];
  
  // 归一化字符串辅助函数
  const norm = (s) => String(s || "").trim().replace(/\s+/g, " ");
  
  // 按 key = platform + charts[0] + itemKey 分组（itemKey = id 或 track+artist）
  const byKey = {};
  events.forEach(e => {
    if (!e.date || !e.platform) return;
    const charts = e.charts || [];
    const chart0 = charts[0] || '';
    const itemKey = (e.id && String(e.id).trim()) ? String(e.id).trim() : `${norm(e.track)}__${norm(e.artist)}`;
    const key = `${e.platform}|${chart0}|${itemKey}`;
    if (!byKey[key]) {
      byKey[key] = [];
    }
    byKey[key].push(e);
  });
  
  // 处理每个 key
  Object.values(byKey).forEach(group => {
    if (group.length < 3) return; // 至少需要3条数据
    
    // 按日期升序排序
    group.sort((a, b) => a.date.localeCompare(b.date));
    
    // 提取 itemKey 和 chart0（用于去重）
    const itemKey = (group[0].id && String(group[0].id).trim()) ? String(group[0].id).trim() : `${norm(group[0].track)}__${norm(group[0].artist)}`;
    const chart0 = (group[0].charts || [])[0] || '';
    
    // 1. 派生"连续3天Top10"（实际为连续3条记录 Top10）
    const consecutive3Days = new Set(); // (key, date) 去重
    for (let i = 0; i <= group.length - 3; i++) {
      const e1 = group[i];
      const e2 = group[i + 1];
      const e3 = group[i + 2];
      
      const r1 = e1.rank_now;
      const r2 = e2.rank_now;
      const r3 = e3.rank_now;
      
      // 检查是否都 <= 10（连续3条记录）
      if (r1 != null && r1 > 0 && r1 <= 10 &&
          r2 != null && r2 > 0 && r2 <= 10 &&
          r3 != null && r3 > 0 && r3 <= 10) {
        
        const d3 = e3.date;
        const dedupeKey = `${e1.platform}|${chart0}|${itemKey}|${d3}`;
        if (!consecutive3Days.has(dedupeKey)) {
          consecutive3Days.add(dedupeKey);
          
          // 在第3条记录生成虚拟事件
          derived.push({
            ...e3,
            event_type: "连续3天Top10",
            _mTag: "连续3天Top10",
            _primaryType: "连续3天Top10",
            tags: [...(e3.tags || []), "连续3天Top10"],
            narrative: `连续3次Top10（按记录序列，截至 ${d3}）`,
            derived: true,
            _dateObj: e3._dateObj || (d3 ? new Date(d3) : new Date(0)),
          });
        }
      }
    }
    
    // 2. 派生"Top10稳定"（最近7条记录窗口，>=5条Top10）
    // 从第7条记录开始（索引6），检查最近7条记录
    for (let i = 6; i < group.length; i++) {
      const window = group.slice(i - 6, i + 1); // 最近7条记录
      
      // 统计Top10条数
      let top10Count = 0;
      window.forEach(e => {
        if (e.rank_now != null && e.rank_now > 0 && e.rank_now <= 10) {
          top10Count++;
        }
      });
      
      if (top10Count >= 5) {
        const dateEnd = group[i].date;
        const dedupeKey = `${group[0].platform}|${chart0}|${itemKey}|${dateEnd}`;
        // 使用 itemKey 进行去重检查
        const existing = derived.find(d => {
          if (d.event_type !== "Top10稳定" || d.platform !== group[0].platform || d.date !== dateEnd) return false;
          const dItemKey = (d.id && String(d.id).trim()) ? String(d.id).trim() : `${norm(d.track)}__${norm(d.artist)}`;
          return dItemKey === itemKey;
        });
        
        if (!existing) {
          const baseEvent = group[i];
          derived.push({
            ...baseEvent,
            date: dateEnd,
            event_type: "Top10稳定",
            _mTag: "Top10稳定",
            _primaryType: "Top10稳定",
            tags: [...(baseEvent.tags || []), "Top10稳定"],
            narrative: `Top10稳定（近7条记录 Top10 条数 ${top10Count}）`,
            derived: true,
            _dateObj: dateEnd ? new Date(dateEnd) : new Date(0),
          });
        }
      }
    }
  });
  
  return derived;
}

function processData(events) {
  const pickFirst = (v) => Array.isArray(v) ? v[0] : v;

  const parseArr = (v) => {
    if (!v) return [];
    if (Array.isArray(v)) return v;
    if (typeof v === "string") {
      try { return JSON.parse(v); } catch (e) { return []; }
    }
    return [];
  };

  return (events || []).map(raw => {
    // ---- 1) 兼容 tags/charts（新旧字段）----
    const tags = parseArr(raw.tags ?? raw.tags_json);
    const charts = parseArr(raw.charts ?? raw.charts_json);

    // ---- 2) 兼容日期（date/day/merge_date/created_at）----
    const dateStr = raw.date ?? raw.day ?? raw.merge_date ?? raw.created_at ?? "";

    // ---- 3) 统一成前端"标准字段"（缺失字段兜底）----
    const it = {
      ...raw,
      platform: PLATFORM_DISPLAY[raw.platform] ?? raw.platform ?? raw.platform_name ?? "未知平台",
      chart: raw.chart ?? raw.chart_name ?? "",

      track: raw.track ?? raw.track_name ?? raw.trackTitle ?? "未知歌曲",
      artist: raw.artist ?? raw.artist_name ?? raw.artist_name_raw ?? "未知",

      date: dateStr ? String(dateStr).slice(0, 10) : "",
      tags,
      charts,

      // 数值字段统一，缺失给默认
      severity: raw.severity ?? raw.max_severity ?? 0,
      rank_now: raw.rank_now ?? raw.best_rank_now ?? raw.rank ?? null,
      rank_prev: raw.rank_prev ?? raw.best_rank_prev ?? null,
      delta: raw.delta ?? raw.best_delta ?? null,
    };

    // ---- 4) 类型标签：优先使用 event_type（回归榜、多榜爆发等衍生类型），否则按原逻辑 ----
    let _mTag;
    const eventType = (raw.event_type || "").trim();
    if (eventType) {
      _mTag = eventType;
    } else if (it.platform === "抖音(汽水)") {
      const ch0 = charts && charts[0];
      if (ch0 && QISHUI_CHART_WHITELIST.has(ch0)) {
        _mTag = ch0;
      } else {
        const mapped = (tags || []).find(t => QISHUI_TAG_TO_CHART[t]);
        _mTag = mapped ? QISHUI_TAG_TO_CHART[mapped] : "其他";
      }
    } else {
      const validTag = (tags || []).find(t => !isQishuiOcrTag(t));
      const tag0 = validTag ?? pickFirst(tags);
      _mTag = isQishuiOcrTag(tag0)
        ? ((charts && charts[0]) || "其他")
        : (TAG_MAP[tag0] || tag0 || (charts && charts[0]) || "其他");
    }
    // 提取主类型（用于分析事件判断）
    const _primaryType = getPrimaryType({ ...it, event_type: eventType, tags });
    
    return {
      ...it,
      _mTag,
      _primaryType,
      _dateObj: it.date ? new Date(it.date) : new Date(0),
    };
  });
}


// 修复后的排序处理函数
function handleSort(key) {
    if (currentSortKey === key) {
        currentSortOrder = currentSortOrder === 'desc' ? 'asc' : 'desc';
    } else {
        currentSortKey = key;
        currentSortOrder = 'desc';
    }

    // 重置所有排序图标样式
    $("sortRank").className = "fa-solid fa-sort sort-icon-muted";
    $("sortDelta").className = "fa-solid fa-sort sort-icon-muted";
    $("sortDate").className = "fa-solid fa-sort sort-icon-muted";

    // 更新当前激活的图标
    let iconId = "sortRank";
    if (key === 'delta') iconId = "sortDelta";
    if (key === '_dateObj') iconId = "sortDate";

    $(iconId).className = currentSortOrder === 'asc' ? "fa-solid fa-sort-up active" : "fa-solid fa-sort-down active";

    applyFilters();
}

function applyFilters() {
    let p = $("fPlatform").value;
    let t = $("fType").value;
    let ch = $("chartSelect").value;
    const tr = $("fTime").value, q = $("fSearch").value.toLowerCase();
    const now = new Date();

    // 多平台不是平台：若残留选中则重置为所有平台
    if (p === "多平台") {
      p = "";
      $("fPlatform").value = "";
    }

    filteredForTypeChart = RAW.filter(it => {
        if (tr !== 'all') {
            const diff = (now - it._dateObj) / (1000 * 3600 * 24);
            if (diff > parseInt(tr)) return false;
        }
        if (!platformMatch(it, p)) return false;
        return true;
    });

    // 榜单筛选：仅当平台=抖音(汽水)时显示（主流平台不显示）
    const chartWrap = $("chartFilterWrap");
    if (p === "抖音(汽水)") {
      chartWrap.style.display = "";
      const fromData = new Set();
      filteredForTypeChart.filter(e => e.platform === "抖音(汽水)").forEach(e => {
        getChartsOfEvent(e).forEach(c => { if (c) fromData.add(c); });
      });
      const chartOpts = [...QISHUI_CHART_ORDER.filter(c => fromData.has(c)), ...[...fromData].filter(c => !QISHUI_CHART_ORDER.includes(c))];
      $("chartSelect").innerHTML = '<option value="">所有榜单</option>' + chartOpts.map(c => `<option value="${c}">${c}</option>`).join('');
      if (ch && !chartOpts.includes(ch)) ch = "";
      $("chartSelect").value = ch || "";
    } else {
      chartWrap.style.display = "none";
      ch = "";
      $("chartSelect").value = "";
    }

    // 类型下拉：tags 或 event_type 匹配（多榜爆发等可能只有 event_type）
    const presentTypes = new Set();
    filteredForTypeChart.forEach(e => {
      for (const ty of ANALYSIS_TYPES) {
        if (eventHasType(e, ty)) presentTypes.add(ty);
      }
    });
    const typeOptions = ANALYSIS_TYPES.filter(ty => presentTypes.has(ty));
    const typeDisplay = (x) => TYPE_DISPLAY[x] || x;
    $("fType").innerHTML = '<option value="">所有类型</option>' + typeOptions.map(x => `<option value="${x}">${typeDisplay(x)}</option>`).join('');
    if (t && !typeOptions.includes(t)) t = "";
    $("fType").value = t || "";

    filtered = filteredForTypeChart.filter(it => {
        if (t) {
          if (!eventHasType(it, t)) return false;
        }
        if (p === "抖音(汽水)" && ch) {
          if (!getChartsOfEvent(it).includes(ch)) return false;
        }
        if (q && !`${it.track}${it.artist}`.toLowerCase().includes(q)) return false;
        return true;
    });

    // 排序逻辑执行：默认按日期降序（最新在前）
    const sortKey = currentSortKey || '_dateObj';
    const sortOrder = currentSortKey ? currentSortOrder : 'desc';
    filtered.sort((a, b) => {
            let vA = a[sortKey];
            let vB = b[sortKey];

            // 对趋势(delta)的特殊处理
            if (sortKey === 'delta') {
                vA = vA === null || vA === undefined ? (sortOrder === 'asc' ? 9999 : -9999) : vA;
                vB = vB === null || vB === undefined ? (sortOrder === 'asc' ? 9999 : -9999) : vB;
            } else if (sortKey === '_dateObj') {
                // 日期对象转为时间戳进行比较
                vA = vA ? vA.getTime() : 0;
                vB = vB ? vB.getTime() : 0;
            } else {
                vA = vA == null ? 0 : vA; vB = vB == null ? 0 : vB;
            }

            return sortOrder === 'asc' ? vA - vB : vB - vA;
        });

    currentPage = 1;
    refreshUI();
}

function refreshUI() {
    $("kpiTotal").textContent = filtered.length;
    $("kpiHigh").textContent = filtered.filter(x => (x.severity || 0) >= 3).length;

    const artistMap = {};
    filtered.forEach(it => {
        const name = getArtistName(it);
        if (!name) return;
        artistMap[name] = (artistMap[name] || 0) + 1;
    });
    const topArr = Object.entries(artistMap).sort((a,b)=>b[1]-a[1]);
    const infoSpan = $("topArtistInfo");
    if (topArr.length) {
        infoSpan.textContent = `该分类最高频歌手: ${topArr[0][0]} (${topArr[0][1]}次)`;
        $("topArtistContainer").style.display = "flex";
    } else {
        infoSpan.textContent = "该分类最高频歌手: —";
        $("topArtistContainer").style.display = "flex";
    }

    updateCharts();
    renderTable();
}

function updateCharts() {
    const pMap = {};
    
    // 构建平台分布（排除"多平台"，多榜爆发作为事件类型在类型筛选中可选）
    filtered.forEach(it => {
        if (it.platform === "多平台") return;
        pMap[it.platform] = (pMap[it.platform] || 0) + 1;
    });

    // 柱状图：按 tag/event_type 匹配计数（非互斥）
    const tMap = {};
    for (const ty of ANALYSIS_TYPES) tMap[ty] = 0;
    filtered.forEach(e => {
      for (const ty of ANALYSIS_TYPES) {
        if (eventHasType(e, ty)) tMap[ty] += 1;
      }
    });
    const labels = ANALYSIS_TYPES.filter(t => tMap[t] > 0);
    
    labels.forEach((l, i) => { if(!typeColorMap[l]) typeColorMap[l] = COLORS[i % COLORS.length]; });

    if (chartTypes) chartTypes.destroy();
    chartTypes = new Chart($("chartTypes"), {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                data: labels.map(l => tMap[l] || 0),
                backgroundColor: labels.map(l => typeColorMap[l]),
                hoverBackgroundColor: labels.map(l => typeColorMap[l]),
                borderRadius: 6
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            hover: { mode: 'nearest', intersect: true },
            animation: { duration: 0 },
            onClick: (e, el) => {
                if (el && el.length > 0) {
                    const val = labels[el[0].index];
                    const select = $("fType");
                    select.value = val;
                    select.classList.add('flash-highlight');
                    setTimeout(() => select.classList.remove('flash-highlight'), 600);
                    applyFilters();
                }
            },
            plugins: {
                legend: { display: false },
                datalabels: { anchor: 'end', align: 'top', font: { weight: 'bold' } }
            },
            scales: { y: { beginAtZero: true, grid: { display: false } } }
        }
    });

    if (chartPlatforms) chartPlatforms.destroy();
    chartPlatforms = new Chart($("chartPlatforms"), {
        type: 'doughnut',
        data: { labels: Object.keys(pMap), datasets: [{ data: Object.values(pMap), backgroundColor: COLORS, hoverOffset: 0 }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            hover: { mode: null },
            onClick: (e, el) => {
                if (el && el.length > 0) {
                    const val = Object.keys(pMap)[el[0].index];
                    const select = $("fPlatform");
                    select.value = val;
                    select.classList.add('flash-highlight');
                    setTimeout(() => select.classList.remove('flash-highlight'), 600);
                    applyFilters();
                }
            },
            plugins: {
                datalabels: {
                    color: '#fff',
                    font: { weight: 'bold' },
                    formatter: (value, ctx) => {
                        let sum = 0;
                        const dataArr = ctx.chart.data.datasets[0].data;
                        dataArr.forEach(data => { sum += data; });
                        return value + "\n" + (value * 100 / sum).toFixed(1) + "%";
                    }
                }
            }
        }
    });
    
    // 多榜爆发作为事件类型，不再显示平台占比提示
    const hintEl = document.getElementById("platformHint");
    if (hintEl) hintEl.innerHTML = "";
}

function renderTable() {
    const rowsForTable = filtered.filter(it => !isDropEvent(it));
    const start = (currentPage - 1) * PAGE_SIZE;
    const pageData = rowsForTable.slice(start, start + PAGE_SIZE);
    const tbody = $("tbody");
    tbody.innerHTML = "";

    pageData.forEach(it => {
        const d = it.delta != null ? it.delta : 0;
        const color = typeColorMap[it._mTag] || '#94a3b8';
        const tr = document.createElement("tr");
        const platform = formatPlatform(it);
        const chart0 = (it.charts && it.charts[0]) ? it.charts[0] : '-';
        const track = (it.track || '').trim() || '-';
        const artist = (it.artist || '').trim() || '-';
        const rankNow = it.rank_now != null ? it.rank_now : '-';
        const date = (it.date || '').trim() || '-';
        const mTag = it._mTag || '其他';
        const trackEsc = escapeHtml(track);
        const trackAttr = (it.track || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        tr.innerHTML = `
            <td><strong>${escapeHtml(platform)}</strong></td>
            <td><span class="pill">${escapeHtml(chart0)}</span></td>
            <td class="copy-cell" data-raw="${trackAttr}" style="cursor:pointer;color:var(--primary);font-weight:700">
                <i class="fa-regular fa-copy" style="opacity:0.4"></i> ${trackEsc}
            </td>
            <td>${escapeHtml(artist)}</td>
            <td><strong>#${rankNow}</strong></td>
            <td>${d < 0 ? '<span style="color:#10b981;font-weight:bold">↑'+Math.abs(d)+'</span>' : (d > 0 ? '<span style="color:#ef4444;font-weight:bold">↓'+d+'</span>' : '-')}</td>
            <td>${escapeHtml(date)}</td>
            <td><span class="type-tag" style="background-color:${color}">${escapeHtml(mTag)}</span></td>
        `;
        tr.querySelector('.copy-cell').addEventListener('click', function () { copyText(this.dataset.raw || ''); });
        tbody.appendChild(tr);
    });
    $("listHint").textContent = `异动明细 (${rowsForTable.length} 条)`;
    renderPagination();
}

function renderPagination() {
    const rowsForTable = filtered.filter(it => !isDropEvent(it));
    const totalPages = Math.ceil(rowsForTable.length / PAGE_SIZE);
    const container = $("pagination"); container.innerHTML = "";
    if (totalPages <= 1) return;

    for(let i=1; i<=Math.min(totalPages, 10); i++) {
        const b = document.createElement("button");
        b.className = `page-btn ${i === currentPage ? 'active' : ''}`;
        b.textContent = i;
        b.onclick = () => {
            currentPage = i;
            renderTable();
            document.querySelector('.list-section').scrollIntoView({ behavior: 'smooth' });
        };
        container.appendChild(b);
    }
}

async function init() {
    try {
        const res = await fetch(DATA_URL + "?v=" + Date.now());
        const data = await res.json();
        const processed = processData(data.events || data);
        const derived = deriveTop10Signals(processed);
        RAW = processed.concat(derived);
        console.log("[DEBUG] page counts:", RAW.filter(e=>(e.tags||[]).includes("Top10稳定")).length, RAW.filter(e=>(e.tags||[]).includes("连续3天Top10")).length);

        const platsSet = new Set(RAW.map(x => x.platform).filter(Boolean));
        const plats = [...platsSet]
          .filter(p => p !== "多平台")
          .sort((a, b) => a.localeCompare(b));
        const mainstreamOpt = `<option value="${PLATFORM_MAINSTREAM}">主流平台(QQ/酷狗/网易云)</option>`;
        $("fPlatform").innerHTML = '<option value="" selected>所有平台</option>' + mainstreamOpt + plats.map(p => `<option value="${p}">${p}</option>`).join('');
        $("fPlatform").value = "";

        $("fPlatform").onchange = applyFilters;
        $("chartSelect").onchange = applyFilters;
        $("fType").onchange = function () {
          const t = $("fType").value;
          if (t && QISHUI_CHART_WHITELIST.has(t) && $("fPlatform").value !== "抖音(汽水)") {
            $("fPlatform").value = "抖音(汽水)";
          }
          applyFilters();
        };
        $("fTime").onchange = applyFilters;
        $("fSearch").oninput = applyFilters;

        $("btnSideReset").onclick = () => { if(confirm("返回主页并重置筛选？")) {
            $("fPlatform").value = ""; $("fType").value=""; $("chartSelect").value=""; $("fSearch").value=""; $("fTime").value="all";
            $("chartFilterWrap").style.display = "none";
            currentSortKey = '_dateObj'; currentSortOrder = 'desc';
            $("sortRank").className = "fa-solid fa-sort sort-icon-muted";
            $("sortDelta").className = "fa-solid fa-sort sort-icon-muted";
            $("sortDate").className = "fa-solid fa-sort sort-icon-muted";
            applyFilters();
        }};

        if(data.generated_at) $("updateDate").textContent = `同步: ${String(data.generated_at).split('T')[0]}`;
        $("sortDate").className = "fa-solid fa-sort-down active";
        applyFilters();
    } catch (e) { console.error(e); }
}

function copyText(t) {
    const raw = typeof t === 'string' ? t : '';
    if (!raw) return;
    navigator.clipboard.writeText(raw).then(()=>{
        const toast = $("toast"); toast.classList.add("show");
        setTimeout(() => toast.classList.remove("show"), 2000);
    });
}

init();