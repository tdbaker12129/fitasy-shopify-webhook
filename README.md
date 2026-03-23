# Fitticy — Shopify → Google Sheets Webhook

Automatically logs every new Shopify order to a Google Sheet with these columns:

| Col | Field                        |
|-----|------------------------------|
| A   | Purchase Date                |
| B   | Total Price                  |
| C   | Discount Code                |
| D   | Order Number (#1001)         |
| E   | Customer Email               |
| F   | Customer Phone               |
| G   | Customer Display Name        |
| H   | Formatted Address (City, ST) |
| I   | State (province code)        |
| J   | All-Time Order Count         |
| K   | Total Line Items             |

---

## Setup Guide

### Step 1 — Google Sheets Service Account

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use existing)
3. Enable **Google Sheets API**
4. Go to **IAM & Admin → Service Accounts → Create Service Account**
5. Download the JSON key file
6. Open your Google Sheet → Share it with the service account email (Editor access)
7. Copy the entire contents of the JSON key — you'll need it as `GOOGLE_CREDS_JSON`

### Step 2 — Shopify Custom App

1. In Shopify Admin go to **Settings → Apps → Develop apps**
2. Create a new app called "Fitticy Order Logger"
3. Under **Configuration → Admin API**, add these scopes:
   - `read_orders`
   - `read_customers`
4. Install the app and copy the **Admin API access token** → `SHOPIFY_ACCESS_TOKEN`

### Step 3 — Deploy to Render (free)

1. Push this folder to a GitHub repo
2. Go to [render.com](https://render.com) → New → Web Service → connect your repo
3. Render will auto-detect `render.yaml`
4. Add these environment variables in the Render dashboard:

| Variable               | Value                                      |
|------------------------|--------------------------------------------|
| SHOPIFY_WEBHOOK_SECRET | (set after step 4 below, or leave blank)  |
| SHOPIFY_STORE_DOMAIN   | fitticy.myshopify.com                      |
| SHOPIFY_ACCESS_TOKEN   | from Step 2                                |
| GOOGLE_SHEET_ID        | from your Sheet URL (the long ID string)   |
| GOOGLE_SHEET_TAB       | Orders (or whatever your tab is named)     |
| GOOGLE_CREDS_JSON      | paste the entire service account JSON      |

5. Deploy — your URL will be something like `https://fitticy-shopify-webhook.onrender.com`

### Step 4 — Register the Shopify Webhook

1. In Shopify Admin go to **Settings → Notifications → Webhooks**
2. Add webhook:
   - **Event**: Order creation
   - **URL**: `https://fitticy-shopify-webhook.onrender.com/webhook/orders/create`
   - **Format**: JSON
3. Copy the **Signing secret** → set it as `SHOPIFY_WEBHOOK_SECRET` in Render

### Step 5 — Add Headers to your Google Sheet

In your sheet's first row, add these headers in order:
```
Purchase Date | Total Price | Discount Code | Order Number | Email | Phone | Customer Name | Shipping Address | State | All-Time Orders | Total Items
```

---

## Local Testing

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your values
uvicorn main:app --reload
```

Then use [ngrok](https://ngrok.com) to expose it:
```bash
ngrok http 8000
```
Use the ngrok URL as your Shopify webhook URL for testing.
