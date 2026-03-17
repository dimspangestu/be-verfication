from pydantic import BaseModel, Field
from typing import Optional


class BankAccountCreateIn(BaseModel):
    owner_type: str  # vendor / investor
    bank_name: str
    bank_code: Optional[str] = None
    account_number: str
    account_name: str
    is_default: bool = True


class BankAccountUpdateIn(BaseModel):
    bank_name: Optional[str] = None
    bank_code: Optional[str] = None
    account_number: Optional[str] = None
    account_name: Optional[str] = None
    is_default: Optional[bool] = None


class VendorDisbursementCreateIn(BaseModel):
    gross_amount: int = Field(gt=0)
    fee_pct: float = Field(ge=0, le=100)
    bank_account_id: str
    note: Optional[str] = None


class VendorRepaymentPaymentCreateIn(BaseModel):
    schedule_id: str
    amount: int = Field(gt=0)


class InvestorPayoutProcessIn(BaseModel):
    bank_account_id: str
    note: Optional[str] = None