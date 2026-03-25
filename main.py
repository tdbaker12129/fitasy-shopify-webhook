import os
import json
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials

app = FastAPI()

# ── ENV VARS ──────────────────────────────────────────────────────────────────
SHOPIFY_CLIENT_ID      = os.environ.get("SHOPIFY_CLIENT_ID", "")
SHOPIFY_CLIENT_SECRET  = os.environ.get("SHOPIFY_CLIENT_SECRET", "")
SHOPIFY_STORE_DOMAIN   = os.environ.get("SHOPIFY_STORE_DOMAIN", "fitasy-ai.myshopify.com")
GOOGLE_SHEET_ID        = os.environ.get("GOOGLE_SHEET_ID", "1Z7B-Q9j13aMcH7Ye0ixjVcWeALyQLzk9f3qAGoig_QQ")
GOOGLE_SHEET_TAB       = os.environ.get("GOOGLE_SHEET_TAB", "Orders")
GOOGLE_CREDS_JSON      = os.environ.get("GOOGLE_CREDS_JSON", "")

_token_cache = {"token": None, "expires_at": None}


async def get_shopify_token() -> str:
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
        _token_cache["expires_at"] = now + 23 * 3600
        return _token_cache["token"]


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


async def get_customer_order_count(customer_id: int) -> int:
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


@app.post("/webhook/orders/create")
async def orders_create(request: Request):
    body = await request.body()
    order = json.loads(body)

    # FILTER — only log orders using Fitasy's discount codes:
    # 1. "FitasyAffiliate" (any amount)
    # 2. Any code that gives exactly $20 off
    discount_codes = order.get("discount_codes", [])
    if not discount_codes:
        return JSONResponse(content={"status": "skipped - no discount code"}, status_code=200)

    ALLOWED_CODES = {"fitasyaffiliate"}
    applied_code   = discount_codes[0].get("code", "").lower()
    applied_amount = float(discount_codes[0].get("amount", "0") or 0)

    is_allowed_code  = applied_code in ALLOWED_CODES
    is_twenty_dollars = applied_amount == 20.0

    if not (is_allowed_code or is_twenty_dollars):
        return JSONResponse(content={"status": "skipped - not an allowed discount"}, status_code=200)

    # Extract fields
    raw_date = order.get("created_at", "")
    try:
        purchase_date = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        purchase_date = raw_date

    total_price           = order.get("total_price", "")
    discount_code         = discount_codes[0].get("code", "")
    order_number          = order.get("name", "")
    email                 = order.get("email", "")
    phone                 = order.get("phone", "") or order.get("billing_address", {}).get("phone", "")
    billing               = order.get("billing_address", {})
    customer_display_name = billing.get("name", "")
    shipping              = order.get("shipping_address", {})
    shipping_city         = shipping.get("city", "")
    shipping_province     = shipping.get("province", "")
    shipping_state        = shipping.get("province_code", "")
    formatted_address     = f"{shipping_city}, {shipping_province}" if shipping_city else ""
    customer_id           = (order.get("customer") or {}).get("id")
    all_time_orders       = await get_customer_order_count(customer_id)
    line_items            = order.get("line_items", [])
    total_line_items      = sum(item.get("quantity", 0) for item in line_items)

    row = [
        purchase_date, total_price, discount_code, order_number,
        email, phone, customer_display_name, formatted_address,
        shipping_state, all_time_orders, total_line_items,
    ]

    append_row(row)
    return JSONResponse(content={"status": "ok"}, status_code=200)


@app.get("/")
def health():
    return {"status": "Fitasy webhook server is running"}
