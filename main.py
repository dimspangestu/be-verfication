from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from db import Base, engine
from api_key_guard import verify_api_key

from auth_routes import router as auth_router
from vendor_routes import router as vendor_router
from project_routes import router as project_router

# import semua model agar create_all mengenali tabelnya
from models import User, TokenBlacklist, PasswordResetToken
from models_vendor_project import Vendor, Project, ProjectDocument, ProjectConsent
from admin_routes import router as admin_router
from investor_funding_routes import router as investor_router
from payment_webhook_routes import router as payment_router
from models_admin_funding import ProjectFunding, PaymentTransaction, LedgerEntry
from investor_funding_routes import router as investor_router
from vendor_signature_routes import router as vendor_signature_router
from models_admin_funding import SignatureRequest
from signed_document_routes import router as signed_document_router
from models_documents import DocumentTemplate, GeneratedDocument, SignedDocument
from document_routes import router as document_router
app = FastAPI(
    title="Fondofund Auth API",
    version="1.0",
    dependencies=[Depends(verify_api_key)],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3123",
        "http://localhost:3000",
        "http://localhost:5173",
        "https://fondofund.com/",
        "www.fondofund.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)

app.include_router(auth_router, dependencies=[Depends(verify_api_key)])
app.include_router(vendor_router, dependencies=[Depends(verify_api_key)])
app.include_router(project_router, dependencies=[Depends(verify_api_key)])
app.include_router(admin_router, dependencies=[Depends(verify_api_key)])
app.include_router(investor_router, dependencies=[Depends(verify_api_key)])
app.include_router(payment_router)
app.include_router(investor_router, dependencies=[Depends(verify_api_key)])
app.include_router(vendor_signature_router, dependencies=[Depends(verify_api_key)])
app.include_router(signed_document_router)
app.include_router(document_router)
@app.get("/health")
def health():
    return {"ok": True}