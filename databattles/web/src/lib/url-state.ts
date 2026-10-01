"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";

/** Filters/sort/page kept in the URL so lists are shareable and back/forward works. */
export function useUrlState<T extends Record<string, string | undefined>>(defaults: T) {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const state = useMemo(() => {
    const out: Record<string, string | undefined> = { ...defaults };
    for (const key of Object.keys(defaults)) {
      const v = params.get(key);
      if (v !== null) out[key] = v;
    }
    return out as T;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);
  const set = useCallback(
    (patch: Partial<T>, { resetPage = true }: { resetPage?: boolean } = {}) => {
      const sp = new URLSearchParams(params.toString());
      for (const [k, v] of Object.entries(patch)) {
        if (v === undefined || v === "" || v === defaults[k]) sp.delete(k);
        else sp.set(k, String(v));
      }
      if (resetPage && !("page" in patch)) sp.delete("page");
      const qs = sp.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [params, pathname, router, defaults],
  );
  const reset = useCallback(() => router.replace(pathname, { scroll: false }), [pathname, router]);
  return [state, set, reset] as const;
}
