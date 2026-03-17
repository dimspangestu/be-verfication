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


def generate_vendor_code():
    return f"VND-{uuid.uuid4().hex[:10].upper()}"


class Vendor(Base):
    __tablename__ = "vendors"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    user_id = Column(UNIQUEIDENTIFIER, nullable=True, index=True)

    # Penting: jangan None, karena unique + SQL Server akan bentrok kalau banyak NULL
    vendor_code = Column(
        String(40),
        unique=True,
        nullable=False,
        default=generate_vendor_code,
        index=True,
    )

    vendor_type = Column(String(20), nullable=False, default="company")  # company|individual

    company_name = Column(String(180), nullable=True)
    pic_name = Column(String(120), nullable=True)
    email = Column(String(160), nullable=True)
    phone = Column(String(40), nullable=True)

    npwp = Column(String(32), nullable=True)
    nik = Column(String(32), nullable=True)
    address = Column(String(400), nullable=True)

    kyc_status = Column(String(20), nullable=False, default="unverified")
    kyc_note = Column(String(400), nullable=True)

    bank_name = Column(String(80), nullable=True)
    bank_account_no = Column(String(40), nullable=True)
    bank_account_name = Column(String(120), nullable=True)

    pg_provider = Column(String(20), nullable=True)  # midtrans|xendit
    pg_merchant_id = Column(String(120), nullable=True)
    pg_customer_id = Column(String(120), nullable=True)
    pg_va_bank = Column(String(50), nullable=True)
    pg_va_number = Column(String(80), nullable=True)
    pg_status = Column(String(20), nullable=False, default="inactive")
    pg_last_sync_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    projects = relationship("Project", back_populates="vendor")


class Project(Base):
    __tablename__ = "projects"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    vendor_id = Column(
        UNIQUEIDENTIFIER,
        ForeignKey("vendors.id"),
        nullable=False,
        index=True,
    )

    project_code = Column(String(80), nullable=False, index=True)
    project_name = Column(String(200), nullable=True)

    project_type = Column(String(30), nullable=False)  # infrastruktur|properti|umkm|lainnya
    project_type_other = Column(String(120), nullable=True)

    funding_needed = Column(BigInteger, nullable=False)
    min_funding = Column(BigInteger, nullable=True)

    tenor_months = Column(Integer, nullable=False)
    fixed_return_pct = Column(Numeric(6, 2), nullable=False)

    return_total_amount = Column(BigInteger, nullable=False, default=0)
    payback_total_amount = Column(BigInteger, nullable=False, default=0)
    return_monthly_amount = Column(BigInteger, nullable=False, default=0)

    status = Column(String(30), nullable=False, default="draft")
    submitted_at = Column(DateTime, nullable=True)

    created_by_user_id = Column(UNIQUEIDENTIFIER, nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    vendor = relationship("Vendor", back_populates="projects")
    documents = relationship(
        "ProjectDocument",
        back_populates="project",
        cascade="all, delete-orphan",
    )
    consents = relationship(
        "ProjectConsent",
        back_populates="project",
        uselist=False,
        cascade="all, delete-orphan",
    )


class ProjectDocument(Base):
    __tablename__ = "project_documents"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UNIQUEIDENTIFIER,
        ForeignKey("projects.id"),
        nullable=False,
        index=True,
    )

    doc_type = Column(String(20), nullable=False)  # proposal|rab|lainnya
    file_name = Column(String(260), nullable=False)  # original file name
    mime = Column(String(120), nullable=True)
    file_size = Column(BigInteger, nullable=True)
    storage_path = Column(String(400), nullable=True)  # file path di server

    created_at = Column(DateTime, nullable=False, default=utcnow)

    project = relationship("Project", back_populates="documents")


class ProjectConsent(Base):
    __tablename__ = "project_consents"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UNIQUEIDENTIFIER,
        ForeignKey("projects.id"),
        nullable=False,
        unique=True,
    )

    st1 = Column(Boolean, nullable=False, default=False)
    st2 = Column(Boolean, nullable=False, default=False)
    st3 = Column(Boolean, nullable=False, default=False)
    st4 = Column(Boolean, nullable=False, default=False)
    st5 = Column(Boolean, nullable=False, default=False)

    ec1 = Column(Boolean, nullable=False, default=False)
    ec2 = Column(Boolean, nullable=False, default=False)
    ec3 = Column(Boolean, nullable=False, default=False)

    submission_date = Column(Date, nullable=True)
    signer_name = Column(String(150), nullable=True)
    otp_or_esign = Column(String(80), nullable=True)

    collection_mechanism_accepted = Column(Boolean, nullable=False, default=False)
    accepted_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow)

    project = relationship("Project", back_populates="consents")