type BadgeVariant = "platform" | "darkHorse" | "rank" | "neutral";

const variantClass: Record<BadgeVariant, string> = {
  platform:
    "bg-slate-100 text-slate-700 border border-slate-200/80",
  darkHorse:
    "bg-amber-100 text-amber-800 border border-amber-200/80",
  rank:
    "bg-blue-50 text-blue-700 border border-blue-200/80",
  neutral:
    "bg-slate-50 text-slate-600 border border-slate-200/80",
};

interface BadgeProps {
  children: React.ReactNode;
  variant?: BadgeVariant;
  className?: string;
}

export function Badge({
  children,
  variant = "neutral",
  className = "",
}: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${variantClass[variant]} ${className}`}
    >
      {children}
    </span>
  );
}
