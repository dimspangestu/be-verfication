from sqlalchemy import Column, String, DateTime, Date, BigInteger, Integer, ForeignKey, Numeric, Boolean
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid

from db import Base


def utcnow():
    return datetime.now(timezone.utc)


class UserBankAccount(Base):
    __tablename__ = "user_bank_accounts"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    user_id = Column(UNIQUEIDENTIFIER, nullable=False, index=True)
    owner_type = Column(String(20), nullable=False)  # vendor / investor

    bank_name = Column(String(100), nullable=False)
    bank_code = Column(String(20), nullable=True)
    account_number = Column(String(50), nullable=False)
    account_name = Column(String(150), nullable=False)

    is_default = Column(Boolean, nullable=False, default=True)
    is_verified = Column(Boolean, nullable=False, default=False)
    verification_note = Column(String(255), nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow)


class VendorDisbursement(Base):
    __tablename__ = "vendor_disbursements"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    project_id = Column(UNIQUEIDENTIFIER, ForeignKey("projects.id"), nullable=False, index=True)
    vendor_id = Column(UNIQUEIDENTIFIER, ForeignKey("vendors.id"), nullable=False, index=True)
    admin_user_id = Column(UNIQUEIDENTIFIER, nullable=True)

    gross_amount = Column(BigInteger, nullable=False, default=0)
    fee_pct = Column(Numeric(6, 2), nullable=False, default=0)
    fee_amount = Column(BigInteger, nullable=False, default=0)
    net_amount = Column(BigInteger, nullable=False, default=0)

    bank_account_id = Column(UNIQUEIDENTIFIER, ForeignKey("user_bank_accounts.id"), nullable=True)
    disbursement_status = Column(String(30), nullable=False, default="pending")  # pending, processing, disbursed, failed
    provider = Column(String(30), nullable=True)
    provider_ref_no = Column(String(120), nullable=True)
    note = Column(String(255), nullable=True)
    disbursed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow)

    bank_account = relationship("UserBankAccount")


class VendorRepaymentSchedule(Base):
    __tablename__ = "vendor_repayment_schedules"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    project_id = Column(UNIQUEIDENTIFIER, ForeignKey("projects.id"), nullable=False, index=True)

    installment_no = Column(Integer, nullable=False)
    due_date = Column(Date, nullable=False)

    principal_amount = Column(BigInteger, nullable=False, default=0)
    return_amount = Column(BigInteger, nullable=False, default=0)
    total_due = Column(BigInteger, nullable=False)

    paid_amount = Column(BigInteger, nullable=False, default=0)
    repayment_status = Column(String(30), nullable=False, default="unpaid")  # unpaid, partial, paid, overdue
    paid_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow)


class VendorRepayment(Base):
    __tablename__ = "vendor_repayments"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    project_id = Column(UNIQUEIDENTIFIER, ForeignKey("projects.id"), nullable=False, index=True)
    schedule_id = Column(UNIQUEIDENTIFIER, ForeignKey("vendor_repayment_schedules.id"), nullable=True)
    vendor_id = Column(UNIQUEIDENTIFIER, ForeignKey("vendors.id"), nullable=False, index=True)

    order_id = Column(String(120), nullable=False, unique=True)
    gross_amount = Column(BigInteger, nullable=False)

    payment_status = Column(String(30), nullable=False, default="pending_payment")
    provider = Column(String(30), nullable=True)
    transaction_id = Column(String(120), nullable=True)
    payment_type = Column(String(50), nullable=True)

    paid_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow)


class InvestorPayout(Base):
    __tablename__ = "investor_payouts"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    project_id = Column(UNIQUEIDENTIFIER, ForeignKey("projects.id"), nullable=False, index=True)
    funding_id = Column(UNIQUEIDENTIFIER, ForeignKey("project_fundings.id"), nullable=False, index=True)
    investor_user_id = Column(UNIQUEIDENTIFIER, nullable=False, index=True)

    source_repayment_id = Column(UNIQUEIDENTIFIER, ForeignKey("vendor_repayments.id"), nullable=True)

    principal_amount = Column(BigInteger, nullable=False, default=0)
    return_amount = Column(BigInteger, nullable=False, default=0)
    total_amount = Column(BigInteger, nullable=False)

    bank_account_id = Column(UNIQUEIDENTIFIER, ForeignKey("user_bank_accounts.id"), nullable=True)
    payout_status = Column(String(30), nullable=False, default="pending")  # pending, processing, paid, failed
    provider = Column(String(30), nullable=True)
    provider_ref_no = Column(String(120), nullable=True)
    note = Column(String(255), nullable=True)
    paid_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow)

    bank_account = relationship("UserBankAccount")