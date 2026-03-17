from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime, timezone
import uuid

from db import get_db
from models import User
from models_vendor_project import Project, Vendor
from models_admin_funding import SignatureRequest
from auth_routes import get_current_user
from signature_service import (
    build_vendor_project_document_html,
    create_signature_request,
    mark_signature_signed,
)

router = APIRouter(prefix="/vendor-signatures", tags=["vendor_signatures"])


def require_vendor(me: User):
    if getattr(me, "role", None) != "penerima_dana":
        raise HTTPException(status_code=403, detail="Vendor only")


@router.post("/project/{project_id}/prepare")
def prepare_vendor_project_signature(
    project_id: str,
    payload: dict,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_vendor(me)

    project_uuid = uuid.UUID(project_id)

    p = db.execute(
        select(Project).where(Project.id == project_uuid)
    ).scalar_one_or_none()

    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    vendor = db.execute(
        select(Vendor).where(Vendor.id == p.vendor_id)
    ).scalar_one_or_none()

    if not vendor or str(vendor.user_id) != str(me.id):
        raise HTTPException(status_code=403, detail="Forbidden")

    html = build_vendor_project_document_html(
        project_name=p.project_name or p.project_code or "-",
        company_name=vendor.company_name or "-",
        funding_needed=int(p.funding_needed or 0),
        tenor_months=int(p.tenor_months or 0),
        fixed_return_pct=float(p.fixed_return_pct or 0),
    )

    sig = create_signature_request(
        module_type="vendor_project",
        module_ref_id=p.id,
        user_id=me.id,
        vendor_id=vendor.id,
        project_id=p.id,
        signer_name=payload.get("signer_name"),
        signer_email=payload.get("signer_email"),
        signer_phone=payload.get("signer_phone"),
        document_name=f"Pengajuan Vendor - {p.project_name or p.project_code}",
        document_html=html,
    )
    db.add(sig)

    p.status = "waiting_vendor_signature"
    p.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(sig)

    return {
        "signature_request_id": str(sig.id),
        "project_id": str(p.id),
        "status": sig.status,
        "document_name": sig.document_name,
        "sign_url": sig.external_sign_url,
    }


@router.post("/project/sign-complete/{signature_request_id}")
def mark_vendor_project_signed(
    signature_request_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_vendor(me)

    sig_uuid = uuid.UUID(signature_request_id)
    sig = db.execute(
        select(SignatureRequest).where(SignatureRequest.id == sig_uuid)
    ).scalar_one_or_none()

    if not sig:
        raise HTTPException(status_code=404, detail="Signature request not found")

    if sig.user_id and str(sig.user_id) != str(me.id):
        raise HTTPException(status_code=403, detail="Forbidden")

    p = db.execute(
        select(Project).where(Project.id == sig.project_id)
    ).scalar_one_or_none()

    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    mark_signature_signed(sig)

    p.status = "submitted"
    p.submitted_at = datetime.now(timezone.utc)
    p.updated_at = datetime.now(timezone.utc)

    db.commit()

    return {
        "message": "Vendor project signed and submitted",
        "project_id": str(p.id),
        "status": p.status,
    }