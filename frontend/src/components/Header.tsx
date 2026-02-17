interface HeaderProps {
  sourceLabel: string;
  updatedAt: string | null;
  customFile: boolean;
  onLoadCustom: () => void;
  onRestore: () => void;
  onRefresh: () => void;
  canRefresh: boolean;
}

export function Header({
  sourceLabel,
  updatedAt,
  customFile,
  onLoadCustom,
  onRestore,
  onRefresh,
  canRefresh,
}: HeaderProps) {
  return (
    <header className="sticky top-0 z-50 border-b border-slate-200/80 bg-white/80 shadow-sm backdrop-blur-md">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold tracking-tight text-slate-900">
            MDLM
          </h1>
          <span className="text-slate-500">/</span>
          <span className="text-sm font-medium text-slate-600">音乐黑马榜</span>
          <span className="hidden text-sm text-slate-400 sm:inline">
            · 数据源：{sourceLabel}
            {updatedAt != null && ` · ${updatedAt}`}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {customFile && (
            <button
              type="button"
              onClick={onRestore}
              className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 shadow-sm transition hover:bg-slate-50 hover:text-slate-900"
            >
              恢复默认
            </button>
          )}
          {canRefresh && (
            <button
              type="button"
              onClick={onRefresh}
              className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 shadow-sm transition hover:bg-slate-50 hover:text-slate-900"
              title="重新加载"
            >
              刷新
            </button>
          )}
          <button
            type="button"
            onClick={onLoadCustom}
            className="rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-slate-800"
          >
            加载自定义 JSON
          </button>
        </div>
      </div>
    </header>
  );
}
