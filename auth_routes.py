# auth_routes.py
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import get_db
from models import User, TokenBlacklist, PasswordResetToken
from schemas import (
    RegisterIn,
    LoginIn,
    TokenOut,
    UserOut,
    MessageOut,
    ForgotPasswordIn,
    ResetPasswordIn,
)
from security import (
    hash_password,
    verify_password,
    create_access_token,
    decode_token,
    parse_bearer,
    AuthError,
    generate_reset_token,
    hash_token,
    reset_token_expiry,
)
from emailer import send_email

router = APIRouter(prefix="/auth", tags=["auth"])

FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:3123")


def ensure_role(role: str):
    if role not in ("penerima_dana", "investor", "admin"):
        raise HTTPException(
            status_code=400, detail="role must be 'penerima_dana', 'investor', or 'admin'"
        )


def token_is_blacklisted(db: Session, jti: str) -> bool:
    q = select(TokenBlacklist).where(TokenBlacklist.jti == jti)
    return db.execute(q).scalar_one_or_none() is not None

def get_current_user(
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    try:
        token = parse_bearer(authorization)
        payload = decode_token(token)

        jti = payload.get("jti")
        uid = payload.get("uid")

        if not jti or not uid:
            raise HTTPException(status_code=401, detail="Invalid token")

        if token_is_blacklisted(db, jti):
            raise HTTPException(status_code=401, detail="Token revoked")

        user = db.get(User, uid)
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="User not found/inactive")

        return user
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


@router.post("/register", response_model=UserOut)
def register(payload: RegisterIn, db: Session = Depends(get_db)):
    ensure_role(payload.role)

    email = str(payload.email).lower().strip()
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        name=payload.name.strip(),
        email=email,
        phone=(payload.phone.strip() if payload.phone else None),
        role=payload.role,
        password_hash=hash_password(payload.password),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return UserOut(
        id=str(user.id),
        name=user.name,
        email=user.email,
        phone=user.phone,
        role=user.role,
    )


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, db: Session = Depends(get_db)):
    email = str(payload.email).lower().strip()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(subject=user.email, user_id=str(user.id), role=user.role)
    return TokenOut(access_token=token)


@router.post("/logout", response_model=MessageOut)
def logout(db: Session = Depends(get_db), authorization: str = Header(default=None)):
    try:
        token = parse_bearer(authorization)
        payload = decode_token(token)

        jti = payload.get("jti")
        uid = payload.get("uid")
        exp = payload.get("exp")

        if not jti or not uid or not exp:
            raise HTTPException(status_code=401, detail="Invalid token")

        if token_is_blacklisted(db, jti):
            return MessageOut(message="Already logged out")

        expires_at = datetime.fromtimestamp(int(exp), tz=timezone.utc)

        db.add(TokenBlacklist(jti=jti, user_id=uid, expires_at=expires_at))
        db.commit()
        return MessageOut(message="Logged out")
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut(
        id=str(user.id),
        name=user.name,
        email=user.email,
        phone=user.phone,
        role=user.role,
    )


@router.post("/forgot-password", response_model=MessageOut)
def forgot_password(payload: ForgotPasswordIn, db: Session = Depends(get_db)):
    """
    Anti user enumeration:
    - Selalu return sukses.
    - Kalau user ada, kirim email link reset.
    """
    email = str(payload.email).lower().strip()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    if user and user.is_active:
        raw_token = generate_reset_token()
        token_h = hash_token(raw_token)
        exp = reset_token_expiry()

        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=token_h,
                expires_at=exp,
                used_at=None,
            )
        )
        db.commit()

        reset_link = f"{FRONTEND_BASE_URL}/auth/reset-password?token={raw_token}"

        subject = "Reset Password FondoFund"
        html = f"""
        <div style="font-family:Arial,sans-serif;line-height:1.5">
          <h2>Reset Password</h2>
          <p>Kami menerima permintaan reset password untuk akun: <b>{user.email}</b></p>
          <p>Klik tombol di bawah untuk membuat password baru:</p>
          <p>
            <a href="{reset_link}"
               style="background:#083A57;color:#fff;padding:12px 16px;border-radius:10px;
                      text-decoration:none;display:inline-block">
              Reset Password
            </a>
          </p>
          <p>Atau copy link ini:</p>
          <p style="word-break:break-all">{reset_link}</p>
          <hr/>
          <p style="color:#64748b;font-size:12px">
            Jika kamu tidak merasa meminta reset password, abaikan email ini.
          </p>
        </div>
        """
        text = f"Reset password link: {reset_link}"

        # Jangan bocorkan error SMTP ke response
        try:
            send_email(to_email=user.email, subject=subject, html_body=html, text_body=text)
        except Exception:
            pass

    return MessageOut(message="Jika email terdaftar, link reset password akan dikirim.")


@router.get("/reset-password/verify", response_model=MessageOut)
def verify_reset_password_token(token: str, db: Session = Depends(get_db)):
    token_h = hash_token(token)
    row = db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_h)
    ).scalar_one_or_none()

    if not row:
        raise HTTPException(status_code=400, detail="Token invalid")
    if row.used_at is not None:
        raise HTTPException(status_code=400, detail="Token already used")
    if row.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Token expired")

    return MessageOut(message="Token valid")


@router.post("/reset-password", response_model=MessageOut)
def reset_password(payload: ResetPasswordIn, db: Session = Depends(get_db)):
    token_h = hash_token(payload.token)
    row = db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_h)
    ).scalar_one_or_none()

    if not row:
        raise HTTPException(status_code=400, detail="Token invalid")
    if row.used_at is not None:
        raise HTTPException(status_code=400, detail="Token already used")
    if row.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Token expired")

    user = db.get(User, row.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail="User invalid")

    user.password_hash = hash_password(payload.new_password)
    row.used_at = datetime.now(timezone.utc)

    db.commit()
    return MessageOut(message="Password berhasil direset. Silakan login.")