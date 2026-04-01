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
GOOGLE_SHEET_TAB       = os.environ.get("GOOGLE_SHEET_TAB", "ORDERS")
AFFILIATE_TAB          = os.environ.get("AFFILIATE_TAB", "Affiliate Database")
GOOGLE_CREDS_JSON      = os.environ.get("GOOGLE_CREDS_JSON", "")

# ── CACHED CLIENTS (built once, reused) ──────────────────────────────────────
_sheets_service = None
_token_cache = {"token": None, "expires_at": None}


def get_sheets_service():
    global _sheets_service
    if _sheets_service is None:
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        _sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return _sheets_service


async def get_shopify_token() -> str:
    now = datetime.now(timezone.utc).timestamp()
    if _token_cache["token"] and _token_cache["expires_at"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]
    url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/oauth/access_token"
    async with httpx.AsyncClient(timeout=10) as client:
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


def get_affiliate_codes() -> set:
    """Pull all discount codes from column B of the Affiliate Database sheet."""
    service = get_sheets_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=GOOGLE_SHEET_ID,
        range=f"{AFFILIATE_TAB}!B2:B1000"
    ).execute()
    values = result.get("values", [])
    return {row[0].strip().lower() for row in values if row and row[0].strip()}


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
    try:
        token = await get_shopify_token()
        url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/2024-01/customers/{customer_id}.json"
        headers = {"X-Shopify-Access-Token": token}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json().get("customer", {}).get("orders_count", 0)
    except Exception:
        pass
    return 0


@app.post("/webhook/orders/create")
async def orders_create(request: Request):
    body = await request.body()
    order = json.loads(body)

    # 1. Check if order has a discount code
    discount_codes = order.get("discount_codes", [])
    if not discount_codes:
        return JSONResponse(content={"status": "skipped - no discount code"}, status_code=200)

    applied_code = discount_codes[0].get("code", "").strip().lower()

    # 2. Check against Affiliate Database
    try:
        affiliate_codes = get_affiliate_codes()
        if applied_code not in affiliate_codes:
            return JSONResponse(content={"status": f"skipped - not in affiliate database"}, status_code=200)
    except Exception as e:
        return JSONResponse(content={"status": f"error checking affiliates: {str(e)}"}, status_code=500)

    # 3. Extract order fields
    raw_date = order.get("created_at", "")
    try:
        purchase_date = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).strftime("%-m/%d/%Y %H:%M:%S")
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

    try:
        append_row(row)
    except Exception as e:
        return JSONResponse(content={"status": f"error writing to sheet: {str(e)}"}, status_code=500)

    return JSONResponse(content={"status": "ok", "code": discount_code}, status_code=200)


@app.get("/")
def health():
    return {"status": "Fitasy webhook server is running"}
