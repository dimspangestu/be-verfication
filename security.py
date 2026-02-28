import os, secrets, hashlib, uuid
from datetime import datetime, timedelta, timezone
from jose import jwt
from passlib.context import CryptContext

# ✅ anti 72-byte issue by design
pwd_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto"
)


JWT_SECRET = os.getenv("JWT_SECRET", "CHANGE_ME")
JWT_ALG = os.getenv("JWT_ALG", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "120"))
RESET_TOKEN_EXPIRE_MINUTES = int(os.getenv("RESET_TOKEN_EXPIRE_MINUTES", "30"))

def hash_password(password: str) -> str:
    return pwd_context.hash(password or "")

def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password or "", password_hash)

def create_access_token(subject: str, user_id: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    jti = uuid.uuid4().hex
    payload = {
        "sub": subject,
        "uid": user_id,
        "role": role,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def decode_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])

class AuthError(Exception):
    pass

def parse_bearer(auth_header: str | None) -> str:
    if not auth_header:
        raise AuthError("Missing Authorization header")
    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthError("Invalid Authorization header")
    return parts[1].strip()

def generate_reset_token() -> str:
    return secrets.token_urlsafe(48)

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def reset_token_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES)