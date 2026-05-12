# Entity Detection Fix — Completed

## What Was Wrong

The frontend search logic was **backwards**. It classified charities as companies:

```javascript
// ❌ BROKEN
const digitsOnly = q.replace(/[A-Za-z]/g, '');
const isCompany = digitsOnly.length >= 7 && /^[A-Za-z]{0,2}\d+$/.test(q);
```

**Problem:** Charity numbers are 1-7 digits, company numbers are 8 digits. The condition `>= 7` caught both.

**Result:** Searching `1155899` (charity) → marked as company → hit Companies House API → 404 error.

---

## What Was Fixed

### 1. **Frontend Search Logic** (`frontend/public/index.html`)

**Before:**
```javascript
const digitsOnly = q.replace(/[A-Za-z]/g, '');
const isCompany = digitsOnly.length >= 7 && /^[A-Za-z]{0,2}\d+$/.test(q);
const type = isCompany ? 'company' : 'charity';
```

**After:**
```javascript
// Extract prefix (letters) and digits
const match = q.match(/^([A-Z]*)(\d+)$/i);
if (!match) {
  alert('Invalid entity number. Please enter a UK charity number (1-7 digits) or company number (8 digits or SC/NI prefix).');
  return;
}

const prefix = (match[1] || '').toUpperCase();
const digitsOnly = match[2];

let entityType = null;

// Charity: 1-7 digits, no prefix
if (prefix === '' && digitsOnly.length >= 1 && digitsOnly.length <= 7) {
  entityType = 'charity';
}
// Company: 8 digits (numeric) or SC/NI + 6 digits
else if ((prefix === '' && digitsOnly.length === 8) || 
         ((prefix === 'SC' || prefix === 'NI') && digitsOnly.length === 6)) {
  entityType = 'company';
}

if (!entityType) {
  alert('Invalid entity number. Please enter a UK charity number (1-7 digits) or company number (8 digits or SC/NI prefix).');
  return;
}
```

### 2. **Added Validation Tests** (`frontend/test-entity-detection.js`)

✅ **All 16 test cases passing:**

**Charities (valid):**
- `220949` (British Red Cross) ✅
- `1155899` (World Aid Convoy, 7 digits) ✅
- `1` (1-digit edge case) ✅
- `1234567` (7-digit edge case) ✅

**Companies (valid):**
- `09238471` (8-digit) ✅
- `SC123456` (Scottish company) ✅
- `NI123456` (N.I. company) ✅

**Invalid (rejected):**
- `123456789` (9 digits — too many) ✅
- `SC12345` (SC prefix but only 5 digits) ✅
- `XX123456` (invalid prefix) ✅

---

## How It Works Now

### Decision Tree

```
Input: "1155899"
  ↓
Match regex: /^([A-Z]*)(\d+)$/
  prefix = ""
  digits = "1155899"
  ↓
Check charity: prefix == "" AND 1 <= digits.length <= 7?
  YES ✓ "1155899" is 7 digits
  ↓
Route to: /api/preview/charity/1155899
  ↓
Charity Commission API ✅

---

Input: "09238471"
  ↓
Match regex: /^([A-Z]*)(\d+)$/
  prefix = ""
  digits = "09238471"
  ↓
Check charity: 1 <= digits.length <= 7?
  NO (8 digits)
  ↓
Check company: prefix == "" AND digits.length == 8?
  YES ✓
  ↓
Route to: /api/preview/company/09238471
  ↓
Companies House API ✅

---

Input: "SC123456"
  ↓
Match regex: /^([A-Z]*)(\d+)$/
  prefix = "SC"
  digits = "123456"
  ↓
Check company: (prefix in ["SC", "NI"]) AND digits.length == 6?
  YES ✓
  ↓
Route to: /api/preview/company/SC123456
  ↓
Companies House API (Scottish company) ✅
```

---

## Error Handling

If user enters invalid input, they now get a clear message:

```
Invalid entity number. 
Please enter a UK charity number (1-7 digits) 
or company number (8 digits or SC/NI prefix).
```

Examples that trigger error:
- `123456789` (9 digits — too many)
- `ABC` (no digits)
- `SC12345` (SC prefix but only 5 digits)
- `XX123456` (invalid prefix)

---

## Files Changed

| File | Change | Impact |
|------|--------|--------|
| `frontend/public/index.html` | Fixed search form detection logic | ✅ Searches now route correctly |
| `frontend/test-entity-detection.js` | Added 16 test cases | ✅ Validates detection logic |
| `ENTITY_TYPE_MAPPING.md` | Created comprehensive reference | 📚 Documentation for future |

---

## Testing

Run the test suite:

```bash
node frontend/test-entity-detection.js
```

**Expected output:**
```
=== Summary ===
Passed: 16/16
Failed: 0/16

🎉 All tests passed!
```

---

## Next Steps (Optional)

1. **Update preview error messages** — maybe show specific tips:
   - "This looks like a charity number. Are you sure it's not an 8-digit company number?"
   - "Scottish companies start with SC (e.g., SC123456). Check your input."

2. **Add input hints on frontend:**
   - Show "Enter 1-7 digits for charity" when field is focused
   - Show "8 digits or SC/NI prefix for company" dynamically

3. **Backend validation** — add to `api_clients/` to reject invalid formats early

4. **Support other UK company types:**
   - Jersey companies (JE)
   - Guernsey companies (GG)
   - Isle of Man companies (IM)
   - (Currently not supported by Companies House API, but good for future)

---

## Summary

✅ **Fixed:** Charity/Company detection logic  
✅ **Tested:** 16 comprehensive test cases (all passing)  
✅ **Documented:** Complete entity type mapping guide  
✅ **Error handling:** Clear user-facing messages  

**Result:** `1155899` now correctly routes to Charity Commission API instead of Companies House ✅
