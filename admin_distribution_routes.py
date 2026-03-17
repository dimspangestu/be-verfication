from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select

from db import get_db
from models import User
from models_vendor_project import Project, Vendor
from models_admin_funding import (
    ProjectFunding,
    FundingDistribution,
    RepaymentDistribution,
)
from auth_routes import get_current_user
from wallet_service import get_wallet_balance, post_ledger_and_wallet

router = APIRouter(prefix="/admin/distributions", tags=["admin_distributions"])


def require_admin(me: User):
    if getattr(me, "role", None) != "admin":
        raise HTTPException(status_code=403, detail="Admin only")


@router.post("/funding-to-vendor/{project_id}")
def distribute_funding_to_vendor(
    project_id: str,
    payload: dict,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_admin(me)

    project = db.execute(select(Project).where(Project.id == project_id)).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    vendor = db.execute(select(Vendor).where(Vendor.id == project.vendor_id)).scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    amount = int(payload.get("amount") or 0)
    if amount <= 0:
        raise HTTPException(status_code=422, detail="amount must be > 0")

    escrow_balance = get_wallet_balance(db, "admin_escrow")
    if escrow_balance < amount:
        raise HTTPException(status_code=422, detail="Admin escrow balance is insufficient")

    dist = FundingDistribution(
        project_id=project.id,
        vendor_id=vendor.id,
        admin_user_id=me.id,
        amount=amount,
        distribution_status="done",
        created_at=datetime.now(timezone.utc),
    )
    db.add(dist)
    db.flush()

    post_ledger_and_wallet(
        db=db,
        ref_type="funding_distribution",
        ref_id=dist.id,
        project_id=project.id,
        vendor_id=vendor.id,
        entry_side="debit",
        wallet_type="admin_escrow",
        amount=amount,
        note="Funding distributed from admin escrow",
    )

    post_ledger_and_wallet(
        db=db,
        ref_type="funding_distribution",
        ref_id=dist.id,
        project_id=project.id,
        vendor_id=vendor.id,
        entry_side="credit",
        wallet_type="vendor_wallet",
        amount=amount,
        note="Funding received by vendor wallet",
    )

    project.status = "disbursed_to_vendor"
    project.updated_at = datetime.now(timezone.utc)

    db.commit()

    return {
        "message": "Funding distributed to vendor",
        "project_id": str(project.id),
        "vendor_id": str(vendor.id),
        "amount": amount,
    }


@router.post("/repayment-to-investor/{funding_id}")
def distribute_repayment_to_investor(
    funding_id: str,
    payload: dict,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_admin(me)

    funding = db.execute(select(ProjectFunding).where(ProjectFunding.id == funding_id)).scalar_one_or_none()
    if not funding:
        raise HTTPException(status_code=404, detail="Funding not found")

    amount = int(payload.get("amount") or 0)
    if amount <= 0:
        raise HTTPException(status_code=422, detail="amount must be > 0")

    escrow_balance = get_wallet_balance(db, "admin_escrow")
    if escrow_balance < amount:
        raise HTTPException(status_code=422, detail="Admin escrow balance is insufficient")

    dist = RepaymentDistribution(
        project_id=funding.project_id,
        funding_id=funding.id,
        investor_user_id=funding.investor_user_id,
        admin_user_id=me.id,
        amount=amount,
        distribution_status="done",
        created_at=datetime.now(timezone.utc),
    )
    db.add(dist)
    db.flush()

    post_ledger_and_wallet(
        db=db,
        ref_type="repayment_distribution",
        ref_id=dist.id,
        project_id=funding.project_id,
        user_id=funding.investor_user_id,
        entry_side="debit",
        wallet_type="admin_escrow",
        amount=amount,
        note="Repayment distributed from admin escrow",
    )

    post_ledger_and_wallet(
        db=db,
        ref_type="repayment_distribution",
        ref_id=dist.id,
        project_id=funding.project_id,
        user_id=funding.investor_user_id,
        entry_side="credit",
        wallet_type="investor_wallet",
        amount=amount,
        note="Repayment received by investor wallet",
    )

    db.commit()

    return {
        "message": "Repayment distributed to investor",
        "funding_id": str(funding.id),
        "investor_user_id": str(funding.investor_user_id),
        "amount": amount,
    }