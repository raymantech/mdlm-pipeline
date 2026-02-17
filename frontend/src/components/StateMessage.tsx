interface StateMessageProps {
  title: string;
  description?: string;
  actions?: React.ReactNode;
}

export function StateMessage({ title, description, actions }: StateMessageProps) {
  return (
    <div className="flex min-h-[280px] flex-col items-center justify-center rounded-xl border border-slate-200 bg-white px-6 py-12 text-center shadow-sm">
      <p className="text-lg font-medium text-slate-900">{title}</p>
      {description != null && description !== "" && (
        <p className="mt-2 max-w-sm text-sm text-slate-500">{description}</p>
      )}
      {actions != null && <div className="mt-6 flex flex-wrap justify-center gap-3">{actions}</div>}
    </div>
  );
}
