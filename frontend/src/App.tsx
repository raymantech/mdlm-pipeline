import { useCallback, useEffect, useRef, useState } from "react";
import { Header } from "./components/Header";
import { Card, type TrackItem } from "./components/Card";
import { Drawer } from "./components/Drawer";
import { StatCard } from "./components/StatCard";
import { StateMessage } from "./components/StateMessage";

/** 主站默认读取合并后的榜单（含汽水）；无则回退到 test_merged_result.json */
const JSON_URL = "/merged_latest.json";
const JSON_FALLBACK = "/test_merged_result.json";
const DEFAULT_SOURCE_LABEL = "merged_latest.json";

/** 平台筛选选项（与后端 platform 字段一致，前端统一显示「抖音(汽水）」 */
const PLATFORM_OPTIONS = [
  { value: "", label: "全部" },
  { value: "网易云音乐", label: "网易云音乐" },
  { value: "QQ音乐", label: "QQ音乐" },
  { value: "酷狗音乐", label: "酷狗音乐" },
  { value: "抖音(汽水)", label: "抖音(汽水)" },
] as const;

type DataState =
  | { status: "loading" }
  | { status: "error"; error: string }
  | {
      status: "ok";
      data: {
        generated_at?: string;
        summary?: {
          dark_horse_count?: number;
          total_analyzed?: number;
          douyin_tracks_count?: number;
          mainstream_tracks_count?: number;
          qishui_tracks_count?: number;
        };
        dark_horses?: TrackItem[];
        all_tracks?: TrackItem[];
      };
      source: string;
    };

export default function App() {
  const fileRef = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<DataState>({ status: "loading" });
  const [customFile, setCustomFile] = useState(false);
  const [search, setSearch] = useState("");
  const [platformFilter, setPlatformFilter] = useState<string>("");
  const [selected, setSelected] = useState<TrackItem | null>(null);

  const fetchData = useCallback(async () => {
    setState({ status: "loading" });
    setCustomFile(false);
    try {
      let res = await fetch(JSON_URL, { cache: "no-store" });
      if (!res.ok) res = await fetch(JSON_FALLBACK, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const source = res.url?.includes("merged_latest") ? DEFAULT_SOURCE_LABEL : "test_merged_result.json";
      setState({ status: "ok", data, source });
    } catch (e) {
      setState({
        status: "error",
        error: e instanceof Error ? e.message : String(e),
      });
    }
  }, []);

  const refresh = useCallback(async () => {
    if (customFile) return;
    setState((prev) => (prev.status === "ok" ? { status: "loading" } : prev));
    try {
      let res = await fetch(JSON_URL, { cache: "no-store" });
      if (!res.ok) res = await fetch(JSON_FALLBACK, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const source = res.url?.includes("merged_latest") ? DEFAULT_SOURCE_LABEL : "test_merged_result.json";
      setState({ status: "ok", data, source });
    } catch (e) {
      setState({
        status: "error",
        error: e instanceof Error ? e.message : String(e),
      });
    }
  }, [customFile]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const loadFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f?.name.toLowerCase().endsWith(".json")) return;
    e.target.value = "";
    setState({ status: "loading" });
    setCustomFile(true);
    const fr = new FileReader();
    fr.onload = () => {
      try {
        const data = JSON.parse(String(fr.result));
        setState({ status: "ok", data, source: f.name });
      } catch {
        setState({ status: "error", error: "JSON 解析失败" });
      }
    };
    fr.readAsText(f, "utf-8");
  };

  const restore = () => fetchData();

  const rawItems: TrackItem[] =
    state.status === "ok"
      ? (state.data.all_tracks ?? state.data.dark_horses ?? [])
      : [];

  const platformLabel = (t: TrackItem) =>
    (t.platform ?? t.mainstream_platform ?? "").trim() || "未上榜";
  const byPlatform =
    platformFilter === ""
      ? rawItems
      : rawItems.filter((t) => platformLabel(t) === platformFilter);

  const q = search.trim().toLowerCase();
  const items = q
    ? byPlatform.filter(
        (t) =>
          (String(t.track_name ?? "").toLowerCase().includes(q)) ||
          (String(t.artist ?? "").toLowerCase().includes(q)) ||
          (String(t.mainstream_platform ?? "").toLowerCase().includes(q)) ||
          (String(t.platform ?? "").toLowerCase().includes(q))
      )
    : byPlatform;

  const sourceLabel = state.status === "ok" ? state.source : "—";
  const updatedAt =
    state.status === "ok" && state.data.generated_at
      ? state.data.generated_at
      : null;
  const summary = state.status === "ok" ? state.data.summary : undefined;

  return (
    <div className="min-h-screen bg-slate-50">
      <input
        ref={fileRef}
        type="file"
        accept=".json"
        onChange={loadFile}
        className="hidden"
      />

      <Header
        sourceLabel={sourceLabel}
        updatedAt={updatedAt}
        customFile={customFile}
        onLoadCustom={() => fileRef.current?.click()}
        onRestore={restore}
        onRefresh={refresh}
        canRefresh={!customFile}
      />

      <main className="mx-auto max-w-6xl px-4 py-6">
        {state.status === "loading" && (
          <StateMessage
            title="加载中…"
            description="正在获取数据"
          />
        )}

        {state.status === "error" && (
          <StateMessage
            title="加载失败"
            description={state.error}
            actions={
              <>
                <button
                  type="button"
                  onClick={restore}
                  className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50"
                >
                  恢复默认
                </button>
                <button
                  type="button"
                  onClick={() => fetchData()}
                  className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-slate-800"
                >
                  重新加载
                </button>
              </>
            }
          />
        )}

        {state.status === "ok" && (
          <>
            <div className="grid gap-6 lg:grid-cols-[1fr,auto]">
              <div className="space-y-4">
                <label className="block text-sm font-medium text-slate-700">
                  搜索
                </label>
                <input
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="歌曲名、艺人、平台…"
                  className="w-full rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-900 shadow-sm placeholder:text-slate-400 focus:border-slate-300 focus:outline-none focus:ring-1 focus:ring-slate-300"
                />
                <div className="flex flex-wrap items-center gap-3">
                  <label className="text-sm font-medium text-slate-700">
                    平台
                  </label>
                  <select
                    value={platformFilter}
                    onChange={(e) => setPlatformFilter(e.target.value)}
                    className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm focus:border-slate-300 focus:outline-none focus:ring-1 focus:ring-slate-300"
                    aria-label="平台筛选"
                  >
                    {PLATFORM_OPTIONS.map((opt) => (
                      <option key={opt.value || "all"} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-2">
                {summary?.dark_horse_count != null && (
                  <StatCard label="黑马数" value={summary.dark_horse_count} />
                )}
                {summary?.total_analyzed != null && (
                  <StatCard label="分析数" value={summary.total_analyzed} />
                )}
                {summary?.douyin_tracks_count != null && (
                  <StatCard label="抖音曲目" value={summary.douyin_tracks_count} />
                )}
                {summary?.mainstream_tracks_count != null && (
                  <StatCard label="主流曲目" value={summary.mainstream_tracks_count} />
                )}
                {summary?.qishui_tracks_count != null && (
                  <StatCard label="汽水曲目" value={summary.qishui_tracks_count} />
                )}
              </div>
            </div>

            <div className="mt-6">
              {items.length === 0 ? (
                <StateMessage
                  title="暂无数据"
                  description={
                    search || platformFilter
                      ? "尝试调整搜索或平台筛选"
                      : "当前列表为空"
                  }
                  actions={
                    search || platformFilter ? (
                      <button
                        type="button"
                        onClick={() => {
                          setSearch("");
                          setPlatformFilter("");
                        }}
                        className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50"
                      >
                        清空筛选
                      </button>
                    ) : customFile ? (
                      <button
                        type="button"
                        onClick={restore}
                        className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-slate-800"
                      >
                        恢复默认
                      </button>
                    ) : undefined
                  }
                />
              ) : (
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
                  {items.map((t, i) => (
                    <Card
                      key={`${String(t.track_name)}-${platformLabel(t)}-${i}`}
                      item={t}
                      onClick={() => setSelected(t)}
                    />
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </main>

      <Drawer item={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
