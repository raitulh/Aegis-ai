"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { EMPTY_PROJECT, ProjectForm, projectPayload, type ProjectFormValues } from "@/components/catalog/project-form";
import type { OrgMembership, ProjectDetail } from "@/components/catalog/types";
import { Container, PageHeader } from "@/components/ui/page";
import { InlineNotice, SkeletonRows } from "@/components/ui/states";
import { ApiError, get, post } from "@/lib/api";
import { useApiMutation, useRequireAuth } from "@/lib/hooks";

export default function NewProjectPage() {
  const me = useRequireAuth();
  const router = useRouter();
  const [error, setError] = useState<ApiError | null>(null);
  const memberships = useQuery({
    queryKey: ["orgs", "me", "memberships"],
    queryFn: () => get<OrgMembership[]>("/orgs/me/memberships"),
    enabled: Boolean(me.data),
  });
  const create = useApiMutation((v: ProjectFormValues) => post<ProjectDetail>("/projects", projectPayload(v)), {
    success: "Project created — add screenshots and collaborators from Edit.",
    invalidate: [["projects", "list"]],
    onSuccess: (p) => router.push(`/projects/${p.slug}`),
    onError: (e) => setError(e),
  });

  if (me.isPending || !me.data) {
    return <Container size="lg"><div className="py-8"><SkeletonRows rows={8} /></div></Container>;
  }

  return (
    <Container size="lg" className="pb-16">
      <PageHeader
        eyebrow="Projects"
        title="New project"
        description="Showcase what you built. After creating it you can upload screenshots and invite maintainers and contributors."
      />
      {!me.data.email_verified ? (
        <div className="mb-6">
          <InlineNotice tone="warning" title="Verify your email first">Creating projects requires a verified email address.</InlineNotice>
        </div>
      ) : null}
      <ProjectForm
        mode="create"
        initial={EMPTY_PROJECT}
        memberships={memberships.data}
        submitting={create.isPending}
        error={error}
        submitLabel="Create project"
        onSubmit={(v) => {
          setError(null);
          create.mutate(v);
        }}
        onCancel={() => router.push("/projects")}
      />
    </Container>
  );
}
