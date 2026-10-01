"use client";

import { ChevronRight, Plus } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Suspense } from "react";

import { CategoryNav, ThreadListView, useCategories } from "@/components/discussions/thread-list";
import { LinkButton } from "@/components/ui/button";
import { Container, PageHeader } from "@/components/ui/page";
import { ErrorState, InlineNotice, NotFoundState, Skeleton } from "@/components/ui/states";
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
    <Container className="pb-16">
      <nav aria-label="Breadcrumb" className="flex items-center gap-1 pt-6 text-sm text-muted">
        <Link href="/discussions" className="hover:text-fg">Discussions</Link>
        <ChevronRight className="h-3.5 w-3.5" aria-hidden />
        <span className="text-fg" aria-current="page">{cat?.name ?? "…"}</span>
      </nav>
      {cat ? (
        <PageHeader className="pt-4" title={cat.name} description={cat.description ?? undefined} actions={action} />
      ) : (
        <div className="py-8" role="status" aria-label="Loading category">
          <Skeleton className="h-8 w-64" />
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
      <div className="grid gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
        <aside>
          <CategoryNav current={category} />
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
