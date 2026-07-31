import os
import requests
import base64
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# Load environment variables from config/.env relative to this file's location
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', '.env')
load_dotenv(dotenv_path)

# Daraja Credentials (Use Safaricom Developer Portal Keys)
CONSUMER_KEY = os.getenv("MPESA_CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("MPESA_CONSUMER_SECRET")
BUSINESS_SHORTCODE = os.getenv("MPESA_SHORTCODE", "174379")  # Sandbox Shortcode

# FIXED: Correct Safaricom Sandbox Passkey
PASSKEY = os.getenv("MPESA_PASSKEY", "bfb279f0929bdb0511d07142f21a0c28bea5ea772543fe284637c5fe96397383")
CALLBACK_URL = os.getenv("MPESA_CALLBACK_URL", "https://yourdomain.com/api/v1/mpesa/callback")

# Determine environment (sandbox vs production)
MPESA_ENVIRONMENT = os.getenv("MPESA_ENVIRONMENT", "sandbox").lower()
if MPESA_ENVIRONMENT == "production":
    OAUTH_URL = "https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
    STK_PUSH_URL = "https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
else:
    OAUTH_URL = "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
    STK_PUSH_URL = "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest"


def get_mpesa_access_token():
    """Obtains OAuth access token from Safaricom Daraja API."""
    if not CONSUMER_KEY or not CONSUMER_SECRET:
        raise Exception("M-PESA CONSUMER_KEY or CONSUMER_SECRET not configured in .env file.")
    
    response = requests.get(OAUTH_URL, auth=(CONSUMER_KEY, CONSUMER_SECRET), timeout=15)
    if response.status_code == 200:
        token = response.json().get("access_token")
        print(f"✅ M-PESA OAuth token acquired successfully.")
        return token
    raise Exception(f"Failed to acquire M-PESA OAuth token. Status: {response.status_code} - {response.text}")


def initiate_stk_push(phone_number, amount, account_reference):
    """
    Initiates an M-PESA STK Push (Lipa Na M-PESA Online) request.
    """
    access_token = get_mpesa_access_token()
    
    # FIXED: Enforce UTC+3 (Nairobi Timezone) to avoid timestamp drift errors
    eat_tz = timezone(timedelta(hours=3))
    timestamp = datetime.now(eat_tz).strftime("%Y%m%d%H%M%S")
    
    password = base64.b64encode(f"{BUSINESS_SHORTCODE}{PASSKEY}{timestamp}".encode()).decode('utf-8')
    
    # Format phone number to 2547XXXXXXXX / 2541XXXXXXXX
    formatted_phone = str(phone_number).strip().replace("+", "").replace(" ", "").replace("-", "")
    if formatted_phone.startswith("0"):
        formatted_phone = "254" + formatted_phone[1:]
    elif not formatted_phone.startswith("254"):
        formatted_phone = "254" + formatted_phone

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    payload = {
        "BusinessShortCode": BUSINESS_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(amount),
        "PartyA": formatted_phone,
        "PartyB": BUSINESS_SHORTCODE,
        "PhoneNumber": formatted_phone,
        "CallBackURL": CALLBACK_URL,
        "AccountReference": account_reference[:12],  # Safaricom truncates max 12 chars
        "TransactionDesc": "EarthGuard Premium Subscription"
    }

    print(f"📲 Initiating M-PESA STK Push to {formatted_phone} for KES {amount}...")
    response = requests.post(STK_PUSH_URL, json=payload, headers=headers, timeout=20)
    
    if response.status_code in [200, 201]:
        result = response.json()
        print(f"✅ M-PESA STK Push response: {result.get('ResponseDescription', 'Sent')}")
        return result
    else:
        print(f"❌ M-PESA STK Push failed: {response.status_code} - {response.text}")
        return {"error": "STK push request failed", "status_code": response.status_code, "details": response.text}