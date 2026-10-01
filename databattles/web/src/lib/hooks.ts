"use client";

import { useMutation, useQuery, useQueryClient, type UseMutationOptions } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { ApiError, errorMessage, get, post } from "./api";
import { qk } from "./query";
import type { Me, PublicConfig } from "./types";

/** The signed-in user (null when signed out). */
export function useMe() {
  return useQuery({ queryKey: qk.me, queryFn: () => get<Me | null>("/auth/me"), staleTime: 60_000 });
}

export function useConfig() {
  return useQuery({ queryKey: qk.config, queryFn: () => get<PublicConfig>("/meta/config"), staleTime: 5 * 60_000 });
}

export function hasRole(me: Me | null | undefined, role: "platform_admin" | "moderator"): boolean {
  if (!me) return false;
  if (me.platform_roles.includes("platform_admin")) return true;
  return me.platform_roles.includes(role);
}

/** Redirects to /login when the viewer is signed out. Returns the user once known. */
export function useRequireAuth() {
  const me = useMe();
  const router = useRouter();
  const pathname = usePathname();
  useEffect(() => {
    if (me.isSuccess && me.data === null) {
      // Keep the query string (e.g. ?token=…) so the user returns to exactly where they were.
      const search = typeof window !== "undefined" ? window.location.search : "";
      router.replace(`/login?next=${encodeURIComponent(pathname + search)}`);
    }
  }, [me.isSuccess, me.data, router, pathname]);
  return me;
}

export function useSignOut() {
  const qc = useQueryClient();
  const router = useRouter();
  return useCallback(async () => {
    try {
      await post("/auth/logout");
    } finally {
      qc.clear();
      // Per-viewer conveniences must not leak to the next person on a shared machine.
      try {
        window.localStorage.removeItem("db-palette-recent");
      } catch {
        /* storage unavailable */
      }
      router.push("/");
      router.refresh();
    }
  }, [qc, router]);
}

/**
 * Mutation with standard UX: success toast, error toast (except field validation errors, which the form shows),
 * and query invalidation.
 */
export function useApiMutation<TData, TVars>(
  fn: (vars: TVars) => Promise<TData>,
  {
    success,
    invalidate = [],
    onSuccess,
    onError,
    silentFieldErrors = true,
  }: {
    success?: string | ((data: TData) => string);
    invalidate?: readonly (readonly unknown[])[];
    onSuccess?: (data: TData, vars: TVars) => void;
    onError?: (e: ApiError) => void;
    silentFieldErrors?: boolean;
  } = {},
  options?: Omit<UseMutationOptions<TData, ApiError, TVars>, "mutationFn">,
) {
  const qc = useQueryClient();
  return useMutation<TData, ApiError, TVars>({
    mutationFn: fn,
    onSuccess: async (data, vars) => {
      if (success) toast.success(typeof success === "function" ? success(data) : success);
      await Promise.all(invalidate.map((key) => qc.invalidateQueries({ queryKey: key })));
      onSuccess?.(data, vars);
    },
    onError: (e) => {
      const hasFields = e instanceof ApiError && Object.keys(e.fields).length > 0;
      if (!(silentFieldErrors && hasFields)) toast.error(errorMessage(e));
      onError?.(e);
    },
    ...options,
  });
}

export function useDebounced<T>(value: T, delay = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return v;
}

/** Re-renders every `ms` — used by countdowns. */
export function useNow(ms = 1000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

/** Warn before leaving a page with unsaved changes. */
export function useUnsavedChangesWarning(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);
}

/** Local draft persistence for long forms (never for secrets). */
export function useDraft<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(initial);
  const loaded = useRef(false);
  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;
    try {
      const raw = window.localStorage.getItem(`draft:${key}`);
      if (raw) setValue(JSON.parse(raw) as T);
    } catch {
      /* ignore */
    }
  }, [key]);
  useEffect(() => {
    if (!loaded.current) return;
    try {
      window.localStorage.setItem(`draft:${key}`, JSON.stringify(value));
    } catch {
      /* ignore quota */
    }
  }, [key, value]);
  const clear = useCallback(() => {
    try {
      window.localStorage.removeItem(`draft:${key}`);
    } catch {
      /* ignore */
    }
  }, [key]);
  return [value, setValue, clear] as const;
}
