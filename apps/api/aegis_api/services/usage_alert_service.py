"""Usage threshold alerts (50 / 75 / 90 / 100 % of a quota), sent once per metric, threshold and period."""

from __future__ import annotations

from sqlalchemy import select

from aegis_api.db.session import admin_session_scope
from aegis_api.models import Membership, Notification, Organization
from aegis_api.services import entitlements


def send_threshold_alerts() -> int:
    sent = 0
    with admin_session_scope() as session:
        orgs = session.scalars(
            select(Organization).where(Organization.is_sandbox.is_(False), Organization.is_demo.is_(False))
        ).all()
        for org in orgs:
            snap = entitlements.snapshot(session, org.id)
            period = snap["period"]["start"][:10]
            recipients = session.scalars(
                select(Membership.user_id).where(
                    Membership.organization_id == org.id,
                    Membership.status == "active",
                    Membership.role.in_(["owner", "admin"]),
                )
            ).all()
            for quota in snap["quotas"]:
                threshold = quota["threshold_reached"]
                if not threshold or not quota["periodic"]:
                    continue
                key = f"usage:{quota['metric']}:{threshold}:{period}"
                already = session.scalar(
                    select(Notification.id).where(
                        Notification.organization_id == org.id,
                        Notification.type == "usage.threshold",
                        Notification.link == f"/dashboard/billing?alert={key}",
                    )
                )
                if already:
                    continue
                for user_id in recipients:
                    session.add(
                        Notification(
                            organization_id=org.id,
                            user_id=user_id,
                            type="usage.threshold",
                            title=f"{quota['label']}: {threshold}% of your plan used",
                            body=f"{quota['used']:,} of {quota['limit']:,} used this period.",
                            link=f"/dashboard/billing?alert={key}",
                            severity="high" if threshold >= 90 else "info",
                        )
                    )
                from aegis_api.services import webhook_service

                webhook_service.enqueue_event(
                    session,
                    org.id,
                    "usage.threshold",
                    {"metric": quota["metric"], "threshold": threshold, "used": quota["used"], "limit": quota["limit"]},
                )
                sent += 1
    return sent
