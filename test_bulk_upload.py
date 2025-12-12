import requests
import io

BASE = "http://localhost:8000"  # or remote URL
USERNAME = "ashmita"
PASSWORD = "Ashmita1225*"

def login():
    print("Logging in...")
    r = requests.post(f"{BASE}/auth/login", json={
        "username": USERNAME,
        "password": PASSWORD
    })
    if r.status_code != 200:
        print("Login failed:", r.text)
        return None
    data = r.json()
    return data.get("access_token")

def test_upload():
    token = login()
    if not token:
        print("Aborting.")
        return

    csv_content = """category,name,variant_label,price,description,is_active
BulkTest,TestItem1,Regular,100,Details here,TRUE
BulkTest,TestItem1,Large,150,Details here,TRUE
BulkTest,TestItem2,Regular,200,New Item,TRUE
"""
    
    files = {
        'file': ('test_menu.csv', csv_content, 'text/csv')
    }
    headers = {
        "Authorization": f"Bearer {token}"
    }

    print("\nUploading CSV...")
    # Note: Using /menu/upload-csv (the name I gave the function in menu.py was upload_menu_csv but route was /upload-csv)
    # Wait, let me check the route decorator...
    # @router.post("/upload-csv")
    # Prefix is /menu. So /menu/upload-csv
    
    r = requests.post(f"{BASE}/menu/upload-csv", headers=headers, files=files)
    
    print(f"Status: {r.status_code}")
    print("Response:", r.text)

if __name__ == "__main__":
    test_upload()
