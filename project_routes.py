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


def compute_returns(funding_needed: int, fixed_return_pct: float, tenor_months: int):
    return_total = round((funding_needed * float(fixed_return_pct)) / 100.0)
    payback_total = int(funding_needed) + int(return_total)
    return_monthly = round(return_total / tenor_months) if tenor_months else 0
    return int(return_total), int(payback_total), int(return_monthly)


@router.post("/upload", response_model=ProjectOut)
async def create_project_with_upload(
    projectNameOrCode: str = Form(...),
    recipientName: str = Form(...),
    projectType: str = Form(...),
    projectTypeOther: str | None = Form(None),
    fundingNeeded: int = Form(...),
    minFunding: int | None = Form(None),
    tenorMonths: int = Form(...),
    fixedReturnPct: float = Form(...),

    st1: bool = Form(...),
    st2: bool = Form(...),
    st3: bool = Form(...),
    st4: bool = Form(...),
    st5: bool = Form(...),

    ec1: bool = Form(...),
    ec2: bool = Form(...),
    ec3: bool = Form(...),
    submissionDate: str = Form(...),
    signerName: str = Form(...),
    otpOrESign: str = Form(...),

    collectionMechanismAccepted: bool = Form(True),

    proposalFile: UploadFile = File(...),
    rabFile: UploadFile = File(...),
    otherFile: UploadFile | None = File(None),

    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
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

    if projectType == "lainnya" and not (projectTypeOther or "").strip():
        raise HTTPException(status_code=422, detail="projectTypeOther is required when projectType is 'lainnya'")

    if fundingNeeded <= 0:
        raise HTTPException(status_code=422, detail="fundingNeeded must be > 0")

    if tenorMonths <= 0:
        raise HTTPException(status_code=422, detail="tenorMonths must be > 0")

    if not all([st1, st2, st3, st4, st5]):
        raise HTTPException(status_code=422, detail="All statements must be checked")

    if not all([ec1, ec2, ec3]):
        raise HTTPException(status_code=422, detail="All electronic consents must be checked")

    return_total, payback_total, return_monthly = compute_returns(
        fundingNeeded, fixedReturnPct, tenorMonths
    )

    now = datetime.now(timezone.utc)

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

    proposal_meta = await save_upload_file(str(p.id), "proposal", proposalFile)
    rab_meta = await save_upload_file(str(p.id), "rab", rabFile)

    db.add(ProjectDocument(
        project_id=p.id,
        doc_type="proposal",
        file_name=proposal_meta["file_name"],
        mime=proposal_meta["mime"],
        file_size=proposal_meta["file_size"],
        storage_path=proposal_meta["storage_path"],
        created_at=now,
    ))

    db.add(ProjectDocument(
        project_id=p.id,
        doc_type="rab",
        file_name=rab_meta["file_name"],
        mime=rab_meta["mime"],
        file_size=rab_meta["file_size"],
        storage_path=rab_meta["storage_path"],
        created_at=now,
    ))

    if otherFile and otherFile.filename:
        other_meta = await save_upload_file(str(p.id), "lainnya", otherFile)
        db.add(ProjectDocument(
            project_id=p.id,
            doc_type="lainnya",
            file_name=other_meta["file_name"],
            mime=other_meta["mime"],
            file_size=other_meta["file_size"],
            storage_path=other_meta["storage_path"],
            created_at=now,
        ))

    db.add(ProjectConsent(
        project_id=p.id,
        st1=st1,
        st2=st2,
        st3=st3,
        st4=st4,
        st5=st5,
        ec1=ec1,
        ec2=ec2,
        ec3=ec3,
        submission_date=date.fromisoformat(submissionDate),
        signer_name=signerName,
        otp_or_esign=otpOrESign,
        collection_mechanism_accepted=collectionMechanismAccepted,
        accepted_at=now if collectionMechanismAccepted else None,
        created_at=now,
    ))

    db.commit()
    db.refresh(p)
    return p


@router.get("/my", response_model=list[ProjectOut])
def list_my_projects(
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    vendor = db.execute(select(Vendor).where(Vendor.user_id == me.id)).scalar_one_or_none()
    if not vendor:
        return []

    rows = db.execute(
        select(Project).where(Project.vendor_id == vendor.id).order_by(Project.created_at.desc())
    ).scalars().all()
    return rows


@router.get("/{project_id}", response_model=ProjectOut)
def get_project_detail(
    project_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    p = db.execute(select(Project).where(Project.id == project_id)).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    if getattr(me, "role", None) != "admin":
        vendor = db.execute(select(Vendor).where(Vendor.user_id == me.id)).scalar_one_or_none()
        if not vendor or str(p.vendor_id) != str(vendor.id):
            raise HTTPException(status_code=403, detail="Forbidden")

    return p


@router.get("/{project_id}/documents")
def list_project_documents(
    project_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
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
            "id": str(d.id),
            "doc_type": d.doc_type,
            "file_name": d.file_name,
            "mime": d.mime,
            "file_size": d.file_size,
            "storage_path": d.storage_path,
            "url": f"/projects/documents/{d.id}/download",
        }
        for d in docs
    ]


@router.get("/documents/{document_id}/download")
def download_document(
    document_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    doc = db.execute(select(ProjectDocument).where(ProjectDocument.id == document_id)).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    project = db.execute(select(Project).where(Project.id == doc.project_id)).scalar_one_or_none()
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