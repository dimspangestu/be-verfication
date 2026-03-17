from sqlalchemy import Column, String, DateTime, BigInteger
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from datetime import datetime, timezone
import uuid
from sqlalchemy import Text

from db import Base

def utcnow():
    return datetime.now(timezone.utc)

class ProjectFunding(Base):
    __tablename__ = "project_fundings"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    project_id = Column(UNIQUEIDENTIFIER, nullable=False, index=True)
    investor_user_id = Column(UNIQUEIDENTIFIER, nullable=False, index=True)

    funding_amount = Column(BigInteger, nullable=False)
    expected_return_amount = Column(BigInteger, nullable=False, default=0)
    expected_total_back = Column(BigInteger, nullable=False, default=0)

    funding_status = Column(String(30), nullable=False, default="draft")
    signed_at = Column(DateTime, nullable=True)
    reserved_at = Column(DateTime, nullable=True)
    reservation_expires_at = Column(DateTime, nullable=True)
    payment_started_at = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow)


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)

    project_id = Column(UNIQUEIDENTIFIER, nullable=True, index=True)
    funding_id = Column(UNIQUEIDENTIFIER, nullable=True, index=True)
    vendor_id = Column(UNIQUEIDENTIFIER, nullable=True)
    investor_user_id = Column(UNIQUEIDENTIFIER, nullable=True)

    payment_for = Column(String(30), nullable=False)
    provider = Column(String(20), nullable=False, default="midtrans")

    order_id = Column(String(100), nullable=False, unique=True)
    transaction_id = Column(String(120), nullable=True)
    payment_type = Column(String(50), nullable=True)
    gross_amount = Column(BigInteger, nullable=False)

    transaction_status = Column(String(50), nullable=True)
    fraud_status = Column(String(50), nullable=True)
    settlement_time = Column(DateTime, nullable=True)
    redirect_url = Column(String(500), nullable=True)
    snap_token = Column(String(255), nullable=True)
    raw_request = Column(String, nullable=True)
    raw_response = Column(String, nullable=True)
    raw_notification = Column(String, nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow)


# class VendorDisbursement(Base):
#     __tablename__ = "vendor_disbursements"

#     id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
#     project_id = Column(UNIQUEIDENTIFIER, nullable=False)
#     vendor_id = Column(UNIQUEIDENTIFIER, nullable=False)
#     admin_user_id = Column(UNIQUEIDENTIFIER, nullable=True)

#     amount = Column(BigInteger, nullable=False)
#     disbursement_status = Column(String(30), nullable=False, default="pending")
#     disbursed_at = Column(DateTime, nullable=True)
#     note = Column(String(300), nullable=True)

#     created_at = Column(DateTime, nullable=False, default=utcnow)
#     updated_at = Column(DateTime, nullable=False, default=utcnow)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)

    ref_type = Column(String(30), nullable=False)
    ref_id = Column(UNIQUEIDENTIFIER, nullable=True)

    user_id = Column(UNIQUEIDENTIFIER, nullable=True)
    vendor_id = Column(UNIQUEIDENTIFIER, nullable=True)
    project_id = Column(UNIQUEIDENTIFIER, nullable=True)

    entry_side = Column(String(10), nullable=False)
    wallet_type = Column(String(30), nullable=False)
    amount = Column(BigInteger, nullable=False)
    note = Column(String(300), nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow)

class WalletBalance(Base):
    __tablename__ = "wallet_balances"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    user_id = Column(UNIQUEIDENTIFIER, nullable=True, index=True)
    vendor_id = Column(UNIQUEIDENTIFIER, nullable=True, index=True)
    wallet_type = Column(String(30), nullable=False, index=True)  # admin_escrow | investor_wallet | vendor_wallet
    balance = Column(BigInteger, nullable=False, default=0)
    updated_at = Column(DateTime, nullable=False, default=utcnow)


class FundingDistribution(Base):
    __tablename__ = "funding_distributions"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    project_id = Column(UNIQUEIDENTIFIER, nullable=False)
    vendor_id = Column(UNIQUEIDENTIFIER, nullable=False)
    admin_user_id = Column(UNIQUEIDENTIFIER, nullable=True)
    amount = Column(BigInteger, nullable=False)
    distribution_status = Column(String(30), nullable=False, default="done")
    created_at = Column(DateTime, nullable=False, default=utcnow)


class RepaymentDistribution(Base):
    __tablename__ = "repayment_distributions"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    project_id = Column(UNIQUEIDENTIFIER, nullable=False)
    funding_id = Column(UNIQUEIDENTIFIER, nullable=False)
    investor_user_id = Column(UNIQUEIDENTIFIER, nullable=False)
    admin_user_id = Column(UNIQUEIDENTIFIER, nullable=True)
    amount = Column(BigInteger, nullable=False)
    distribution_status = Column(String(30), nullable=False, default="done")
    created_at = Column(DateTime, nullable=False, default=utcnow)

class SignatureRequest(Base):
    __tablename__ = "signature_requests"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)

    module_type = Column(String(40), nullable=False)  # vendor_project | investor_funding
    module_ref_id = Column(UNIQUEIDENTIFIER, nullable=False)

    user_id = Column(UNIQUEIDENTIFIER, nullable=True)
    vendor_id = Column(UNIQUEIDENTIFIER, nullable=True)
    project_id = Column(UNIQUEIDENTIFIER, nullable=True)
    funding_id = Column(UNIQUEIDENTIFIER, nullable=True)

    provider = Column(String(30), nullable=False, default="mekari")
    document_name = Column(String(255), nullable=True)
    document_html = Column(Text, nullable=True)
    document_pdf_path = Column(String(400), nullable=True)

    signer_name = Column(String(150), nullable=True)
    signer_email = Column(String(150), nullable=True)
    signer_phone = Column(String(50), nullable=True)

    status = Column(String(30), nullable=False, default="draft")
    external_document_id = Column(String(120), nullable=True)
    external_sign_url = Column(String(500), nullable=True)
    signed_file_url = Column(String(500), nullable=True)
    signed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow)