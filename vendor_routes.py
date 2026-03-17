from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime, timezone
from uuid import uuid4

from db import get_db
from models import User
from models_vendor_project import Vendor
from schemas_vendor_project import VendorUpsertIn, VendorOut
from auth_routes import get_current_user

router = APIRouter(prefix="/vendor", tags=["vendor"])


@router.get("/me", response_model=VendorOut)
def get_vendor_me(
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """
    Return vendor profile for the current user.
    If no profile exists yet, auto-create an empty one so the
    frontend always gets a 200 with a valid object — never a 404.
    """
    v = db.execute(
        select(Vendor).where(Vendor.user_id == me.id)
    ).scalar_one_or_none()

    if not v:
        # Auto-create a blank vendor row so GET never returns 404.
        # The frontend form will prompt the user to fill in the details.
        now = datetime.now(timezone.utc)
        v = Vendor(
            user_id=me.id,
            vendor_type="company",
            # Pre-fill email/phone from the auth User if available
            email=getattr(me, "email", None),
            phone=getattr(me, "phone", None),
            pg_status="inactive",
            kyc_status="unverified",
            created_at=now,
            updated_at=now,
        )
        db.add(v)
        db.commit()
        db.refresh(v)

    return v


@router.post("/me", response_model=VendorOut)
def upsert_vendor_me(
    payload: VendorUpsertIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """
    Create or update vendor profile (upsert).
    Only non-None values in the payload overwrite existing data.
    """
    v = db.execute(
        select(Vendor).where(Vendor.user_id == me.id)
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)

    if not v:
        v = Vendor(
            user_id=me.id,
            vendor_type=payload.vendor_type,
            vendor_code=payload.vendor_code,
            company_name=payload.company_name,
            pic_name=payload.pic_name,
            email=payload.email or getattr(me, "email", None),
            phone=payload.phone or getattr(me, "phone", None),
            npwp=payload.npwp,
            nik=payload.nik,
            address=payload.address,
            bank_name=payload.bank_name,
            bank_account_no=payload.bank_account_no,
            bank_account_name=payload.bank_account_name,
            pg_provider=payload.pg_provider,
            pg_merchant_id=payload.pg_merchant_id,
            pg_customer_id=payload.pg_customer_id,
            pg_va_bank=payload.pg_va_bank,
            pg_va_number=payload.pg_va_number,
            pg_status=payload.pg_status or "inactive",
            kyc_status="unverified",
            created_at=now,
            updated_at=now,
        )
        db.add(v)
    else:
        # Only overwrite fields that are explicitly provided (not None)
        for field, value in payload.model_dump(exclude_none=True).items():
            setattr(v, field, value)
        v.updated_at = now

    db.commit()
    db.refresh(v)
    return v