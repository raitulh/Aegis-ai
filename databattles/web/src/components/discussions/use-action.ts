"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError, errorMessage } from "@/lib/api";

function waitText(seconds: number | null): string {
  if (!seconds || !Number.isFinite(seconds) || seconds <= 0) return "a few minutes";
  if (seconds < 90) return `${Math.max(1, Math.round(seconds))} seconds`;
  const minutes = Math.ceil(seconds / 60);
  return minutes === 1 ? "a minute" : `about ${minutes} minutes`;
}

/**
 * User-facing copy for API errors, with friendlier wording for rate limits (429) and
 * unverified accounts than the raw backend message.
 */
export function friendlyError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 429) {
      const retry = Number(e.details?.retry_after_seconds ?? NaN);
      return `You're doing that a little too often. Please wait ${waitText(Number.isNaN(retry) ? null : retry)} and try again.`;
    }
    if (e.code === "email_not_verified") return "Please verify your email address first — check your inbox for the verification link.";
    if (e.status === 401) return "Your session has ended. Please sign in again.";
  }
  return errorMessage(e);
}

/**
 * Mutation with toast feedback and cache invalidation (like `useApiMutation`), but with friendly
 * rate-limit/verification copy. Field validation errors are left for the form to render.
 */
export function useAction<TData, TVars = void>(
  fn: (vars: TVars) => Promise<TData>,
  opts: {
    success?: string | ((data: TData) => string);
    invalidate?: readonly (readonly unknown[])[];
    onSuccess?: (data: TData, vars: TVars) => void | Promise<void>;
    onError?: (e: ApiError) => void;
    /** Set false when the caller renders the error inline instead of a toast. */
    toastErrors?: boolean;
  } = {},
) {
  const qc = useQueryClient();
  return useMutation<TData, ApiError, TVars>({
    mutationFn: fn,
    onSuccess: async (data, vars) => {
      if (opts.success) toast.success(typeof opts.success === "function" ? opts.success(data) : opts.success);
      await Promise.all((opts.invalidate ?? []).map((key) => qc.invalidateQueries({ queryKey: key })));
      await opts.onSuccess?.(data, vars);
    },
    onError: (e) => {
      const hasFields = e instanceof ApiError && Object.keys(e.fields).length > 0;
      if (opts.toastErrors !== false && !hasFields) toast.error(friendlyError(e));
      opts.onError?.(e);
    },
  });
}
