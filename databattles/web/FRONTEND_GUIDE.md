# Frontend guide (conventions for every page)

Next.js 16 App Router · React 19 · TypeScript (strict) · Tailwind v4 (CSS-first tokens in `src/app/globals.css`) ·
TanStack Query 5 · Radix primitives · lucide-react icons · sonner toasts. Dark-first, light theme via `[data-theme="light"]`.

## Data access

* The browser calls the API **same-origin** at `/api/v1/*` (Next rewrite → FastAPI). Never call the backend host directly.
* Use `src/lib/api.ts`: `get`, `post`, `patch`, `put`, `del`, `upload` (XHR with progress + CSRF), `idempotencyKey()`.
  Errors are `ApiError` with `.status`, `.code` (stable backend code), `.message` (user-safe), `.fields` (per-field validation).
* Queries: `useQuery({ queryKey: qk.xxx(...), queryFn: () => get<T>(...) })`. Query keys live in `src/lib/query.ts` (`qk`);
  add page-local keys as arrays like `["orgs", slug, "members", filters]`.
* Mutations: `useApiMutation(fn, { success: "Saved", invalidate: [qk.x] })` from `src/lib/hooks.ts` — shows toasts, invalidates.
  Field errors (`e.fields`) must be displayed next to inputs via `<Field error=...>`; other errors via toast or `<FormError>`.
* Auth: `useMe()` (null when signed out), `useRequireAuth()` (redirects to /login), `hasRole(me, "moderator")`.
  The server enforces authorization; the UI only hides controls. 401 → sign-in prompt, 403 → `<PermissionDenied>`, 404 → `<NotFoundState>`.
* Types: `src/lib/types.ts` (+ generated `src/lib/api-schema.d.ts` from backend OpenAPI; `Schemas["Name"]`). Many endpoints return
  ad-hoc dicts — read the backend service (`backend/app/modules/<area>/service.py`) and declare a local interface.
* API reference: `backend/openapi.json` (all paths) and routers in `backend/app/modules/*/router.py`.

## Every screen must handle

loading (skeletons: `SkeletonCards`, `SkeletonRows`, `Skeleton`), empty (`EmptyState` with a helpful next action),
error (`ErrorState` with retry — `QueryState` does all of this), permission denied, and success feedback (toast).
No fake buttons: if an action has no API, don't render it. Destructive actions use `ConfirmDialog` (with `requireReason`
when the API takes a reason). Long forms warn on unsaved changes (`useUnsavedChangesWarning`).

## Components (import from `@/components/...`)

* `ui/button`: `Button` (variant primary|secondary|ghost|outline|danger|link, size sm|md|lg|icon, `loading`, `icon`), `LinkButton`.
* `ui/form`: `Field` (render-prop wiring id/aria), `Input`, `Textarea`, `Select`, `Checkbox`, `Switch`, `TagInput`, `FormError`, `Label`.
* `ui/card`: `Card`, `CardHeader`, `CardBody`, `CardFooter`, `Stat`.
* `ui/badge`: `Badge` (tone), `StatusBadge` (competition/submission/verification statuses), `DemoBadge` (**always show on `is_demo`**),
  `VerifiedBadge`, `SelfDeclaredBadge`.
* `ui/avatar`: `Avatar`, `AvatarStack`. `domain/cards`: `CompetitionCard`, `DatasetCard`, `ProjectCard`, `CourseCard`, `OrgCard`, `UserLink`.
* `ui/dialog`: `Dialog`, `ConfirmDialog`. `ui/menu`: `Menu*`, `Tooltip`. `ui/tabs`: `Tabs`/`TabPanel` (local), `NavTabs` (route tabs).
* `ui/states`: `QueryState`, `EmptyState`, `NoResults`, `ErrorState`, `PermissionDenied`, `NotFoundState`, `Spinner`, `InlineNotice`, `SignInPrompt`.
* `ui/table`: `Table, THead, TBody, TR, TH, TD`. `ui/pagination`: `Pagination`.
* `ui/markdown`: `Prose` (server-sanitized HTML only), `MarkdownEditor` (server preview), `Highlighted` (search snippets).
* `ui/misc`: `CopyButton`, `ProgressBar`, `ProgressRing`, `Countdown`, `Cover` (generated art from `cover_style`), `FileDrop`, `KeyValue`.
* `ui/page`: `Container`, `PageHeader`, `Section`. `charts/charts`: `Sparkline`, `LineChart`, `BarChart`, `HBarList`, `Histogram`, `ActivityHeatmap`.
* `lib/format`: `formatDate`, `formatDateTime` (shows zone), `relativeTime`, `formatNumber`, `compactNumber`, `formatScore`, `formatBytes`,
  `formatMoney`, `titleCase`. `lib/url-state`: `useUrlState(defaults)` for filters/sort/page in the URL.

## Rules

* Pages are client components (`"use client"`) using the hooks above; wrap `useSearchParams` users in `<Suspense>`.
* Next 16: in client pages read route params with `useParams<{ slug: string }>()`.
* Accessibility: semantic headings (one `h1` per page via `PageHeader`), labels on every control, `aria-current` in nav,
  keyboard reachable menus/dialogs (Radix), color never the only signal, visible focus.
* Deadlines: show local time with zone (`formatDateTime`) plus relative time; the API stores UTC.
* Never display private scores unless the API returned them; never render unsanitized HTML; external links get
  `target="_blank" rel="noopener noreferrer"`.
* Seeded demo content (`is_demo`) always shows `<DemoBadge/>`; never claim demo data is live.
