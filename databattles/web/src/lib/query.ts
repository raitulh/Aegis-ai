import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./api";

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 20_000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => {
          if (error instanceof ApiError && [400, 401, 403, 404, 409, 422].includes(error.status)) return false;
          return failureCount < 2;
        },
      },
      mutations: { retry: false },
    },
  });
}

/** Query-key factory — keeps invalidation consistent across pages. */
export const qk = {
  me: ["me"] as const,
  config: ["config"] as const,
  unread: ["notifications", "unread"] as const,
  notifications: (filter?: object) => ["notifications", "list", filter ?? {}] as const,
  competitions: (filter?: object) => ["competitions", "list", filter ?? {}] as const,
  competition: (slug: string) => ["competitions", slug] as const,
  leaderboard: (slug: string, filter?: object) => ["competitions", slug, "leaderboard", filter ?? {}] as const,
  submissions: (slug: string, filter?: object) => ["competitions", slug, "submissions", filter ?? {}] as const,
  team: (slug: string) => ["competitions", slug, "team"] as const,
  datasets: (filter?: object) => ["datasets", "list", filter ?? {}] as const,
  dataset: (slug: string, version?: number) => ["datasets", slug, version ?? "latest"] as const,
  projects: (filter?: object) => ["projects", "list", filter ?? {}] as const,
  project: (slug: string) => ["projects", slug] as const,
  courses: (filter?: object) => ["courses", "list", filter ?? {}] as const,
  course: (slug: string) => ["courses", slug] as const,
  lesson: (slug: string, lesson: string) => ["courses", slug, "lessons", lesson] as const,
  threads: (filter?: object) => ["discussions", "threads", filter ?? {}] as const,
  thread: (id: string, page?: number) => ["discussions", "thread", id, page ?? 1] as const,
  orgs: (filter?: object) => ["orgs", "list", filter ?? {}] as const,
  org: (slug: string) => ["orgs", slug] as const,
  profile: (handle: string) => ["users", handle] as const,
  dashboard: ["dashboard"] as const,
  search: (q: string, type?: string, page?: number) => ["search", q, type ?? "all", page ?? 1] as const,
};
