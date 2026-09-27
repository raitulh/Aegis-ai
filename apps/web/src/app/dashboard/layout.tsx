import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { DashboardShell } from "@/components/dashboard/shell";
import { serverFetch } from "@/lib/server-api";
import type { Session } from "@/lib/types";

export default async function DashboardLayout({ children }: { children: ReactNode }) {
  const session = await serverFetch<Session>("/auth/session");
  if (!session) redirect("/login");
  return <DashboardShell>{children}</DashboardShell>;
}
