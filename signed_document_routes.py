from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import select
from pathlib import Path
from datetime import datetime, timezone
import os
import uuid
import shutil

from db import get_db
from models import User
from auth_routes import get_current_user
from models_vendor_project import Project, Vendor
from models_admin_funding import ProjectFunding
from models_documents import GeneratedDocument, SignedDocument
from funding_reservation_service import reserve_funding_24h

router = APIRouter(prefix="/signed-documents", tags=["signed_documents"])

BASE_SIGNED_DIR = os.getenv("SIGNED_DOCUMENT_STORAGE_DIR", "storage/signed_documents")


def utcnow():
    return datetime.now(timezone.utc)


def require_vendor_or_admin(me: User):
    if getattr(me, "role", None) not in ["penerima_dana", "admin"]:
        raise HTTPException(status_code=403, detail="Vendor/Admin only")


def require_investor_or_admin(me: User):
    if getattr(me, "role", None) not in ["investor", "admin"]:
        raise HTTPException(status_code=403, detail="Investor/Admin only")


def ensure_pdf(upload: UploadFile):
    filename = (upload.filename or "").lower()
    content_type = (upload.content_type or "").lower()

    if not filename.endswith(".pdf") and "pdf" not in content_type:
        raise HTTPException(status_code=422, detail="Only PDF file is allowed")


def save_upload_file(upload: UploadFile, output_path: str):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as buffer:
        shutil.copyfileobj(upload.file, buffer)
    return output_path


@router.post("/vendor-project/{project_id}/upload")
def upload_vendor_signed_pdf(
    project_id: str,
    file: UploadFile = File(...),
    generated_document_id: str | None = Form(None),
    note: str | None = Form(None),
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_vendor_or_admin(me)
    ensure_pdf(file)

    project = db.execute(
        select(Project).where(Project.id == project_id)
    ).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    vendor = db.execute(
        select(Vendor).where(Vendor.id == project.vendor_id)
    ).scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    if getattr(me, "role", None) == "penerima_dana" and str(vendor.user_id) != str(me.id):
        raise HTTPException(status_code=403, detail="Forbidden")

    generated_doc = None
    if generated_document_id:
        generated_doc = db.execute(
            select(GeneratedDocument).where(GeneratedDocument.id == generated_document_id)
        ).scalar_one_or_none()
        if not generated_doc:
            raise HTTPException(status_code=404, detail="Generated document not found")

    file_ext = ".pdf"
    file_name = f"vendor-project-signed-{project.id}{file_ext}"
    output_path = str(Path(BASE_SIGNED_DIR) / "vendor_project" / file_name)

    save_upload_file(file, output_path)
    file_size = os.path.getsize(output_path) if os.path.exists(output_path) else None

    existing = db.execute(
        select(SignedDocument).where(
            SignedDocument.module_type == "vendor_project",
            SignedDocument.module_ref_id == project.id,
        )
    ).scalar_one_or_none()

    if existing:
        existing.generated_document_id = generated_doc.id if generated_doc else existing.generated_document_id
        existing.uploaded_by_user_id = me.id
        existing.file_name = file_name
        existing.storage_path = output_path
        existing.mime_type = "application/pdf"
        existing.file_size = file_size
        existing.verification_status = "uploaded"
        existing.note = note
        existing.updated_at = utcnow()
        doc = existing
    else:
        doc = SignedDocument(
            module_type="vendor_project",
            module_ref_id=project.id,
            generated_document_id=generated_doc.id if generated_doc else None,
            uploaded_by_user_id=me.id,
            file_name=file_name,
            storage_path=output_path,
            mime_type="application/pdf",
            file_size=file_size,
            verification_status="uploaded",
            note=note,
        )
        db.add(doc)

    project.status = "submitted"
    project.submitted_at = utcnow()
    project.updated_at = utcnow()

    db.commit()
    db.refresh(doc)

    return {
        "message": "Signed vendor PDF uploaded successfully",
        "signed_document_id": str(doc.id),
        "module_type": doc.module_type,
        "module_ref_id": str(doc.module_ref_id),
        "file_name": doc.file_name,
        "storage_path": doc.storage_path,
        "file_size": doc.file_size,
        "verification_status": doc.verification_status,
        "project_status": project.status,
        "download_url": f"/signed-documents/{doc.id}/download",
    }


@router.post("/investor-funding/{funding_id}/upload")
def upload_investor_signed_pdf(
    funding_id: str,
    file: UploadFile = File(...),
    generated_document_id: str | None = Form(None),
    note: str | None = Form(None),
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_investor_or_admin(me)
    ensure_pdf(file)

    funding = db.execute(
        select(ProjectFunding).where(ProjectFunding.id == funding_id)
    ).scalar_one_or_none()
    if not funding:
        raise HTTPException(status_code=404, detail="Funding not found")

    if getattr(me, "role", None) == "investor" and str(funding.investor_user_id) != str(me.id):
        raise HTTPException(status_code=403, detail="Forbidden")

    generated_doc = None
    if generated_document_id:
        generated_doc = db.execute(
            select(GeneratedDocument).where(GeneratedDocument.id == generated_document_id)
        ).scalar_one_or_none()
        if not generated_doc:
            raise HTTPException(status_code=404, detail="Generated document not found")

    if funding.funding_status == "paid":
        raise HTTPException(status_code=422, detail="Funding already paid")

    file_name = f"investor-funding-signed-{funding.id}.pdf"
    output_path = str(Path(BASE_SIGNED_DIR) / "investor_funding" / file_name)

    save_upload_file(file, output_path)
    file_size = os.path.getsize(output_path) if os.path.exists(output_path) else None

    existing = db.execute(
        select(SignedDocument).where(
            SignedDocument.module_type == "investor_funding",
            SignedDocument.module_ref_id == funding.id,
        )
    ).scalar_one_or_none()

    if existing:
        existing.generated_document_id = generated_doc.id if generated_doc else existing.generated_document_id
        existing.uploaded_by_user_id = me.id
        existing.file_name = file_name
        existing.storage_path = output_path
        existing.mime_type = "application/pdf"
        existing.file_size = file_size
        existing.verification_status = "uploaded"
        existing.note = note
        existing.updated_at = utcnow()
        doc = existing
    else:
        doc = SignedDocument(
            module_type="investor_funding",
            module_ref_id=funding.id,
            generated_document_id=generated_doc.id if generated_doc else None,
            uploaded_by_user_id=me.id,
            file_name=file_name,
            storage_path=output_path,
            mime_type="application/pdf",
            file_size=file_size,
            verification_status="uploaded",
            note=note,
        )
        db.add(doc)

    # upload signed PDF => funding jadi reserved 24 jam
    reserve_funding_24h(funding)

    db.commit()
    db.refresh(doc)

    return {
        "message": "Signed investor PDF uploaded successfully",
        "signed_document_id": str(doc.id),
        "module_type": doc.module_type,
        "module_ref_id": str(doc.module_ref_id),
        "file_name": doc.file_name,
        "storage_path": doc.storage_path,
        "file_size": doc.file_size,
        "verification_status": doc.verification_status,
        "funding_status": funding.funding_status,
        "reserved_at": funding.reserved_at,
        "reservation_expires_at": funding.reservation_expires_at,
        "download_url": f"/signed-documents/{doc.id}/download",
    }


@router.get("")
def list_signed_documents(
    module_type: str | None = None,
    module_ref_id: str | None = None,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    stmt = select(SignedDocument)

    if module_type:
        stmt = stmt.where(SignedDocument.module_type == module_type)

    if module_ref_id:
        stmt = stmt.where(SignedDocument.module_ref_id == module_ref_id)

    rows = db.execute(stmt.order_by(SignedDocument.created_at.desc())).scalars().all()

    return [
        {
            "id": str(r.id),
            "module_type": r.module_type,
            "module_ref_id": str(r.module_ref_id),
            "generated_document_id": str(r.generated_document_id) if r.generated_document_id else None,
            "uploaded_by_user_id": str(r.uploaded_by_user_id) if r.uploaded_by_user_id else None,
            "file_name": r.file_name,
            "storage_path": r.storage_path,
            "mime_type": r.mime_type,
            "file_size": r.file_size,
            "verification_status": r.verification_status,
            "verified_by_user_id": str(r.verified_by_user_id) if r.verified_by_user_id else None,
            "verified_at": r.verified_at,
            "note": r.note,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
            "download_url": f"/signed-documents/{r.id}/download",
        }
        for r in rows
    ]


@router.get("/{signed_document_id}")
def get_signed_document_detail(
    signed_document_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    doc = db.execute(
        select(SignedDocument).where(SignedDocument.id == signed_document_id)
    ).scalar_one_or_none()

    if not doc:
        raise HTTPException(status_code=404, detail="Signed document not found")

    return {
        "id": str(doc.id),
        "module_type": doc.module_type,
        "module_ref_id": str(doc.module_ref_id),
        "generated_document_id": str(doc.generated_document_id) if doc.generated_document_id else None,
        "uploaded_by_user_id": str(doc.uploaded_by_user_id) if doc.uploaded_by_user_id else None,
        "file_name": doc.file_name,
        "storage_path": doc.storage_path,
        "mime_type": doc.mime_type,
        "file_size": doc.file_size,
        "verification_status": doc.verification_status,
        "verified_by_user_id": str(doc.verified_by_user_id) if doc.verified_by_user_id else None,
        "verified_at": doc.verified_at,
        "note": doc.note,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
        "download_url": f"/signed-documents/{doc.id}/download",
    }


@router.get("/{signed_document_id}/download")
def download_signed_document(
    signed_document_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    doc = db.execute(
        select(SignedDocument).where(SignedDocument.id == signed_document_id)
    ).scalar_one_or_none()

    if not doc:
        raise HTTPException(status_code=404, detail="Signed document not found")

    if not os.path.exists(doc.storage_path):
        raise HTTPException(status_code=404, detail="Signed file not found on storage")

    return FileResponse(
        path=doc.storage_path,
        media_type=doc.mime_type or "application/pdf",
        filename=doc.file_name,
    )