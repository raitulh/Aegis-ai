"use client";

import { createContext, useContext } from "react";

import type { OrgDetail } from "./types";

/** The organization being administered, provided by `app/orgs/[slug]/admin/layout.tsx`. */
export const OrgAdminContext = createContext<OrgDetail | null>(null);

export function useAdminOrg(): OrgDetail {
  const org = useContext(OrgAdminContext);
  if (!org) throw new Error("useAdminOrg must be used inside the organization admin layout.");
  return org;
}

/**
 * Owners (and platform admins, who act at owner level) may assign any role and act on anyone;
 * org admins only on members below their own level. The server enforces this; the UI only hides controls.
 */
export function viewerIsOwnerLevel(org: OrgDetail, isPlatformAdmin: boolean): boolean {
  return isPlatformAdmin || org.viewer.role === "owner" || (org.viewer.can_manage && org.viewer.role === null);
}
