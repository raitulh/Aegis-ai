import type { Metadata } from "next";
import Link from "next/link";

import { LegalDoc, type LegalSection } from "../terms/legal-doc";

export const metadata: Metadata = {
  title: "Community Guidelines",
  description: "How we compete, learn and collaborate here — and what happens when something is reported.",
};

const sections: LegalSection[] = [
  {
    id: "respect",
    title: "Be respectful",
    body: (
      <ul>
        <li>Critique ideas and code, not people. Assume good faith, especially with beginners.</li>
        <li>No harassment, hate speech, threats, sexual content, or targeting people based on who they are.</li>
        <li>Don’t post other people’s personal information (doxxing), even if it is findable elsewhere.</li>
      </ul>
    ),
  },
  {
    id: "fair-play",
    title: "Compete fairly",
    body: (
      <ul>
        <li>One account per person. Don’t use extra accounts to get more submissions or to probe the leaderboard.</li>
        <li>Share code and insights only as the competition rules allow — private sharing outside your team is usually not allowed.</li>
        <li>Don’t hand-label test data, exploit leaks, or try to reverse-engineer the private leaderboard.</li>
        <li>Respect dataset licences and any external-data rules set by the host.</li>
        <li>Hosts may disqualify rule-breaking submissions; finalized results record corrections with a reason.</li>
      </ul>
    ),
  },
  {
    id: "honesty",
    title: "Be honest about your work",
    body: (
      <ul>
        <li>Credit collaborators, papers, notebooks and open-source code you build on.</li>
        <li>Only claim affiliations, skills and projects that are yours. Verified information is marked as such; self-declared information is labelled too.</li>
        <li>Follow your institution’s academic-integrity rules for coursework and assessed competitions.</li>
      </ul>
    ),
  },
  {
    id: "content",
    title: "Share responsibly",
    body: (
      <ul>
        <li>Only upload data you have the right to share, with an accurate licence. Remove or anonymize personal data.</li>
        <li>No malware, credential phishing, spam, or undisclosed advertising.</li>
        <li>Keep discussions on topic and use the right category; search before posting duplicates.</li>
      </ul>
    ),
  },
  {
    id: "reporting",
    title: "Reporting",
    body: (
      <p>
        Use the report option on threads, replies, projects, datasets, competitions and profiles. Choose the closest reason and add context.
        Reporters are never revealed to the person being reported. Please don’t report content just because you disagree with it.
      </p>
    ),
  },
  {
    id: "moderation",
    title: "How moderation works",
    body: (
      <>
        <p>Moderators review reports grouped by item. Every decision requires a written note and is recorded in an audit log. Possible outcomes:</p>
        <ul>
          <li><strong>Dismiss</strong> — no violation found.</li>
          <li><strong>Warn</strong> — the content stays up and the owner is notified.</li>
          <li><strong>Hide or take down</strong> — the content is removed from public view; it can be restored if the decision is reversed.</li>
          <li><strong>Delete</strong> — for posts and replies that clearly break these guidelines.</li>
          <li><strong>Suspend</strong> — the account can’t sign in and all sessions end. Platform admins may ban accounts for severe or repeated violations.</li>
        </ul>
        <p>Owners are notified when action is taken on their content.</p>
      </>
    ),
  },
  {
    id: "appeals",
    title: "Appeals",
    body: (
      <p>
        If you think a decision was wrong, contact the operator of this deployment with a link to the content and why you believe it should be
        restored. A different moderator should review appeals where possible. <em>Operator: publish your appeals contact and response times.</em>
      </p>
    ),
  },
  {
    id: "more",
    title: "Related",
    body: (
      <p>
        These guidelines sit alongside the <Link href="/terms">Terms of Service</Link> and the <Link href="/privacy">Privacy Policy</Link>.
        Individual competitions may add their own rules.
      </p>
    ),
  },
];

export default function GuidelinesPage() {
  return (
    <LegalDoc
      current="guidelines"
      eyebrow="Community"
      title="Community Guidelines"
      description="How we compete, learn and collaborate here — and what happens when something is reported."
      sections={sections}
    />
  );
}
