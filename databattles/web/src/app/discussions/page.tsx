"use client";

import { Hash, MessagesSquare, Plus } from "lucide-react";
import { Suspense } from "react";

import { GuidelinesNote } from "@/components/discussions/guidelines-note";
import { CategoryNav, ThreadListView, useCategories } from "@/components/discussions/thread-list";
import { LinkButton } from "@/components/ui/button";
import { Container, PageHeader } from "@/components/ui/page";
import { compactNumber } from "@/lib/format";
import { useMe } from "@/lib/hooks";

function newHref(signedIn: boolean, path = "/discussions/new") {
  return signedIn ? path : `/login?next=${encodeURIComponent(path)}`;
}

function DiscussionsHome() {
  const me = useMe();
  const cats = useCategories();
  const signedIn = Boolean(me.data);
  const total = cats.data?.reduce((n, c) => n + c.thread_count, 0);
  const action = (
    <LinkButton href={newHref(signedIn)} icon={<Plus className="h-4 w-4" aria-hidden />}>
      New discussion
    </LinkButton>
  );
  return (
    <Container className="pb-20">
      <PageHeader
        eyebrow="Community"
        icon={<MessagesSquare />}
        title="Discussions"
        description="Ask questions, share approaches and help each other. Be kind, credit others' work, and never post competition test labels or private data."
        actions={action}
        meta={
          cats.data ? (
            <>
              <span className="inline-flex items-center gap-1.5">
                <MessagesSquare className="h-3.5 w-3.5" aria-hidden />
                <span className="tabular font-medium text-muted">{compactNumber(total)}</span> {total === 1 ? "thread" : "threads"}
              </span>
              <span className="inline-flex items-center gap-1.5">
                <Hash className="h-3.5 w-3.5" aria-hidden />
                <span className="tabular font-medium text-muted">{cats.data.length}</span> {cats.data.length === 1 ? "category" : "categories"}
              </span>
            </>
          ) : null
        }
      />
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[17rem_minmax(0,1fr)] lg:gap-10">
        <aside className="min-w-0 lg:sticky lg:top-24 lg:space-y-6 lg:self-start">
          <CategoryNav />
          <GuidelinesNote className="hidden lg:block" />
        </aside>
        <section aria-label="All discussions" className="min-w-0">
          <ThreadListView signedIn={signedIn} emptyAction={action} />
        </section>
      </div>
    </Container>
  );
}

export default function DiscussionsPage() {
  return (
    <Suspense>
      <DiscussionsHome />
    </Suspense>
  );
}
