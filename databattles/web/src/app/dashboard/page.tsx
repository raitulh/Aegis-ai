"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, BadgeCheck, Bell, LayoutDashboard, Settings2, UserRound } from "lucide-react";
import Link from "next/link";
import { useMemo, useState, useSyncExternalStore, type ReactNode } from "react";
import { toast } from "sonner";

import { MiniSwitch } from "@/components/profile/mini-switch";
import { DASHBOARD_WIDGETS, type DashboardData, type DashboardWidget, type PublicProfile } from "@/components/profile/types";
import { Button, LinkButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Container, PageHeader } from "@/components/ui/page";
import { EmptyState, ErrorState, InlineNotice, Skeleton } from "@/components/ui/states";
import { errorMessage, get, post, put } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";
import { useApiMutation, useRequireAuth } from "@/lib/hooks";
import { qk } from "@/lib/query";
import type { Me } from "@/lib/types";
import { MetricsStrip, UpNext, useGreeting } from "./_components/overview";
import { CompactContext, isWidgetEmpty, RENDER, WIDGET_META, widgetAction, type WidgetContext } from "./_components/widgets";

const isWidget = (k: string): k is DashboardWidget => (DASHBOARD_WIDGETS as readonly string[]).includes(k);

function resolveLayout(prefs: DashboardData["prefs"] | undefined) {
  const saved = (prefs?.order ?? []).filter(isWidget);
  const order = Array.from(new Set<DashboardWidget>([...saved, ...DASHBOARD_WIDGETS]));
  const hidden = (prefs?.hidden ?? []).filter(isWidget);
  return { order, hidden };
}

/**
 * The rail layout only exists at Tailwind's `lg` breakpoint (64rem). Below it every visible widget is laid out in one
 * flow in exactly the saved order, so moving e.g. "Upcoming deadlines" to the top still puts it at the top on phones.
 * The server snapshot is the single-flow layout; the dashboard body only renders on the client after auth resolves.
 */
const LG_QUERY = "(min-width: 64rem)";
function subscribeLg(onChange: () => void) {
  const mq = window.matchMedia(LG_QUERY);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}
function useIsLg(): boolean {
  return useSyncExternalStore(subscribeLg, () => window.matchMedia(LG_QUERY).matches, () => false);
}

/** A widget on its own, or a run of two or more consecutive empty widgets collapsed into one compact panel. */
type Block = { kind: "widget"; id: DashboardWidget } | { kind: "empty"; ids: DashboardWidget[] };

const blockKey = (b: Block) => (b.kind === "widget" ? b.id : `empty:${b.ids.join(",")}`);
const blockIsWide = (b: Block) => b.kind === "empty" || Boolean(WIDGET_META[b.id].wide);

function toBlocks(ids: DashboardWidget[], d: DashboardData): Block[] {
  const out: Block[] = [];
  let run: DashboardWidget[] = [];
  const flush = () => {
    if (run.length >= 2) out.push({ kind: "empty", ids: run });
    else for (const id of run) out.push({ kind: "widget", id });
    run = [];
  };
  for (const id of ids) {
    if (isWidgetEmpty(id, d)) {
      run.push(id);
    } else {
      flush();
      out.push({ kind: "widget", id });
    }
  }
  flush();
  return out;
}

/**
 * Which blocks take the full width of their column: wide blocks always do, and a narrow widget that has no narrow
 * neighbour to pair with stretches too — so the saved order is kept and the grid never leaves a hole.
 */
function fullWidth(blocks: Block[]): Set<string> {
  const full = new Set<string>();
  let i = 0;
  while (i < blocks.length) {
    const b = blocks[i];
    const next = blocks[i + 1];
    if (blockIsWide(b)) {
      full.add(blockKey(b));
      i += 1;
    } else if (next && !blockIsWide(next)) {
      i += 2;
    } else {
      full.add(blockKey(b));
      i += 1;
    }
  }
  return full;
}

// ----------------------------------------------------------------------------- widget shells

function Widget({ id, full, empty, action, children }: { id: DashboardWidget; full: boolean; empty?: boolean; action?: ReactNode; children: ReactNode }) {
  const meta = WIDGET_META[id];
  const Icon = meta.icon;
  return (
    <Card className={cn("flex min-w-0 flex-col", full && "md:col-span-2")}>
      <CardHeader icon={<Icon />} title={meta.label} description={meta.description} action={action} />
      {/* A lone empty widget paired with a taller neighbour keeps its empty state centred rather than top-heavy. */}
      <CardBody className={cn("flex-1", empty && "flex flex-col justify-center")}>{children}</CardBody>
    </Card>
  );
}

/** A compact, hairline-separated widget section (side rail and collapsed empty panels) instead of another card. */
function CompactSection({ id, action, children }: { id: DashboardWidget; action?: ReactNode; children: ReactNode }) {
  const meta = WIDGET_META[id];
  const Icon = meta.icon;
  return (
    <section aria-labelledby={`w-${id}`} className="px-5 py-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <span className="mt-px flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-border bg-surface-2 text-accent-strong" aria-hidden>
            <Icon className="h-3.5 w-3.5" />
          </span>
          <div className="min-w-0">
            <h2 id={`w-${id}`} className="text-sm font-semibold tracking-[-0.01em] text-fg">{meta.label}</h2>
            <p className="mt-0.5 text-xs leading-relaxed text-muted">{meta.description}</p>
          </div>
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
      <div className="mt-4">{children}</div>
    </section>
  );
}

/** Consecutive empty widgets share one panel of compact cells, in saved order. */
function EmptyPanel({ ids, d, ctx }: { ids: DashboardWidget[]; d: DashboardData; ctx: WidgetContext }) {
  return (
    <Card className="min-w-0 overflow-hidden md:col-span-2">
      <CompactContext.Provider value>
        <div className="grid grid-cols-1 gap-px bg-border md:grid-cols-2">
          {ids.map((id, i) => (
            <div key={id} className={cn("min-w-0 bg-surface", ids.length % 2 === 1 && i === ids.length - 1 && "md:col-span-2")}>
              <CompactSection id={id} action={widgetAction(id, d)}>
                {RENDER[id](d, ctx)}
              </CompactSection>
            </div>
          ))}
        </div>
      </CompactContext.Provider>
    </Card>
  );
}

function WidgetGrid({ ids, d, ctx }: { ids: DashboardWidget[]; d: DashboardData; ctx: WidgetContext }) {
  const blocks = toBlocks(ids, d);
  const full = fullWidth(blocks);
  return (
    <div className="grid min-w-0 grid-cols-1 gap-6 md:grid-cols-2">
      {blocks.map((b) =>
        b.kind === "widget" ? (
          <Widget key={b.id} id={b.id} full={full.has(b.id)} empty={isWidgetEmpty(b.id, d)} action={widgetAction(b.id, d)}>
            {/* An empty widget spanning the whole row has no neighbour to balance, so it uses the compact empty state. */}
            <CompactContext.Provider value={full.has(b.id) && isWidgetEmpty(b.id, d)}>{RENDER[b.id](d, ctx)}</CompactContext.Provider>
          </Widget>
        ) : (
          <EmptyPanel key={blockKey(b)} ids={b.ids} d={d} ctx={ctx} />
        ),
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------- customize

function CustomizeDialog({ prefs }: { prefs: DashboardData["prefs"] }) {
  const [open, setOpen] = useState(false);
  const [order, setOrder] = useState<DashboardWidget[]>([]);
  const [hidden, setHidden] = useState<Set<DashboardWidget>>(new Set());
  const save = useApiMutation((body: { hidden: string[]; order: string[] }) => put("/me/dashboard-prefs", body), {
    success: "Dashboard layout saved",
    invalidate: [qk.dashboard],
    onSuccess: () => setOpen(false),
  });

  const onOpenChange = (o: boolean) => {
    if (o) {
      const l = resolveLayout(prefs);
      setOrder(l.order);
      setHidden(new Set(l.hidden));
    }
    setOpen(o);
  };

  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= order.length) return;
    const next = [...order];
    [next[i], next[j]] = [next[j], next[i]];
    setOrder(next);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      trigger={<Button variant="secondary" icon={<Settings2 className="h-4 w-4" />}>Customize</Button>}
      title="Customize dashboard"
      description="Show, hide and reorder widgets. Your layout is saved to your account."
      footer={
        <>
          <Button
            variant="ghost"
            className="mr-auto"
            onClick={() => {
              setOrder([...DASHBOARD_WIDGETS]);
              setHidden(new Set());
            }}
          >
            Reset to default
          </Button>
          <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
          <Button loading={save.isPending} onClick={() => save.mutate({ hidden: [...hidden], order })}>Save layout</Button>
        </>
      }
    >
      <p className="mb-3 text-xs leading-relaxed text-muted">
        On phones and tablets, widgets appear in exactly this order. On wide screens, widgets tagged{" "}
        <span className="font-medium text-fg">Side rail</span> stack beside the main column, keeping the same relative order.
      </p>
      <ol className="divide-y divide-border overflow-hidden rounded-[var(--radius-md)] border border-border bg-bg-elevated/40" aria-label="Dashboard widgets">
        {order.map((id, i) => {
          const meta = WIDGET_META[id];
          const visible = !hidden.has(id);
          return (
            <li key={id} className="flex items-center gap-3 px-3 py-2.5">
              <MiniSwitch
                checked={visible}
                label={`Show ${meta.label}`}
                onChange={(v) => {
                  const next = new Set(hidden);
                  if (v) next.delete(id);
                  else next.add(id);
                  setHidden(next);
                }}
              />
              <div className="min-w-0 flex-1">
                <p className={cn("flex items-center gap-2 text-sm font-medium", visible ? "text-fg" : "text-subtle")}>
                  <span className="truncate">{meta.label}</span>
                  <span className="shrink-0 rounded border border-border px-1 font-mono text-[9.5px] uppercase tracking-[0.12em] text-subtle">
                    {meta.zone === "side" ? "Side rail" : "Main"}
                  </span>
                </p>
                <p className="truncate text-xs text-subtle">{meta.description}</p>
              </div>
              <div className="flex shrink-0 gap-1">
                <Button variant="ghost" size="icon" className="h-8 w-8" aria-label={`Move ${meta.label} up`} disabled={i === 0} onClick={() => move(i, -1)}>
                  <ArrowUp className="h-4 w-4" />
                </Button>
                <Button variant="ghost" size="icon" className="h-8 w-8" aria-label={`Move ${meta.label} down`} disabled={i === order.length - 1} onClick={() => move(i, 1)}>
                  <ArrowDown className="h-4 w-4" />
                </Button>
              </div>
            </li>
          );
        })}
      </ol>
    </Dialog>
  );
}

// ----------------------------------------------------------------------------- page

function Nudges({ me }: { me: Me }) {
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  if (me.onboarding_completed && me.email_verified) return null;
  return (
    <div className="mb-6 space-y-3">
      {!me.onboarding_completed ? (
        <InlineNotice
          tone="info"
          title="Finish setting up your profile"
          action={<LinkButton href="/onboarding?next=/dashboard" size="sm">Continue setup</LinkButton>}
        >
          Tell us your university and interests so we can suggest relevant competitions — it takes about a minute.
        </InlineNotice>
      ) : null}
      {!me.email_verified ? (
        <InlineNotice
          tone="warning"
          title="Verify your email address"
          action={
            <Button
              size="sm"
              variant="secondary"
              loading={busy}
              disabled={sent}
              onClick={async () => {
                setBusy(true);
                try {
                  await post("/auth/resend-verification", { email: me.email });
                  setSent(true);
                  toast.success("Verification link sent");
                } catch (e) {
                  toast.error(errorMessage(e));
                } finally {
                  setBusy(false);
                }
              }}
            >
              {sent ? "Link sent" : "Resend link"}
            </Button>
          }
        >
          We sent a link to <span className="font-medium">{me.email}</span>. Some actions, like submitting to competitions, need a verified email.
        </InlineNotice>
      ) : null}
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div role="status" aria-label="Loading dashboard">
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-xl)] border border-border bg-border sm:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="bg-surface px-5 py-4">
            <Skeleton className="h-2.5 w-20" />
            <Skeleton className="mt-4 h-7 w-12" />
            <Skeleton className="mt-3 h-2.5 w-28" />
          </div>
        ))}
      </div>
      <div className="mt-6 grid gap-6 lg:grid-cols-12">
        <div className="grid gap-6 md:grid-cols-2 lg:col-span-8">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className={cn("rounded-[var(--radius-lg)] border border-border bg-surface p-5", (i === 0 || i === 3) && "md:col-span-2")}>
              <Skeleton className="h-4 w-40" />
              <Skeleton className="mt-2 h-3 w-64 max-w-full" />
              <Skeleton className={cn("mt-6 w-full", i === 0 ? "h-28" : "h-16")} />
            </div>
          ))}
        </div>
        <div className="hidden rounded-[var(--radius-lg)] border border-border bg-surface p-5 lg:col-span-4 lg:block">
          <Skeleton className="h-3 w-20" />
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="mt-4 flex items-center gap-3">
              <Skeleton className="h-8 w-8 shrink-0 rounded-[var(--radius-md)]" />
              <Skeleton className="h-3 flex-1" />
            </div>
          ))}
          <Skeleton className="mt-8 h-24 w-full" />
        </div>
      </div>
    </div>
  );
}

function HeaderSkeleton() {
  return (
    <div className="pb-8 pt-10 sm:pt-12" aria-hidden>
      <Skeleton className="h-3 w-24" />
      <Skeleton className="mt-4 h-9 w-72 max-w-full" />
      <Skeleton className="mt-4 h-4 w-96 max-w-full" />
    </div>
  );
}

export default function DashboardPage() {
  const me = useRequireAuth();
  const dash = useQuery({
    queryKey: qk.dashboard,
    queryFn: () => get<DashboardData>("/me/dashboard"),
    enabled: Boolean(me.data),
  });
  // The viewer's own public profile (same key and endpoint as /u/[handle]) — used for real achievement counts and
  // recent milestones. It never blocks the dashboard: tiles show a dash until it resolves.
  const handle = me.data?.handle.toLowerCase() ?? "";
  const profile = useQuery({
    queryKey: qk.profile(handle),
    queryFn: () => get<PublicProfile>(`/users/${encodeURIComponent(handle)}`),
    enabled: Boolean(handle),
  });
  const layout = useMemo(() => resolveLayout(dash.data?.prefs), [dash.data?.prefs]);
  const greeting = useGreeting();
  const isLg = useIsLg();

  if (me.isPending || !me.data) {
    return (
      <Container className="pb-16">
        <HeaderSkeleton />
        <DashboardSkeleton />
      </Container>
    );
  }
  const d = dash.data;
  const visible = layout.order.filter((id) => !layout.hidden.includes(id));
  const visibleMain = visible.filter((id) => WIDGET_META[id].zone === "main");
  const visibleSide = visible.filter((id) => WIDGET_META[id].zone === "side");
  // If every main-column widget is hidden, the side widgets move into the main column instead of leaving it empty.
  const mainIds = visibleMain.length ? visibleMain : visibleSide;
  const sideIds = visibleMain.length ? visibleSide : [];
  const ctx: WidgetContext = { profile: profile.data, profileLoading: profile.isPending };
  const firstName = d ? d.greeting_name : me.data.display_name.split(" ")[0];
  const verifiedUni = profile.data?.verified.university;
  const unread = me.data.unread_notifications;

  return (
    <Container className="pb-16">
      <PageHeader
        eyebrow="Dashboard"
        icon={<LayoutDashboard />}
        title={
          <>
            {greeting}, <span className="text-gradient">{firstName}</span>
          </>
        }
        description="Your competitions, deadlines, learning and credentials in one place."
        meta={
          <>
            <Link href={`/u/${me.data.handle}`} className="inline-flex items-center gap-1.5 font-mono text-muted transition-colors hover:text-accent-strong">
              <UserRound className="h-3.5 w-3.5" aria-hidden />@{me.data.handle}
            </Link>
            {verifiedUni ? (
              <span className="inline-flex min-w-0 items-center gap-1.5">
                <BadgeCheck className="h-3.5 w-3.5 shrink-0 text-success" aria-hidden />
                <span className="truncate">{verifiedUni.name}</span>
                <span className="sr-only">(verified member)</span>
              </span>
            ) : null}
            {unread > 0 ? (
              <Link href="/notifications" className="inline-flex items-center gap-1.5 transition-colors hover:text-accent-strong">
                <Bell className="h-3.5 w-3.5" aria-hidden />
                <span className="tabular">{formatNumber(unread)}</span> unread
              </Link>
            ) : null}
          </>
        }
        actions={
          <>
            <LinkButton href={`/u/${me.data.handle}`} variant="ghost">View profile</LinkButton>
            {d ? <CustomizeDialog prefs={d.prefs} /> : null}
          </>
        }
      />
      <Nudges me={me.data} />
      {dash.isPending ? (
        <DashboardSkeleton />
      ) : dash.isError ? (
        <ErrorState error={dash.error} onRetry={() => dash.refetch()} />
      ) : d ? (
        <>
          <MetricsStrip d={d} profile={profile.data} />
          {!visible.length ? (
            <div className="mt-6">
              <EmptyState
                icon={<Settings2 className="h-5 w-5" />}
                title="All widgets are hidden"
                description="Use Customize to bring back the widgets you want to see."
                action={<CustomizeDialog prefs={d.prefs} />}
              />
            </div>
          ) : isLg ? (
            <div className="mt-6 grid grid-cols-12 items-start gap-6">
              <div className="col-span-8 min-w-0">
                <WidgetGrid ids={mainIds} d={d} ctx={ctx} />
              </div>
              <aside aria-label="Up next and deadlines" className="sticky top-24 col-span-4 min-w-0">
                <div className="max-h-[calc(100dvh-7.5rem)] overflow-x-hidden overflow-y-auto rounded-[var(--radius-lg)] border border-border bg-surface surface-sheen shadow-card [scrollbar-width:thin]">
                  <div className={cn("relative overflow-hidden px-5 py-5", sideIds.length && "border-b border-border")}>
                    <div aria-hidden className="pointer-events-none absolute -right-16 -top-20 h-48 w-48 rounded-full opacity-70 blur-2xl" style={{ background: "radial-gradient(closest-side, var(--ambient-a), transparent)" }} />
                    <UpNext d={d} className="relative" />
                  </div>
                  <CompactContext.Provider value>
                    <div className="divide-y divide-border">
                      {sideIds.map((id) => (
                        <CompactSection key={id} id={id} action={widgetAction(id, d)}>
                          {RENDER[id](d, ctx)}
                        </CompactSection>
                      ))}
                    </div>
                  </CompactContext.Provider>
                </div>
              </aside>
            </div>
          ) : (
            <>
              <Card variant="glass" className="mt-6 p-5">
                <UpNext d={d} />
              </Card>
              <div className="mt-6">
                <WidgetGrid ids={visible} d={d} ctx={ctx} />
              </div>
            </>
          )}
        </>
      ) : null}
    </Container>
  );
}
