/** Query keys for the open-source hub. Kept dependency-free so light pages (the landing) can share the cache. */
export const osKeys = {
  all: ["opensource"] as const,
  overview: ["opensource", "overview"] as const,
  repos: (f: object) => ["opensource", "repos", f] as const,
  issues: (f: object) => ["opensource", "issues", f] as const,
  account: ["opensource", "github-account"] as const,
};
