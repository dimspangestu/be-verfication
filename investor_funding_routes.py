from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from datetime import datetime, timezone
from pathlib import Path
import os
import json
import uuid

from db import get_db
from models import User
from models_vendor_project import Project, Vendor
from models_admin_funding import ProjectFunding, PaymentTransaction
from models_documents import DocumentTemplate, GeneratedDocument, SignedDocument
from schemas_admin_funding import FundingCreateIn, MidtransSnapOut
from auth_routes import get_current_user
from midtrans_service import create_snap_transaction, get_transaction_status


router = APIRouter(prefix="/investor", tags=["investor"])

BASE_DOC_DIR = os.getenv("DOCUMENT_STORAGE_DIR", "storage/documents")
BASE_SIGNED_DIR = os.getenv("SIGNED_DOCUMENT_STORAGE_DIR", "storage/signed_documents")


def require_investor(me: User):
    if getattr(me, "role", None) != "investor":
        raise HTTPException(status_code=403, detail="Investor only")


def utcnow():
    return datetime.now(timezone.utc)


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


def fmt_idr(n: int | float | None):
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


@router.get("/projects")
def investor_open_projects(
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_investor(me)

    rows = db.execute(
        select(Project).where(Project.status == "funding_open").order_by(Project.created_at.desc())
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

        result.append(
            {
                "id": str(p.id),
                "project_code": p.project_code,
                "project_name": p.project_name,
                "project_type": p.project_type,
                "project_type_other": p.project_type_other,
                "funding_needed": int(p.funding_needed or 0),
                "funded_amount": int(paid_amount or 0),
                "remaining_amount": int(remaining_amount),
                "min_funding": int(p.min_funding or 0),
                "tenor_months": p.tenor_months,
                "fixed_return_pct": float(p.fixed_return_pct or 0),
                "status": p.status,
                "vendor_company_name": vendor.company_name if vendor else None,
            }
        )
    return result

@router.post("/fundings/prepare-agreement")
def investor_prepare_agreement(
    payload: FundingCreateIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_investor(me)

    project = db.execute(
        select(Project).where(Project.id == payload.project_id)
    ).scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status != "funding_open":
        raise HTTPException(status_code=422, detail="Project is not open for funding")

    if int(payload.funding_amount or 0) <= 0:
        raise HTTPException(status_code=422, detail="Funding amount must be greater than 0")

    # if project.min_funding and int(payload.funding_amount) < int(project.min_funding):
    #     raise HTTPException(
    #         status_code=422,
    #         detail=f"Minimum funding amount is {int(project.min_funding)}",
    #     )

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

    template = get_active_template(
        db,
        module_type="investor_funding",
        template_code="INVESTOR_FUNDING_AGREEMENT",
    )
    if not template:
        raise HTTPException(status_code=404, detail="Investor agreement template not found")

    agreement_date = datetime.now().strftime("%Y-%m-%d")
    agreement_number = f"INV-{str(funding.id)[:8].upper()}"

    render_payload = {
        "agreement_number": agreement_number,
        "agreement_date": agreement_date,
        "agreement_city": "Depok",
        "dispute_city": "Depok",
        "platform_name": "Fondofund",
        "platform_address": "Depok, Indonesia",
        "platform_representative_name": "Admin Fondofund",
        "platform_representative_position": "Administrator",
        "investor_name": getattr(me, "name", None) or getattr(me, "email", None) or "Investor",
        "investor_address": "-",
        "project_name": project.project_name or project.project_code or "",
        "project_code": project.project_code or "",
        "recipient_name": vendor.company_name if vendor else "",
        "recipient_address": vendor.address if vendor else "",
        "funding_amount": fmt_idr(payload.funding_amount),
        "expected_return_amount": fmt_idr(expected_return),
        "expected_total_back": fmt_idr(expected_total),
        "tenor_months": project.tenor_months or 0,
        "fixed_return_pct": float(project.fixed_return_pct or 0),
    }

    rendered_html = render_template_html(template.content_html, render_payload)

    file_name = f"investor-funding-agreement-{funding.id}.pdf"
    output_path = str(Path(BASE_DOC_DIR) / "investor_funding" / file_name)

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
    funding.updated_at = utcnow()

    db.commit()

    return {
        "funding_id": str(funding.id),
        "project_id": str(project.id),
        "generated_document_id": str(generated_doc.id),
        "generated_file_name": generated_doc.file_name,
        "funding_status": funding.funding_status,
        "funding_amount": funding.funding_amount,
        "expected_return_amount": funding.expected_return_amount,
        "expected_total_back": funding.expected_total_back,
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
    ext = Path(signed_file.filename).suffix or ".pdf"
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
    funding.updated_at = utcnow()

    db.commit()

    return {
        "message": "Signed document uploaded",
        "funding_status": funding.funding_status,
        "signed_document": {
            "id": str(signed_doc.id),
            "file_name": signed_doc.file_name,
            "mime_type": signed_doc.mime_type,
            "file_size": signed_doc.file_size,
            "verification_status": signed_doc.verification_status,
            "created_at": signed_doc.created_at,
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
        "id": str(doc.id),
        "file_name": doc.file_name,
        "mime_type": doc.mime_type,
        "file_size": doc.file_size,
        "verification_status": doc.verification_status,
        "created_at": doc.created_at,
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

    order_id = f"fund-{funding.id}"

    existing_tx = db.execute(
        select(PaymentTransaction).where(PaymentTransaction.order_id == order_id)
    ).scalar_one_or_none()

    if existing_tx and existing_tx.redirect_url:
        funding.funding_status = existing_tx.transaction_status or "pending_payment"
        funding.updated_at = utcnow()
        db.commit()

        return MidtransSnapOut(
            funding_id=funding.id,
            order_id=order_id,
            redirect_url=existing_tx.redirect_url,
            token=existing_tx.snap_token,
            transaction_status=existing_tx.transaction_status,
        )

    status_code, raw_req, raw_res = create_snap_transaction(
        order_id=order_id,
        gross_amount=funding.funding_amount,
        first_name=getattr(me, "name", None) or "Investor",
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
    funding.updated_at = utcnow()

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
        select(ProjectFunding).where(ProjectFunding.investor_user_id == me.id).order_by(ProjectFunding.created_at.desc())
    ).scalars().all()

    result = []
    for f in rows:
        project = db.execute(select(Project).where(Project.id == f.project_id)).scalar_one_or_none()
        vendor = None
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

        result.append(
            {
                "id": str(f.id),
                "project_id": str(f.project_id),
                "project_name": project.project_name if project else None,
                "project_code": project.project_code if project else None,
                "vendor_company_name": vendor.company_name if vendor else None,
                "funding_amount": f.funding_amount,
                "expected_return_amount": f.expected_return_amount,
                "expected_total_back": f.expected_total_back,
                "funding_status": f.funding_status,
                "paid_at": f.paid_at,
                "created_at": f.created_at,
                "order_id": tx.order_id if tx else None,
                "signed_document": (
                    {
                        "id": str(signed_doc.id),
                        "file_name": signed_doc.file_name,
                        "verification_status": signed_doc.verification_status,
                        "created_at": signed_doc.created_at,
                    }
                    if signed_doc
                    else None
                ),
            }
        )

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
    transaction_status = raw_res.get("transaction_status")

    tx.transaction_status = transaction_status
    tx.payment_type = raw_res.get("payment_type")
    tx.settlement_time = raw_res.get("settlement_time")
    tx.raw_response = json.dumps(raw_res)
    tx.updated_at = utcnow()

    funding = db.execute(
        select(ProjectFunding).where(ProjectFunding.id == tx.funding_id)
    ).scalar_one_or_none()

    if funding:
        if transaction_status in ["settlement", "capture"]:
            funding.funding_status = "paid"
            funding.paid_at = utcnow()
        elif transaction_status in ["pending"]:
            funding.funding_status = "pending_payment"
        elif transaction_status in ["deny", "cancel", "expire", "failure"]:
            funding.funding_status = transaction_status

        funding.updated_at = utcnow()

    db.commit()

    return {
        "order_id": order_id,
        "transaction_status": transaction_status,
        "funding_status": funding.funding_status if funding else None,
    }