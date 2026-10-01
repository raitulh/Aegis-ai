"use client";

import { useQuery } from "@tanstack/react-query";

import { CompetitionShowcase } from "@/components/landing/competition-showcase";
import { DatasetShowcase } from "@/components/landing/dataset-showcase";
import { FinalCta } from "@/components/landing/final-cta";
import { Hero } from "@/components/landing/hero";
import { LearningSection } from "@/components/landing/learning-section";
import { Metrics } from "@/components/landing/metrics";
import { OpenSourceSection } from "@/components/landing/open-source-section";
import { OrganizationSection } from "@/components/landing/organization-section";
import { PlatformLoop } from "@/components/landing/platform-loop";
import { ProjectShowcase } from "@/components/landing/project-showcase";
import { ScoringSection } from "@/components/landing/scoring-section";
import type { Landing } from "@/components/landing/types";
import { VerificationSection } from "@/components/landing/verification-section";
import { Container } from "@/components/ui/page";
import { InlineNotice } from "@/components/ui/states";
import { get } from "@/lib/api";
import { useMe } from "@/lib/hooks";

/**
 * Landing narrative: hero → live metrics → the loop → compete → data → build → learn → contribute →
 * verify → connect → scoring → call to action. Every number and list comes from the API; sections below the
 * fold fetch only when they approach the viewport.
 */
export default function HomePage() {
  const me = useMe().data;
  const landing = useQuery({ queryKey: ["landing"], queryFn: () => get<Landing>("/meta/landing") });
  const demo = Boolean(landing.data?.includes_demo_data);

  return (
    <>
      <Hero signedIn={Boolean(me)} demo={demo} />
      <Metrics stats={landing.data?.stats} demo={demo} loading={landing.isPending} />
      {landing.isError ? (
        <Container className="mt-6">
          <InlineNotice tone="warning" title="Live platform data is temporarily unavailable" action={<button type="button" className="text-sm font-medium underline" onClick={() => landing.refetch()}>Retry</button>}>
            Featured competitions and projects will appear once the connection recovers.
          </InlineNotice>
        </Container>
      ) : null}
      <PlatformLoop handle={me?.handle} />
      <CompetitionShowcase items={landing.data?.featured_competitions} loading={landing.isPending} error={landing.isError} />
      <DatasetShowcase />
      <ProjectShowcase items={landing.data?.featured_projects} loading={landing.isPending} error={landing.isError} />
      <LearningSection />
      <OpenSourceSection />
      <VerificationSection />
      <OrganizationSection universities={landing.data?.stats.universities} />
      <ScoringSection competitions={landing.data?.featured_competitions} />
      <FinalCta signedIn={Boolean(me)} />
    </>
  );
}
