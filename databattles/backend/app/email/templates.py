"""Versioned transactional email templates.

Each template has a plain-text body and an accessible HTML body (semantic
markup, sufficient contrast, no image-only content). Bump `version` whenever
copy changes; the outbox records which version was sent.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any

from app.core.config import settings


@dataclass(frozen=True)
class EmailTemplate:
    name: str
    version: int
    subject: str
    text: str
    essential: bool = False  # essential mail (account/security) ignores unsubscribe preferences


TEMPLATES: dict[str, EmailTemplate] = {
    t.name: t
    for t in [
        EmailTemplate("verify_email", 1, "Verify your email for {app}",
                      "Hi {name},\n\nConfirm your email address to finish setting up your {app} account:\n{link}\n\n"
                      "This link expires in 24 hours. If you did not sign up, you can ignore this message.", True),
        EmailTemplate("account_exists", 1, "Sign-up attempt on {app}",
                      "Hi {name},\n\nSomeone tried to create a {app} account with this email address, which already has "
                      "an account. If this was you, sign in instead: {link}\n\nIf you forgot your password you can reset it "
                      "from the sign-in page. If this was not you, no action is needed.", True),
        EmailTemplate("password_reset", 1, "Reset your {app} password",
                      "Hi {name},\n\nUse the link below to choose a new password. It expires in 30 minutes and can be used once.\n"
                      "{link}\n\nIf you did not request this, you can ignore this email — your password will not change.", True),
        EmailTemplate("password_changed", 1, "Your {app} password was changed",
                      "Hi {name},\n\nThe password for your account was just changed and all other sessions were signed out. "
                      "If this was not you, reset your password immediately: {link}", True),
        EmailTemplate("org_email_verify", 1, "Confirm your {org} affiliation on {app}",
                      "Hi {name},\n\nConfirm that {email} belongs to you to verify your membership in {org}:\n{link}\n\n"
                      "This link expires in 24 hours.", True),
        EmailTemplate("team_invite", 1, "{inviter} invited you to join {team}",
                      "Hi {name},\n\n{inviter} invited you to join the team \"{team}\" in {competition}.\n"
                      "Review the invitation: {link}"),
        EmailTemplate("competition_deadline", 1, "{competition} closes {when}",
                      "Hi {name},\n\n{competition} stops accepting submissions {when}.\nOpen the competition: {link}"),
        EmailTemplate("submission_result", 1, "Submission {status}: {competition}",
                      "Hi {name},\n\nYour submission to {competition} was {status}.\n{detail}\n\nView submissions: {link}"),
        EmailTemplate("certificate_issued", 1, "Your certificate for {event} is ready",
                      "Hi {name},\n\nA certificate was issued to you for {event} ({result}).\n"
                      "Anyone can verify it at: {link}"),
        EmailTemplate("announcement", 1, "[{competition}] {title}",
                      "Hi {name},\n\nThe organizers of {competition} posted an update:\n\n{title}\n\nRead it: {link}"),
        EmailTemplate("results_published", 1, "Results are final for {competition}",
                      "Hi {name},\n\nFinal results for {competition} have been published.\nSee the leaderboard: {link}"),
        EmailTemplate("moderation_outcome", 1, "An update about your content on {app}",
                      "Hi {name},\n\nA moderator reviewed content associated with your account.\nOutcome: {outcome}\n\n"
                      "Community guidelines: {link}"),
        EmailTemplate("generic_notification", 1, "{title}", "Hi {name},\n\n{body}\n\n{link}"),
    ]
}


class _SafeDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return ""


def render(template_name: str, context: dict[str, Any], *, unsubscribe_url: str | None = None) -> tuple[EmailTemplate, str, str, str]:
    tpl = TEMPLATES[template_name]
    ctx = _SafeDict({"app": settings.APP_NAME, **context})
    subject = tpl.subject.format_map(ctx).replace("\n", " ")[:200]
    text = tpl.text.format_map(ctx)
    footer_text = f"\n\n—\n{settings.APP_NAME}"
    if unsubscribe_url and not tpl.essential:
        footer_text += f"\nManage email preferences or unsubscribe: {unsubscribe_url}"
    text += footer_text

    escaped = _SafeDict({k: html.escape(str(v)) for k, v in ctx.items()})
    body_html = html.escape(tpl.text).format_map(escaped)
    paragraphs = "".join(
        f'<p style="margin:0 0 16px;line-height:1.55">{p.replace(chr(10), "<br>")}</p>' for p in body_html.split("\n\n")
    )
    link = escaped.get("link", "")
    button = (f'<p style="margin:24px 0"><a href="{link}" style="background:#5B3DF5;color:#ffffff;padding:12px 20px;'
              f'border-radius:8px;text-decoration:none;font-weight:600;display:inline-block">Open {html.escape(settings.APP_NAME)}</a></p>'
              if link else "")
    unsub = (f'<p style="font-size:12px;color:#5B6474">You can <a href="{html.escape(unsubscribe_url)}" style="color:#5B3DF5">'
             f"manage email preferences or unsubscribe</a>.</p>" if unsubscribe_url and not tpl.essential else "")
    html_doc = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width"><title>{html.escape(subject)}</title></head>'
        '<body style="margin:0;background:#F7F8FA;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#0F172A">'
        '<main style="max-width:560px;margin:0 auto;padding:32px 24px">'
        f'<p style="font-weight:700;font-size:18px;margin:0 0 24px">{html.escape(settings.APP_NAME)}</p>'
        f'<div role="article" aria-label="{html.escape(subject)}" style="background:#ffffff;border:1px solid #E5E7EB;border-radius:12px;padding:24px">'
        f"{paragraphs}{button}</div>{unsub}</main></body></html>"
    )
    return tpl, subject, text, html_doc
