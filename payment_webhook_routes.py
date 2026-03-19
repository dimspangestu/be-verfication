from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime, timezone
import json

from db import get_db
from models_vendor_project import Project
from models_admin_funding import ProjectFunding, PaymentTransaction
from wallet_service import post_ledger_and_wallet
from midtrans_service import verify_notification_signature

router = APIRouter(prefix="/payments", tags=["payments"])


def map_midtrans_paid(transaction_status: str, fraud_status: str | None):
    if transaction_status == "settlement":
        return True
    if transaction_status == "capture" and fraud_status == "accept":
        return True
    return False


@router.post("/midtrans/notification")
async def midtrans_notification(
    request: Request,
    db: Session = Depends(get_db),
):
    body = await request.json()

    # ✅ Verifikasi signature Midtrans
    if not verify_notification_signature(body):
        raise HTTPException(status_code=403, detail="Invalid signature")

    order_id = body.get("order_id")
    transaction_status = body.get("transaction_status")
    fraud_status = body.get("fraud_status")
    payment_type = body.get("payment_type")
    transaction_id = body.get("transaction_id")
    settlement_time = body.get("settlement_time")

    tx = db.execute(
        select(PaymentTransaction).where(PaymentTransaction.order_id == order_id)
    ).scalar_one_or_none()

    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    tx.transaction_status = transaction_status
    tx.fraud_status = fraud_status
    tx.payment_type = payment_type
    tx.transaction_id = transaction_id
    tx.raw_notification = json.dumps(body)
    tx.updated_at = datetime.now(timezone.utc)

    if map_midtrans_paid(transaction_status, fraud_status):
        funding = db.execute(
            select(ProjectFunding).where(ProjectFunding.id == tx.funding_id)
        ).scalar_one_or_none()

        if funding and funding.funding_status != "paid":
            funding.funding_status = "paid"
            funding.paid_at = datetime.now(timezone.utc)
            funding.updated_at = datetime.now(timezone.utc)

            # ✅ masuk ke wallet admin escrow + ledger
            post_ledger_and_wallet(
                db=db,
                ref_type="funding",
                ref_id=funding.id,
                user_id=funding.investor_user_id,
                project_id=funding.project_id,
                entry_side="credit",
                wallet_type="admin_escrow",
                amount=funding.funding_amount,
                note="Investor funding settled into admin escrow",
            )

    elif transaction_status in ["deny", "cancel", "expire", "failure"]:
        funding = db.execute(
            select(ProjectFunding).where(ProjectFunding.id == tx.funding_id)
        ).scalar_one_or_none()

        if funding:
            funding.funding_status = transaction_status
            funding.updated_at = datetime.now(timezone.utc)

    db.commit()
    return {"message": "Notification processed"}