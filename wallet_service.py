from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from models_admin_funding import WalletBalance, LedgerEntry


def _utcnow():
    return datetime.now(timezone.utc)


def get_or_create_wallet(
    db: Session,
    wallet_type: str,
    user_id=None,
    vendor_id=None,
):
    row = db.execute(
        select(WalletBalance).where(
            WalletBalance.wallet_type == wallet_type,
            WalletBalance.user_id == user_id,
            WalletBalance.vendor_id == vendor_id,
        )
    ).scalar_one_or_none()

    if not row:
        row = WalletBalance(
            wallet_type=wallet_type,
            user_id=user_id,
            vendor_id=vendor_id,
            balance=0,
            updated_at=_utcnow(),
        )
        db.add(row)
        db.flush()

    return row


def get_wallet_balance(
    db: Session,
    wallet_type: str,
    user_id=None,
    vendor_id=None,
) -> int:
    row = db.execute(
        select(WalletBalance).where(
            WalletBalance.wallet_type == wallet_type,
            WalletBalance.user_id == user_id,
            WalletBalance.vendor_id == vendor_id,
        )
    ).scalar_one_or_none()

    return int(row.balance or 0) if row else 0


def apply_wallet_delta(
    db: Session,
    wallet_type: str,
    amount: int,
    user_id=None,
    vendor_id=None,
):
    wallet = get_or_create_wallet(
        db=db,
        wallet_type=wallet_type,
        user_id=user_id,
        vendor_id=vendor_id,
    )

    new_balance = int(wallet.balance or 0) + int(amount)
    if new_balance < 0:
        raise ValueError(f"Insufficient balance for wallet {wallet_type}")

    wallet.balance = new_balance
    wallet.updated_at = _utcnow()
    return wallet


def post_ledger_and_wallet(
    db: Session,
    *,
    ref_type: str,
    ref_id,
    entry_side: str,
    wallet_type: str,
    amount: int,
    note: str = "",
    user_id=None,
    vendor_id=None,
    project_id=None,
):
    signed_amount = int(amount)
    if entry_side == "debit":
        signed_amount = -abs(int(amount))
    elif entry_side == "credit":
        signed_amount = abs(int(amount))
    else:
        raise ValueError("entry_side must be debit or credit")

    db.add(
        LedgerEntry(
            ref_type=ref_type,
            ref_id=ref_id,
            user_id=user_id,
            vendor_id=vendor_id,
            project_id=project_id,
            entry_side=entry_side,
            wallet_type=wallet_type,
            amount=abs(int(amount)),
            note=note,
        )
    )

    apply_wallet_delta(
        db=db,
        wallet_type=wallet_type,
        amount=signed_amount,
        user_id=user_id,
        vendor_id=vendor_id,
    )