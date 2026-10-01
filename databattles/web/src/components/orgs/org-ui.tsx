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

/** Organization logo, or its initial on the accent color. */
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
  if (logoUrl) {
     
    return <img src={logoUrl} alt="" width={size} height={size} style={style} className={cn("shrink-0 rounded-lg object-cover ring-1 ring-border", className)} />;
  }
  return (
    <span
      aria-hidden
      style={{ ...style, background: accentColor ?? "var(--accent)", fontSize: Math.max(12, size * 0.42) }}
      className={cn("flex shrink-0 items-center justify-center rounded-lg font-bold text-white", className)}
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

export function EntitlementList({ entitlements, className }: { entitlements: Record<string, unknown>; className?: string }) {
  const entries = Object.entries(entitlements ?? {});
  if (!entries.length) return <p className="text-sm text-subtle">No entitlements configured.</p>;
  return (
    <ul className={cn("space-y-2 text-sm", className)}>
      {entries.map(([key, value]) => {
        const label = ENTITLEMENT_LABELS[key] ?? titleCase(key);
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
          <li key={key} className={cn("flex items-start gap-2", on ? "text-fg" : "text-subtle")}>
            {on ? <Check className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden /> : <Minus className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />}
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

export function PlanPrice({ cents }: { cents: number }) {
  if (!cents) return <span className="text-2xl font-semibold text-fg">Free</span>;
  return (
    <span>
      <span className="text-2xl font-semibold tabular-nums text-fg">{formatMoney(cents)}</span>
      <span className="text-sm text-muted"> / month</span>
    </span>
  );
}
