import os
from datetime import datetime, timezone, date

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import select

from db import get_db
from models import User
from models_vendor_project import Vendor, Project, ProjectDocument, ProjectConsent
from schemas_vendor_project import ProjectOut
from auth_routes import get_current_user
from file_storage import save_upload_file

router = APIRouter(prefix="/projects", tags=["projects"])

MAX_DOC_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_MIME = {"application/pdf"}
ALLOWED_EXT  = {".pdf"}


def _validate_doc_file(file: UploadFile, label: str):
    """Raise 422 if file is not PDF or exceeds 10 MB."""
    if not file or not file.filename:
        return
    ext  = os.path.splitext(file.filename.lower())[1]
    mime = (file.content_type or "").lower()
    if ext not in ALLOWED_EXT and mime not in ALLOWED_MIME:
        raise HTTPException(
            status_code=422,
            detail=f"{label}: hanya file PDF yang diperbolehkan (diterima: {file.filename}).",
        )
    # file.size may be None for streaming uploads — we check after reading in route


def compute_returns(funding_needed: int, fixed_return_pct: float, tenor_months: int):
    return_total   = round((funding_needed * float(fixed_return_pct)) / 100.0)
    payback_total  = int(funding_needed) + int(return_total)
    return_monthly = round(return_total / tenor_months) if tenor_months else 0
    return int(return_total), int(payback_total), int(return_monthly)


@router.post("/upload", response_model=ProjectOut)
async def create_project_with_upload(
    projectNameOrCode: str  = Form(...),
    recipientName:     str  = Form(...),
    projectType:       str  = Form(...),
    projectTypeOther:  str | None = Form(None),
    fundingNeeded:     int  = Form(...),
    minFunding:        int | None = Form(None),
    tenorMonths:       int  = Form(...),
    fixedReturnPct:    float = Form(...),

    st1: bool = Form(...),
    st2: bool = Form(...),
    st3: bool = Form(...),
    st4: bool = Form(...),
    st5: bool = Form(...),

    ec1: bool = Form(...),
    ec2: bool = Form(...),
    ec3: bool = Form(...),
    submissionDate: str = Form(...),
    signerName:     str = Form(...),

    collectionMechanismAccepted: bool = Form(True),

    proposalFile: UploadFile       = File(...),
    rabFile:      UploadFile       = File(...),
    otherFile:    UploadFile | None = File(None),

    db: Session = Depends(get_db),
    me: User    = Depends(get_current_user),
):
    # ── Vendor guard ───────────────────────────────────────────────────
    vendor = db.execute(select(Vendor).where(Vendor.user_id == me.id)).scalar_one_or_none()
    if not vendor:
        vendor = Vendor(
            user_id=me.id,
            vendor_type="company",
            company_name=getattr(me, "name", None) or recipientName,
            email=getattr(me, "email", None),
            phone=getattr(me, "phone", None),
            kyc_status="unverified",
            pg_status="inactive",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(vendor)
        db.commit()
        db.refresh(vendor)

    # ── Basic validation ───────────────────────────────────────────────
    if projectType == "lainnya" and not (projectTypeOther or "").strip():
        raise HTTPException(status_code=422, detail="projectTypeOther is required when projectType is 'lainnya'")

    if fundingNeeded <= 0:
        raise HTTPException(status_code=422, detail="fundingNeeded must be > 0")

    if tenorMonths <= 0:
        raise HTTPException(status_code=422, detail="tenorMonths must be > 0")

    if not all([st1, st2, st3, st4, st5]):
        raise HTTPException(status_code=422, detail="All statements (C) must be checked")

    if not all([ec1, ec2, ec3]):
        raise HTTPException(status_code=422, detail="All electronic consents (D) must be checked")

    # ── File validation — PDF only, ≤ 10 MB ───────────────────────────
    for f, label in [(proposalFile, "Surat Penunjukan + SPK"), (rabFile, "RAB")]:
        _validate_doc_file(f, label)

    if otherFile and otherFile.filename:
        _validate_doc_file(otherFile, "Dokumen Pendukung")

    # Read files into memory to check size
    proposal_content = await proposalFile.read()
    rab_content      = await rabFile.read()
    other_content    = await otherFile.read() if otherFile and otherFile.filename else None

    for content, label in [
        (proposal_content, "Surat Penunjukan + SPK"),
        (rab_content,      "RAB"),
    ]:
        if len(content) > MAX_DOC_SIZE:
            raise HTTPException(
                status_code=422,
                detail=f"{label}: ukuran file melebihi batas 10 MB ({len(content) // (1024*1024)} MB).",
            )

    if other_content and len(other_content) > MAX_DOC_SIZE:
        raise HTTPException(
            status_code=422,
            detail=f"Dokumen Pendukung: ukuran file melebihi batas 10 MB ({len(other_content) // (1024*1024)} MB).",
        )

    # ── Compute returns ────────────────────────────────────────────────
    return_total, payback_total, return_monthly = compute_returns(
        fundingNeeded, fixedReturnPct, tenorMonths
    )

    now = datetime.now(timezone.utc)

    # ── Create project ─────────────────────────────────────────────────
    p = Project(
        vendor_id=vendor.id,
        project_code=projectNameOrCode.strip(),
        project_name=projectNameOrCode.strip(),
        project_type=projectType,
        project_type_other=(projectTypeOther or "").strip() if projectType == "lainnya" else None,
        funding_needed=int(fundingNeeded),
        min_funding=int(minFunding) if minFunding is not None else None,
        tenor_months=int(tenorMonths),
        fixed_return_pct=float(fixedReturnPct),
        return_total_amount=return_total,
        payback_total_amount=payback_total,
        return_monthly_amount=return_monthly,
        status="ongoing",
        submitted_at=now,
        created_by_user_id=me.id,
        created_at=now,
        updated_at=now,
    )
    db.add(p)
    db.flush()

    # ── Save documents ─────────────────────────────────────────────────
    # Reset seek position before saving (we already read the content for validation)
    import io
    proposalFile.file = io.BytesIO(proposal_content)
    rabFile.file      = io.BytesIO(rab_content)

    proposal_meta = await save_upload_file(str(p.id), "proposal", proposalFile)
    rab_meta      = await save_upload_file(str(p.id), "rab",      rabFile)

    db.add(ProjectDocument(
        project_id=p.id, doc_type="proposal",
        file_name=proposal_meta["file_name"], mime=proposal_meta["mime"],
        file_size=proposal_meta["file_size"], storage_path=proposal_meta["storage_path"],
        created_at=now,
    ))
    db.add(ProjectDocument(
        project_id=p.id, doc_type="rab",
        file_name=rab_meta["file_name"], mime=rab_meta["mime"],
        file_size=rab_meta["file_size"], storage_path=rab_meta["storage_path"],
        created_at=now,
    ))

    if other_content and otherFile and otherFile.filename:
        otherFile.file = io.BytesIO(other_content)
        other_meta = await save_upload_file(str(p.id), "lainnya", otherFile)
        db.add(ProjectDocument(
            project_id=p.id, doc_type="lainnya",
            file_name=other_meta["file_name"], mime=other_meta["mime"],
            file_size=other_meta["file_size"], storage_path=other_meta["storage_path"],
            created_at=now,
        ))

    # ── Consent ───────────────────────────────────────────────────────
    db.add(ProjectConsent(
        project_id=p.id,
        st1=st1, st2=st2, st3=st3, st4=st4, st5=st5,
        ec1=ec1, ec2=ec2, ec3=ec3,
        submission_date=date.fromisoformat(submissionDate),
        signer_name=signerName,
        otp_or_esign="",                            # no longer required
        collection_mechanism_accepted=collectionMechanismAccepted,
        accepted_at=now if collectionMechanismAccepted else None,
        created_at=now,
    ))

    db.commit()
    db.refresh(p)
    return p


# ─────────────────────────────────────────────
#  My projects
# ─────────────────────────────────────────────

@router.get("/my")
def list_my_projects(
    db: Session = Depends(get_db),
    me: User    = Depends(get_current_user),
):
    """List all projects by the current vendor, enriched with funding stats."""
    from models_admin_funding import ProjectFunding
    from sqlalchemy import func

    vendor = db.execute(select(Vendor).where(Vendor.user_id == me.id)).scalar_one_or_none()
    if not vendor:
        return []

    rows = db.execute(
        select(Project).where(Project.vendor_id == vendor.id).order_by(Project.created_at.desc())
    ).scalars().all()

    result = []
    for p in rows:
        funded_amount = db.execute(
            select(func.coalesce(func.sum(ProjectFunding.funding_amount), 0)).where(
                ProjectFunding.project_id == p.id,
                ProjectFunding.funding_status == "paid",
            )
        ).scalar_one() or 0

        investor_count = db.execute(
            select(func.count()).select_from(ProjectFunding).where(
                ProjectFunding.project_id == p.id,
                ProjectFunding.funding_status == "paid",
            )
        ).scalar_one() or 0

        result.append({
            "id":                    str(p.id),
            "project_code":          p.project_code,
            "project_name":          p.project_name,
            "project_type":          p.project_type,
            "project_type_other":    p.project_type_other,
            "funding_needed":        int(p.funding_needed or 0),
            "min_funding":           int(p.min_funding or 0) if p.min_funding else None,
            "tenor_months":          p.tenor_months,
            "fixed_return_pct":      float(p.fixed_return_pct or 0),
            "return_total_amount":   int(p.return_total_amount or 0),
            "payback_total_amount":  int(p.payback_total_amount or 0),
            "return_monthly_amount": int(p.return_monthly_amount or 0),
            "status":                p.status,
            "submitted_at":          p.submitted_at,
            "created_at":            p.created_at,
            "updated_at":            p.updated_at,
            # funding enrichment
            "funded_amount":         int(funded_amount),
            "remaining_amount":      max(int(p.funding_needed or 0) - int(funded_amount), 0),
            "investor_count":        int(investor_count),
        })

    return result


# ─────────────────────────────────────────────
#  Project detail
# ─────────────────────────────────────────────

@router.get("/{project_id}", response_model=ProjectOut)
def get_project_detail(
    project_id: str,
    db: Session = Depends(get_db),
    me: User    = Depends(get_current_user),
):
    p = db.execute(select(Project).where(Project.id == project_id)).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    if getattr(me, "role", None) != "admin":
        vendor = db.execute(select(Vendor).where(Vendor.user_id == me.id)).scalar_one_or_none()
        if not vendor or str(p.vendor_id) != str(vendor.id):
            raise HTTPException(status_code=403, detail="Forbidden")

    return p


# ─────────────────────────────────────────────
#  Documents list
# ─────────────────────────────────────────────

@router.get("/{project_id}/documents")
def list_project_documents(
    project_id: str,
    db: Session = Depends(get_db),
    me: User    = Depends(get_current_user),
):
    p = db.execute(select(Project).where(Project.id == project_id)).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    if getattr(me, "role", None) != "admin":
        vendor = db.execute(select(Vendor).where(Vendor.user_id == me.id)).scalar_one_or_none()
        if not vendor or str(p.vendor_id) != str(vendor.id):
            raise HTTPException(status_code=403, detail="Forbidden")

    docs = db.execute(
        select(ProjectDocument).where(ProjectDocument.project_id == p.id)
    ).scalars().all()

    return [
        {
            "id":           str(d.id),
            "doc_type":     d.doc_type,
            "file_name":    d.file_name,
            "mime":         d.mime,
            "file_size":    d.file_size,
            "storage_path": d.storage_path,
            "url":          f"/projects/documents/{d.id}/download",
        }
        for d in docs
    ]


# ─────────────────────────────────────────────
#  Download document
# ─────────────────────────────────────────────

@router.get("/documents/{document_id}/download")
def download_document(
    document_id: str,
    db: Session  = Depends(get_db),
    me: User     = Depends(get_current_user),
):
    doc = db.execute(
        select(ProjectDocument).where(ProjectDocument.id == document_id)
    ).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    project = db.execute(
        select(Project).where(Project.id == doc.project_id)
    ).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if getattr(me, "role", None) != "admin":
        vendor = db.execute(select(Vendor).where(Vendor.user_id == me.id)).scalar_one_or_none()
        if not vendor or str(project.vendor_id) != str(vendor.id):
            raise HTTPException(status_code=403, detail="Forbidden")

    abs_path = os.path.abspath(doc.storage_path)
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="Stored file not found")

    return FileResponse(
        path=abs_path,
        filename=doc.file_name,
        media_type=doc.mime or "application/octet-stream",
    )