from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select, update
from datetime import datetime, timezone
import uuid

from db import get_db
from models import User
from auth_routes import get_current_user
from models_disbursement import UserBankAccount
from schemas_disbursement import BankAccountCreateIn, BankAccountUpdateIn

router = APIRouter(prefix="/bank-accounts", tags=["bank-accounts"])


def utcnow():
    return datetime.now(timezone.utc)


def parse_uuid_or_400(value: str, field_name: str = "id"):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")


@router.get("/me")
def list_my_bank_accounts(
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    rows = db.execute(
        select(UserBankAccount)
        .where(UserBankAccount.user_id == me.id)
        .order_by(UserBankAccount.is_default.desc(), UserBankAccount.created_at.desc())
    ).scalars().all()

    return [
        {
            "id": str(r.id),
            "user_id": str(r.user_id),
            "owner_type": r.owner_type,
            "bank_name": r.bank_name,
            "bank_code": r.bank_code,
            "account_number": r.account_number,
            "account_name": r.account_name,
            "is_default": r.is_default,
            "is_verified": r.is_verified,
            "verification_note": r.verification_note,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        }
        for r in rows
    ]


@router.post("")
def create_bank_account(
    payload: BankAccountCreateIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    if payload.owner_type not in {"vendor", "investor"}:
        raise HTTPException(status_code=422, detail="owner_type must be vendor or investor")

    if payload.owner_type == "vendor" and getattr(me, "role", None) != "penerima_dana":
        raise HTTPException(status_code=403, detail="Only vendor account can add vendor bank account")

    if payload.owner_type == "investor" and getattr(me, "role", None) != "investor":
        raise HTTPException(status_code=403, detail="Only investor account can add investor bank account")

    if payload.is_default:
        db.execute(
            update(UserBankAccount)
            .where(
                UserBankAccount.user_id == me.id,
                UserBankAccount.owner_type == payload.owner_type,
            )
            .values(is_default=False, updated_at=utcnow())
        )

    row = UserBankAccount(
        user_id=me.id,
        owner_type=payload.owner_type,
        bank_name=payload.bank_name,
        bank_code=payload.bank_code,
        account_number=payload.account_number,
        account_name=payload.account_name,
        is_default=payload.is_default,
        is_verified=False,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "message": "Bank account created",
        "data": {
            "id": str(row.id),
            "user_id": str(row.user_id),
            "owner_type": row.owner_type,
            "bank_name": row.bank_name,
            "bank_code": row.bank_code,
            "account_number": row.account_number,
            "account_name": row.account_name,
            "is_default": row.is_default,
            "is_verified": row.is_verified,
            "verification_note": row.verification_note,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        },
    }


@router.patch("/{bank_account_id}")
def update_bank_account(
    bank_account_id: str,
    payload: BankAccountUpdateIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    bank_uuid = parse_uuid_or_400(bank_account_id, "bank account id")

    row = db.execute(
        select(UserBankAccount).where(
            UserBankAccount.id == bank_uuid,
            UserBankAccount.user_id == me.id,
        )
    ).scalar_one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail="Bank account not found")

    if payload.is_default:
        db.execute(
            update(UserBankAccount)
            .where(
                UserBankAccount.user_id == me.id,
                UserBankAccount.owner_type == row.owner_type,
                UserBankAccount.id != row.id,
            )
            .values(is_default=False, updated_at=utcnow())
        )

    if payload.bank_name is not None:
        row.bank_name = payload.bank_name
    if payload.bank_code is not None:
        row.bank_code = payload.bank_code
    if payload.account_number is not None:
        row.account_number = payload.account_number
    if payload.account_name is not None:
        row.account_name = payload.account_name
    if payload.is_default is not None:
        row.is_default = payload.is_default

    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)

    return {"message": "Bank account updated", "id": str(row.id)}


@router.delete("/{bank_account_id}")
def delete_bank_account(
    bank_account_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    bank_uuid = parse_uuid_or_400(bank_account_id, "bank account id")

    row = db.execute(
        select(UserBankAccount).where(
            UserBankAccount.id == bank_uuid,
            UserBankAccount.user_id == me.id,
        )
    ).scalar_one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail="Bank account not found")

    db.delete(row)
    db.commit()

    return {"message": "Bank account deleted", "id": str(bank_uuid)}