import type { Metadata } from "next";
import Link from "next/link";

import { LegalDoc, type LegalSection } from "./legal-doc";

export const metadata: Metadata = {
  title: "Terms of Service",
  description: "The rules for using this DataBattles deployment: accounts, competitions, content, credentials and organizations.",
};

const sections: LegalSection[] = [
  {
    id: "agreement",
    title: "Agreement",
    body: (
      <>
        <p>
          These terms govern your use of this DataBattles deployment, operated by the organization named by the operator. By creating an
          account or using the service you agree to them, to the <Link href="/privacy">Privacy Policy</Link> and to the{" "}
          <Link href="/guidelines">Community Guidelines</Link>.
        </p>
        <p>If you use the service on behalf of an organization, you confirm you are authorized to accept these terms for it.</p>
      </>
    ),
  },
  {
    id: "accounts",
    title: "Accounts",
    body: (
      <ul>
        <li>Provide accurate information and keep your credentials secure. You are responsible for activity on your account.</li>
        <li>One person, one account. Multiple accounts to gain an advantage in competitions are not allowed.</li>
        <li>Some actions (joining organizations, creating organizations) require a verified email address.</li>
        <li>You can delete your account at any time from your settings. What happens to your data is described in the Privacy Policy.</li>
      </ul>
    ),
  },
  {
    id: "competitions",
    title: "Competitions",
    body: (
      <>
        <p>
          Each competition has its own rules set by its host, including eligibility, team limits, submission limits, deadlines, data licences
          and prizes. Competition rules apply in addition to these terms; if they conflict, the competition rules govern that competition.
        </p>
        <ul>
          <li>Submissions are scored automatically and reproducibly. Hosts may re-score, disqualify submissions that break the rules, and finalize results.</li>
          <li>
            Final rankings use a private leaderboard that is published when the host finalizes results. Finalized results are versioned;
            corrections are recorded with a reason and earlier versions are kept.
          </li>
          <li>Prizes, if any, are offered and fulfilled by the host or sponsor, not by the operator, unless stated otherwise.</li>
          <li>Deadlines are stored in UTC and shown in your local time zone.</li>
        </ul>
      </>
    ),
  },
  {
    id: "content",
    title: "Your content",
    body: (
      <>
        <p>
          You keep ownership of what you upload — submissions, datasets, projects, discussion posts. You grant the operator a licence to host,
          process, display and back up that content as needed to run the service (for example, scoring submissions and showing public projects).
        </p>
        <p>
          Only upload content you have the right to share. Datasets must carry an accurate licence. We may remove content that violates these
          terms, the guidelines or the law, and we act on valid copyright and privacy complaints.
        </p>
      </>
    ),
  },
  {
    id: "credentials",
    title: "Certificates and badges",
    body: (
      <p>
        Certificates and badges attest only to what their public verification page states. They may be revoked if they were issued in error or
        obtained by breaking the rules; revoked credentials remain verifiable and show their revoked status. Organization membership is shown as
        verified only after institutional-email confirmation, an admin invite or admin approval; self-declared affiliations are labelled as such.
      </p>
    ),
  },
  {
    id: "organizations",
    title: "Organizations",
    body: (
      <ul>
        <li>Organization owners and admins are responsible for their organization’s content, competitions, invites and member management.</li>
        <li>Verification is granted by the platform team at its discretion and can be withdrawn.</li>
        <li>Rosters and analytics must only be used to run the organization’s activities, in line with applicable privacy law.</li>
        <li>Paid plans are billed manually by the operator under a separate agreement or invoice; this app does not collect card details.</li>
      </ul>
    ),
  },
  {
    id: "acceptable-use",
    title: "Acceptable use",
    body: (
      <ul>
        <li>Don’t attack, overload or probe the service, the scoring sandbox or other users’ accounts.</li>
        <li>Don’t scrape personal data, share private datasets outside their licence, or attempt to access private leaderboards.</li>
        <li>Follow the <Link href="/guidelines">Community Guidelines</Link>. Moderators may hide content, and we may suspend or ban accounts that break the rules.</li>
      </ul>
    ),
  },
  {
    id: "demo",
    title: "Demo data",
    body: (
      <p>
        Deployments may include synthetic demo content created by a seed script (users, organizations, competitions and results). It is always
        labelled “Demo data”, does not describe real people or institutions, and may be removed at any time.
      </p>
    ),
  },
  {
    id: "disclaimers",
    title: "Disclaimers and liability",
    body: (
      <p>
        The service is provided “as is”. To the extent permitted by law, the operator is not liable for indirect or consequential losses, lost
        prizes or opportunities, or losses caused by content provided by hosts, sponsors or other users. Nothing in these terms limits liability
        that cannot be limited by law. <em>Operator: adapt this section with your counsel.</em>
      </p>
    ),
  },
  {
    id: "changes",
    title: "Changes and contact",
    body: (
      <p>
        We may update these terms; material changes will be announced in the app before they take effect. Questions about these terms should be
        sent to the contact address published by the operator of this deployment.
      </p>
    ),
  },
];

export default function TermsPage() {
  return (
    <LegalDoc
      current="terms"
      eyebrow="Legal"
      title="Terms of Service"
      description="The rules for using this platform: accounts, competitions, your content, credentials and organizations."
      sections={sections}
    />
  );
}
