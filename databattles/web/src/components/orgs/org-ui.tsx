import { BadgeCheck, Check, Minus } from "lucide-react";
import type { ReactNode } from "react";

import { Badge, StatusBadge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import { formatMoney, formatNumber, titleCase } from "@/lib/format";
import type { OrgRole, OrgType } from "./types";

export const ORG_TYPES: { value: OrgType; label: string; description: string }[] = [
  {
    value: "university",
    label: "University",
    description:
      "An accredited institution. Starts unverified — request verification from the admin page so the platform team can enable institutional-email membership.",
  },
  { value: "club", label: "Club", description: "A student society or club, usually attached to a university." },
  { value: "community", label: "Community", description: "An independent learning or practitioner community." },
  { value: "sponsor", label: "Sponsor", description: "A company or foundation that sponsors competitions and discovers opted-in talent." },
  { value: "company", label: "Company", description: "A company hosting its own competitions or internal learning." },
];

export const ROLE_LABELS: Record<OrgRole, string> = {
  owner: "Owner",
  admin: "Admin",
  manager: "Manager",
  member: "Member",
};

export const ROLE_DESCRIPTIONS: Record<OrgRole, string> = {
  owner: "Full control, including ownership and billing.",
  admin: "Manages members, invites, departments and settings.",
  manager: "Manages competitions and content; can view the roster and dashboard.",
  member: "Regular member.",
};

export const VERIFICATION_METHOD_LABELS: Record<string, string> = {
  domain: "Institutional email",
  invite: "Admin invite",
  admin_review: "Approved by an admin",
  none: "Not verified",
};

export function isManagerRole(role: string | null | undefined): boolean {
  return role === "owner" || role === "admin" || role === "manager";
}

export function isAdminRole(role: string | null | undefined): boolean {
  return role === "owner" || role === "admin";
}

const HEX6 = /^#[0-9a-fA-F]{6}$/;

/** The org's own accent colour when it is a valid hex, otherwise the brand accent. */
export function orgAccent(color: string | null | undefined): string {
  return color && HEX6.test(color) ? color : "var(--accent)";
}

/**
 * Organization logo, or its initial on the accent color (a soft accent gradient with a top highlight).
 * Props are unchanged; corner radius scales with `size` and can still be overridden with `className`.
 */
export function OrgLogo({
  name,
  logoUrl,
  accentColor,
  size = 44,
  className,
}: {
  name: string;
  logoUrl?: string | null;
  accentColor?: string | null;
  size?: number;
  className?: string;
}) {
  const style = { width: size, height: size };
  const radius = size >= 64 ? "rounded-2xl" : size >= 40 ? "rounded-xl" : "rounded-lg";
  if (logoUrl) {
    return <img src={logoUrl} alt="" width={size} height={size} style={style} className={cn("shrink-0 object-cover ring-1 ring-border", radius, className)} />;
  }
  const accent = orgAccent(accentColor);
  return (
    <span
      aria-hidden
      style={{
        ...style,
        background: `linear-gradient(135deg, color-mix(in oklab, ${accent} 92%, white), ${accent} 45%, color-mix(in oklab, ${accent} 58%, black))`,
        fontSize: Math.max(12, size * 0.42),
      }}
      className={cn(
        "flex shrink-0 select-none items-center justify-center font-bold tracking-[-0.02em] text-white",
        "shadow-[inset_0_1px_0_rgb(255_255_255/0.28),inset_0_0_0_1px_rgb(255_255_255/0.08),0_8px_24px_-12px_rgb(0_0_0/0.6)]",
        radius,
        className,
      )}
    >
      {name.trim()[0]?.toUpperCase() ?? "?"}
    </span>
  );
}

export function OrgVerificationBadge({ status }: { status: string }) {
  if (status === "verified") {
    return (
      <Badge tone="success" icon={<BadgeCheck className="h-3 w-3" aria-hidden />} title="Verified by the platform team">
        Verified organization
      </Badge>
    );
  }
  if (status === "pending") return <Badge tone="warning" title="Verification requested; awaiting platform review">Verification pending</Badge>;
  return <StatusBadge status={status} />;
}

export function RoleBadge({ role }: { role: string }) {
  const tone = role === "owner" ? "accent" : role === "admin" ? "info" : role === "manager" ? "success" : "neutral";
  return <Badge tone={tone}>{ROLE_LABELS[role as OrgRole] ?? titleCase(role)}</Badge>;
}

/** Human labels for plan entitlements (configuration, not code branches — unknown keys are title-cased). */
const ENTITLEMENT_LABELS: Record<string, string> = {
  max_active_competitions: "Active competitions",
  private_competitions: "Private & members-only competitions",
  custom_certificates: "Custom certificate templates",
  analytics_export: "Analytics & roster export",
  talent_discovery: "Consent-based talent discovery",
};

export function entitlementLabel(key: string): string {
  return ENTITLEMENT_LABELS[key] ?? titleCase(key);
}

/** Union of entitlement keys across plans, in first-seen order (for comparison tables). */
export function entitlementKeys(plans: { entitlements: Record<string, unknown> }[]): string[] {
  const keys: string[] = [];
  for (const p of plans) for (const k of Object.keys(p.entitlements ?? {})) if (!keys.includes(k)) keys.push(k);
  return keys;
}

/** One entitlement value as a compact cell: check / dash for booleans, the number, or "Unlimited" for null. */
export function EntitlementValue({ value }: { value: unknown }) {
  if (typeof value === "boolean" || value === undefined) {
    return value ? (
      <span className="inline-flex items-center gap-1.5 text-success">
        <Check className="h-4 w-4" aria-hidden />
        <span className="sr-only">Included</span>
      </span>
    ) : (
      <span className="inline-flex items-center text-subtle">
        <Minus className="h-4 w-4" aria-hidden />
        <span className="sr-only">Not included</span>
      </span>
    );
  }
  if (typeof value === "number") return <span className="tabular font-medium text-fg">{formatNumber(value)}</span>;
  if (value === null) return <span className="font-medium text-fg">Unlimited</span>;
  return <span className="font-medium text-fg">{String(value)}</span>;
}

export function EntitlementList({ entitlements, className }: { entitlements: Record<string, unknown>; className?: string }) {
  const entries = Object.entries(entitlements ?? {});
  if (!entries.length) return <p className="text-sm text-subtle">No entitlements configured.</p>;
  return (
    <ul className={cn("space-y-2.5 text-sm", className)}>
      {entries.map(([key, value]) => {
        const label = entitlementLabel(key);
        let content: ReactNode;
        let on = true;
        if (typeof value === "boolean") {
          on = value;
          content = label;
        } else if (typeof value === "number") {
          content = (
            <>
              {label}: <span className="font-medium tabular-nums text-fg">{formatNumber(value)}</span>
            </>
          );
        } else if (value === null) {
          content = <>{label}: <span className="font-medium text-fg">Unlimited</span></>;
        } else {
          content = <>{label}: <span className="font-medium text-fg">{String(value)}</span></>;
        }
        return (
          <li key={key} className={cn("flex items-start gap-2.5", on ? "text-fg" : "text-subtle")}>
            {on ? (
              <span className="mt-px flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full bg-success-soft text-success">
                <Check className="h-3 w-3" aria-hidden />
              </span>
            ) : (
              <span className="mt-px flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full bg-surface-3 text-subtle">
                <Minus className="h-3 w-3" aria-hidden />
              </span>
            )}
            <span>
              {content}
              {!on ? <span className="sr-only"> (not included)</span> : null}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export function PlanPrice({ cents, size = "md" }: { cents: number; size?: "md" | "lg" }) {
  const big = size === "lg" ? "text-[2.5rem] leading-none tracking-[-0.04em]" : "text-2xl tracking-[-0.02em]";
  if (!cents) return <span className={cn("font-semibold text-fg", big)}>Free</span>;
  return (
    <span className="inline-flex items-baseline gap-1">
      <span className={cn("font-semibold tabular-nums text-fg", big)}>{formatMoney(cents)}</span>
      <span className="text-sm text-muted">/ month</span>
    </span>
  );
}
