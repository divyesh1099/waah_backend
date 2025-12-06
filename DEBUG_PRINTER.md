# Printer Testing Checklist

## Step-by-Step Debugging

### 1. ✅ Mock Server is Running
You confirmed it's running on: `http://192.168.0.102:9100`

### 2. 🔍 Add Printer in App
1. Open app → Settings → Printers
2. Click "+ Add Printer" button
3. Fill in:
   - **Name**: "Test Printer"
   - **Type**: BILLING
   - **Connection URL**: `http://192.168.0.102:9100` (EXACT match from mock server)
4. Click Save

### 3. 🧪 Test the Printer
1. Find "Test Printer" in the list
2. Click the 🖨️ Test button
3. **Watch BOTH of these:**
   - **Backend terminal** (uvicorn): Should show logs like:
     ```
     🧪 Test printer called for ID: ...
     📡 Sending test print to: http://192.168.0.102:9100
     🚀 POSTing to http://192.168.0.102:9100
     ✅ Test print SUCCESS: ...
     ```
   - **Mock server terminal**: Should show:
     ```
     🖨️  PRINT JOB RECEIVED @ ...
     ```

### 4. 📱 Check App Feedback
- **Success**: Shows snackbar "Test job sent to Test Printer"
- **Failure**: Shows snackbar "Test failed: ..."

## ⚠️ If Still No Output:

### A) Backend Shows Nothing
❌ The test endpoint isn't being called at all
→ Check frontend console (F12) for errors
→ Make sure you're clicking the right button

### B) Backend Shows Logs BUT Mock Server Silent
❌ The printer URL in database doesn't match mock server
→ Delete and re-add printer with EXACT URL
→ Verify URL: `SELECT connection_url FROM printer;`

### C) Backend Shows Error
→ Share the error message with me

### D) App Shows "Success" BUT Nothing Prints
❌ Asyncio or HTTP client issue
→ Check backend logs for error details

## 🔧 Quick SQL Check
```sql
-- Check what URL is stored
SELECT id, name, connection_url FROM printer;
```

The URL MUST be exactly: `http://192.168.0.102:9100`
