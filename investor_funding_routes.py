import os
import json
import uuid
from datetime import datetime, timezone, date as date_type
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from db import get_db
from models import User
from models_vendor_project import Project, Vendor
from models_investor import Investor, InvestorKycDocument, INVESTOR_KYC_DOCS
from models_admin_funding import ProjectFunding, PaymentTransaction
from models_documents import DocumentTemplate, GeneratedDocument, SignedDocument
from schemas_admin_funding import FundingCreateIn, MidtransSnapOut
from auth_routes import get_current_user
from midtrans_service import create_snap_transaction, get_transaction_status

router = APIRouter(prefix="/investor", tags=["investor"])

BASE_DOC_DIR    = os.getenv("DOCUMENT_STORAGE_DIR",        "storage/documents")
BASE_SIGNED_DIR = os.getenv("SIGNED_DOCUMENT_STORAGE_DIR", "storage/signed_documents")
KYC_UPLOAD_DIR  = os.getenv("INVESTOR_KYC_UPLOAD_DIR",     "uploads/investor_kyc")

os.makedirs(KYC_UPLOAD_DIR, exist_ok=True)

ALLOWED_KYC_MIME = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    # beberapa browser/OS kirim generic type ini
    "application/octet-stream",
    "binary/octet-stream",
}
MAX_KYC_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


# ─────────────────────────────────────────────
#  Schemas
# ─────────────────────────────────────────────

class InvestorUpsertIn(BaseModel):
    investor_type: Optional[str] = None   # individual | company

    # Shared
    full_name: Optional[str] = None
    email:     Optional[str] = None
    phone:     Optional[str] = None
    address:   Optional[str] = None

    # Individual only
    nik:         Optional[str] = None
    birth_date:  Optional[str] = None     # "YYYY-MM-DD"
    birth_place: Optional[str] = None
    gender:      Optional[str] = None     # male | female
    occupation:  Optional[str] = None
    npwp:        Optional[str] = None

    # Company only
    company_name: Optional[str] = None
    pic_name:     Optional[str] = None
    pic_position: Optional[str] = None
    npwp_company: Optional[str] = None
    nib:          Optional[str] = None

    # Bank (both types)
    bank_name:         Optional[str] = None
    bank_account_no:   Optional[str] = None
    bank_account_name: Optional[str] = None


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def require_investor(me: User):
    if getattr(me, "role", None) != "investor":
        raise HTTPException(status_code=403, detail="Investor only")


def utcnow():
    return datetime.now(timezone.utc)


def _serialize_investor(v: Investor) -> dict:
    return {
        "id":                str(v.id),
        "user_id":           str(v.user_id),
        "investor_code":     v.investor_code,
        "investor_type":     v.investor_type,
        "full_name":         v.full_name,
        "email":             v.email,
        "phone":             v.phone,
        "address":           v.address,
        "nik":               v.nik,
        "birth_date":        str(v.birth_date) if v.birth_date else None,
        "birth_place":       v.birth_place,
        "gender":            v.gender,
        "occupation":        v.occupation,
        "npwp":              v.npwp,
        "company_name":      v.company_name,
        "pic_name":          v.pic_name,
        "pic_position":      v.pic_position,
        "npwp_company":      v.npwp_company,
        "nib":               v.nib,
        "bank_name":         v.bank_name,
        "bank_account_no":   v.bank_account_no,
        "bank_account_name": v.bank_account_name,
        "kyc_status":        v.kyc_status,
        "kyc_note":          v.kyc_note,
        "created_at":        v.created_at.isoformat() if v.created_at else None,
        "updated_at":        v.updated_at.isoformat() if v.updated_at else None,
    }


def _get_or_create_investor(db: Session, me: User) -> Investor:
    v = db.execute(
        select(Investor).where(Investor.user_id == me.id)
    ).scalar_one_or_none()

    if not v:
        now = utcnow()
        v = Investor(
            user_id=me.id,
            investor_type="individual",
            email=getattr(me, "email", None),
            phone=getattr(me, "phone", None),
            kyc_status="unverified",
            created_at=now,
            updated_at=now,
        )
        db.add(v)
        db.commit()
        db.refresh(v)

    return v


def _required_docs(investor_type: str) -> set:
    return set(INVESTOR_KYC_DOCS.get(investor_type, []))


def _auto_set_pending(db: Session, investor: Investor) -> None:
    """Auto-transition unverified → pending when all required docs uploaded."""
    required = _required_docs(investor.investor_type)
    if not required:
        return
    uploaded = {
        r.doc_type
        for r in db.execute(
            select(InvestorKycDocument).where(
                InvestorKycDocument.investor_id == investor.id
            )
        ).scalars().all()
    }
    if required.issubset(uploaded) and investor.kyc_status == "unverified":
        investor.kyc_status = "pending"
        investor.updated_at = utcnow()


def render_template_html(template_html: str, payload: dict) -> str:
    html = template_html or ""
    for key, value in payload.items():
        html = html.replace("{{" + key + "}}", str(value if value is not None else ""))
    return html


def generate_pdf_fallback(html_content: str, output_path: str):
    from xhtml2pdf import pisa
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as f:
        result = pisa.CreatePDF(html_content, dest=f)
    if result.err:
        raise Exception("Failed to generate PDF from HTML")
    return output_path


def calc_expected_return(project: Project, amount: int):
    pct = float(project.fixed_return_pct or 0)
    ret = round((int(amount) * pct) / 100.0)
    return int(ret), int(amount) + int(ret)


def fmt_idr(n):
    try:
        return f"{int(n or 0):,}".replace(",", ".")
    except Exception:
        return "0"


def get_active_template(db: Session, module_type: str, template_code: str):
    return db.execute(
        select(DocumentTemplate).where(
            DocumentTemplate.module_type == module_type,
            DocumentTemplate.template_code == template_code,
        )
    ).scalar_one_or_none()


# ═════════════════════════════════════════════
#  PROFILE
# ═════════════════════════════════════════════

@router.get("/profile")
def get_investor_profile(
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """
    Return investor profile. Auto-creates blank profile if none exists.
    Also returns required_docs list based on current investor_type.
    """
    require_investor(me)
    v = _get_or_create_investor(db, me)
    return {
        **_serialize_investor(v),
        "required_docs": INVESTOR_KYC_DOCS.get(v.investor_type, []),
    }


@router.post("/profile")
def upsert_investor_profile(
    payload: InvestorUpsertIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """
    Create or update investor profile (upsert).
    Changing investor_type resets kyc_status → unverified
    because required KYC documents differ between types.
    """
    require_investor(me)

    v = db.execute(
        select(Investor).where(Investor.user_id == me.id)
    ).scalar_one_or_none()

    now = utcnow()

    def _parse_birth_date(raw: Optional[str]):
        if not raw:
            return None
        try:
            return date_type.fromisoformat(raw)
        except (ValueError, TypeError):
            return None

    if not v:
        v = Investor(
            user_id=me.id,
            investor_type=payload.investor_type or "individual",
            full_name=payload.full_name,
            email=payload.email or getattr(me, "email", None),
            phone=payload.phone or getattr(me, "phone", None),
            address=payload.address,
            nik=payload.nik,
            birth_date=_parse_birth_date(payload.birth_date),
            birth_place=payload.birth_place,
            gender=payload.gender,
            occupation=payload.occupation,
            npwp=payload.npwp,
            company_name=payload.company_name,
            pic_name=payload.pic_name,
            pic_position=payload.pic_position,
            npwp_company=payload.npwp_company,
            nib=payload.nib,
            bank_name=payload.bank_name,
            bank_account_no=payload.bank_account_no,
            bank_account_name=payload.bank_account_name,
            kyc_status="unverified",
            created_at=now,
            updated_at=now,
        )
        db.add(v)
    else:
        old_type = v.investor_type

        # Guard: never overwrite kyc_status via profile save
        safe = payload.model_dump(exclude_none=True)
        safe.pop("kyc_status", None)

        if "birth_date" in safe:
            safe["birth_date"] = _parse_birth_date(safe["birth_date"])

        for field, value in safe.items():
            setattr(v, field, value)

        # If investor_type changed → reset KYC (required docs change)
        new_type = payload.investor_type
        if new_type and new_type != old_type and v.kyc_status in ("unverified", "rejected"):
            v.kyc_status = "unverified"
            v.kyc_note = None

        # If investor was verified and updates their profile, reset KYC to pending
        # so admin re-verifies the updated data.
        if v.kyc_status == "verified":
            v.kyc_status = "pending"
            v.kyc_note = None

        v.updated_at = now

    db.commit()
    db.refresh(v)

    return {
        **_serialize_investor(v),
        "required_docs": INVESTOR_KYC_DOCS.get(v.investor_type, []),
    }


# ═════════════════════════════════════════════
#  KYC DOCUMENTS
# ═════════════════════════════════════════════

@router.get("/profile/kyc-documents")
def list_investor_kyc_documents(
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """List all uploaded KYC documents for the current investor."""
    require_investor(me)

    investor = db.execute(
        select(Investor).where(Investor.user_id == me.id)
    ).scalar_one_or_none()

    if not investor:
        return []

    docs = db.execute(
        select(InvestorKycDocument).where(
            InvestorKycDocument.investor_id == investor.id
        )
    ).scalars().all()

    return [
        {
            "id":         str(d.id),
            "doc_type":   d.doc_type,
            "file_name":  d.file_name,
            "mime":       d.mime,
            "file_size":  d.file_size,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in docs
    ]


@router.post("/profile/kyc-documents")
async def upload_investor_kyc_document(
    doc_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """
    Upload or replace a KYC document.
    doc_type must match the investor_type requirement:
      individual → ktp | npwp | selfie_ktp | buku_tabungan
      company    → akta_perusahaan | npwp | siup_nib | buku_tabungan | ktp_direksi
    Allowed formats: PDF, JPG, PNG (max 10 MB).
    After all required docs uploaded → kyc_status auto → pending.
    """
    require_investor(me)

    investor = db.execute(
        select(Investor).where(Investor.user_id == me.id)
    ).scalar_one_or_none()

    if not investor:
        raise HTTPException(
            status_code=404,
            detail="Profil investor belum dibuat. Lengkapi profil terlebih dahulu.",
        )

    if investor.kyc_status == "verified":
        raise HTTPException(
            status_code=400,
            detail="KYC sudah terverifikasi. Dokumen tidak bisa diubah.",
        )

    # Validate doc_type against ALL possible types (individual + company combined).
    # Hard block only if doc_type is completely unknown.
    # If user changed investor_type in the form but did not save yet, we auto-fix
    # the investor_type in DB so the upload still works.
    all_valid_doc_types = set(
        INVESTOR_KYC_DOCS.get("individual", []) + INVESTOR_KYC_DOCS.get("company", [])
    )
    if doc_type not in all_valid_doc_types:
        raise HTTPException(
            status_code=400,
            detail=f"doc_type '{doc_type}' tidak valid. Pilihan: {sorted(all_valid_doc_types)}",
        )

    # If doc_type belongs to a different investor_type than what is saved in DB,
    # auto-update investor_type (only when unverified — safe to change).
    expected = _required_docs(investor.investor_type)
    if doc_type not in expected and investor.kyc_status == "unverified":
        for itype, docs in INVESTOR_KYC_DOCS.items():
            if doc_type in docs:
                investor.investor_type = itype
                investor.updated_at = utcnow()
                db.flush()
                break

    content_type = file.content_type or ""
    if content_type not in ALLOWED_KYC_MIME:
        raise HTTPException(
            status_code=400,
            detail="Format file tidak didukung. Gunakan PDF, JPG, atau PNG.",
        )

    contents = await file.read()
    if len(contents) > MAX_KYC_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Ukuran file maksimal {MAX_KYC_FILE_SIZE // (1024 * 1024)} MB.",
        )

    # Delete old doc of same type (replace)
    old = db.execute(
        select(InvestorKycDocument).where(
            InvestorKycDocument.investor_id == investor.id,
            InvestorKycDocument.doc_type == doc_type,
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

    ext = os.path.splitext(file.filename or "")[1] or ".bin"
    file_id      = str(uuid.uuid4())
    investor_dir = os.path.join(KYC_UPLOAD_DIR, str(investor.id))
    os.makedirs(investor_dir, exist_ok=True)
    save_path = os.path.join(investor_dir, f"{file_id}{ext}")

    with open(save_path, "wb") as f:
        f.write(contents)

    doc = InvestorKycDocument(
        investor_id=investor.id,
        doc_type=doc_type,
        file_name=file.filename or f"{doc_type}{ext}",
        mime=content_type,
        file_size=len(contents),
        storage_path=save_path,
    )
    db.add(doc)
    db.flush()

    _auto_set_pending(db, investor)

    # Rejected → reset → re-check pending
    if investor.kyc_status == "rejected":
        investor.kyc_status = "unverified"
        investor.kyc_note   = None
        investor.updated_at = utcnow()
        db.flush()
        _auto_set_pending(db, investor)

    db.commit()
    db.refresh(doc)

    return {
        "id":         str(doc.id),
        "doc_type":   doc.doc_type,
        "file_name":  doc.file_name,
        "file_size":  doc.file_size,
        "kyc_status": investor.kyc_status,
        "message":    "Dokumen berhasil diupload.",
    }


@router.get("/profile/kyc-documents/{doc_id}/download")
def download_investor_kyc_document(
    doc_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """Download a KYC document (owner only)."""
    require_investor(me)

    investor = db.execute(
        select(Investor).where(Investor.user_id == me.id)
    ).scalar_one_or_none()
    if not investor:
        raise HTTPException(status_code=404, detail="Investor tidak ditemukan.")

    doc = db.execute(
        select(InvestorKycDocument).where(
            InvestorKycDocument.id == doc_id,
            InvestorKycDocument.investor_id == investor.id,
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


@router.delete("/profile/kyc-documents/{doc_id}")
def delete_investor_kyc_document(
    doc_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """
    Delete a KYC document (owner only).
    Blocked if kyc_status == verified.
    Reverts pending → unverified if docs become incomplete.
    """
    require_investor(me)

    investor = db.execute(
        select(Investor).where(Investor.user_id == me.id)
    ).scalar_one_or_none()
    if not investor:
        raise HTTPException(status_code=404, detail="Investor tidak ditemukan.")

    if investor.kyc_status == "verified":
        raise HTTPException(
            status_code=400,
            detail="KYC sudah terverifikasi. Dokumen tidak bisa dihapus.",
        )

    doc = db.execute(
        select(InvestorKycDocument).where(
            InvestorKycDocument.id == doc_id,
            InvestorKycDocument.investor_id == investor.id,
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

    remaining = {
        r.doc_type
        for r in db.execute(
            select(InvestorKycDocument).where(
                InvestorKycDocument.investor_id == investor.id
            )
        ).scalars().all()
    }
    required = _required_docs(investor.investor_type)
    if not required.issubset(remaining) and investor.kyc_status == "pending":
        investor.kyc_status = "unverified"
        investor.updated_at = utcnow()

    db.commit()
    return {"message": "Dokumen berhasil dihapus.", "kyc_status": investor.kyc_status}


# ═════════════════════════════════════════════
#  PROJECTS (open for funding)
# ═════════════════════════════════════════════

@router.get("/projects")
def investor_open_projects(
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_investor(me)

    # KYC guard
    investor = db.execute(
        select(Investor).where(Investor.user_id == me.id)
    ).scalar_one_or_none()
    if not investor or investor.kyc_status != "verified":
        raise HTTPException(
            status_code=403,
            detail="KYC investor belum terverifikasi. Lengkapi profil dan dokumen KYC terlebih dahulu.",
        )

    # Show all projects that are visible to investors (funding_open + funded/closed for reference)
    VISIBLE_STATUSES = ("funding_open", "funding_closed", "repayment_running", "completed")
    rows = db.execute(
        select(Project).where(Project.status.in_(VISIBLE_STATUSES)).order_by(Project.created_at.desc())
    ).scalars().all()

    result = []
    for p in rows:
        vendor = db.execute(select(Vendor).where(Vendor.id == p.vendor_id)).scalar_one_or_none()

        paid_amount = db.execute(
            select(func.coalesce(func.sum(ProjectFunding.funding_amount), 0)).where(
                ProjectFunding.project_id == p.id,
                ProjectFunding.funding_status == "paid",
            )
        ).scalar_one()

        remaining_amount = max(int(p.funding_needed or 0) - int(paid_amount or 0), 0)

        result.append({
            "id":                  str(p.id),
            "project_code":        p.project_code,
            "project_name":        p.project_name,
            "project_type":        p.project_type,
            "project_type_other":  p.project_type_other,
            "funding_needed":      int(p.funding_needed or 0),
            "funded_amount":       int(paid_amount or 0),
            "remaining_amount":    int(remaining_amount),
            "min_funding":         int(p.min_funding or 0),
            "tenor_months":        p.tenor_months,
            "fixed_return_pct":    float(p.fixed_return_pct or 0),
            "status":              p.status,
            "vendor_company_name": vendor.company_name if vendor else None,
        })
    return result


@router.get("/projects/{project_id}")
def investor_project_detail(
    project_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """Get detail of a single funding_open project for investor."""
    require_investor(me)

    investor = db.execute(
        select(Investor).where(Investor.user_id == me.id)
    ).scalar_one_or_none()
    if not investor or investor.kyc_status != "verified":
        raise HTTPException(status_code=403, detail="KYC investor belum terverifikasi.")

    try:
        project_uuid = uuid.UUID(str(project_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project id")

    VISIBLE_STATUSES = ("funding_open", "funding_closed", "repayment_running", "completed")
    p = db.execute(
        select(Project).where(
            Project.id == project_uuid,
            Project.status.in_(VISIBLE_STATUSES),
        )
    ).scalar_one_or_none()

    if not p:
        raise HTTPException(status_code=404, detail="Project tidak ditemukan.")

    vendor = db.execute(select(Vendor).where(Vendor.id == p.vendor_id)).scalar_one_or_none()

    from models_vendor_project import ProjectDocument
    documents = db.execute(
        select(ProjectDocument).where(ProjectDocument.project_id == p.id)
        .order_by(ProjectDocument.created_at.asc())
    ).scalars().all()

    paid_amount = db.execute(
        select(func.coalesce(func.sum(ProjectFunding.funding_amount), 0)).where(
            ProjectFunding.project_id == p.id,
            ProjectFunding.funding_status == "paid",
        )
    ).scalar_one() or 0

    remaining_amount = max(int(p.funding_needed or 0) - int(paid_amount or 0), 0)

    return {
        "id":                    str(p.id),
        "project_code":          p.project_code,
        "project_name":          p.project_name,
        "project_type":          p.project_type,
        "project_type_other":    p.project_type_other,
        "funding_needed":        int(p.funding_needed or 0),
        "funded_amount":         int(paid_amount or 0),
        "remaining_amount":      int(remaining_amount),
        "min_funding":           int(p.min_funding or 0),
        "tenor_months":          p.tenor_months,
        "fixed_return_pct":      float(p.fixed_return_pct or 0),
        "return_total_amount":   int(p.return_total_amount or 0),
        "payback_total_amount":  int(p.payback_total_amount or 0),
        "return_monthly_amount": int(p.return_monthly_amount or 0),
        "status":                p.status,
        "vendor_company_name":   vendor.company_name if vendor else None,
        "vendor": {
            "id":           str(vendor.id),
            "company_name": vendor.company_name,
            "vendor_type":  vendor.vendor_type,
            "pic_name":     vendor.pic_name,
            "email":        vendor.email,
            "phone":        vendor.phone,
            "address":      vendor.address,
        } if vendor else None,
        "documents": [
            {
                "id":        str(d.id),
                "doc_type":  d.doc_type,
                "file_name": d.file_name,
                "mime":      d.mime,
                "file_size": int(d.file_size or 0) if d.file_size is not None else None,
            }
            for d in documents
        ],
    }


@router.get("/projects/{project_id}/documents/{doc_id}/download")
def investor_download_project_document(
    project_id: str,
    doc_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """Download a project document for preview (investor only, project must be funding_open)."""
    require_investor(me)

    investor = db.execute(
        select(Investor).where(Investor.user_id == me.id)
    ).scalar_one_or_none()
    if not investor or investor.kyc_status != "verified":
        raise HTTPException(status_code=403, detail="KYC investor belum terverifikasi.")

    try:
        project_uuid = uuid.UUID(str(project_id))
        doc_uuid     = uuid.UUID(str(doc_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid id")

    VISIBLE_STATUSES = ("funding_open", "funding_closed", "repayment_running", "completed")
    p = db.execute(
        select(Project).where(
            Project.id == project_uuid,
            Project.status.in_(VISIBLE_STATUSES),
        )
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Project tidak ditemukan.")

    from models_vendor_project import ProjectDocument
    doc = db.execute(
        select(ProjectDocument).where(
            ProjectDocument.id == doc_uuid,
            ProjectDocument.project_id == p.id,
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


# ═════════════════════════════════════════════
#  FUNDINGS
# ═════════════════════════════════════════════

@router.post("/fundings/prepare-agreement")
def investor_prepare_agreement(
    payload: FundingCreateIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_investor(me)

    # KYC guard
    investor = db.execute(
        select(Investor).where(Investor.user_id == me.id)
    ).scalar_one_or_none()
    if not investor or investor.kyc_status != "verified":
        raise HTTPException(status_code=403, detail="KYC investor belum terverifikasi.")

    project = db.execute(
        select(Project).where(Project.id == payload.project_id)
    ).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status != "funding_open":
        raise HTTPException(status_code=422, detail="Project is not open for funding")
    if int(payload.funding_amount or 0) <= 0:
        raise HTTPException(status_code=422, detail="Funding amount must be greater than 0")

    vendor = db.execute(select(Vendor).where(Vendor.id == project.vendor_id)).scalar_one_or_none()
    expected_return, expected_total = calc_expected_return(project, payload.funding_amount)

    funding = ProjectFunding(
        id=uuid.uuid4(),
        project_id=project.id,
        investor_user_id=me.id,
        funding_amount=int(payload.funding_amount),
        expected_return_amount=expected_return,
        expected_total_back=expected_total,
        funding_status="draft",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(funding)
    db.flush()

    template = get_active_template(db, "investor_funding", "INVESTOR_FUNDING_AGREEMENT")
    if not template:
        raise HTTPException(status_code=404, detail="Investor agreement template not found")

    agreement_date   = datetime.now().strftime("%Y-%m-%d")
    agreement_number = f"INV-{str(funding.id)[:8].upper()}"

    render_payload = {
        "agreement_number":                 agreement_number,
        "agreement_date":                   agreement_date,
        "agreement_city":                   "Depok",
        "dispute_city":                     "Depok",
        "platform_name":                    "Fondofund",
        "platform_address":                 "Depok, Indonesia",
        "platform_representative_name":     "Admin Fondofund",
        "platform_representative_position": "Administrator",
        # Use investor profile name — more accurate than auth user name
        "investor_name":    investor.full_name or getattr(me, "name", None) or getattr(me, "email", None) or "Investor",
        "investor_address": investor.address or "-",
        "project_name":     project.project_name or project.project_code or "",
        "project_code":     project.project_code or "",
        "recipient_name":   vendor.company_name if vendor else "",
        "recipient_address":vendor.address if vendor else "",
        "funding_amount":         fmt_idr(payload.funding_amount),
        "expected_return_amount": fmt_idr(expected_return),
        "expected_total_back":    fmt_idr(expected_total),
        "tenor_months":           project.tenor_months or 0,
        "fixed_return_pct":       float(project.fixed_return_pct or 0),
    }

    rendered_html = render_template_html(template.content_html, render_payload)
    file_name     = f"investor-funding-agreement-{funding.id}.pdf"
    output_path   = str(Path(BASE_DOC_DIR) / "investor_funding" / file_name)

    generate_pdf_fallback(rendered_html, output_path)
    file_size = os.path.getsize(output_path) if os.path.exists(output_path) else None

    generated_doc = GeneratedDocument(
        id=uuid.uuid4(),
        template_id=template.id,
        module_type="investor_funding",
        module_ref_id=funding.id,
        file_name=file_name,
        storage_path=output_path,
        mime_type="application/pdf",
        file_size=file_size,
        rendered_html=rendered_html,
        render_payload=json.dumps(render_payload, ensure_ascii=False),
        document_status="generated",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(generated_doc)

    funding.funding_status = "agreement_generated"
    funding.updated_at     = utcnow()
    db.commit()

    return {
        "funding_id":             str(funding.id),
        "project_id":             str(project.id),
        "generated_document_id":  str(generated_doc.id),
        "generated_file_name":    generated_doc.file_name,
        "funding_status":         funding.funding_status,
        "funding_amount":         funding.funding_amount,
        "expected_return_amount": funding.expected_return_amount,
        "expected_total_back":    funding.expected_total_back,
    }


@router.post("/fundings/{funding_id}/upload-signed")
async def investor_upload_signed_document(
    funding_id: str,
    signed_file: UploadFile = File(...),
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_investor(me)

    funding = db.execute(
        select(ProjectFunding).where(ProjectFunding.id == funding_id)
    ).scalar_one_or_none()
    if not funding:
        raise HTTPException(status_code=404, detail="Funding not found")
    if str(funding.investor_user_id) != str(me.id):
        raise HTTPException(status_code=403, detail="Forbidden")

    generated_doc = db.execute(
        select(GeneratedDocument).where(
            GeneratedDocument.module_type == "investor_funding",
            GeneratedDocument.module_ref_id == funding.id,
        )
    ).scalar_one_or_none()
    if not generated_doc:
        raise HTTPException(status_code=404, detail="Generated agreement not found")

    if not signed_file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Signed file must be PDF")

    os.makedirs(str(Path(BASE_SIGNED_DIR) / "investor_funding"), exist_ok=True)
    ext       = Path(signed_file.filename).suffix or ".pdf"
    safe_name = f"signed-investor-funding-{funding.id}{ext}"
    save_path = str(Path(BASE_SIGNED_DIR) / "investor_funding" / safe_name)

    content = await signed_file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    signed_doc = SignedDocument(
        id=uuid.uuid4(),
        module_type="investor_funding",
        module_ref_id=funding.project_id,
        generated_document_id=generated_doc.id,
        uploaded_by_user_id=me.id,
        file_name=safe_name,
        storage_path=save_path,
        mime_type=signed_file.content_type or "application/pdf",
        file_size=len(content),
        verification_status="uploaded",
        note="Investor signed agreement uploaded",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(signed_doc)

    funding.funding_status = "signed_uploaded"
    funding.updated_at     = utcnow()
    db.commit()

    return {
        "message":        "Signed document uploaded",
        "funding_status": funding.funding_status,
        "signed_document": {
            "id":                  str(signed_doc.id),
            "file_name":           signed_doc.file_name,
            "mime_type":           signed_doc.mime_type,
            "file_size":           signed_doc.file_size,
            "verification_status": signed_doc.verification_status,
            "created_at":          signed_doc.created_at,
        },
    }


@router.get("/fundings/{funding_id}/signed-document")
def investor_get_signed_document(
    funding_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_investor(me)

    funding = db.execute(
        select(ProjectFunding).where(ProjectFunding.id == funding_id)
    ).scalar_one_or_none()
    if not funding:
        raise HTTPException(status_code=404, detail="Funding not found")
    if str(funding.investor_user_id) != str(me.id):
        raise HTTPException(status_code=403, detail="Forbidden")

    doc = db.execute(
        select(SignedDocument).where(
            SignedDocument.generated_document_id.in_(
                select(GeneratedDocument.id).where(
                    GeneratedDocument.module_type == "investor_funding",
                    GeneratedDocument.module_ref_id == funding.id,
                )
            )
        )
    ).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Signed document not found")

    return {
        "id":                  str(doc.id),
        "file_name":           doc.file_name,
        "mime_type":           doc.mime_type,
        "file_size":           doc.file_size,
        "verification_status": doc.verification_status,
        "created_at":          doc.created_at,
    }


@router.post("/fundings/{funding_id}/checkout", response_model=MidtransSnapOut)
def investor_checkout_funding(
    funding_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_investor(me)

    funding = db.execute(
        select(ProjectFunding).where(ProjectFunding.id == funding_id)
    ).scalar_one_or_none()
    if not funding:
        raise HTTPException(status_code=404, detail="Funding not found")
    if str(funding.investor_user_id) != str(me.id):
        raise HTTPException(status_code=403, detail="Forbidden")

    project = db.execute(
        select(Project).where(Project.id == funding.project_id)
    ).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    signed_doc = db.execute(
        select(SignedDocument).where(
            SignedDocument.generated_document_id.in_(
                select(GeneratedDocument.id).where(
                    GeneratedDocument.module_type == "investor_funding",
                    GeneratedDocument.module_ref_id == funding.id,
                )
            )
        )
    ).scalar_one_or_none()
    if not signed_doc:
        raise HTTPException(status_code=422, detail="Signed agreement is required before payment")

    order_id    = f"fund-{funding.id}"
    existing_tx = db.execute(
        select(PaymentTransaction).where(PaymentTransaction.order_id == order_id)
    ).scalar_one_or_none()

    if existing_tx and existing_tx.redirect_url:
        funding.funding_status = existing_tx.transaction_status or "pending_payment"
        funding.updated_at     = utcnow()
        db.commit()
        return MidtransSnapOut(
            funding_id=funding.id,
            order_id=order_id,
            redirect_url=existing_tx.redirect_url,
            token=existing_tx.snap_token,
            transaction_status=existing_tx.transaction_status,
        )

    investor = db.execute(
        select(Investor).where(Investor.user_id == me.id)
    ).scalar_one_or_none()

    status_code, raw_req, raw_res = create_snap_transaction(
        order_id=order_id,
        gross_amount=funding.funding_amount,
        first_name=(investor.full_name if investor else None) or getattr(me, "name", None) or "Investor",
        email=getattr(me, "email", None) or "investor@example.com",
    )

    tx = PaymentTransaction(
        id=uuid.uuid4(),
        project_id=project.id,
        funding_id=funding.id,
        investor_user_id=me.id,
        payment_for="investor_funding",
        provider="midtrans",
        order_id=order_id,
        gross_amount=funding.funding_amount,
        transaction_status=raw_res.get("transaction_status") or "pending_payment",
        payment_type=raw_res.get("payment_type"),
        redirect_url=raw_res.get("redirect_url"),
        snap_token=raw_res.get("token"),
        raw_request=json.dumps(raw_req),
        raw_response=json.dumps(raw_res),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(tx)

    funding.funding_status = "pending_payment"
    funding.updated_at     = utcnow()
    db.commit()

    return MidtransSnapOut(
        funding_id=funding.id,
        order_id=order_id,
        redirect_url=raw_res.get("redirect_url"),
        token=raw_res.get("token"),
        transaction_status=tx.transaction_status,
    )


@router.get("/fundings")
def investor_my_fundings(
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_investor(me)

    rows = db.execute(
        select(ProjectFunding)
        .where(ProjectFunding.investor_user_id == me.id)
        .order_by(ProjectFunding.created_at.desc())
    ).scalars().all()

    result = []
    for f in rows:
        project = db.execute(select(Project).where(Project.id == f.project_id)).scalar_one_or_none()
        vendor  = None
        if project:
            vendor = db.execute(select(Vendor).where(Vendor.id == project.vendor_id)).scalar_one_or_none()

        tx = db.execute(
            select(PaymentTransaction).where(PaymentTransaction.funding_id == f.id)
        ).scalar_one_or_none()

        signed_doc = db.execute(
            select(SignedDocument).where(
                SignedDocument.generated_document_id.in_(
                    select(GeneratedDocument.id).where(
                        GeneratedDocument.module_type == "investor_funding",
                        GeneratedDocument.module_ref_id == f.id,
                    )
                )
            )
        ).scalar_one_or_none()

        result.append({
            "id":                     str(f.id),
            "project_id":             str(f.project_id),
            "project_name":           project.project_name if project else None,
            "project_code":           project.project_code if project else None,
            "vendor_company_name":    vendor.company_name if vendor else None,
            "funding_amount":         f.funding_amount,
            "expected_return_amount": f.expected_return_amount,
            "expected_total_back":    f.expected_total_back,
            "funding_status":         f.funding_status,
            "paid_at":                f.paid_at,
            "created_at":             f.created_at,
            "order_id":               tx.order_id if tx else None,
            "signed_document": (
                {
                    "id":                  str(signed_doc.id),
                    "file_name":           signed_doc.file_name,
                    "verification_status": signed_doc.verification_status,
                    "created_at":          signed_doc.created_at,
                }
                if signed_doc else None
            ),
        })

    return result


@router.get("/fundings/sync-status")
def investor_sync_status(
    order_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_investor(me)

    tx = db.execute(
        select(PaymentTransaction).where(PaymentTransaction.order_id == order_id)
    ).scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if str(tx.investor_user_id) != str(me.id):
        raise HTTPException(status_code=403, detail="Forbidden")

    status_code, raw_res = get_transaction_status(order_id)
    transaction_status   = raw_res.get("transaction_status")

    tx.transaction_status = transaction_status
    tx.payment_type       = raw_res.get("payment_type")
    tx.settlement_time    = raw_res.get("settlement_time")
    tx.raw_response       = json.dumps(raw_res)
    tx.updated_at         = utcnow()

    funding = db.execute(
        select(ProjectFunding).where(ProjectFunding.id == tx.funding_id)
    ).scalar_one_or_none()

    if funding:
        if transaction_status in ["settlement", "capture"]:
            funding.funding_status = "paid"
            funding.paid_at        = utcnow()
        elif transaction_status in ["pending"]:
            funding.funding_status = "pending_payment"
        elif transaction_status in ["deny", "cancel", "expire", "failure"]:
            funding.funding_status = transaction_status
        funding.updated_at = utcnow()

    db.commit()

    return {
        "order_id":           order_id,
        "transaction_status": transaction_status,
        "funding_status":     funding.funding_status if funding else None,
    }