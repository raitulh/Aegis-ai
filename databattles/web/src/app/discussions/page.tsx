"use client";

import { Plus } from "lucide-react";
import { Suspense } from "react";

import { CategoryNav, ThreadListView } from "@/components/discussions/thread-list";
import { LinkButton } from "@/components/ui/button";
import { Container, PageHeader } from "@/components/ui/page";
import { useMe } from "@/lib/hooks";

function newHref(signedIn: boolean, path = "/discussions/new") {
  return signedIn ? path : `/login?next=${encodeURIComponent(path)}`;
}

function DiscussionsHome() {
  const me = useMe();
  const signedIn = Boolean(me.data);
  const action = (
    <LinkButton href={newHref(signedIn)} icon={<Plus className="h-4 w-4" aria-hidden />}>
      New discussion
    </LinkButton>
  );
  return (
    <Container className="pb-16">
      <PageHeader
        eyebrow="Community"
        title="Discussions"
        description="Ask questions, share approaches and help each other. Be kind, credit others' work, and never post competition test labels or private data."
        actions={action}
      />
      <div className="grid gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
        <aside>
          <CategoryNav />
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
