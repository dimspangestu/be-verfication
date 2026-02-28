# api_key_guard.py
import os
from fastapi import Header, HTTPException, status

API_KEY = os.getenv("API_KEY", "")

def verify_api_key(key: str = Header(None)):
    if not key or key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API key"
        )