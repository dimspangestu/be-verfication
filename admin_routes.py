from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from datetime import datetime, timezone
import uuid

from db import get_db
from models import User
from models_vendor_project import Project, Vendor, ProjectDocument
from models_admin_funding import ProjectFunding, PaymentTransaction
from models_disbursement import UserBankAccount, VendorDisbursement
from schemas_admin_funding import AdminStatusUpdateIn
from schemas_disbursement import VendorDisbursementCreateIn
from auth_routes import get_current_user
from wallet_service import get_wallet_balance, post_ledger_and_wallet
from models_documents import SignedDocument

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(me: User):
    if getattr(me, "role", None) != "admin":
        raise HTTPException(status_code=403, detail="Admin only")


def parse_uuid_or_400(value: str, field_name: str = "id"):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")


def utcnow():
    return datetime.now(timezone.utc)


@router.get("/dashboard-summary")
def admin_dashboard_summary(
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_admin(me)

    total_projects = db.execute(select(func.count()).select_from(Project)).scalar() or 0
    funding_open_projects = db.execute(
        select(func.count()).select_from(Project).where(Project.status == "funding_open")
    ).scalar() or 0
    total_fundings = db.execute(select(func.count()).select_from(ProjectFunding)).scalar() or 0
    paid_fundings = db.execute(
        select(func.count()).select_from(ProjectFunding).where(ProjectFunding.funding_status == "paid")
    ).scalar() or 0
    paid_funding_amount = db.execute(
        select(func.coalesce(func.sum(ProjectFunding.funding_amount), 0)).where(
            ProjectFunding.funding_status == "paid"
        )
    ).scalar() or 0
    admin_escrow_balance = get_wallet_balance(db, "admin_escrow")

    return {
        "total_projects": int(total_projects),
        "funding_open_projects": int(funding_open_projects),
        "total_fundings": int(total_fundings),
        "paid_fundings": int(paid_fundings),
        "paid_funding_amount": int(paid_funding_amount),
        "admin_escrow_balance": int(admin_escrow_balance),
    }


@router.get("/projects")
def admin_list_projects(
    status: str | None = Query(None, description="Filter by project status"),
    q: str | None = Query(None, description="Search by name / code"),
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_admin(me)

    stmt = select(Project)

    if status:
        stmt = stmt.where(Project.status == status)

    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            func.lower(Project.project_name).like(like)
            | func.lower(Project.project_code).like(like)
        )

    stmt = stmt.order_by(
        Project.created_at.desc() if order == "desc" else Project.created_at.asc()
    )

    rows = db.execute(stmt.offset(offset).limit(limit)).scalars().all()

    result = []
    for p in rows:
        funded_amount = db.execute(
            select(func.coalesce(func.sum(ProjectFunding.funding_amount), 0)).where(
                ProjectFunding.project_id == p.id,
                ProjectFunding.funding_status == "paid",
            )
        ).scalar() or 0

        disbursed_amount = db.execute(
            select(func.coalesce(func.sum(VendorDisbursement.gross_amount), 0)).where(
                VendorDisbursement.project_id == p.id,
                VendorDisbursement.disbursement_status == "disbursed",
            )
        ).scalar() or 0

        result.append({
            "id": str(p.id),
            "vendor_id": str(p.vendor_id) if p.vendor_id else None,
            "project_code": p.project_code,
            "project_name": p.project_name,
            "project_type": p.project_type,
            "project_type_other": p.project_type_other,
            "funding_needed": int(p.funding_needed or 0),
            "min_funding": int(p.min_funding or 0) if p.min_funding is not None else None,
            "tenor_months": int(p.tenor_months or 0),
            "fixed_return_pct": float(p.fixed_return_pct or 0),
            "return_total_amount": int(p.return_total_amount or 0),
            "payback_total_amount": int(p.payback_total_amount or 0),
            "return_monthly_amount": int(p.return_monthly_amount or 0),
            "status": p.status,
            "submitted_at": p.submitted_at,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
            "funded_amount": int(funded_amount),
            "remaining_amount": max(int(p.funding_needed or 0) - int(funded_amount), 0),
            "disbursed_amount": int(disbursed_amount),
            "max_disburse_amount": max(int(funded_amount) - int(disbursed_amount), 0),
        })

    # ✅ tetap return plain array — kompatibel dengan semua FE yang sudah ada
    return result


@router.get("/projects/{project_id}")
def admin_project_detail(
    project_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_admin(me)

    project_uuid = parse_uuid_or_400(project_id, "project id")

    p = db.execute(select(Project).where(Project.id == project_uuid)).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    vendor = db.execute(select(Vendor).where(Vendor.id == p.vendor_id)).scalar_one_or_none()

    vendor_bank_accounts = []
    if vendor and vendor.user_id:
        vendor_bank_accounts = db.execute(
            select(UserBankAccount)
            .where(
                UserBankAccount.user_id == vendor.user_id,
                UserBankAccount.owner_type == "vendor",
            )
            .order_by(UserBankAccount.is_default.desc(), UserBankAccount.created_at.desc())
        ).scalars().all()

    fundings = db.execute(
        select(ProjectFunding)
        .where(ProjectFunding.project_id == p.id)
        .order_by(ProjectFunding.created_at.desc())
    ).scalars().all()

    documents = db.execute(
        select(ProjectDocument)
        .where(ProjectDocument.project_id == p.id)
        .order_by(ProjectDocument.created_at.desc())
    ).scalars().all()

    vendor_signed_documents = db.execute(
        select(SignedDocument)
        .where(
            SignedDocument.module_type == "vendor_project",
            SignedDocument.module_ref_id == p.id,
        )
        .order_by(SignedDocument.created_at.desc())
    ).scalars().all()

    investor_signed_documents = db.execute(
        select(SignedDocument)
        .where(
            SignedDocument.module_type == "investor_funding",
            SignedDocument.module_ref_id == p.id,
        )
        .order_by(SignedDocument.created_at.desc())
    ).scalars().all()

    paid_fundings = [f for f in fundings if f.funding_status == "paid"]
    funded_amount = sum(int(f.funding_amount or 0) for f in paid_fundings)

    disbursed_amount = db.execute(
        select(func.coalesce(func.sum(VendorDisbursement.gross_amount), 0)).where(
            VendorDisbursement.project_id == p.id,
            VendorDisbursement.disbursement_status == "disbursed",
        )
    ).scalar() or 0

    remaining_amount = max(int(p.funding_needed or 0) - int(funded_amount), 0)
    max_disburse_amount = max(int(funded_amount) - int(disbursed_amount), 0)
    admin_escrow_balance = get_wallet_balance(db, "admin_escrow")

    return {
        "project": {
            "id": str(p.id),
            "vendor_id": str(p.vendor_id) if p.vendor_id else None,
            "project_code": p.project_code,
            "project_name": p.project_name,
            "project_type": p.project_type,
            "project_type_other": p.project_type_other,
            "funding_needed": int(p.funding_needed or 0),
            "min_funding": int(p.min_funding or 0) if p.min_funding is not None else None,
            "tenor_months": int(p.tenor_months or 0),
            "fixed_return_pct": float(p.fixed_return_pct or 0),
            "return_total_amount": int(p.return_total_amount or 0),
            "payback_total_amount": int(p.payback_total_amount or 0),
            "return_monthly_amount": int(p.return_monthly_amount or 0),
            "status": p.status,
            "submitted_at": p.submitted_at,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        },
        "vendor": {
            "id": str(vendor.id) if vendor else None,
            "user_id": str(vendor.user_id) if vendor and vendor.user_id else None,
            "vendor_code": vendor.vendor_code if vendor else None,
            "vendor_type": vendor.vendor_type if vendor else None,
            "company_name": vendor.company_name if vendor else None,
            "pic_name": vendor.pic_name if vendor else None,
            "email": vendor.email if vendor else None,
            "phone": vendor.phone if vendor else None,
            "npwp": vendor.npwp if vendor else None,
            "nik": vendor.nik if vendor else None,
            "address": vendor.address if vendor else None,
            "kyc_status": vendor.kyc_status if vendor else None,
            "bank_name": vendor.bank_name if vendor else None,
            "bank_account_no": vendor.bank_account_no if vendor else None,
            "bank_account_name": vendor.bank_account_name if vendor else None,
            "pg_provider": vendor.pg_provider if vendor else None,
            "pg_status": vendor.pg_status if vendor else None,
        },
        "vendor_bank_accounts": [
            {
                "id": str(b.id),
                "user_id": str(b.user_id),
                "owner_type": b.owner_type,
                "bank_name": b.bank_name,
                "bank_code": b.bank_code,
                "account_number": b.account_number,
                "account_name": b.account_name,
                "is_default": b.is_default,
                "is_verified": b.is_verified,
                "verification_note": b.verification_note,
                "created_at": b.created_at,
                "updated_at": b.updated_at,
            }
            for b in vendor_bank_accounts
        ],
        "funding_summary": {
            "target": int(p.funding_needed or 0),
            "funded_amount": int(funded_amount),
            "remaining_amount": int(remaining_amount),
            "investor_count": len(paid_fundings),
            "paid_funding_count": len(paid_fundings),
            "total_funding_count": len(fundings),
            "disbursed_amount": int(disbursed_amount),
            "max_disburse_amount": int(max_disburse_amount),
            "admin_escrow_balance": int(admin_escrow_balance),
        },
        "fundings": [
            {
                "id": str(f.id),
                "project_id": str(f.project_id),
                "investor_user_id": str(f.investor_user_id),
                "funding_amount": int(f.funding_amount or 0),
                "expected_return_amount": int(f.expected_return_amount or 0),
                "expected_total_back": int(f.expected_total_back or 0),
                "funding_status": f.funding_status,
                "paid_at": f.paid_at,
                "created_at": f.created_at,
            }
            for f in fundings
        ],
        "documents": [
            {
                "id": str(d.id),
                "project_id": str(d.project_id),
                "doc_type": d.doc_type,
                "file_name": d.file_name,
                "mime": d.mime,
                "file_size": int(d.file_size or 0) if d.file_size is not None else None,
                "storage_path": d.storage_path,
                "created_at": d.created_at,
            }
            for d in documents
        ],
        "vendor_signed_documents": [
            {
                "id": str(d.id),
                "module_type": d.module_type,
                "module_ref_id": str(d.module_ref_id),
                "generated_document_id": str(d.generated_document_id) if d.generated_document_id else None,
                "uploaded_by_user_id": str(d.uploaded_by_user_id) if d.uploaded_by_user_id else None,
                "file_name": d.file_name,
                "storage_path": d.storage_path,
                "mime_type": d.mime_type,
                "file_size": int(d.file_size or 0) if d.file_size is not None else None,
                "verification_status": d.verification_status,
                "verified_by_user_id": str(d.verified_by_user_id) if d.verified_by_user_id else None,
                "verified_at": d.verified_at,
                "note": d.note,
                "created_at": d.created_at,
                "updated_at": d.updated_at,
            }
            for d in vendor_signed_documents
        ],
        "investor_signed_documents": [
            {
                "id": str(d.id),
                "module_type": d.module_type,
                "module_ref_id": str(d.module_ref_id),
                "generated_document_id": str(d.generated_document_id) if d.generated_document_id else None,
                "uploaded_by_user_id": str(d.uploaded_by_user_id) if d.uploaded_by_user_id else None,
                "file_name": d.file_name,
                "storage_path": d.storage_path,
                "mime_type": d.mime_type,
                "file_size": int(d.file_size or 0) if d.file_size is not None else None,
                "verification_status": d.verification_status,
                "verified_by_user_id": str(d.verified_by_user_id) if d.verified_by_user_id else None,
                "verified_at": d.verified_at,
                "note": d.note,
                "created_at": d.created_at,
                "updated_at": d.updated_at,
            }
            for d in investor_signed_documents
        ],
    }


@router.patch("/projects/{project_id}/review")
def admin_review_project(
    project_id: str,
    payload: AdminStatusUpdateIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_admin(me)

    project_uuid = parse_uuid_or_400(project_id, "project id")

    p = db.execute(select(Project).where(Project.id == project_uuid)).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    allowed = {
        "doc_review",
        "funding_open",
        "funding_closed",
        "rejected",
        "disbursed_to_vendor",
        "repayment_running",
        "completed",
    }

    if payload.status not in allowed:
        raise HTTPException(status_code=422, detail="Invalid status")

    p.status = payload.status
    p.updated_at = utcnow()

    db.commit()
    db.refresh(p)

    return {
        "message": "Project updated",
        "id": str(p.id),
        "status": p.status,
        "note": payload.note,
    }


@router.get("/fundings")
def admin_list_fundings(
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_admin(me)

    rows = db.execute(select(ProjectFunding).order_by(ProjectFunding.created_at.desc())).scalars().all()

    return [
        {
            "id": str(f.id),
            "project_id": str(f.project_id),
            "investor_user_id": str(f.investor_user_id),
            "funding_amount": int(f.funding_amount or 0),
            "expected_return_amount": int(f.expected_return_amount or 0),
            "expected_total_back": int(f.expected_total_back or 0),
            "funding_status": f.funding_status,
            "paid_at": f.paid_at,
            "created_at": f.created_at,
            "updated_at": f.updated_at,
        }
        for f in rows
    ]


@router.get("/history")
def admin_history(
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_admin(me)

    txs = db.execute(select(PaymentTransaction).order_by(PaymentTransaction.created_at.desc())).scalars().all()
    disbursements = db.execute(select(VendorDisbursement).order_by(VendorDisbursement.created_at.desc())).scalars().all()

    return {
        "payments": [
            {
                "id": str(t.id),
                "payment_for": t.payment_for,
                "provider": t.provider,
                "order_id": t.order_id,
                "project_id": str(t.project_id) if t.project_id else None,
                "funding_id": str(t.funding_id) if t.funding_id else None,
                "vendor_id": str(t.vendor_id) if getattr(t, "vendor_id", None) else None,
                "investor_user_id": str(t.investor_user_id) if t.investor_user_id else None,
                "gross_amount": int(t.gross_amount or 0),
                "transaction_status": t.transaction_status,
                "fraud_status": t.fraud_status,
                "payment_type": t.payment_type,
                "transaction_id": t.transaction_id,
                "settlement_time": t.settlement_time,
                "created_at": t.created_at,
                "updated_at": t.updated_at,
            }
            for t in txs
        ],
        "disbursements": [
            {
                "id": str(d.id),
                "project_id": str(d.project_id),
                "vendor_id": str(d.vendor_id),
                "admin_user_id": str(d.admin_user_id) if d.admin_user_id else None,
                "gross_amount": int(d.gross_amount or 0),
                "fee_pct": float(d.fee_pct or 0),
                "fee_amount": int(d.fee_amount or 0),
                "net_amount": int(d.net_amount or 0),
                "bank_account_id": str(d.bank_account_id) if d.bank_account_id else None,
                "disbursement_status": d.disbursement_status,
                "provider": d.provider,
                "provider_ref_no": d.provider_ref_no,
                "disbursed_at": d.disbursed_at,
                "note": d.note,
                "created_at": d.created_at,
                "updated_at": d.updated_at,
            }
            for d in disbursements
        ],
    }


@router.post("/projects/{project_id}/disburse")
def admin_disburse_to_vendor(
    project_id: str,
    payload: VendorDisbursementCreateIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_admin(me)
 
    project_uuid = parse_uuid_or_400(project_id, "project id")
 
    p = db.execute(select(Project).where(Project.id == project_uuid)).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
 
    vendor = db.execute(select(Vendor).where(Vendor.id == p.vendor_id)).scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
 
    # ── Resolve bank account ────────────────────────────────────────────────
    bank_account = None
 
    if payload.bank_account_id:
        # Case A: real UserBankAccount row was selected
        bank_account_uuid = parse_uuid_or_400(payload.bank_account_id, "bank_account_id")
        bank_account = db.execute(
            select(UserBankAccount).where(UserBankAccount.id == bank_account_uuid)
        ).scalar_one_or_none()
 
        if not bank_account:
            raise HTTPException(status_code=404, detail="Bank account not found")
        if vendor.user_id and bank_account.user_id != vendor.user_id:
            raise HTTPException(status_code=422, detail="Bank account does not belong to vendor")
        if bank_account.owner_type != "vendor":
            raise HTTPException(status_code=422, detail="Bank account is not a vendor account")
 
    else:
        # Case B: no UserBankAccount row — fall back to Vendor profile fields
        # Validate that vendor has bank info in their profile
        if not vendor.bank_account_no or not vendor.bank_name:
            raise HTTPException(
                status_code=422,
                detail="Vendor has no bank account registered. "
                       "Please ask vendor to complete their profile.",
            )
        # No bank_account row needed — disbursement will be recorded with bank_account_id = None
        # and bank info noted in the note field for audit trail
 
    # ── Amount validation ──────────────────────────────────────────────────
    gross_amount = int(payload.gross_amount or 0)
    if gross_amount <= 0:
        raise HTTPException(status_code=422, detail="gross_amount must be > 0")
 
    fee_pct    = float(payload.fee_pct or 0)
    fee_amount = round(gross_amount * fee_pct / 100)
    net_amount = gross_amount - fee_amount
 
    if net_amount <= 0:
        raise HTTPException(status_code=422, detail="net_amount must be > 0")
 
    funded_amount = db.execute(
        select(func.coalesce(func.sum(ProjectFunding.funding_amount), 0)).where(
            ProjectFunding.project_id == p.id,
            ProjectFunding.funding_status == "paid",
        )
    ).scalar() or 0
 
    disbursed_amount = db.execute(
        select(func.coalesce(func.sum(VendorDisbursement.gross_amount), 0)).where(
            VendorDisbursement.project_id == p.id,
            VendorDisbursement.disbursement_status == "disbursed",
        )
    ).scalar() or 0
 
    max_disburse_amount = max(int(funded_amount or 0) - int(disbursed_amount or 0), 0)
 
    if max_disburse_amount <= 0:
        raise HTTPException(status_code=422, detail="No paid funding available to disburse")
    if gross_amount > max_disburse_amount:
        raise HTTPException(
            status_code=422,
            detail=f"Maximum disbursement is Rp {max_disburse_amount:,}",
        )
 
    escrow_balance = get_wallet_balance(db, "admin_escrow")
    if int(escrow_balance or 0) < net_amount:
        raise HTTPException(
            status_code=422,
            detail=f"Admin escrow balance insufficient. Current: Rp {int(escrow_balance or 0):,}",
        )
 
    # ── Build note with bank info for audit trail ──────────────────────────
    if bank_account:
        bank_info = f"{bank_account.bank_name} {bank_account.account_number} a/n {bank_account.account_name}"
    else:
        bank_info = f"{vendor.bank_name} {vendor.bank_account_no} a/n {vendor.bank_account_name} (from vendor profile)"
 
    final_note = payload.note or f"Admin disbursement to vendor — {bank_info}"
 
    # ── Create disbursement record ─────────────────────────────────────────
    d = VendorDisbursement(
        project_id=p.id,
        vendor_id=vendor.id,
        admin_user_id=me.id,
        gross_amount=gross_amount,
        fee_pct=fee_pct,
        fee_amount=fee_amount,
        net_amount=net_amount,
        bank_account_id=bank_account.id if bank_account else None,  # ← null OK
        disbursement_status="disbursed",
        provider="manual",
        provider_ref_no=None,
        disbursed_at=utcnow(),
        note=final_note,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(d)
    db.flush()
 
    post_ledger_and_wallet(
        db=db, ref_type="vendor_disbursement", ref_id=d.id,
        vendor_id=vendor.id, project_id=p.id,
        entry_side="debit", wallet_type="admin_escrow",
        amount=net_amount, note=f"Admin escrow → vendor: {bank_info}",
    )
 
    if fee_amount > 0:
        post_ledger_and_wallet(
            db=db, ref_type="vendor_disbursement_fee", ref_id=d.id,
            vendor_id=vendor.id, project_id=p.id,
            entry_side="credit", wallet_type="admin_fee_wallet",
            amount=fee_amount, note="Platform fee from vendor disbursement",
        )
 
    p.status    = "disbursed_to_vendor"
    p.updated_at = utcnow()
    db.commit()
 
    return {
        "message":                  "Disbursement recorded",
        "project_id":               str(p.id),
        "vendor_id":                str(vendor.id),
        "bank_account_id":          str(bank_account.id) if bank_account else None,
        "bank_info":                bank_info,
        "gross_amount":             gross_amount,
        "fee_pct":                  fee_pct,
        "fee_amount":               fee_amount,
        "net_amount":               net_amount,
        "status":                   p.status,
        "funded_amount":            int(funded_amount),
        "disbursed_amount":         int(disbursed_amount + gross_amount),
        "max_disburse_amount_after": max(int(funded_amount) - int(disbursed_amount + gross_amount), 0),
    }


@router.get("/projects/{project_id}/vendor-signed-documents")
def admin_project_vendor_signed_documents(
    project_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_admin(me)

    project_uuid = parse_uuid_or_400(project_id, "project id")

    p = db.execute(select(Project).where(Project.id == project_uuid)).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    rows = db.execute(
        select(SignedDocument)
        .where(
            SignedDocument.module_type == "vendor_project",
            SignedDocument.module_ref_id == p.id,
        )
        .order_by(SignedDocument.created_at.desc())
    ).scalars().all()

    return [
        {
            "id": str(d.id),
            "module_type": d.module_type,
            "module_ref_id": str(d.module_ref_id),
            "generated_document_id": str(d.generated_document_id) if d.generated_document_id else None,
            "uploaded_by_user_id": str(d.uploaded_by_user_id) if d.uploaded_by_user_id else None,
            "file_name": d.file_name,
            "storage_path": d.storage_path,
            "mime_type": d.mime_type,
            "file_size": int(d.file_size or 0) if d.file_size is not None else None,
            "verification_status": d.verification_status,
            "verified_by_user_id": str(d.verified_by_user_id) if d.verified_by_user_id else None,
            "verified_at": d.verified_at,
            "note": d.note,
            "created_at": d.created_at,
            "updated_at": d.updated_at,
        }
        for d in rows
    ]


@router.get("/projects/{project_id}/investor-signed-documents")
def admin_project_investor_signed_documents(
    project_id: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    require_admin(me)

    project_uuid = parse_uuid_or_400(project_id, "project id")

    p = db.execute(select(Project).where(Project.id == project_uuid)).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    rows = db.execute(
        select(SignedDocument)
        .where(
            SignedDocument.module_type == "investor_funding",
            SignedDocument.module_ref_id == p.id,
        )
        .order_by(SignedDocument.created_at.desc())
    ).scalars().all()

    return [
        {
            "id": str(d.id),
            "module_type": d.module_type,
            "module_ref_id": str(d.module_ref_id),
            "generated_document_id": str(d.generated_document_id) if d.generated_document_id else None,
            "uploaded_by_user_id": str(d.uploaded_by_user_id) if d.uploaded_by_user_id else None,
            "file_name": d.file_name,
            "storage_path": d.storage_path,
            "mime_type": d.mime_type,
            "file_size": int(d.file_size or 0) if d.file_size is not None else None,
            "verification_status": d.verification_status,
            "verified_by_user_id": str(d.verified_by_user_id) if d.verified_by_user_id else None,
            "verified_at": d.verified_at,
            "note": d.note,
            "created_at": d.created_at,
            "updated_at": d.updated_at,
        }
        for d in rows
    ]