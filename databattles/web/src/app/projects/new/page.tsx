"use client";

import { useQuery } from "@tanstack/react-query";
import { FolderPlus } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { FlowSteps } from "@/components/catalog/dataset-bits";
import { EMPTY_PROJECT, ProjectForm, projectPayload, type ProjectFormValues } from "@/components/catalog/project-form";
import type { OrgMembership, ProjectDetail } from "@/components/catalog/types";
import { Container, PageHeader } from "@/components/ui/page";
import { InlineNotice, Skeleton, SkeletonRows } from "@/components/ui/states";
import { ApiError, get, post } from "@/lib/api";
import { useApiMutation, useRequireAuth } from "@/lib/hooks";

const STEPS = [
  { title: "Describe what you built", text: "Summary, write-up, stack, links and the competition or datasets behind it." },
  { title: "Add screenshots & people", text: "Upload a gallery and invite maintainers and contributors from Edit." },
  { title: "Share it", text: "Public projects appear in the showcase and on your profile." },
];

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
    return (
      <Container size="lg">
        <div className="pb-8 pt-12">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="mt-4 h-9 w-56" />
          <Skeleton className="mt-4 h-4 w-full max-w-xl" />
        </div>
        <SkeletonRows rows={8} />
      </Container>
    );
  }

  return (
    <Container size="lg" className="pb-16">
      <PageHeader
        eyebrow="Projects"
        icon={<FolderPlus />}
        title="New project"
        description="Showcase what you built. After creating it you can upload screenshots and invite maintainers and contributors."
      />
      {!me.data.email_verified ? (
        <div className="mb-6">
          <InlineNotice tone="warning" title="Verify your email first">Creating projects requires a verified email address.</InlineNotice>
        </div>
      ) : null}
      <FlowSteps label="Project steps" steps={STEPS} current={0} />
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
