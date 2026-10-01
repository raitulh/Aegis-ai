import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/cn";

type CardVariant = "default" | "glass" | "elevated" | "outline" | "inset";

const cardVariants: Record<CardVariant, string> = {
  // Solid surface with a top sheen and hairline highlight — the workhorse.
  default: "border border-border bg-surface surface-sheen shadow-card",
  // Translucent surface for content floating over ambient visuals.
  glass: "border border-border surface-glass shadow-card",
  // Higher elevation for focal panels (hero side panels, dialogs-in-page).
  elevated: "border border-border-strong bg-surface-2 surface-sheen shadow-elevated",
  outline: "border border-border bg-transparent",
  // Recessed well for nested data (code, previews, key/value groups).
  inset: "border border-border bg-bg-elevated",
};

export function Card({ className, variant = "default", ...props }: HTMLAttributes<HTMLDivElement> & { variant?: CardVariant }) {
  return <div className={cn("rounded-[var(--radius-lg)]", cardVariants[variant], className)} {...props} />;
}

export function CardHeader({
  title,
  description,
  action,
  className,
  icon,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
  icon?: ReactNode;
}) {
  return (
    <div className={cn("flex items-start justify-between gap-4 border-b border-border px-5 py-4", className)}>
      <div className="flex min-w-0 items-start gap-3">
        {icon ? (
          <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-2 text-accent-strong [&_svg]:h-3.5 [&_svg]:w-3.5">
            {icon}
          </span>
        ) : null}
        <div className="min-w-0">
          <h2 className="text-sm font-semibold tracking-[-0.01em] text-fg">{title}</h2>
          {description ? <p className="mt-0.5 text-xs leading-relaxed text-muted">{description}</p> : null}
        </div>
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

export function CardBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-5 py-4", className)} {...props} />;
}

export function CardFooter({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex items-center justify-end gap-2 border-t border-border bg-bg-elevated/40 px-5 py-3", className)} {...props} />;
}

/**
 * Metric tile. The value is always a real number/string supplied by the caller; tiles never invent data.
 * `accent` tints the icon chip; `footer` holds context such as "of 5 left today".
 */
export function Stat({
  label,
  value,
  hint,
  icon,
  className,
  accent = "accent",
}: {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  icon?: ReactNode;
  className?: string;
  accent?: "accent" | "cyan" | "success" | "warning" | "danger" | "info";
}) {
  const chip = {
    accent: "bg-accent-soft text-accent-strong",
    cyan: "bg-cyan-soft text-cyan",
    success: "bg-success-soft text-success",
    warning: "bg-warning-soft text-warning",
    danger: "bg-danger-soft text-danger",
    info: "bg-info-soft text-info",
  }[accent];
  return (
    <div className={cn("relative overflow-hidden rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen p-4 shadow-card", className)}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-eyebrow text-subtle">{label}</span>
        {icon ? <span className={cn("flex h-7 w-7 items-center justify-center rounded-lg [&_svg]:h-3.5 [&_svg]:w-3.5", chip)}>{icon}</span> : null}
      </div>
      <div className="tabular mt-2.5 text-[1.65rem] font-semibold leading-none tracking-[-0.03em] text-fg">{value}</div>
      {hint ? <div className="mt-2 text-xs text-subtle">{hint}</div> : null}
    </div>
  );
}
