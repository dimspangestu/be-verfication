from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select

from db import get_db
from models import User
from models_vendor_project import Vendor
from auth_routes import get_current_user
from wallet_service import get_wallet_balance

router = APIRouter(prefix="/wallets", tags=["wallets"])


@router.get("/me")
def wallet_me(
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    role = getattr(me, "role", None)

    if role == "admin":
        return {
            "role": role,
            "wallet_type": "admin_escrow",
            "balance": get_wallet_balance(db, "admin_escrow"),
        }

    if role == "investor":
        return {
            "role": role,
            "wallet_type": "investor_wallet",
            "balance": get_wallet_balance(db, "investor_wallet", user_id=me.id),
        }

    if role == "penerima_dana":
        vendor = db.execute(select(Vendor).where(Vendor.user_id == me.id)).scalar_one_or_none()
        if not vendor:
            return {
                "role": role,
                "wallet_type": "vendor_wallet",
                "balance": 0,
            }

        return {
            "role": role,
            "wallet_type": "vendor_wallet",
            "vendor_id": str(vendor.id),
            "balance": get_wallet_balance(db, "vendor_wallet", vendor_id=vendor.id),
        }

    raise HTTPException(status_code=403, detail="Unknown role")


@router.get("/admin/escrow")
def wallet_admin_escrow(
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    if getattr(me, "role", None) != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    return {
        "wallet_type": "admin_escrow",
        "balance": get_wallet_balance(db, "admin_escrow"),
    }