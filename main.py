import os
import hmac
import hashlib
import base64
import json
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials

app = FastAPI()

# ── ENV VARS (set these in Render dashboard) ──────────────────────────────────
SHOPIFY_CLIENT_ID      = os.environ.get("SHOPIFY_CLIENT_ID", "")
SHOPIFY_CLIENT_SECRET  = os.environ.get("SHOPIFY_CLIENT_SECRET", "")
SHOPIFY_STORE_DOMAIN   = os.environ.get("SHOPIFY_STORE_DOMAIN", "fitasy-ai.myshopify.com")
GOOGLE_SHEET_ID        = os.environ.get("GOOGLE_SHEET_ID", "1Z7B-Q9j13aMcH7Ye0ixjVcWeALyQLzk9f3qAGoig_QQ")
GOOGLE_SHEET_TAB       = os.environ.get("GOOGLE_SHEET_TAB", "Orders")
GOOGLE_CREDS_JSON      = os.environ.get("GOOGLE_CREDS_JSON", "")

# ── TOKEN CACHE ───────────────────────────────────────────────────────────────
_token_cache = {"token": None, "expires_at": None}


async def get_shopify_token() -> str:
    """Fetch a fresh Shopify access token using client credentials, cache it for 23hrs."""
    now = datetime.now(timezone.utc).timestamp()
    if _token_cache["token"] and _token_cache["expires_at"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/oauth/access_token"
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, data={
            "grant_type": "client_credentials",
            "client_id": SHOPIFY_CLIENT_ID,
            "client_secret": SHOPIFY_CLIENT_SECRET,
        })
        resp.raise_for_status()
        data = resp.json()
        _token_cache["token"] = data["access_token"]
        _token_cache["expires_at"] = now + 23 * 3600  # refresh after 23hrs (token lasts 24)
        return _token_cache["token"]


# ── GOOGLE SHEETS HELPER ──────────────────────────────────────────────────────
def get_sheets_service():
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=creds)


def append_row(row: list):
    service = get_sheets_service()
    body = {"values": [row]}
    service.spreadsheets().values().append(
        spreadsheetId=GOOGLE_SHEET_ID,
        range=f"{GOOGLE_SHEET_TAB}!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body=body
    ).execute()


# ── SHOPIFY HELPERS ───────────────────────────────────────────────────────────
def verify_shopify_webhook(data: bytes, hmac_header: str) -> bool:
    """Verify the webhook actually came from Shopify using client secret."""
    if not SHOPIFY_CLIENT_SECRET:
        return True  # skip verification in dev if secret not set
    digest = hmac.new(
        SHOPIFY_CLIENT_SECRET.encode("utf-8"),
        data,
        hashlib.sha256
    ).digest()
    computed = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(computed, hmac_header)


async def get_customer_order_count(customer_id: int) -> int:
    """Fetch all-time order count for a customer from Shopify API."""
    if not customer_id:
        return 0
    token = await get_shopify_token()
    url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/2024-01/customers/{customer_id}.json"
    headers = {"X-Shopify-Access-Token": token}
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code == 200:
            return resp.json().get("customer", {}).get("orders_count", 0)
    return 0


# ── WEBHOOK ENDPOINT ──────────────────────────────────────────────────────────
@app.post("/webhook/orders/create")
async def orders_create(request: Request):
    body = await request.body()

    # 1. Verify signature
    hmac_header = request.headers.get("X-Shopify-Hmac-Sha256", "")
    if not verify_shopify_webhook(body, hmac_header):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    order = json.loads(body)

    # 2. Extract fields
    # Purchase date
    raw_date = order.get("created_at", "")
    try:
        purchase_date = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        purchase_date = raw_date

    # Total price
    total_price = order.get("total_price", "")

    # Discount code (first one if multiple)
    discount_codes = order.get("discount_codes", [])
    discount_code = discount_codes[0].get("code", "") if discount_codes else ""

    # Order number (e.g. #1001)
    order_number = order.get("name", "")

    # Customer email & phone
    email = order.get("email", "")
    phone = order.get("phone", "") or order.get("billing_address", {}).get("phone", "")

    # Customer display name
    billing = order.get("billing_address", {})
    customer_display_name = billing.get("name", "")

    # Shipping address
    shipping = order.get("shipping_address", {})
    shipping_city    = shipping.get("city", "")
    shipping_province = shipping.get("province", "")         # e.g. "California"
    shipping_state   = shipping.get("province_code", "")     # e.g. "CA"
    formatted_address = f"{shipping_city}, {shipping_province}" if shipping_city else ""

    # All-time order count
    customer_id = (order.get("customer") or {}).get("id")
    all_time_orders = await get_customer_order_count(customer_id)

    # Total line items quantity
    line_items = order.get("line_items", [])
    total_line_items = sum(item.get("quantity", 0) for item in line_items)

    # 3. Build row in your desired column order
    row = [
        purchase_date,          # A - Purchase Date
        total_price,            # B - Total Price
        discount_code,          # C - Discount Code
        order_number,           # D - Order Number
        email,                  # E - Customer Email
        phone,                  # F - Customer Phone
        customer_display_name,  # G - Customer Display Name
        formatted_address,      # H - Formatted Shipping Address (City, State)
        shipping_state,         # I - State (province code)
        all_time_orders,        # J - All-Time Order Count
        total_line_items,       # K - Total Line Items
    ]

    # 4. Append to Google Sheet
    append_row(row)

    return JSONResponse(content={"status": "ok"}, status_code=200)


# ── HEALTH CHECK ──────────────────────────────────────────────────────────────
@app.get("/")
def health():
    return {"status": "Fitasy webhook server is running"}
