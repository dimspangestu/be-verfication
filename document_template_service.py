import json
from sqlalchemy import select
from models_documents import DocumentTemplate

def render_template_html(template_html: str, payload: dict) -> str:
    html = template_html
    for key, value in payload.items():
        html = html.replace("{{" + key + "}}", str(value if value is not None else ""))
    return html

def get_active_template(db, module_type: str, template_code: str | None = None):
    stmt = select(DocumentTemplate).where(
        DocumentTemplate.module_type == module_type,
        DocumentTemplate.is_active == True
    )
    if template_code:
        stmt = stmt.where(DocumentTemplate.template_code == template_code)

    return db.execute(stmt.order_by(DocumentTemplate.version_no.desc())).scalar_one_or_none()

def payload_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)