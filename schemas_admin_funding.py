from pydantic import BaseModel, Field, EmailStr
from typing import Optional
from uuid import UUID
from datetime import datetime

class FundingCreateIn(BaseModel):
    project_id: UUID
    funding_amount: int = Field(gt=0)

class FundingOut(BaseModel):
    id: UUID
    project_id: UUID
    investor_user_id: UUID
    funding_amount: int
    expected_return_amount: int
    expected_total_back: int
    funding_status: str
    paid_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class MidtransSnapOut(BaseModel):
    funding_id: UUID
    order_id: str
    redirect_url: Optional[str] = None
    token: Optional[str] = None
    transaction_status: Optional[str] = None

class AdminStatusUpdateIn(BaseModel):
    status: str
    note: Optional[str] = None

class DisbursementCreateIn(BaseModel):
    amount: int = Field(gt=0)
    note: Optional[str] = None

class InvestorFundingPrepareIn(BaseModel):
    project_id: UUID
    funding_amount: int = Field(gt=0)
    signer_name: str
    signer_email: EmailStr
    signer_phone: Optional[str] = None

class SignaturePrepareOut(BaseModel):
    signature_request_id: UUID
    funding_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    document_name: str
    status: str
    sign_url: Optional[str] = None