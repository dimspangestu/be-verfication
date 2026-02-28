import uuid
from sqlalchemy import Column, String, DateTime, Boolean, BigInteger, ForeignKey
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from sqlalchemy.sql import func
from db import Base

class User(Base):
    __tablename__ = "users"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    name = Column(String(120), nullable=False)
    email = Column(String(190), nullable=False, unique=True, index=True)
    phone = Column(String(30), nullable=True)
    role = Column(String(30), nullable=False)  # penerima_dana / investor
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.sysutcdatetime(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.sysutcdatetime(), onupdate=func.sysutcdatetime(), nullable=False)

class TokenBlacklist(Base):
    __tablename__ = "token_blacklist"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    jti = Column(String(64), nullable=False, unique=True, index=True)
    user_id = Column(UNIQUEIDENTIFIER, ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.sysutcdatetime(), nullable=False)

class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(UNIQUEIDENTIFIER, ForeignKey("users.id"), nullable=False)
    token_hash = Column(String(128), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.sysutcdatetime(), nullable=False)