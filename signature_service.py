from datetime import datetime, timezone
from models_admin_funding import SignatureRequest

def utcnow():
    return datetime.now(timezone.utc)


def build_vendor_project_document_html(
    project_name: str,
    company_name: str,
    funding_needed: int,
    tenor_months: int,
    fixed_return_pct: float,
):
    return f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #0f172a; line-height: 1.6;">
        <h2>Dokumen Pengajuan Pendanaan Vendor</h2>
        <p><b>Nama Project:</b> {project_name}</p>
        <p><b>Nama Vendor:</b> {company_name}</p>
        <p><b>Nilai Pendanaan:</b> Rp {funding_needed:,}</p>
        <p><b>Tenor:</b> {tenor_months} bulan</p>
        <p><b>Fixed Return:</b> {fixed_return_pct}%</p>

        <p>
          Dengan ini saya menyatakan bahwa data dan pengajuan pendanaan yang saya ajukan
          adalah benar, dan saya menyetujui seluruh ketentuan yang berlaku pada platform Fondofund.
        </p>
      </body>
    </html>
    """


def build_investor_funding_document_html(
    project_name: str,
    vendor_name: str,
    funding_amount: int,
    tenor_months: int,
    fixed_return_pct: float,
    expected_return: int,
    total_back: int,
):
    return f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #0f172a; line-height: 1.6;">
        <h2>Persetujuan Pendanaan Investor</h2>
        <p><b>Nama Project:</b> {project_name}</p>
        <p><b>Vendor:</b> {vendor_name}</p>
        <p><b>Nilai Pendanaan:</b> Rp {funding_amount:,}</p>
        <p><b>Tenor:</b> {tenor_months} bulan</p>
        <p><b>Fixed Return:</b> {fixed_return_pct}%</p>
        <p><b>Estimasi Return:</b> Rp {expected_return:,}</p>
        <p><b>Estimasi Total Kembali:</b> Rp {total_back:,}</p>

        <p>
          Saya memahami risiko pendanaan dan menyetujui untuk menempatkan dana sesuai nominal di atas
          pada platform Fondofund.
        </p>
      </body>
    </html>
    """


def create_signature_request(
    *,
    module_type: str,
    module_ref_id,
    user_id=None,
    vendor_id=None,
    project_id=None,
    funding_id=None,
    signer_name=None,
    signer_email=None,
    signer_phone=None,
    document_name=None,
    document_html=None,
):
    return SignatureRequest(
        module_type=module_type,
        module_ref_id=module_ref_id,
        user_id=user_id,
        vendor_id=vendor_id,
        project_id=project_id,
        funding_id=funding_id,
        provider="mekari",
        document_name=document_name,
        document_html=document_html,
        signer_name=signer_name,
        signer_email=signer_email,
        signer_phone=signer_phone,
        status="waiting_signature",
        created_at=utcnow(),
        updated_at=utcnow(),
    )


def mark_signature_signed(sig: SignatureRequest):
    sig.status = "signed"
    sig.signed_at = utcnow()
    sig.updated_at = utcnow()