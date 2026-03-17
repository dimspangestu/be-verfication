import os
import uuid
from pathlib import Path
from fastapi import UploadFile, HTTPException

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "20"))
MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024

ALLOWED_EXTENSIONS = {
    "proposal": {".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png"},
    "rab": {".pdf", ".xls", ".xlsx", ".doc", ".docx", ".jpg", ".jpeg", ".png"},
    "lainnya": {".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png", ".xls", ".xlsx"},
}


def ensure_dir(path: str):
    Path(path).mkdir(parents=True, exist_ok=True)


def get_ext(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def validate_extension(doc_type: str, filename: str):
    ext = get_ext(filename)
    allowed = ALLOWED_EXTENSIONS.get(doc_type, set())
    if ext not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"File extension not allowed for {doc_type}. Allowed: {', '.join(sorted(allowed))}"
        )


async def save_upload_file(project_id: str, doc_type: str, file: UploadFile):
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail=f"{doc_type} file is required")

    validate_extension(doc_type, file.filename)

    base_dir = os.path.join(UPLOAD_DIR, "projects", str(project_id))
    ensure_dir(base_dir)

    ext = get_ext(file.filename)
    stored_name = f"{doc_type}_{uuid.uuid4().hex}{ext}"
    abs_path = os.path.join(base_dir, stored_name)

    size = 0
    with open(abs_path, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_FILE_SIZE:
                out.close()
                try:
                    os.remove(abs_path)
                except OSError:
                    pass
                raise HTTPException(
                    status_code=400,
                    detail=f"File too large. Max {MAX_FILE_SIZE_MB} MB"
                )
            out.write(chunk)

    rel_path = os.path.join(UPLOAD_DIR, "projects", str(project_id), stored_name).replace("\\", "/")

    return {
        "file_name": file.filename,
        "mime": file.content_type,
        "file_size": size,
        "storage_path": rel_path,
    }