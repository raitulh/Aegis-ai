import { ApiError, errorMessage } from "@/lib/api";

/** Friendly titles for stable backend error codes used by the catalog areas. The backend message is shown as the body. */
const TITLES: Record<string, string> = {
  quota_exceeded: "Storage quota reached",
  file_too_large: "File is too large",
  payload_too_large: "File is too large",
  invalid_file_type: "File type not allowed",
  malware_detected: "File rejected by the malware scan",
  duplicate_file: "A file with that name already exists",
  version_immutable: "This version is locked",
  version_empty: "Add a file first",
  draft_exists: "A draft already exists",
  invalid_transition: "That change isn't allowed",
  terms_required: "Terms must be accepted",
  github_repo_not_found: "Repository not found",
  github_rate_limited: "GitHub is rate limiting requests",
  github_unavailable: "GitHub is unreachable",
  github_repo_private: "Private repository",
  sync_too_soon: "Synced recently",
  demo_repo: "Demo repository",
  media_limit: "Image limit reached",
  invalid_image: "Not a supported image",
  member_limit: "Member limit reached",
  email_not_verified: "Verify your email first",
  rate_limited: "Too many requests",
  network_error: "Connection problem",
  aborted: "Upload canceled",
};

export function describeError(e: unknown, fallbackTitle = "Something went wrong"): { title: string; message: string } {
  if (e instanceof ApiError) return { title: TITLES[e.code] ?? fallbackTitle, message: e.message };
  return { title: fallbackTitle, message: errorMessage(e) };
}

/**
 * Field errors that the form does not render next to an input (so they would otherwise be invisible),
 * joined into one message for a <FormError>.
 */
export function unplacedFieldErrors(e: ApiError | null | undefined, placed: readonly string[]): string | null {
  if (!e) return null;
  const fields = e.fields;
  const keys = Object.keys(fields);
  if (!keys.length) return null;
  const rest = keys.filter((k) => !placed.includes(k));
  if (!rest.length) return null;
  return rest.map((k) => `${k.replace(/_/g, " ")}: ${fields[k]}`).join(" · ");
}

/**
 * For ConfirmDialog.onConfirm: errors are already surfaced by useApiMutation's toast, so resolve instead of
 * rejecting (an unhandled rejection from the dialog's click handler would otherwise surface as a runtime error).
 */
export function settle(p: Promise<unknown>): Promise<void> {
  return p.then(
    () => undefined,
    () => undefined,
  );
}
