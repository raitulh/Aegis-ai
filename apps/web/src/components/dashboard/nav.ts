import {
  Activity,
  Bot,
  Boxes,
  FileBarChart,
  FileCheck2,
  FlaskConical,
  Gauge,
  History,
  LayoutDashboard,
  Network,
  Plug,
  Radar,
  Receipt,
  RefreshCcw,
  ScrollText,
  Settings,
  ShieldAlert,
  SquareTerminal,
  Users,
  type LucideIcon,
} from "lucide-react";

export type NavItem = { href: string; label: string; icon: LucideIcon; permission?: string; keywords?: string };
export type NavGroup = { section?: string; items: NavItem[] };

/** Information architecture: organised around the assurance loop (assure → govern → prove → build). */
export const NAV: NavGroup[] = [
  {
    items: [
      { href: "/dashboard", label: "Overview", icon: LayoutDashboard, keywords: "home command center posture" },
      { href: "/dashboard/graph", label: "Assurance Graph", icon: Network, keywords: "topology relationships map" },
    ],
  },
  {
    section: "Assure",
    items: [
      { href: "/dashboard/systems", label: "Systems", icon: Boxes, keywords: "ai systems models endpoints" },
      { href: "/dashboard/agents", label: "Agents", icon: Bot, keywords: "agent traces tools" },
      { href: "/dashboard/runtime", label: "Runtime Guard", icon: Radar, permission: "runtime:read", keywords: "runtime events decisions approvals enforce" },
      { href: "/dashboard/audits", label: "Audits", icon: Gauge, keywords: "tests evaluations runs" },
      { href: "/dashboard/red-team", label: "Red Team", icon: FlaskConical, permission: "redteam:run", keywords: "attack campaign probes" },
      { href: "/dashboard/assurance", label: "Continuous Assurance", icon: RefreshCcw, permission: "assurance:read", keywords: "schedule ci cd triggers regression baseline" },
      { href: "/dashboard/monitoring", label: "Monitoring", icon: Activity, keywords: "production alerts drift" },
    ],
  },
  {
    section: "Govern & prove",
    items: [
      { href: "/dashboard/findings", label: "Findings", icon: ShieldAlert, keywords: "issues vulnerabilities remediation" },
      { href: "/dashboard/policies", label: "Policies & Controls", icon: ScrollText, keywords: "policy studio runtime compliance controls frameworks" },
      { href: "/dashboard/evidence", label: "Evidence", icon: FileCheck2, keywords: "integrity verify export hash chain" },
      { href: "/dashboard/reports", label: "Reports", icon: FileBarChart, keywords: "pdf export" },
    ],
  },
  {
    section: "Build",
    items: [
      { href: "/dashboard/integrations", label: "Integrations", icon: Plug, keywords: "webhooks slack providers" },
      { href: "/dashboard/developers", label: "API & SDK", icon: SquareTerminal, keywords: "api keys sdk mcp developers" },
    ],
  },
  {
    section: "Workspace",
    items: [
      { href: "/dashboard/team", label: "Members & roles", icon: Users, keywords: "team invite roles rbac" },
      { href: "/dashboard/billing", label: "Usage & Billing", icon: Receipt, permission: "usage:read", keywords: "plan quota limits subscription" },
      { href: "/dashboard/audit-log", label: "Audit log", icon: History, permission: "audit_logs:read", keywords: "activity history who changed" },
      { href: "/dashboard/settings", label: "Settings", icon: Settings, keywords: "workspace retention sla" },
    ],
  },
];

export const ALL_NAV_ITEMS: NavItem[] = NAV.flatMap((g) => g.items);
