import os
import requests
import base64
from datetime import datetime

# Daraja Credentials (Use Safaricom Developer Portal Keys)
CONSUMER_KEY = os.getenv("MPESA_CONSUMER_KEY", "your_consumer_key")
CONSUMER_SECRET = os.getenv("MPESA_CONSUMER_SECRET", "your_consumer_secret")
BUSINESS_SHORTCODE = os.getenv("MPESA_SHORTCODE", "174379")  # Sandbox Shortcode
PASSKEY = os.getenv("MPESA_PASSKEY", "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919")
CALLBACK_URL = os.getenv("MPESA_CALLBACK_URL", "https://yourdomain.com/api/v1/mpesa/callback")

def get_mpesa_access_token():
    url = "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
    response = requests.get(url, auth=(CONSUMER_KEY, CONSUMER_SECRET))
    if response.status_code == 200:
        return response.json().get("access_token")
    raise Exception("Failed to acquire M-PESA OAuth token.")

def initiate_stk_push(phone_number, amount, account_reference):
    access_token = get_mpesa_access_token()
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(f"{BUSINESS_SHORTCODE}{PASSKEY}{timestamp}".encode()).decode('utf-8')
    
    # Format phone number to 2547XXXXXXXX
    formatted_phone = phone_number.strip().replace("+", "")
    if formatted_phone.startswith("0"):
        formatted_phone = "254" + formatted_phone[1:]

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
        "AccountReference": account_reference,
        "TransactionDesc": "EarthGuard Premium Subscription"
    }

    url = "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
    response = requests.post(url, json=payload, headers=headers)
    return response.json()