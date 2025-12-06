#!/usr/bin/env python3
"""
Test the test printer endpoint directly
"""
import requests
import json

# Configuration
BASE_URL = "http://localhost:8080"
PRINTER_ID = "YOUR_PRINTER_ID_HERE"  # Replace with actual printer ID

# You need a valid auth token - get it from your app's login
TOKEN = "YOUR_TOKEN_HERE"  # Replace with actual token

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

print("Testing printer endpoint...")
print(f"Printer ID: {PRINTER_ID}")
print(f"URL: {BASE_URL}/settings/printers/{PRINTER_ID}/test")

try:
    response = requests.post(
        f"{BASE_URL}/settings/printers/{PRINTER_ID}/test",
        headers=headers,
        timeout=15
    )
    
    print(f"\nStatus Code: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    
except Exception as e:
    print(f"\nError: {e}")
    print(f"Response text: {response.text if 'response' in locals() else 'N/A'}")
