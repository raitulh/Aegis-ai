"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Users } from "lucide-react";
import { useParams } from "next/navigation";
import { useState } from "react";

import { DownloadLink, ManageHeading, useCompetitionDetail } from "@/components/organizer/shared";
import { UserLink } from "@/components/domain/cards";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Select } from "@/components/ui/form";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, QueryState, SkeletonRows } from "@/components/ui/states";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { get } from "@/lib/api";
import { formatDateTime, formatNumber, relativeTime } from "@/lib/format";
import type { Page, Schemas } from "@/lib/types";

type Participant = Schemas["ParticipantOut"];

export default function ManageParticipantsPage() {
  const { slug } = useParams<{ slug: string }>();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const detail = useCompetitionDetail(slug);
  const query = useQuery({
    queryKey: ["competitions", slug, "participants", { page, pageSize }],
    queryFn: () => get<Page<Participant>>(`/competitions/${slug}/participants`, { page, page_size: pageSize }),
    placeholderData: keepPreviousData,
  });

  return (
    <div>
      <ManageHeading
        title="Participants"
        description={
          detail.data
            ? `${formatNumber(detail.data.participant_count)} participants in ${formatNumber(detail.data.team_count)} teams. Contact details are never exported — use announcements to reach everyone.`
            : "Everyone who joined, their team and activity."
        }
        actions={<DownloadLink path={`/competitions/${slug}/participants.csv`}>Export CSV</DownloadLink>}
      />
      <QueryState
        query={query}
        loading={<SkeletonRows rows={8} />}
        isEmpty={(d) => d.total === 0}
        empty={
          <EmptyState
            icon={<Users className="h-5 w-5" />}
            title="No participants yet"
            description="Once people join they appear here with their team and submission count. Share the competition link or post an announcement to spread the word."
          />
        }
      >
        {(d) => (
          <>
            <div className="mb-3 flex items-center justify-between gap-3 text-sm text-muted">
              <span aria-live="polite">
                {formatNumber(d.total)} participants{query.isFetching ? " · updating…" : ""}
              </span>
              <label className="flex items-center gap-2">
                <span>Per page</span>
                <Select className="h-8 w-20" value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}>
                  {[25, 50, 100].map((n) => <option key={n} value={n}>{n}</option>)}
                </Select>
              </label>
            </div>
            <Table>
              <caption className="sr-only">Participants</caption>
              <THead>
                <tr>
                  <TH>Participant</TH>
                  <TH>Team</TH>
                  <TH>Joined</TH>
                  <TH>Status</TH>
                  <TH className="text-right">Submissions</TH>
                </tr>
              </THead>
              <TBody>
                {d.items.map((p) => (
                  <TR key={p.user.id}>
                    <TD>
                      <div className="flex flex-col">
                        <UserLink user={p.user} />
                        <span className="pl-8 text-xs text-subtle">@{p.user.handle}</span>
                      </div>
                    </TD>
                    <TD>{p.team_name ?? <span className="text-subtle">No team</span>}</TD>
                    <TD className="whitespace-nowrap" title={formatDateTime(p.joined_at)}>
                      {relativeTime(p.joined_at)}
                    </TD>
                    <TD>{p.status === "active" ? <Badge tone="success">Active</Badge> : <StatusBadge status={p.status} />}</TD>
                    <TD className="text-right tabular-nums">{formatNumber(p.submission_count)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <Pagination page={d.page} pageSize={d.page_size} total={d.total} onPage={setPage} />
          </>
        )}
      </QueryState>
    </div>
  );
}
