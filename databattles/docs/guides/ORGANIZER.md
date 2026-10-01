# Organizer guide

## Before you start

Competitions are hosted by an organization you manage (owner, admin or manager). Create a club or community under
**Universities → Create organization** if you don't have one. Platform admins may host without an organization.

## Create

**Organizer tools → Create competition** walks through: basics (type, task, difficulty, tags, host) → schedule (in your
chosen time zone; stored as UTC) → participation (visibility, team sizes, submission limits, final selections) → scoring
(metric, id/target columns) → content (description, rules, evaluation, prizes, FAQ, starter notebooks).

Visibility options:

* **Public** — listed and joinable by anyone.
* **University** — listed only to members of the host organization.
* **Invite only** — unlisted; anyone with the link can view, joining needs the invite code (share the invite link).
* **Private** — visible only to staff and invited participants.

## Evaluation

1. Attach a published dataset version (participants download `train/test/sample_submission`).
2. Upload the hidden **ground truth** CSV (`id`, target, optional `Usage` = Public/Private). It is validated against the
   metric and never shown to participants.
3. The publish checklist must be green (summary, description, rules, schedule, metric, ground truth). Then **Publish**.

After the first submission is scored, the metric and ground truth lock to keep results reproducible.

## During the event

* Announcements (optionally emailed to participants who opted in), schedule items, award categories.
* Submissions: organizer view shows private scores; **invalidate** a submission with a reason (audited).
* Participants list + CSV export; analytics (funnel, daily activity, score histogram, error codes).
* Staff: add co-organizers and judges by handle; sponsors by organization slug.

## Finishing

1. After the end time, review the private leaderboard preview (no pending submissions allowed).
2. **Finalize** — creates an immutable snapshot and profile results. Corrections later create a new version with a
   public reason.
3. Certificates: preview eligibility (top-N award + participation for anyone with a valid submission, plus award
   categories) → **Issue** (idempotent). Revoke with a reason if needed; the verification page shows the revocation.
4. Export results CSV.

## Judged events (hackathons, demo days)

Define a rubric (weighted criteria), assign judges (optionally per team/panel), record conflicts of interest, schedule
presentation slots. Teams submit a project (title, write-up, repo/demo/video links). Judges score assigned entries;
the rubric locks after the first submitted score. Finalize to publish rankings.
