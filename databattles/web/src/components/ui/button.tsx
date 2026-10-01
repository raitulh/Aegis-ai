import Link from "next/link";
import { forwardRef, type AnchorHTMLAttributes, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Loader2 } from "lucide-react";

import { cn } from "@/lib/cn";

type Variant = "primary" | "secondary" | "ghost" | "outline" | "danger" | "link";
type Size = "sm" | "md" | "lg" | "icon";

const base =
  "group/btn relative inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[var(--radius-md)] font-medium select-none " +
  "transition-[background-color,border-color,color,box-shadow,scale,opacity,filter] duration-200 ease-out-expo " +
  "active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50 aria-busy:cursor-progress " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]";

const variants: Record<Variant, string> = {
  // Brand gradient with a top highlight; glows softly on hover.
  primary:
    "text-accent-fg bg-[linear-gradient(180deg,color-mix(in_oklab,var(--accent-fill)_94%,white)_0%,var(--accent-fill)_55%,color-mix(in_oklab,var(--accent-fill)_86%,var(--blue))_100%)] " +
    "shadow-[inset_0_1px_0_rgb(255_255_255/0.22),inset_0_0_0_1px_rgb(255_255_255/0.08),0_1px_2px_rgb(0_0_0/0.25)] " +
    "hover:shadow-[inset_0_1px_0_rgb(255_255_255/0.28),inset_0_0_0_1px_rgb(255_255_255/0.12),0_8px_28px_-8px_color-mix(in_oklab,var(--accent)_70%,transparent)] hover:brightness-105",
  secondary:
    "bg-surface-2 text-fg border border-border shadow-[inset_0_1px_0_var(--hairline-highlight)] hover:bg-surface-3 hover:border-border-strong",
  ghost: "text-muted hover:text-fg hover:bg-surface-2",
  outline: "border border-border-strong text-fg hover:bg-surface-2 hover:border-[color-mix(in_oklab,var(--accent)_45%,var(--border-strong))]",
  danger:
    "bg-danger-fill text-white shadow-[inset_0_1px_0_rgb(255_255_255/0.18)] hover:shadow-[inset_0_1px_0_rgb(255_255_255/0.2),0_8px_24px_-10px_var(--danger)] hover:brightness-105",
  link: "text-accent-strong underline-offset-4 hover:underline px-0 h-auto active:scale-100",
};

const sizes: Record<Size, string> = {
  sm: "h-8 px-3 text-[13px]",
  md: "h-10 px-4 text-sm",
  lg: "h-11 px-5 text-[15px]",
  icon: "h-9 w-9 p-0",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  icon?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = "primary", size = "md", loading, icon, children, disabled, type = "button", ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cn(base, variants[variant], sizes[size], className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
});

export interface LinkButtonProps extends AnchorHTMLAttributes<HTMLAnchorElement> {
  href: string;
  variant?: Variant;
  size?: Size;
  icon?: ReactNode;
  external?: boolean;
}

export function LinkButton({ href, variant = "primary", size = "md", className, icon, children, external, ...props }: LinkButtonProps) {
  const cls = cn(base, variants[variant], sizes[size], className);
  if (external) {
    return (
      <a href={href} className={cls} target="_blank" rel="noopener noreferrer" {...props}>
        {icon}
        {children}
      </a>
    );
  }
  return (
    <Link href={href} className={cls} {...props}>
      {icon}
      {children}
    </Link>
  );
}
