# auth_routes.py
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, EmailStr
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

FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:3000")


# ─────────────────────────────────────────────
#  Extra schemas (inline — no need to touch schemas.py)
# ─────────────────────────────────────────────

class ChangeEmailIn(BaseModel):
    new_email:        EmailStr
    current_password: str

class ChangeProfileIn(BaseModel):
    name:  str | None = None
    phone: str | None = None


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password:     str

    def validate_strength(self):
        if len(self.new_password) < 8:
            raise ValueError("Password baru minimal 8 karakter.")
        if self.current_password == self.new_password:
            raise ValueError("Password baru tidak boleh sama dengan password lama.")


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def ensure_role(role: str):
    if role not in ("penerima_dana", "investor", "admin"):
        raise HTTPException(
            status_code=400,
            detail="role must be 'penerima_dana', 'investor', or 'admin'",
        )


def token_is_blacklisted(db: Session, jti: str) -> bool:
    return (
        db.execute(
            select(TokenBlacklist).where(TokenBlacklist.jti == jti)
        ).scalar_one_or_none()
        is not None
    )


def get_current_user(
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    try:
        token   = parse_bearer(authorization)
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


# ─────────────────────────────────────────────
#  Register
# ─────────────────────────────────────────────

@router.post("/register", response_model=UserOut)
def register(payload: RegisterIn, db: Session = Depends(get_db)):
    ensure_role(payload.role)

    email    = str(payload.email).lower().strip()
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

    return UserOut(id=str(user.id), name=user.name, email=user.email, phone=user.phone, role=user.role)


# ─────────────────────────────────────────────
#  Login
# ─────────────────────────────────────────────

@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, db: Session = Depends(get_db)):
    email = str(payload.email).lower().strip()
    user  = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(subject=user.email, user_id=str(user.id), role=user.role)
    return TokenOut(access_token=token)


# ─────────────────────────────────────────────
#  Logout
# ─────────────────────────────────────────────

@router.post("/logout", response_model=MessageOut)
def logout(db: Session = Depends(get_db), authorization: str = Header(default=None)):
    try:
        token   = parse_bearer(authorization)
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


# ─────────────────────────────────────────────
#  Me
# ─────────────────────────────────────────────

@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut(id=str(user.id), name=user.name, email=user.email, phone=user.phone, role=user.role)


# ─────────────────────────────────────────────
#  Change Email
# ─────────────────────────────────────────────

@router.patch("/me/profile", response_model=UserOut)
def change_profile(
    payload: ChangeProfileIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """Update name and/or phone number."""
    changed = False

    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Nama tidak boleh kosong.")
        me.name  = name
        changed  = True

    if payload.phone is not None:
        me.phone = payload.phone.strip() or None
        changed  = True

    if changed:
        me.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(me)

    return UserOut(id=str(me.id), name=me.name, email=me.email, phone=me.phone, role=me.role)


@router.patch("/me/email", response_model=UserOut)
def change_email(
    payload: ChangeEmailIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """
    Change the authenticated user's email.
    Requires current password for verification.
    New email must not be taken by another account.
    """
    # Verify current password
    if not verify_password(payload.current_password, me.password_hash):
        raise HTTPException(status_code=400, detail="Password saat ini tidak sesuai.")

    new_email = str(payload.new_email).lower().strip()

    # Check not same as current
    if new_email == me.email.lower():
        raise HTTPException(status_code=400, detail="Email baru sama dengan email saat ini.")

    # Check not taken
    existing = db.execute(
        select(User).where(User.email == new_email, User.id != me.id)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Email sudah digunakan akun lain.")

    old_email = me.email
    me.email  = new_email
    me.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(me)

    # Notify old email
    try:
        send_email(
            to_email=old_email,
            subject="Email Akun Fondofund Berhasil Diubah",
            html_body=f"""
            <div style="font-family:Arial,sans-serif;line-height:1.6;max-width:520px;margin:0 auto">
              <div style="background:#083A57;padding:20px 28px;border-radius:12px 12px 0 0">
                <div style="color:#fff;font-size:20px;font-weight:900">Fondofund</div>
              </div>
              <div style="background:#fff;padding:28px;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 12px 12px">
                <h2 style="margin:0 0 12px;color:#0f172a">Email Akun Diubah</h2>
                <p style="color:#475569">Email akun Fondofund Anda telah berhasil diubah menjadi:</p>
                <div style="background:#f1f5f9;border-radius:8px;padding:12px 16px;margin:16px 0;font-weight:700;color:#0f172a">
                  {new_email}
                </div>
                <p style="color:#475569;font-size:13px">
                  Jika Anda tidak merasa melakukan perubahan ini, segera hubungi tim support kami.
                </p>
              </div>
            </div>
            """,
            text_body=f"Email akun Fondofund Anda telah diubah menjadi {new_email}.",
        )
    except Exception:
        pass  # non-fatal

    return UserOut(id=str(me.id), name=me.name, email=me.email, phone=me.phone, role=me.role)


# ─────────────────────────────────────────────
#  Change Password (authenticated)
# ─────────────────────────────────────────────

@router.patch("/me/password", response_model=MessageOut)
def change_password(
    payload: ChangePasswordIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """
    Change password while authenticated.
    Requires current password.
    """
    # Verify current password
    if not verify_password(payload.current_password, me.password_hash):
        raise HTTPException(status_code=400, detail="Password saat ini tidak sesuai.")

    # Validate new password
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=422, detail="Password baru minimal 8 karakter.")

    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="Password baru tidak boleh sama dengan password lama.")

    me.password_hash = hash_password(payload.new_password)
    me.updated_at    = datetime.now(timezone.utc)
    db.commit()

    # Notify via email
    try:
        send_email(
            to_email=me.email,
            subject="Password Fondofund Berhasil Diubah",
            html_body=f"""
            <div style="font-family:Arial,sans-serif;line-height:1.6;max-width:520px;margin:0 auto">
              <div style="background:#083A57;padding:20px 28px;border-radius:12px 12px 0 0">
                <div style="color:#fff;font-size:20px;font-weight:900">Fondofund</div>
              </div>
              <div style="background:#fff;padding:28px;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 12px 12px">
                <h2 style="margin:0 0 12px;color:#0f172a">Password Berhasil Diubah</h2>
                <p style="color:#475569">Password akun Fondofund Anda ({me.email}) telah berhasil diubah.</p>
                <p style="color:#475569;font-size:13px">
                  Jika Anda tidak merasa melakukan perubahan ini, segera hubungi tim support kami atau
                  gunakan fitur <b>Lupa Password</b> untuk mengamankan akun Anda.
                </p>
              </div>
            </div>
            """,
            text_body=f"Password akun Fondofund ({me.email}) telah berhasil diubah.",
        )
    except Exception:
        pass  # non-fatal

    return MessageOut(message="Password berhasil diubah. Silakan login ulang.")


# ─────────────────────────────────────────────
#  Forgot Password
# ─────────────────────────────────────────────

@router.post("/forgot-password", response_model=MessageOut)
def forgot_password(payload: ForgotPasswordIn, db: Session = Depends(get_db)):
    """Anti user enumeration — always return success."""
    email = str(payload.email).lower().strip()
    user  = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    if user and user.is_active:
        raw_token = generate_reset_token()
        token_h   = hash_token(raw_token)
        exp       = reset_token_expiry()

        db.add(PasswordResetToken(
            user_id=user.id, token_hash=token_h, expires_at=exp, used_at=None,
        ))
        db.commit()

        reset_link = f"{FRONTEND_BASE_URL}/auth/reset-password?token={raw_token}"

        try:
            send_email(
                to_email=user.email,
                subject="Reset Password Fondofund",
                html_body=f"""
                <div style="font-family:Arial,sans-serif;line-height:1.6;max-width:520px;margin:0 auto">
                  <div style="background:#083A57;padding:20px 28px;border-radius:12px 12px 0 0">
                    <div style="color:#fff;font-size:20px;font-weight:900">Fondofund</div>
                    <div style="color:#a5c8e1;font-size:13px">Platform Pendanaan Proyek</div>
                  </div>
                  <div style="background:#fff;padding:28px;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 12px 12px">
                    <h2 style="margin:0 0 12px;color:#0f172a">Reset Password</h2>
                    <p style="color:#475569">
                      Kami menerima permintaan reset password untuk akun:
                      <strong>{user.email}</strong>
                    </p>
                    <p style="color:#475569">Klik tombol berikut untuk membuat password baru:</p>
                    <div style="text-align:center;margin:24px 0">
                      <a href="{reset_link}"
                         style="background:#083A57;color:#fff;padding:14px 28px;border-radius:12px;
                                text-decoration:none;display:inline-block;font-weight:900;font-size:14px">
                        Reset Password
                      </a>
                    </div>
                    <p style="color:#64748b;font-size:12px">
                      Link ini berlaku selama <strong>1 jam</strong>.
                      Jika Anda tidak merasa meminta reset password, abaikan email ini.
                    </p>
                    <hr style="border:none;border-top:1px solid #e2e8f0;margin:20px 0"/>
                    <p style="color:#94a3b8;font-size:11px">Atau copy link:</p>
                    <p style="color:#64748b;font-size:11px;word-break:break-all">{reset_link}</p>
                  </div>
                </div>
                """,
                text_body=f"Reset password Fondofund: {reset_link} (berlaku 1 jam)",
            )
        except Exception:
            pass

    return MessageOut(message="Jika email terdaftar, link reset password akan dikirim ke inbox Anda.")


# ─────────────────────────────────────────────
#  Verify Reset Token
# ─────────────────────────────────────────────

@router.get("/reset-password/verify", response_model=MessageOut)
def verify_reset_password_token(token: str, db: Session = Depends(get_db)):
    token_h = hash_token(token)
    row = db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_h)
    ).scalar_one_or_none()

    if not row:
        raise HTTPException(status_code=400, detail="Token tidak valid.")
    if row.used_at is not None:
        raise HTTPException(status_code=400, detail="Token sudah digunakan.")
    # MSSQL returns naive datetimes — compare both as naive UTC
    expires_cmp = row.expires_at.replace(tzinfo=None) if getattr(row.expires_at, 'tzinfo', None) else row.expires_at
    now_cmp     = datetime.utcnow()
    if expires_cmp <= now_cmp:
        raise HTTPException(status_code=400, detail="Token sudah expired.")

    return MessageOut(message="Token valid")


# ─────────────────────────────────────────────
#  Reset Password
# ─────────────────────────────────────────────

@router.post("/reset-password", response_model=MessageOut)
def reset_password(payload: ResetPasswordIn, db: Session = Depends(get_db)):
    token_h = hash_token(payload.token)
    row = db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_h)
    ).scalar_one_or_none()

    if not row:
        raise HTTPException(status_code=400, detail="Token tidak valid.")
    if row.used_at is not None:
        raise HTTPException(status_code=400, detail="Token sudah digunakan.")
    expires_cmp = row.expires_at.replace(tzinfo=None) if getattr(row.expires_at, 'tzinfo', None) else row.expires_at
    now_cmp     = datetime.utcnow()
    if expires_cmp <= now_cmp:
        raise HTTPException(status_code=400, detail="Token sudah expired.")

    if len(payload.new_password) < 8:
        raise HTTPException(status_code=422, detail="Password minimal 8 karakter.")

    user = db.get(User, row.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail="User tidak valid.")

    user.password_hash = hash_password(payload.new_password)
    user.updated_at    = datetime.utcnow()
    row.used_at        = datetime.utcnow()
    db.commit()

    return MessageOut(message="Password berhasil direset. Silakan login.")