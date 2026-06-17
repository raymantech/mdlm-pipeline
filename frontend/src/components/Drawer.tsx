import { useEffect, useState } from "react";

export type TrackItem = {
  track_name?: string;
  artist?: string;
  douyin_rank?: number | null;
  mainstream_platform?: string | null;
  platform?: string | null;
  gap_score?: number | null;
  is_dark_horse?: boolean;
  discovery_time?: string | null;
  rank?: number | null;
  heat?: number | null;
  [k: string]: unknown;
};

const DISPLAY_KEYS = [
  "track_name",
  "artist",
  "douyin_rank",
  "douyin_heat",
  "mainstream_rank",
  "mainstream_platform",
  "platform",
  "gap_score",
  "is_dark_horse",
  "discovery_time",
  "match_key",
  "douyin_digg",
  "douyin_collect",
  "rank",
  "heat",
];

interface DrawerProps {
  item: TrackItem | null;
  onClose: () => void;
}

function formatValue(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function Drawer({ item, onClose }: DrawerProps) {
  const [rawOpen, setRawOpen] = useState(false);

  useEffect(() => {
    if (!item) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [item, onClose]);

  useEffect(() => {
    if (!item) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [item]);

  if (!item) return null;

  const displayEntries = DISPLAY_KEYS.filter((k) => item[k] !== undefined).map((k) => [k, item[k]] as const);
  const rawJson = JSON.stringify(item, null, 2);

  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-slate-900/20 backdrop-blur-[2px]"
        onClick={onClose}
        onKeyDown={(e) => e.key === "Escape" && onClose()}
        role="button"
        tabIndex={0}
        aria-label="关闭"
      />
      <aside
        className="fixed right-0 top-0 z-50 flex h-full w-full max-w-[420px] flex-col border-l border-slate-200 bg-white shadow-xl"
        aria-modal
        aria-label="详情"
      >
        <div className="flex shrink-0 items-center justify-between border-b border-slate-200 px-4 py-3">
          <h2 className="truncate text-lg font-semibold text-slate-900">
            {item.track_name ?? "—"}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-2 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
            aria-label="关闭"
          >
            <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-4 py-4">
          <section>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
              字段
            </h3>
            <table className="w-full text-sm">
              <tbody>
                {displayEntries.map(([key, value]) => (
                  <tr key={key} className="border-b border-slate-100 last:border-0">
                    <td className="py-2 pr-3 font-medium text-slate-500 align-top">
                      {key}
                    </td>
                    <td className="py-2 text-slate-900 break-words align-top">
                      {formatValue(value)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <section className="mt-6">
            <button
              type="button"
              onClick={() => setRawOpen((o) => !o)}
              className="flex w-full items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-left text-sm font-medium text-slate-600 hover:bg-slate-100"
            >
              <span>Raw JSON</span>
              <span className="text-slate-400">{rawOpen ? "▼" : "▶"}</span>
            </button>
            {rawOpen && (
              <pre className="mt-2 overflow-x-auto rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-700">
                {rawJson}
              </pre>
            )}
          </section>
        </div>
      </aside>
    </>
  );
}
