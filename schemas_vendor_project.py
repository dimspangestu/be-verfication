from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime, date
from uuid import UUID


VendorType = Literal["company", "individual"]
KycStatus = Literal["unverified", "pending", "verified", "rejected"]
PgProvider = Optional[Literal["midtrans", "xendit"]]
PgStatus = Literal["inactive", "active", "suspended"]

ProjectType = Literal["infrastruktur", "properti", "umkm", "lainnya"]
ProjectStatus = Literal["draft", "ongoing", "accepted", "negosiasi", "reject", "final_project", "closed"]

DocType = Literal["proposal", "rab", "lainnya"]


class VendorUpsertIn(BaseModel):
    vendor_type: VendorType = "company"
    vendor_code: Optional[str] = None

    company_name: Optional[str] = None
    pic_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

    npwp: Optional[str] = None
    nik: Optional[str] = None
    address: Optional[str] = None

    bank_name: Optional[str] = None
    bank_account_no: Optional[str] = None
    bank_account_name: Optional[str] = None

    pg_provider: PgProvider = None
    pg_merchant_id: Optional[str] = None
    pg_customer_id: Optional[str] = None
    pg_va_bank: Optional[str] = None
    pg_va_number: Optional[str] = None
    pg_status: Optional[PgStatus] = None


class VendorOut(BaseModel):
    id: UUID
    user_id: Optional[UUID] = None

    vendor_code: Optional[str] = None
    vendor_type: str

    company_name: Optional[str] = None
    pic_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

    npwp: Optional[str] = None
    nik: Optional[str] = None
    address: Optional[str] = None

    kyc_status: str
    kyc_note: Optional[str] = None

    bank_name: Optional[str] = None
    bank_account_no: Optional[str] = None
    bank_account_name: Optional[str] = None

    pg_provider: Optional[str] = None
    pg_merchant_id: Optional[str] = None
    pg_customer_id: Optional[str] = None
    pg_va_bank: Optional[str] = None
    pg_va_number: Optional[str] = None
    pg_status: str

    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class FileMetaIn(BaseModel):
    type: DocType
    fileName: str
    mime: Optional[str] = None
    size: Optional[int] = None


class StatementsIn(BaseModel):
    st1: bool
    st2: bool
    st3: bool
    st4: bool
    st5: bool


class EConsentIn(BaseModel):
    ec1: bool
    ec2: bool
    ec3: bool
    submissionDate: date
    signerName: str
    otpOrESign: str


class ClauseConsentIn(BaseModel):
    collectionMechanismAccepted: bool = True
    acceptedAt: Optional[datetime] = None


class ProjectCreateIn(BaseModel):
    projectNameOrCode: str
    recipientName: str

    projectType: ProjectType
    projectTypeOther: Optional[str] = None

    fundingNeeded: int
    minFunding: Optional[int] = None
    tenorMonths: int
    fixedReturnPct: float

    documents: List[FileMetaIn]
    statements: StatementsIn
    eConsent: EConsentIn
    clauseConsent: Optional[ClauseConsentIn] = None


class ProjectOut(BaseModel):
    id: UUID
    vendor_id: UUID

    project_code: str
    project_name: Optional[str] = None

    project_type: str
    project_type_other: Optional[str] = None

    funding_needed: int
    min_funding: Optional[int] = None
    tenor_months: int
    fixed_return_pct: float

    return_total_amount: int
    payback_total_amount: int
    return_monthly_amount: int

    status: str
    submitted_at: Optional[datetime] = None

    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MessageOut(BaseModel):
    message: str