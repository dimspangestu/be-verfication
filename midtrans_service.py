import os
import base64
import json
import requests

MIDTRANS_SERVER_KEY = os.getenv("MIDTRANS_SERVER_KEY", "")
MIDTRANS_CLIENT_KEY = os.getenv("MIDTRANS_CLIENT_KEY", "")
MIDTRANS_IS_PRODUCTION = os.getenv("MIDTRANS_IS_PRODUCTION", "false").lower() == "true"

MIDTRANS_BASE_URL = (
    "https://app.midtrans.com"
    if MIDTRANS_IS_PRODUCTION
    else "https://app.sandbox.midtrans.com"
)
MIDTRANS_API_URL = (
    "https://api.midtrans.com"
    if MIDTRANS_IS_PRODUCTION
    else "https://api.sandbox.midtrans.com"
)

def _basic_auth_header():
    raw = f"{MIDTRANS_SERVER_KEY}:".encode("utf-8")
    return base64.b64encode(raw).decode("utf-8")

def create_snap_transaction(order_id: str, gross_amount: int, first_name: str, email: str):
    url = f"{MIDTRANS_BASE_URL}/snap/v1/transactions"
    payload = {
        "transaction_details": {
            "order_id": order_id,
            "gross_amount": int(gross_amount),
        },
        "customer_details": {
            "first_name": first_name or "Customer",
            "email": email or "customer@example.com",
        },
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Basic {_basic_auth_header()}",
    }
    res = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
    return res.status_code, payload, res.json()

def get_transaction_status(order_id: str):
    url = f"{MIDTRANS_API_URL}/v2/{order_id}/status"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Basic {_basic_auth_header()}",
    }
    res = requests.get(url, headers=headers, timeout=30)
    return res.status_code, res.json()