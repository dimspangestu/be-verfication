import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ─────────────────────────────────────────────
#  Config — baca dari environment variables
# ─────────────────────────────────────────────

SMTP_HOST     = os.getenv("SMTP_HOST",      "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT",  "587"))
SMTP_USER     = os.getenv("SMTP_USER",      "fondofundofc@gmail.com")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD",  "")          # App Password 16 digit
EMAIL_FROM    = os.getenv("EMAIL_FROM",     "fondofundofc@gmail.com")
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "Fondofund")


def send_email(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str = "",
) -> None:

    if not to_email:
        raise ValueError("to_email tidak boleh kosong")

    if not SMTP_PASSWORD:
        raise RuntimeError(
            "SMTP_PASSWORD belum diset. "
            "Set environment variable SMTP_PASSWORD dengan App Password Gmail."
        )

    # Build message
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{EMAIL_FROM_NAME} <{EMAIL_FROM}>"
    msg["To"]      = to_email

    if text_body:
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # Send via STARTTLS (port 587)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(EMAIL_FROM, [to_email], msg.as_string())