import os
import shutil
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import select

from db import get_db
from models import User
from models_vendor_project import Vendor, VendorKycDocument
from schemas_vendor_project import VendorUpsertIn, VendorOut
from auth_routes import get_current_user

router = APIRouter(prefix="/vendor", tags=["vendor"])

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads/kyc")
os.makedirs(UPLOAD_DIR, exist_ok=True)

KYC_DOC_TYPES = {"buku_tabungan", "npwp", "akta_perusahaan", "izin_usaha"}

ALLOWED_MIME = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "application/octet-stream",
    "binary/octet-stream",
}

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def _get_or_create_vendor(db: Session, me: User) -> Vendor:
    v = db.execute(
        select(Vendor).where(Vendor.user_id == me.id)
    ).scalar_one_or_none()

    if not v:
        now = datetime.now(timezone.utc)
        v = Vendor(
            user_id=me.id,
            vendor_type="company",
            email=getattr(me, "email", None),
            phone=getattr(me, "phone", None),
            pg_status="inactive",
            kyc_status="unverified",
            created_at=now,
            updated_at=now,
        )
        db.add(v)
        db.commit()
        db.refresh(v)

    return v


def _auto_set_pending(db: Session, vendor: Vendor) -> None:
    """
    If all 4 KYC docs are uploaded and status is still unverified,
    automatically move to pending so admin knows to review.
    """
    uploaded_types = {
        r.doc_type
        for r in db.execute(
            select(VendorKycDocument).where(
                VendorKycDocument.vendor_id == vendor.id
            )
        ).scalars().all()
    }
    if KYC_DOC_TYPES.issubset(uploaded_types) and vendor.kyc_status == "unverified":
        vendor.kyc_status = "pending"
        vendor.updated_at = datetime.now(timezone.utc)


# ─────────────────────────────────────────────
#  GET /vendor/me
# ─────────────────────────────────────────────

@router.get("/me", response_model=VendorOut)
def get_vendor_me(
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    v = _get_or_create_vendor(db, me)
    return v


# ─────────────────────────────────────────────
#  POST /vendor/me  (upsert profile)
# ─────────────────────────────────────────────

@router.post("/me", response_model=VendorOut)
def upsert_vendor_me(
    payload: VendorUpsertIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """
    Create or update vendor profile (upsert).
    If vendor was verified and updates profile -> reset KYC to pending
    so admin re-verifies the updated data.
    """
    v = db.execute(
        select(Vendor).where(Vendor.user_id == me.id)
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)

    if not v:
        v = Vendor(
            user_id=me.id,
            vendor_type=payload.vendor_type,
            vendor_code=payload.vendor_code,
            company_name=payload.company_name,
            pic_name=payload.pic_name,
            email=payload.email or getattr(me, "email", None),
            phone=payload.phone or getattr(me, "phone", None),
            npwp=payload.npwp,
            nik=payload.nik,
            address=payload.address,
            bank_name=payload.bank_name,
            bank_account_no=payload.bank_account_no,
            bank_account_name=payload.bank_account_name,
            pg_provider=payload.pg_provider,
            pg_merchant_id=payload.pg_merchant_id,
            pg_customer_id=payload.pg_customer_id,
            pg_va_bank=payload.pg_va_bank,
            pg_va_number=payload.pg_va_number,
            pg_status=payload.pg_status or "inactive",
            kyc_status="unverified",
            created_at=now,
            updated_at=now,
        )
        db.add(v)
    else:
        old_kyc_status = v.kyc_status

        # Never let profile save overwrite kyc_status directly
        safe_fields = payload.model_dump(exclude_none=True)
        safe_fields.pop("kyc_status", None)

        for field, value in safe_fields.items():
            setattr(v, field, value)
        v.updated_at = now

        # If vendor was verified and updates their profile, reset KYC to pending
        if old_kyc_status == "verified":
            v.kyc_status = "pending"
            v.kyc_note = None

    db.commit()
    db.refresh(v)
    return v


# ─────────────────────────────────────────────
#  GET /vendor/me/kyc-documents
# ─────────────────────────────────────────────

@router.get("/me/kyc-documents")
def list_kyc_documents(
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    vendor = db.execute(
        select(Vendor).where(Vendor.user_id == me.id)
    ).scalar_one_or_none()

    if not vendor:
        return []

    docs = db.execute(
        select(VendorKycDocument).where(
            VendorKycDocument.vendor_id == vendor.id
        )
    ).scalars().all()

    return [
        {
            "id":         str(d.id),
            "doc_type":   d.doc_type,
            "file_name":  d.file_name,
            "mime":       d.mime,
            "file_size":  d.file_size,
            "created_at": d.created_at.isoformat(),
        }
        for d in docs
    ]


# ─────────────────────────────────────────────
#  POST /vendor/me/kyc-documents  (upload / replace)
# ─────────────────────────────────────────────

@router.post("/me/kyc-documents")
async def upload_kyc_document(
    doc_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """
    Upload or replace a single KYC document.
    - doc_type must be one of: buku_tabungan | npwp | akta_perusahaan | izin_usaha
    - Allowed formats: PDF, JPG, PNG (max 10 MB)
    - Upload is allowed even when kyc_status == verified (profile change resets to pending first)
    - After upload, if all 4 types present and status is unverified -> auto set to pending
    """
    if doc_type not in KYC_DOC_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"doc_type tidak valid. Pilihan: {sorted(KYC_DOC_TYPES)}",
        )

    content_type = (file.content_type or "").lower()
    # Also check extension as fallback
    ext = os.path.splitext(file.filename or "")[1].lower()
    allowed_exts = {".pdf", ".jpg", ".jpeg", ".png"}
    if content_type not in ALLOWED_MIME and ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail="Format file tidak didukung. Gunakan PDF, JPG, atau PNG.",
        )

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Ukuran file maksimal {MAX_FILE_SIZE // (1024 * 1024)} MB.",
        )

    vendor = db.execute(
        select(Vendor).where(Vendor.user_id == me.id)
    ).scalar_one_or_none()

    if not vendor:
        raise HTTPException(
            status_code=404,
            detail="Profil vendor belum dibuat. Lengkapi profil terlebih dahulu.",
        )

    # NOTE: Upload is NOT blocked when kyc_status == "verified".
    # Profile update already resets verified -> pending.
    # Doc upload should always be allowed so vendor can update their docs.

    # Delete old file for same doc_type (replace)
    old = db.execute(
        select(VendorKycDocument).where(
            VendorKycDocument.vendor_id == vendor.id,
            VendorKycDocument.doc_type == doc_type,
        )
    ).scalar_one_or_none()

    if old:
        if old.storage_path and os.path.exists(old.storage_path):
            try:
                os.remove(old.storage_path)
            except OSError:
                pass
        db.delete(old)
        db.flush()

    # Save new file
    file_ext  = ext or ".bin"
    file_id   = str(uuid4())
    vendor_dir = os.path.join(UPLOAD_DIR, str(vendor.id))
    os.makedirs(vendor_dir, exist_ok=True)
    save_path = os.path.join(vendor_dir, f"{file_id}{file_ext}")

    with open(save_path, "wb") as f:
        f.write(contents)

    doc = VendorKycDocument(
        vendor_id=vendor.id,
        doc_type=doc_type,
        file_name=file.filename or f"{doc_type}{file_ext}",
        mime=content_type or "application/octet-stream",
        file_size=len(contents),
        storage_path=save_path,
    )
    db.add(doc)
    db.flush()

    # If status was rejected -> reset to unverified first
    if vendor.kyc_status == "rejected":
        vendor.kyc_status = "unverified"
        vendor.kyc_note = None
        vendor.updated_at = datetime.now(timezone.utc)
        db.flush()

    # Auto-set pending if all 4 docs now present and status is unverified
    _auto_set_pending(db, vendor)

    db.commit()
    db.refresh(doc)

    return {
        "id":         str(doc.id),
        "doc_type":   doc.doc_type,
        "file_name":  doc.file_name,
        "file_size":  doc.file_size,
        "kyc_status": vendor.kyc_status,
        "message":    "Dokumen berhasil diupload.",
    }


# ─────────────────────────────────────────────
#  GET /vendor/me/kyc-documents/{doc_id}/download
# ─────────────────────────────────────────────

@router.get("/me/kyc-documents/{doc_id}/download")
def download_kyc_document(
    doc_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    vendor = db.execute(
        select(Vendor).where(Vendor.user_id == me.id)
    ).scalar_one_or_none()

    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor tidak ditemukan.")

    doc = db.execute(
        select(VendorKycDocument).where(
            VendorKycDocument.id == doc_id,
            VendorKycDocument.vendor_id == vendor.id,
        )
    ).scalar_one_or_none()

    if not doc:
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")

    if not doc.storage_path or not os.path.exists(doc.storage_path):
        raise HTTPException(status_code=404, detail="File tidak ditemukan di storage.")

    return FileResponse(
        path=doc.storage_path,
        media_type=doc.mime or "application/octet-stream",
        filename=doc.file_name,
    )


# ─────────────────────────────────────────────
#  DELETE /vendor/me/kyc-documents/{doc_id}
# ─────────────────────────────────────────────

@router.delete("/me/kyc-documents/{doc_id}")
def delete_kyc_document(
    doc_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """
    Delete a KYC document (owner only).
    NOTE: No longer blocked when kyc_status == verified.
    Deleting a doc when verified resets status to pending (incomplete docs).
    """
    vendor = db.execute(
        select(Vendor).where(Vendor.user_id == me.id)
    ).scalar_one_or_none()

    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor tidak ditemukan.")

    # NOTE: removed verified block — vendor can always manage their docs

    doc = db.execute(
        select(VendorKycDocument).where(
            VendorKycDocument.id == doc_id,
            VendorKycDocument.vendor_id == vendor.id,
        )
    ).scalar_one_or_none()

    if not doc:
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")

    if doc.storage_path and os.path.exists(doc.storage_path):
        try:
            os.remove(doc.storage_path)
        except OSError:
            pass

    db.delete(doc)
    db.flush()

    # Check remaining docs after deletion
    remaining = {
        r.doc_type
        for r in db.execute(
            select(VendorKycDocument).where(
                VendorKycDocument.vendor_id == vendor.id
            )
        ).scalars().all()
    }

    # If docs no longer complete, reset status to unverified
    if not KYC_DOC_TYPES.issubset(remaining):
        if vendor.kyc_status in ("pending", "verified"):
            vendor.kyc_status = "unverified"
            vendor.kyc_note   = None
            vendor.updated_at = datetime.now(timezone.utc)

    db.commit()

    return {"message": "Dokumen berhasil dihapus.", "kyc_status": vendor.kyc_status}