import { Badge } from "./Badge";

export type TrackItem = {
  track_name?: string;
  artist?: string;
  douyin_rank?: number | null;
  mainstream_platform?: string | null;
  /** 统一平台标识，筛选用；前端显示统一为「抖音(汽水）」 */
  platform?: string | null;
  gap_score?: number | null;
  is_dark_horse?: boolean;
  discovery_time?: string | null;
  rank?: number | null;
  heat?: number | null;
  [k: string]: unknown;
};

interface CardProps {
  item: TrackItem;
  onClick: () => void;
}

export function Card({ item, onClick }: CardProps) {
  const platform = (item.platform ?? item.mainstream_platform ?? "").trim() || "未上榜";
  const rank = item.douyin_rank ?? item.rank ?? null;

  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full rounded-xl border border-slate-200 bg-white p-4 text-left shadow-sm transition hover:shadow-md focus:outline-none focus:ring-2 focus:ring-slate-300 focus:ring-offset-2"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <span className="font-medium text-slate-900 line-clamp-2">
          {item.track_name ?? "—"}
        </span>
        <div className="flex flex-wrap items-center gap-1.5">
          {item.is_dark_horse && (
            <Badge variant="darkHorse">黑马</Badge>
          )}
          {rank != null && (
            <Badge variant="rank">#{rank}</Badge>
          )}
        </div>
      </div>
      {(item.artist ?? "").trim() ? (
        <p className="mt-1 text-sm text-slate-500">{item.artist}</p>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-1.5">
        <Badge variant="platform">{platform}</Badge>
        {item.gap_score != null && (
          <Badge variant="neutral">Gap {Number(item.gap_score).toFixed(2)}</Badge>
        )}
      </div>
    </button>
  );
}
