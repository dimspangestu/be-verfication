from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select

from models_admin_funding import ProjectFunding
from models_vendor_project import Project


def utcnow():
    return datetime.now(timezone.utc)


ACTIVE_STATUSES = ["reserved", "pending_payment", "paid"]


def expire_stale_project_fundings(db: Session, project_id=None):
    now = utcnow()

    stmt = select(ProjectFunding).where(
        ProjectFunding.funding_status.in_(["reserved", "pending_payment"]),
        ProjectFunding.reservation_expires_at.isnot(None),
        ProjectFunding.reservation_expires_at < now,
    )

    if project_id is not None:
        stmt = stmt.where(ProjectFunding.project_id == project_id)

    rows = db.execute(stmt).scalars().all()

    changed = 0
    for f in rows:
        f.funding_status = "expired"
        f.updated_at = now
        changed += 1

    if changed:
        db.flush()

    return changed


def get_project_active_funding_amount(db: Session, project_id):
    expire_stale_project_fundings(db, project_id=project_id)

    rows = db.execute(
        select(ProjectFunding).where(
            ProjectFunding.project_id == project_id,
            ProjectFunding.funding_status.in_(ACTIVE_STATUSES),
        )
    ).scalars().all()

    return sum(int(r.funding_amount or 0) for r in rows)


def get_project_remaining_amount(db: Session, project: Project):
    active_amount = get_project_active_funding_amount(db, project.id)
    return max(int(project.funding_needed or 0) - int(active_amount), 0)


def reserve_funding_24h(funding: ProjectFunding):
    now = utcnow()
    funding.funding_status = "reserved"
    funding.signed_at = now
    funding.reserved_at = now
    funding.reservation_expires_at = now + timedelta(hours=24)
    funding.updated_at = now


def mark_payment_started(funding: ProjectFunding):
    funding.funding_status = "pending_payment"
    funding.payment_started_at = utcnow()
    funding.updated_at = utcnow()