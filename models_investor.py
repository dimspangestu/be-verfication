from sqlalchemy import (
    Column,
    String,
    DateTime,
    Date,
    BigInteger,
    Integer,
    ForeignKey,
    Numeric,
    Boolean,
)
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid

from db import Base


def utcnow():
    return datetime.now(timezone.utc)


def generate_investor_code():
    return f"INV-{uuid.uuid4().hex[:10].upper()}"


# ─────────────────────────────────────────────────────────
#  KYC doc types per investor_type:
#
#  individual (pribadi):
#    - ktp          : Kartu Tanda Penduduk
#    - npwp         : NPWP Pribadi
#    - selfie_ktp   : Foto selfie pegang KTP
#    - buku_tabungan: Buku Tabungan
#
#  company (badan):
#    - akta_perusahaan : Akta Pendirian Perusahaan
#    - npwp            : NPWP Perusahaan
#    - siup_nib        : SIUP / NIB
#    - buku_tabungan   : Buku Tabungan Perusahaan
#    - ktp_direksi     : KTP Direksi / Pengurus
# ─────────────────────────────────────────────────────────

INVESTOR_KYC_DOCS = {
    "individual": ["ktp", "npwp", "selfie_ktp", "buku_tabungan"],
    "company":    ["akta_perusahaan", "npwp", "siup_nib", "buku_tabungan", "ktp_direksi"],
}


class Investor(Base):
    __tablename__ = "investors"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    user_id = Column(UNIQUEIDENTIFIER, nullable=False, unique=True, index=True)

    investor_code = Column(
        String(40),
        unique=True,
        nullable=False,
        default=generate_investor_code,
        index=True,
    )

    # individual | company
    investor_type = Column(String(20), nullable=False, default="individual")

    # ── Shared fields ──────────────────────────────────────────
    full_name = Column(String(180), nullable=True)   # nama lengkap / nama perusahaan
    email     = Column(String(160), nullable=True)
    phone     = Column(String(40),  nullable=True)
    address   = Column(String(400), nullable=True)

    # ── Individual-only ────────────────────────────────────────
    nik             = Column(String(32),  nullable=True)   # 16 digit KTP
    birth_date      = Column(Date,        nullable=True)
    birth_place     = Column(String(100), nullable=True)
    gender          = Column(String(10),  nullable=True)   # male | female
    occupation      = Column(String(120), nullable=True)
    npwp            = Column(String(32),  nullable=True)

    # ── Company-only ───────────────────────────────────────────
    company_name    = Column(String(180), nullable=True)
    pic_name        = Column(String(120), nullable=True)   # nama direksi / PIC
    pic_position    = Column(String(80),  nullable=True)   # jabatan PIC
    npwp_company    = Column(String(32),  nullable=True)
    nib             = Column(String(60),  nullable=True)   # NIB

    # ── Rekening Bank ──────────────────────────────────────────
    bank_name         = Column(String(80),  nullable=True)
    bank_account_no   = Column(String(40),  nullable=True)
    bank_account_name = Column(String(120), nullable=True)

    # ── KYC ────────────────────────────────────────────────────
    kyc_status = Column(String(20), nullable=False, default="unverified")
    # unverified | pending | verified | rejected
    kyc_note   = Column(String(400), nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    kyc_documents = relationship(
        "InvestorKycDocument",
        back_populates="investor",
        cascade="all, delete-orphan",
    )


class InvestorKycDocument(Base):
    __tablename__ = "investor_kyc_documents"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    investor_id = Column(
        UNIQUEIDENTIFIER,
        ForeignKey("investors.id"),
        nullable=False,
        index=True,
    )

    doc_type = Column(String(30), nullable=False)
    # individual: ktp | npwp | selfie_ktp | buku_tabungan
    # company:    akta_perusahaan | npwp | siup_nib | buku_tabungan | ktp_direksi

    file_name    = Column(String(260), nullable=False)
    mime         = Column(String(120), nullable=True)
    file_size    = Column(BigInteger,  nullable=True)
    storage_path = Column(String(400), nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow)

    investor = relationship("Investor", back_populates="kyc_documents")