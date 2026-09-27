"use client";
import { cva, type VariantProps } from "class-variance-authority";
import {
  AlertCircle,
  AlertTriangle,
  Circle,
  Info,
  Loader2,
  Octagon,
  type LucideIcon,
} from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes, type HTMLAttributes, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { RISK_META, SEVERITY_META, STATUS_META } from "@/lib/format";

/* ---------------------------------- Button ---------------------------------- */
const button = cva(
  "inline-flex items-center justify-center gap-2 rounded-[var(--radius)] font-medium transition-all focus-ring disabled:opacity-50 disabled:pointer-events-none select-none whitespace-nowrap",
  {
    variants: {
      variant: {
        primary: "bg-[var(--color-accent)] text-white hover:bg-[var(--color-accent-bright)] shadow-[var(--shadow-sm)]",
        secondary: "bg-[var(--color-surface-2)] text-[var(--color-text)] border border-[var(--color-border-strong)] hover:bg-[var(--color-surface-3)]",
        ghost: "text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-2)]",
        danger: "bg-[var(--color-critical)] text-white hover:opacity-90",
        outline: "border border-[var(--color-border-strong)] text-[var(--color-text)] hover:bg-[var(--color-surface-2)]",
      },
      size: {
        sm: "h-8 px-3 text-xs",
        md: "h-9.5 px-4 text-sm",
        lg: "h-11 px-6 text-sm",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof button> {
  loading?: boolean;
  icon?: LucideIcon;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant, size, loading, icon: Icon, children, disabled, ...props },
  ref,
) {
  return (
    <button ref={ref} className={cn(button({ variant, size }), className)} disabled={disabled || loading} {...props}>
      {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : Icon ? <Icon className="h-4 w-4" /> : null}
      {children}
    </button>
  );
});

/* ---------------------------------- Card ------------------------------------ */
export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface)]", className)} {...props} />;
}
export function CardHeader({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex items-center justify-between gap-3 px-5 py-4 border-b border-[var(--color-border)]", className)} {...props} />;
}
export function CardTitle({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn("text-sm font-semibold tracking-tight", className)} {...props} />;
}
export function CardBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-5", className)} {...props} />;
}

/* --------------------------------- Badges ----------------------------------- */
const SEV_ICONS: Record<string, LucideIcon> = { octagon: Octagon, "alert-triangle": AlertTriangle, "alert-circle": AlertCircle, info: Info, circle: Circle };

export function SeverityBadge({ severity, className }: { severity: string; className?: string }) {
  const meta = SEVERITY_META[severity] ?? SEVERITY_META.info;
  const Icon = SEV_ICONS[meta.icon] ?? Circle;
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium", className)} style={{ color: meta.color, borderColor: `color-mix(in srgb, ${meta.color} 40%, transparent)`, background: `color-mix(in srgb, ${meta.color} 12%, transparent)` }}>
      <Icon className="h-3 w-3" />
      {meta.label}
    </span>
  );
}

export function RiskBadge({ level, className }: { level: string; className?: string }) {
  const meta = RISK_META[level] ?? RISK_META.informational;
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-semibold uppercase tracking-wide", className)} style={{ color: meta.color, background: `color-mix(in srgb, ${meta.color} 14%, transparent)` }}>
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: meta.color }} />
      {meta.label}
    </span>
  );
}

const TONE_COLORS: Record<string, string> = {
  success: "var(--color-success)",
  critical: "var(--color-critical)",
  high: "var(--color-high)",
  medium: "var(--color-medium)",
  low: "var(--color-low)",
  info: "var(--color-info)",
};

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const meta = STATUS_META[status] ?? { label: status, tone: "info" };
  const color = TONE_COLORS[meta.tone] ?? TONE_COLORS.info;
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium", className)} style={{ color, background: `color-mix(in srgb, ${color} 12%, transparent)` }}>
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
      {meta.label}
    </span>
  );
}

export function Badge({ className, children, tone = "neutral" }: { className?: string; children: ReactNode; tone?: "neutral" | "accent" | "success" | "warning" }) {
  const tones: Record<string, string> = {
    neutral: "border-[var(--color-border-strong)] text-[var(--color-text-muted)]",
    accent: "text-[var(--color-accent-bright)] border-[color-mix(in_srgb,var(--color-accent)_40%,transparent)] bg-[var(--color-accent-dim)]",
    success: "text-[var(--color-success)] border-[color-mix(in_srgb,var(--color-success)_40%,transparent)]",
    warning: "text-[var(--color-warning)] border-[color-mix(in_srgb,var(--color-warning)_40%,transparent)]",
  };
  return <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium", tones[tone], className)}>{children}</span>;
}

/* --------------------------------- States ----------------------------------- */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton rounded-[var(--radius)]", className)} />;
}

export function EmptyState({ icon: Icon, title, description, action }: { icon?: LucideIcon; title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-[var(--radius-lg)] border border-dashed border-[var(--color-border-strong)] bg-[var(--color-surface)]/50 px-6 py-14 text-center">
      {Icon ? <Icon className="h-8 w-8 text-[var(--color-text-subtle)]" /> : null}
      <div>
        <p className="text-sm font-semibold">{title}</p>
        {description ? <p className="mt-1 max-w-md text-sm text-[var(--color-text-muted)]">{description}</p> : null}
      </div>
      {action}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message?: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-[var(--radius-lg)] border border-[color-mix(in_srgb,var(--color-critical)_30%,transparent)] bg-[color-mix(in_srgb,var(--color-critical)_6%,transparent)] px-6 py-12 text-center">
      <AlertTriangle className="h-7 w-7 text-[var(--color-critical)]" />
      <p className="text-sm font-medium">{message ?? "Something went wrong."}</p>
      {onRetry ? (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("h-4 w-4 animate-spin text-[var(--color-text-muted)]", className)} />;
}

/* -------------------------------- Progress ---------------------------------- */
export function Progress({ value, className, color = "var(--color-accent)" }: { value: number; className?: string; color?: string }) {
  return (
    <div className={cn("h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-surface-3)]", className)}>
      <div className="h-full rounded-full transition-[width] duration-500" style={{ width: `${Math.max(0, Math.min(100, value))}%`, background: color }} />
    </div>
  );
}
