"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ArrowUpRight, Database, Download, FileStack } from "lucide-react";
import Link from "next/link";

import { Reveal } from "@/components/motion/reveal";
import { DemoBadge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/button";
import { Container } from "@/components/ui/page";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { get } from "@/lib/api";
import { compactNumber, formatBytes, relativeTime } from "@/lib/format";
import { useInView } from "@/lib/motion";
import { qk } from "@/lib/query";
import type { DatasetCard, Page } from "@/lib/types";
import { SectionHeading } from "./section-heading";

const PARAMS = { sort: "downloads", page: 1, page_size: 5 };

/** Most-downloaded public datasets as a catalogue table — data presented like data. */
export function DatasetShowcase() {
  const [ref, near] = useInView<HTMLElement>({ rootMargin: "400px 0px" });
  const datasets = useQuery({
    queryKey: qk.datasets(PARAMS),
    queryFn: ({ signal }) => get<Page<DatasetCard>>("/datasets", PARAMS, signal),
    enabled: near,
    staleTime: 60_000,
  });
  const items = datasets.data?.items ?? [];

  return (
    <section ref={ref} className="relative py-20 sm:py-28" aria-label="Datasets">
      <Container>
        <SectionHeading
          index="03"
          eyebrow="Data"
          title="Versioned datasets, ready to train on."
          description="Licensed, versioned and checksummed — with previews before you download. Sorted by total downloads."
          action={
            <LinkButton href="/datasets" variant="secondary" icon={<ArrowRight className="h-4 w-4" />}>
              Browse datasets
            </LinkButton>
          }
        />
        <Reveal className="mt-12">
          <div className="overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface shadow-card">
            <div className="flex items-center gap-2 border-b border-border bg-bg-elevated/70 px-4 py-2.5">
              <span className="flex gap-1.5" aria-hidden>
                <span className="h-2.5 w-2.5 rounded-full bg-surface-3" />
                <span className="h-2.5 w-2.5 rounded-full bg-surface-3" />
                <span className="h-2.5 w-2.5 rounded-full bg-surface-3" />
              </span>
              <span className="ml-2 font-mono text-[11px] text-subtle">datasets · sorted by downloads</span>
              {datasets.data ? <span className="tabular ml-auto font-mono text-[11px] text-subtle">{datasets.data.total} public</span> : null}
            </div>
            {datasets.isPending ? (
              <div className="divide-y divide-border" role="status" aria-label="Loading datasets">
                {Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} className="flex items-center gap-4 px-4 py-4">
                    <Skeleton className="h-9 w-9 rounded-lg" />
                    <div className="flex-1">
                      <Skeleton className="h-3.5 w-1/3" />
                      <Skeleton className="mt-2 h-3 w-1/2" />
                    </div>
                    <Skeleton className="hidden h-3 w-24 md:block" />
                  </div>
                ))}
              </div>
            ) : datasets.isError || !items.length ? (
              <div className="p-6">
                <EmptyState
                  icon={<Database />}
                  title={datasets.isError ? "Datasets are unavailable right now" : "No public datasets yet"}
                  description={datasets.isError ? "Try again in a moment, or open the catalogue directly." : "Upload one to share it with the community."}
                  action={<LinkButton href="/datasets" variant="secondary">Open the catalogue</LinkButton>}
                />
              </div>
            ) : (
              <ul className="divide-y divide-border">
                {items.map((d, i) => (
                  <li key={d.id}>
                    <Link
                      href={`/datasets/${d.slug}`}
                      className="group grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 px-4 py-4 transition-colors hover:bg-surface-2/60 sm:px-5 md:grid-cols-[auto_minmax(0,1fr)_8rem_6rem_6rem_7rem]"
                    >
                      <span className="tabular row-span-2 flex h-9 w-9 items-center justify-center rounded-lg border border-border bg-cyan-soft font-mono text-[11px] text-cyan md:row-span-1">
                        {String(i + 1).padStart(2, "0")}
                      </span>
                      <span className="min-w-0">
                        <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                          <span className="min-w-0 max-w-full truncate font-medium text-fg transition-colors group-hover:text-accent-strong">{d.title}</span>
                          {d.is_demo ? <DemoBadge /> : null}
                        </span>
                        <span className="block truncate text-xs text-subtle">{d.subtitle ?? d.license_name}</span>
                      </span>
                      <ArrowUpRight className="h-4 w-4 text-subtle transition-transform duration-300 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-accent-strong md:hidden" aria-hidden />
                      <span className="col-start-2 flex flex-wrap gap-x-3 text-xs text-muted md:contents">
                        <span className="truncate md:text-[12px]" title={d.license_name}>{d.license.toUpperCase()}</span>
                        <span className="tabular inline-flex items-center gap-1"><FileStack className="h-3.5 w-3.5 text-subtle" aria-hidden />{d.file_count} · {formatBytes(d.total_bytes)}</span>
                        <span className="tabular inline-flex items-center gap-1"><Download className="h-3.5 w-3.5 text-subtle" aria-hidden />{compactNumber(d.download_count)}</span>
                        <span className="text-subtle md:text-right">v{d.latest_version ?? "—"} · {relativeTime(d.updated_at)}</span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Reveal>
      </Container>
    </section>
  );
}
