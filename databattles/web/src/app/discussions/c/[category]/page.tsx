"use client";

import { ChevronRight, Lock, MessagesSquare, Plus } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Suspense } from "react";

import { GuidelinesNote } from "@/components/discussions/guidelines-note";
import { CategoryIcon, CategoryNav, ThreadListView, useCategories } from "@/components/discussions/thread-list";
import { LinkButton } from "@/components/ui/button";
import { Container, PageHeader } from "@/components/ui/page";
import { ErrorState, InlineNotice, NotFoundState, Skeleton } from "@/components/ui/states";
import { compactNumber } from "@/lib/format";
import { hasRole, useMe } from "@/lib/hooks";

function CategoryView() {
  const { category } = useParams<{ category: string }>();
  const me = useMe();
  const cats = useCategories();
  const signedIn = Boolean(me.data);
  const isStaff = hasRole(me.data, "moderator");

  if (cats.isError) {
    return (
      <Container className="py-12">
        <ErrorState error={cats.error} onRetry={() => cats.refetch()} />
      </Container>
    );
  }
  const cat = cats.data?.find((c) => c.slug === category);
  if (cats.isSuccess && !cat) {
    return (
      <Container className="py-12">
        <NotFoundState what="category" />
      </Container>
    );
  }

  const newPath = `/discussions/new?category=${encodeURIComponent(category)}`;
  const canStart = cat ? !cat.staff_only_posting || isStaff : false;
  const action = canStart ? (
    <LinkButton href={signedIn ? newPath : `/login?next=${encodeURIComponent(newPath)}`} icon={<Plus className="h-4 w-4" aria-hidden />}>
      New discussion
    </LinkButton>
  ) : null;

  return (
    <Container className="pb-20">
      <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1 pt-6 text-sm text-subtle">
        <Link href="/discussions" className="shrink-0 rounded-sm transition-colors hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]">Discussions</Link>
        <ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span className="truncate text-muted" aria-current="page">{cat?.name ?? "…"}</span>
      </nav>
      {cat ? (
        <PageHeader
          className="pt-4"
          eyebrow="Category"
          icon={<CategoryIcon slug={cat.slug} />}
          title={cat.name}
          description={cat.description ?? undefined}
          actions={action}
          meta={
            <>
              <span className="inline-flex items-center gap-1.5">
                <MessagesSquare className="h-3.5 w-3.5" aria-hidden />
                <span className="tabular font-medium text-muted">{compactNumber(cat.thread_count)}</span> {cat.thread_count === 1 ? "thread" : "threads"}
              </span>
              {cat.staff_only_posting ? (
                <span className="inline-flex items-center gap-1.5">
                  <Lock className="h-3.5 w-3.5" aria-hidden /> Staff-only posting
                </span>
              ) : null}
            </>
          }
        />
      ) : (
        <div className="pb-8 pt-10" role="status" aria-label="Loading category">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="mt-4 h-8 w-64 max-w-full" />
          <Skeleton className="mt-3 h-4 w-96 max-w-full" />
        </div>
      )}
      {cat?.staff_only_posting ? (
        <div className="mb-6">
          <InlineNotice tone="info" title="Staff-only posting">
            Only platform staff can start new discussions in this category. Everyone can read and reply to open threads.
          </InlineNotice>
        </div>
      ) : null}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[17rem_minmax(0,1fr)] lg:gap-10">
        <aside className="min-w-0 lg:sticky lg:top-24 lg:space-y-6 lg:self-start">
          <CategoryNav current={category} />
          <GuidelinesNote className="hidden lg:block" />
        </aside>
        <section aria-label={cat ? `${cat.name} discussions` : "Discussions"} className="min-w-0">
          <ThreadListView category={category} signedIn={signedIn} emptyAction={action ?? undefined} />
        </section>
      </div>
    </Container>
  );
}

export default function CategoryPage() {
  return (
    <Suspense>
      <CategoryView />
    </Suspense>
  );
}
