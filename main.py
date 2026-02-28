from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from db import Base, engine
from auth_routes import router as auth_router
from api_key_guard import verify_api_key

# load .env dulu
load_dotenv()

app = FastAPI(
    title="Fondofund Auth API",
    version="1.0",
    dependencies=[Depends(verify_api_key)]  
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3123",
        "http://localhost:3000",
        "http://localhost:5173"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Buat table saat startup (lebih aman daripada import time)
@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)

# Register router
app.include_router(auth_router)

@app.get("/health")
def health():
    return {"ok": True}