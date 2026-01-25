const DATA_URL = "data/merged_events_latest.json";
const PAGE_SIZE = 50;
let RAW = [], filtered = [], currentPage = 1;
let currentSortKey = null, currentSortOrder = 'desc';
let chartTypes = null, chartPlatforms = null;
let typeColorMap = {};

const $ = (id) => document.getElementById(id);
Chart.register(ChartDataLabels);

const TAG_MAP = { 'DOMINANT': '持续霸榜', 'DROP': '排名下降', 'SURGE': '排名飙升', 'ENTRY': '新进榜单', 'NEW': '首发新歌' };
const COLORS = ['#4f46e5', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#ec4899', '#94a3b8'];

function processData(events) {
    return events.map(it => {
        let tags = [];
        try { tags = typeof it.tags === 'string' ? JSON.parse(it.tags) : (it.tags || []); } catch(e) {}
        return { ...it, _mTag: TAG_MAP[tags[0]] || tags[0] || '其他', _dateObj: new Date(it.date) };
    });
}

// 修复后的排序处理函数
function handleSort(key) {
    // 1. 如果点击的是新字段，默认降序；如果是旧字段，切换升降序
    if (currentSortKey === key) {
        currentSortOrder = currentSortOrder === 'desc' ? 'asc' : 'desc';
    } else {
        currentSortKey = key;
        currentSortOrder = 'desc'; // 新选择字段默认大值在前（如：排名上升多、排名序号大）
    }

    // 2. 重置所有排序图标样式
    $("sortRank").className = "fa-solid fa-sort sort-icon-muted";
    $("sortDelta").className = "fa-solid fa-sort sort-icon-muted";

    // 3. 更新当前激活的图标
    const iconId = key === 'rank_now' ? "sortRank" : "sortDelta";
    $(iconId).className = currentSortOrder === 'asc' ? "fa-solid fa-sort-up active" : "fa-solid fa-sort-down active";

    applyFilters();
}

function applyFilters() {
    const p = $("fPlatform").value, t = $("fType").value, tr = $("fTime").value, q = $("fSearch").value.toLowerCase();
    const now = new Date();

    filtered = RAW.filter(it => {
        if (tr !== 'all') {
            const diff = (now - it._dateObj) / (1000 * 3600 * 24);
            if (diff > parseInt(tr)) return false;
        }
        if (p && it.platform !== p) return false;
        if (t && it._mTag !== t) return false;
        if (q && !`${it.track}${it.artist}`.toLowerCase().includes(q)) return false;
        return true;
    });

    // 排序逻辑执行
    if (currentSortKey) {
        filtered.sort((a, b) => {
            let vA = a[currentSortKey];
            let vB = b[currentSortKey];

            // 对趋势(delta)的特殊处理：确保 null 或 undefined 排在最后
            if (currentSortKey === 'delta') {
                vA = vA === null || vA === undefined ? (currentSortOrder === 'asc' ? 9999 : -9999) : vA;
                vB = vB === null || vB === undefined ? (currentSortOrder === 'asc' ? 9999 : -9999) : vB;
            } else {
                vA = vA || 0; vB = vB || 0;
            }

            return currentSortOrder === 'asc' ? vA - vB : vB - vA;
        });
    }

    currentPage = 1;
    refreshUI();
}

function refreshUI() {
    $("kpiTotal").textContent = filtered.length;
    $("kpiHigh").textContent = filtered.filter(x => (x.severity || 0) >= 3).length;

    const artistMap = {};
    filtered.forEach(it => {
        const name = (it.artist || "").trim();
        if (name && name !== "未知") artistMap[name] = (artistMap[name] || 0) + 1;
    });
    const topArr = Object.entries(artistMap).sort((a,b)=>b[1]-a[1]);
    const infoSpan = $("topArtistInfo");
    if (topArr.length) {
        infoSpan.textContent = `该分类最高频歌手: ${topArr[0][0]} (${topArr[0][1]}次)`;
        $("topArtistContainer").style.display = "flex";
    } else {
        $("topArtistContainer").style.display = "none";
    }

    updateCharts();
    renderTable();
}

function updateCharts() {
    const tMap = {}, pMap = {};
    filtered.forEach(it => {
        tMap[it._mTag] = (tMap[it._mTag] || 0) + 1;
        pMap[it.platform] = (pMap[it.platform] || 0) + 1;
    });

    const labels = Object.keys(tMap);
    labels.forEach((l, i) => { if(!typeColorMap[l]) typeColorMap[l] = COLORS[i % COLORS.length]; });

    if (chartTypes) chartTypes.destroy();
    chartTypes = new Chart($("chartTypes"), {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                data: Object.values(tMap),
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
                        let dataArr = ctx.chart.data.datasets[0].data;
                        dataArr.map(data => { sum += data; });
                        return value + "\n" + (value * 100 / sum).toFixed(1) + "%";
                    }
                }
            }
        }
    });
}

function renderTable() {
    const start = (currentPage - 1) * PAGE_SIZE;
    const pageData = filtered.slice(start, start + PAGE_SIZE);
    const tbody = $("tbody");
    tbody.innerHTML = "";

    pageData.forEach(it => {
        const d = it.delta || 0;
        const color = typeColorMap[it._mTag] || '#94a3b8';
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td><strong>${it.platform}</strong></td>
            <td><span class="pill">${(it.charts || [])[0] || '-'}</span></td>
            <td onclick="copyText('${it.track}')" style="cursor:pointer;color:var(--primary);font-weight:700">
                <i class="fa-regular fa-copy" style="opacity:0.4"></i> ${it.track}
            </td>
            <td>${it.artist}</td>
            <td><strong>#${it.rank_now || '-'}</strong></td>
            <td>${d < 0 ? '<span style="color:#10b981;font-weight:bold">↑'+Math.abs(d)+'</span>' : (d > 0 ? '<span style="color:#ef4444;font-weight:bold">↓'+d+'</span>' : '-')}</td>
            <td>${it.date}</td>
            <td><span class="type-tag" style="background-color:${color}">${it._mTag}</span></td>
        `;
        tbody.appendChild(tr);
    });
    $("listHint").textContent = `异动明细 (${filtered.length} 条)`;
    renderPagination();
}

function renderPagination() {
    const totalPages = Math.ceil(filtered.length / PAGE_SIZE);
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
        RAW = processData(data.events || data);

        const plats = [...new Set(RAW.map(x=>x.platform))];
        $("fPlatform").innerHTML = '<option value="">所有平台</option>' + plats.map(p=>`<option value="${p}">${p}</option>`).join('');
        const types = [...new Set(RAW.map(x=>x._mTag))];
        $("fType").innerHTML = '<option value="">所有类型</option>' + types.map(t=>`<option value="${t}">${t}</option>`).join('');

        $("fPlatform").onchange = applyFilters;
        $("fType").onchange = applyFilters;
        $("fTime").onchange = applyFilters;
        $("fSearch").oninput = applyFilters;

        $("btnSideReset").onclick = () => { if(confirm("重置筛选？")) {
            $("fPlatform").value=""; $("fType").value=""; $("fSearch").value=""; $("fTime").value="all";
            currentSortKey=null;
            $("sortRank").className = "fa-solid fa-sort sort-icon-muted";
            $("sortDelta").className = "fa-solid fa-sort sort-icon-muted";
            applyFilters();
        }};

        if(data.generated_at) $("updateDate").textContent = `同步: ${data.generated_at.split('T')[0]}`;
        applyFilters();
    } catch (e) { console.error(e); }
}

function copyText(t) {
    navigator.clipboard.writeText(t).then(()=>{
        const toast = $("toast"); toast.classList.add("show");
        setTimeout(() => toast.classList.remove("show"), 2000);
    });
}

init();