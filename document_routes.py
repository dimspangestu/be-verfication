from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import select
from pathlib import Path
from datetime import datetime, timezone
import os

from db import get_db
from models import User
from auth_routes import get_current_user
from models_vendor_project import Project, Vendor
from models_admin_funding import ProjectFunding
from models_documents import GeneratedDocument
from document_template_service import (
    get_active_template,
    render_template_html,
    payload_json,
)
from pdf_service import generate_pdf_from_html

router = APIRouter(prefix="/documents", tags=["documents"])

BASE_DOC_DIR = os.getenv("DOCUMENT_STORAGE_DIR", "storage/documents")


def fmt_idr_dot(value):
    return f"{int(value or 0):,}".replace(",", ".")


def utc_today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def require_vendor_or_admin(me: User):
    if getattr(me, "role", None) not in ["penerima_dana", "admin"]:
        raise HTTPException(status_code=403, detail="Vendor/Admin only")


def require_investor_or_admin(me: User):
    if getattr(me, "role", None) not in ["investor", "admin"]:
        raise HTTPException(status_code=403, detail="Investor/Admin only")


def build_vendor_project_payload(project: Project, vendor: Vendor) -> dict:
    return {
        "agreement_number": f"VENDOR/{str(project.id)[:8].upper()}",
        "agreement_date": str(project.submitted_at.date()) if project.submitted_at else utc_today_str(),
        "agreement_city": "Jakarta",
        "platform_name": "Fondofund",
        "platform_address": "Alamat Platform",
        "platform_representative_name": "Authorized Representative",
        "platform_representative_position": "Direktur",
        "recipient_name": vendor.company_name or vendor.pic_name or "",
        "recipient_address": vendor.address or "-",
        "project_name": project.project_name or project.project_code or "",
        "project_type": project.project_type_other if project.project_type == "lainnya" else (project.project_type or ""),
        "funding_needed": fmt_idr_dot(project.funding_needed),
        "min_funding": fmt_idr_dot(project.min_funding),
        "tenor_months": int(project.tenor_months or 0),
        "fixed_return_pct": float(project.fixed_return_pct or 0),
        "return_total_amount": fmt_idr_dot(project.return_total_amount),
        "payback_total_amount": fmt_idr_dot(project.payback_total_amount),
        "signer_name": vendor.pic_name or vendor.company_name or "",
        "dispute_city": "Jakarta",
    }


def build_investor_funding_payload(funding: ProjectFunding, project: Project, vendor: Vendor, me: User) -> dict:
    return {
        "agreement_number": f"INVESTOR/{str(funding.id)[:8].upper()}",
        "agreement_date": utc_today_str(),
        "agreement_city": "Jakarta",
        "platform_name": "Fondofund",
        "platform_address": "Alamat Platform",
        "platform_representative_name": "Authorized Representative",
        "platform_representative_position": "Direktur",
        "investor_name": getattr(me, "name", None) or getattr(me, "email", None) or "Investor",
        "investor_address": "-",
        "recipient_name": vendor.company_name or vendor.pic_name or "",
        "project_name": project.project_name or project.project_code or "",
        "project_type": project.project_type_other if project.project_type == "lainnya" else (project.project_type or ""),
        "funding_needed": fmt_idr_dot(funding.funding_amount),
        "min_funding": fmt_idr_dot(project.min_funding),
        "tenor_months": int(project.tenor_months or 0),
        "fixed_return_pct": float(project.fixed_return_pct or 0),
        "return_total_amount": fmt_idr_dot(funding.expected_return_amount),
        "payback_total_amount": fmt_idr_dot(funding.expected_total_back),
        "signer_name": getattr(me, "name", None) or getattr(me, "email", None) or "Investor",
        "dispute_city": "Jakarta",
    }


@router.post("/vendor-project/{project_id}/generate")
def generate_vendor_project_pdf(
    project_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_vendor_or_admin(me)

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

    tpl = get_active_template(
        db,
        module_type="vendor_project",
        template_code="VENDOR_FUNDING_AGREEMENT",
    )
    if not tpl:
        raise HTTPException(status_code=404, detail="Active vendor document template not found")

    payload = build_vendor_project_payload(project, vendor)
    rendered_html = render_template_html(tpl.content_html, payload)

    file_name = f"vendor-project-{project.id}.pdf"
    output_path = str(Path(BASE_DOC_DIR) / "vendor_project" / file_name)

    generate_pdf_from_html(rendered_html, output_path)
    file_size = os.path.getsize(output_path) if os.path.exists(output_path) else None

    existing = db.execute(
        select(GeneratedDocument).where(
            GeneratedDocument.module_type == "vendor_project",
            GeneratedDocument.module_ref_id == project.id,
            GeneratedDocument.document_status == "generated",
        )
    ).scalar_one_or_none()

    if existing:
        existing.template_id = tpl.id
        existing.file_name = file_name
        existing.storage_path = output_path
        existing.mime_type = "application/pdf"
        existing.file_size = file_size
        existing.rendered_html = rendered_html
        existing.render_payload = payload_json(payload)
        existing.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        doc = existing
    else:
        doc = GeneratedDocument(
            template_id=tpl.id,
            module_type="vendor_project",
            module_ref_id=project.id,
            file_name=file_name,
            storage_path=output_path,
            mime_type="application/pdf",
            file_size=file_size,
            rendered_html=rendered_html,
            render_payload=payload_json(payload),
            document_status="generated",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

    return {
        "document_id": str(doc.id),
        "file_name": doc.file_name,
        "storage_path": doc.storage_path,
        "file_size": doc.file_size,
        "status": doc.document_status,
        "download_url": f"/documents/generated/{doc.id}/download",
    }


@router.post("/investor-funding/{funding_id}/generate")
def generate_investor_funding_pdf(
    funding_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_investor_or_admin(me)

    funding = db.execute(
        select(ProjectFunding).where(ProjectFunding.id == funding_id)
    ).scalar_one_or_none()
    if not funding:
        raise HTTPException(status_code=404, detail="Funding not found")

    if getattr(me, "role", None) == "investor" and str(funding.investor_user_id) != str(me.id):
        raise HTTPException(status_code=403, detail="Forbidden")

    project = db.execute(
        select(Project).where(Project.id == funding.project_id)
    ).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    vendor = db.execute(
        select(Vendor).where(Vendor.id == project.vendor_id)
    ).scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    tpl = get_active_template(
        db,
        module_type="investor_funding",
        template_code="INVESTOR_FUNDING_AGREEMENT",
    )
    if not tpl:
        raise HTTPException(status_code=404, detail="Active investor document template not found")

    payload = build_investor_funding_payload(funding, project, vendor, me)
    rendered_html = render_template_html(tpl.content_html, payload)

    file_name = f"investor-funding-{funding.id}.pdf"
    output_path = str(Path(BASE_DOC_DIR) / "investor_funding" / file_name)

    generate_pdf_from_html(rendered_html, output_path)
    file_size = os.path.getsize(output_path) if os.path.exists(output_path) else None

    existing = db.execute(
        select(GeneratedDocument).where(
            GeneratedDocument.module_type == "investor_funding",
            GeneratedDocument.module_ref_id == funding.id,
            GeneratedDocument.document_status == "generated",
        )
    ).scalar_one_or_none()

    if existing:
        existing.template_id = tpl.id
        existing.file_name = file_name
        existing.storage_path = output_path
        existing.mime_type = "application/pdf"
        existing.file_size = file_size
        existing.rendered_html = rendered_html
        existing.render_payload = payload_json(payload)
        existing.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        doc = existing
    else:
        doc = GeneratedDocument(
            template_id=tpl.id,
            module_type="investor_funding",
            module_ref_id=funding.id,
            file_name=file_name,
            storage_path=output_path,
            mime_type="application/pdf",
            file_size=file_size,
            rendered_html=rendered_html,
            render_payload=payload_json(payload),
            document_status="generated",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

    return {
        "document_id": str(doc.id),
        "file_name": doc.file_name,
        "storage_path": doc.storage_path,
        "file_size": doc.file_size,
        "status": doc.document_status,
        "download_url": f"/documents/generated/{doc.id}/download",
    }


@router.get("/generated/{document_id}")
def get_generated_document_detail(
    document_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    doc = db.execute(
        select(GeneratedDocument).where(GeneratedDocument.id == document_id)
    ).scalar_one_or_none()

    if not doc:
        raise HTTPException(status_code=404, detail="Generated document not found")

    return {
        "document_id": str(doc.id),
        "template_id": str(doc.template_id) if doc.template_id else None,
        "module_type": doc.module_type,
        "module_ref_id": str(doc.module_ref_id),
        "file_name": doc.file_name,
        "storage_path": doc.storage_path,
        "mime_type": doc.mime_type,
        "file_size": doc.file_size,
        "document_status": doc.document_status,
        "download_url": f"/documents/generated/{doc.id}/download",
    }


@router.get("/generated/{document_id}/download")
def download_generated_document(
    document_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    doc = db.execute(
        select(GeneratedDocument).where(GeneratedDocument.id == document_id)
    ).scalar_one_or_none()

    if not doc:
        raise HTTPException(status_code=404, detail="Generated document not found")

    if not os.path.exists(doc.storage_path):
        raise HTTPException(status_code=404, detail="File not found on storage")

    return FileResponse(
        path=doc.storage_path,
        media_type=doc.mime_type or "application/pdf",
        filename=doc.file_name,
    )