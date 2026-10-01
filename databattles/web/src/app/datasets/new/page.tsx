"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { DatasetMetaForm, EMPTY_DATASET, datasetPayload, type DatasetFormValues } from "@/components/catalog/dataset-form";
import type { OrgMembership } from "@/components/catalog/types";
import { Container, PageHeader } from "@/components/ui/page";
import { InlineNotice, SkeletonRows } from "@/components/ui/states";
import { ApiError, get, post } from "@/lib/api";
import { useApiMutation, useRequireAuth } from "@/lib/hooks";
import type { DatasetDetail } from "@/lib/types";

const MANAGER_ROLES = ["owner", "admin", "manager"];

export default function NewDatasetPage() {
  const me = useRequireAuth();
  const router = useRouter();
  const [error, setError] = useState<ApiError | null>(null);

  const licenses = useQuery({ queryKey: ["datasets", "licenses"], queryFn: () => get<Record<string, string>>("/datasets/licenses"), staleTime: 10 * 60_000 });
  const memberships = useQuery({
    queryKey: ["orgs", "me", "memberships"],
    queryFn: () => get<OrgMembership[]>("/orgs/me/memberships"),
    enabled: Boolean(me.data),
  });
  const managed = useMemo(
    () => (memberships.data ?? []).filter((m) => m.status === "active" && MANAGER_ROLES.includes(m.role)),
    [memberships.data],
  );

  const create = useApiMutation((v: DatasetFormValues) => post<DatasetDetail>("/datasets", datasetPayload(v, "create")), {
    success: "Dataset created — now add files to version 1.",
    invalidate: [["datasets", "list"]],
    onSuccess: (ds) => router.push(`/datasets/${ds.slug}/manage`),
    onError: (e) => setError(e),
  });

  if (me.isPending || !me.data) {
    return (
      <Container size="lg">
        <div className="py-8"><SkeletonRows rows={8} /></div>
      </Container>
    );
  }

  return (
    <Container size="lg" className="pb-16">
      <PageHeader
        eyebrow="Datasets"
        title="Publish a dataset"
        description="Start with the metadata. You'll upload files to a draft version next and publish it when it's ready — published versions are immutable."
      />
      {!me.data.email_verified ? (
        <div className="mb-6">
          <InlineNotice tone="warning" title="Verify your email first">
            Publishing datasets requires a verified email address. Check your inbox for the verification link.
          </InlineNotice>
        </div>
      ) : null}
      <DatasetMetaForm
        mode="create"
        initial={EMPTY_DATASET}
        licenses={licenses.data}
        orgs={managed}
        submitting={create.isPending}
        error={error}
        submitLabel="Create dataset"
        onSubmit={(v) => {
          setError(null);
          create.mutate(v);
        }}
        onCancel={() => router.push("/datasets")}
      />
    </Container>
  );
}
