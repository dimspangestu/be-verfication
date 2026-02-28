from pydantic import BaseModel, EmailStr, Field
from typing import Literal, Optional

RoleType = Literal["penerima_dana", "investor", "admin"]

class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    phone: Optional[str] = Field(default=None, max_length=30)
    role: RoleType
    password: str = Field(min_length=8, max_length=72)

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class UserOut(BaseModel):
    id: str
    name: str
    email: EmailStr
    phone: Optional[str] = None
    role: RoleType

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"

class MessageOut(BaseModel):
    message: str

class ForgotPasswordIn(BaseModel):
    email: EmailStr

class ResetPasswordIn(BaseModel):
    token: str = Field(min_length=20)
    new_password: str = Field(min_length=8, max_length=72)