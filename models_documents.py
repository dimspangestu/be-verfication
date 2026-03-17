from sqlalchemy import Column, String, DateTime, Boolean, Integer, Text, BigInteger
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from datetime import datetime, timezone
import uuid

from db import Base


def utcnow():
    return datetime.now(timezone.utc)


class DocumentTemplate(Base):
    __tablename__ = "document_templates"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    template_code = Column(String(60), nullable=False)
    template_name = Column(String(150), nullable=False)
    module_type = Column(String(40), nullable=False)
    content_html = Column(Text, nullable=False)
    header_html = Column(Text, nullable=True)
    footer_html = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    version_no = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow)


class GeneratedDocument(Base):
    __tablename__ = "generated_documents"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    template_id = Column(UNIQUEIDENTIFIER, nullable=True)
    module_type = Column(String(40), nullable=False)
    module_ref_id = Column(UNIQUEIDENTIFIER, nullable=False)
    file_name = Column(String(255), nullable=False)
    storage_path = Column(String(500), nullable=False)
    mime_type = Column(String(100), nullable=False, default="application/pdf")
    file_size = Column(BigInteger, nullable=True)
    rendered_html = Column(Text, nullable=True)
    render_payload = Column(Text, nullable=True)
    document_status = Column(String(30), nullable=False, default="generated")
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow)


class SignedDocument(Base):
    __tablename__ = "signed_documents"

    id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    module_type = Column(String(40), nullable=False)
    module_ref_id = Column(UNIQUEIDENTIFIER, nullable=False)

    generated_document_id = Column(UNIQUEIDENTIFIER, nullable=True)

    uploaded_by_user_id = Column(UNIQUEIDENTIFIER, nullable=True)
    file_name = Column(String(255), nullable=False)
    storage_path = Column(String(500), nullable=False)
    mime_type = Column(String(100), nullable=False, default="application/pdf")
    file_size = Column(BigInteger, nullable=True)

    verification_status = Column(String(30), nullable=False, default="uploaded")
    verified_by_user_id = Column(UNIQUEIDENTIFIER, nullable=True)
    verified_at = Column(DateTime, nullable=True)
    note = Column(String(500), nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow)