# Entity Type Mapping — Charity Commission vs Companies House

## The Problem

Your frontend detection logic is **backwards**:

```javascript
// Current (BROKEN)
const digitsOnly = q.replace(/[A-Za-z]/g, '');
const isCompany = digitsOnly.length >= 7 && /^[A-Za-z]{0,2}\d+$/.test(q);
```

This treats anything with ≥7 digits as a **company**, but charity numbers are **1-7 digits**, so they get misclassified.

Example: `1155899` (a real charity) → marked as company → hits company API → 404

---

## Charity Commission Entities

### Identifier Format

| Format | Digits | Example | Name |
|--------|--------|---------|------|
| Numeric only | 1-7 | `220949` | Charity number |
| Numeric only | 1-7 | `1155899` | Charity number (7 digits) |

**Key rule:** Charity numbers are ALWAYS numeric. No letters. 1-7 digits max.

### Entity Types in Charity Commission

The **charity_type** field can be:

| Type | Description | Legal Structure | CH Required? |
|------|-------------|-----------------|--------------|
| **Charitable company** | Limited by guarantee + registered with CC | Incorporated (has separate legal entity) | ✅ YES |
| **CIO** | Charitable Incorporated Organisation | Incorporated (limited liability) | ❌ NO |
| **Trust** | Unincorporated charity governed by trust deed | Unincorporated | ❌ NO |
| **Unincorporated association** | Membership-based unincorporated | Unincorporated | ❌ NO |
| **Royal Charter body** | Created by Royal Charter (universities, etc.) | Incorporated | ❌ Varies |
| **Community Benefit Society** | Registered with FCA (BenCom) | Registered society | ❌ NO |
| **Excepted charity** | Not registered (churches, scout groups, etc.) | Various | ❌ NO |
| **Exempt charity** | Regulated by other principal regulator (some universities) | Various | ❌ NO |

### Flags in Charity Commission API

```
{
  "in_administration": bool,        # Currently under administration
  "insolvent": bool,                # Insolvency proceedings
  "cio_ind": bool,                  # Is a CIO?
  "cio_dissolution_ind": bool,      # CIO dissolution flag
  "interim_manager_ind": bool,      # Has interim manager
  "date_of_removal": str,           # Removed from register (closed)
  "date_of_interim_manager_appt": str,
  "prev_excepted_ind": bool,        # Was previously excepted
  "reporting_status": str,          # e.g., "Overdue", "Up to date"
}
```

### Sample Data

```python
fetch_charity_data("220949")  # Real charity
→ {
    "charity_name": "British Red Cross",
    "charity_number": "220949",
    "organisation_number": "3732267",
    "company_number": "8506774",    # Some charities ARE also companies
    "charity_type": "Charitable company",
    "reg_status": "Registered",
    "date_of_registration": "1985-01-01",
    "in_administration": False,
    ...
}
```

---

## Companies House Entities

### Identifier Formats

| Prefix | Format | Digits | Example | Jurisdiction | Notes |
|--------|--------|--------|---------|--------------|-------|
| None | 8 digits (numeric only) | 8 | `09238471` | England & Wales | Standard company |
| `SC` | 2 letters + 6 digits | 6 | `SC123456` | Scotland | Scottish company |
| `NI` | 2 letters + 6 digits | 6 | `NI123456` | Northern Ireland | N.I. company |

**Key rule:** Companies are ALWAYS 8 digits total (numeric or 2 letters + 6 digits numeric).

### Entity Types in Companies House

The **type** field can be:

| Type | Description | Regulation | Public? |
|------|-------------|-----------|---------|
| **private-unlimited** | Private, unlimited liability | Private | ❌ NO |
| **private-limited** | Private, limited by shares | Most common | ❌ NO |
| **private-limited-guarant-nsc** | Private, limited by guarantee (non-charitable) | Private | ❌ NO |
| **limited-partnership** | Limited partnership | Partnership | ❌ NO |
| **private-limited-guarant-nsc-limited-exemption** | Private, limited by guarantee, smaller company exemption | Private | ❌ NO |
| **old-public-company** | Old-style public company (pre-1980s, rare) | Public | ✅ YES |
| **plc** | Public Limited Company | Public, heavily regulated | ✅ YES |
| **public-limited-guarant-nsc** | Public, limited by guarantee (rare) | Public | ✅ YES |
| **eeig** | European Economic Interest Grouping | European | ❌ NO |
| **se** | Societas Europaea (European company) | European | ❌ NO |
| **registered-society** | BenCom / co-operative society (FCA-regulated) | Regulated | ❌ NO |
| **investment-entity** | Investment entity | Specialist | ❌ NO |
| **conversion-to-SE** | Company converting to SE | European | ❌ NO |
| **protected-cell-company** | Protected cell company | Specialist | ❌ NO |
| **private-unlimited-nsc** | Private unlimited, non-standard | Rare | ❌ NO |

### Status Values in Companies House

The **company_status** field can be:

| Status | Meaning | Active? | Risk |
|--------|---------|--------|------|
| `active` | Operating normally | ✅ YES | Low |
| `dissolved` | Struck off (closed) | ❌ NO | High (defunct) |
| `liquidation` | In liquidation proceedings | ⚠️ WINDING DOWN | Medium-High |
| `administration` | In administration (financial distress) | ⚠️ DISTRESSED | High |
| `converted-closed` | Converted to new entity, old one closed | ❌ NO | High |
| `insolvency-proceedings` | Insolvency process ongoing | ⚠️ DISTRESSED | High |
| `receiver-manager` | Under receiver/manager control | ⚠️ CONTROLLED | High |

### Flags in Companies House API

```python
company_data = {
    "company_name": str,
    "company_number": str,           # 8 digits or 2 letters + 6 digits
    "type": str,                     # plc, private-limited, etc.
    "company_status": str,           # active, dissolved, etc.
    "date_of_creation": str,         # YYYY-MM-DD
    "registered_office_address": {
        "address_line_1": str,
        "address_line_2": str,
        "locality": str,
        "postal_code": str,
    },
    "sic_codes": list[str],          # Industry classification
    "officers": list[dict],          # Directors (name, role, appointed_on, etc.)
}
```

### Virtual Office Detection

Companies House has `_VIRTUAL_OFFICE_MARKERS` — known mailbox addresses:

```
"27 old gloucester street"
"20-22 Wenlock Road"
"71-75 Shelton Street"
"128 City Road"
(etc. — 15+ known virtual office addresses)
```

If registered office matches one of these → **red flag** (possible shell company).

---

## Quick Decision Tree

```
Input: entity_identifier (e.g., "220949" or "09238471" or "SC123456")

1. Strip whitespace: id = id.strip()
2. Extract prefix and digits:
   - prefix = letters at start (e.g., "SC", "NI", or "")
   - digits_only = numeric part
   
3. CHARITY check:
   IF prefix == "" AND 1 <= len(digits_only) <= 7 AND digits_only.isdigit()
      → CHARITY
      → Call: fetch_charity_data(id)
      → API: https://api.charitycommission.gov.uk/register/api
      
4. COMPANY check:
   IF (prefix == "" AND len(digits_only) == 8 AND digits_only.isdigit())
      OR (prefix in ["SC", "NI"] AND len(digits_only) == 6 AND digits_only.isdigit())
      → COMPANY
      → Call: fetch_ch_data(id)
      → API: https://api.company-information.service.gov.uk
      
5. INVALID:
   OTHERWISE → Error: "Not a valid UK charity or company number"
```

---

## Frontend Fix (JavaScript)

Replace the broken detection logic:

```javascript
function detectEntityType(q) {
  q = (q || '').trim().replace(/\s/g, '');
  if (!q) return null;
  
  // Extract prefix (letters) and digits
  const match = q.match(/^([A-Z]*)(\d+)$/i);
  if (!match) return null;
  
  const prefix = (match[1] || '').toUpperCase();
  const digitsOnly = match[2];
  
  // Charity: 1-7 digits, no prefix
  if (prefix === '' && digitsOnly.length >= 1 && digitsOnly.length <= 7) {
    return { type: 'charity', id: q };
  }
  
  // Company: 8 digits (numeric) or 2 letters + 6 digits
  if (prefix === '' && digitsOnly.length === 8) {
    return { type: 'company', id: q };
  }
  
  // Scottish / NI company
  if ((prefix === 'SC' || prefix === 'NI') && digitsOnly.length === 6) {
    return { type: 'company', id: q };
  }
  
  return null;
}

// Usage
const searchForm = document.getElementById('searchForm');
searchForm.addEventListener('submit', (e) => {
  e.preventDefault();
  const q = searchForm.q.value;
  const result = detectEntityType(q);
  
  if (!result) {
    alert('Not a valid UK charity (1-7 digits) or company number (8 digits or SC/NI prefix)');
    return;
  }
  
  const { type, id } = result;
  window.location.href = `preview.html?type=${type}&id=${encodeURIComponent(id)}`;
});
```

---

## Edge Cases & Gotchas

### 1. Charitable Companies (Double Registration)
A charity can ALSO be registered with Companies House:
- Charity Commission: `220949`
- Companies House: `3732267`

The charity API returns both numbers. You can use either to generate a report, but they're the **same entity**.

**Detection rule:** If user enters an 8-digit number, treat as **company** first (higher priority). If it fails, fall back to try as charity if it's ≤7 digits.

### 2. Leading Zeros
Companies with numbers like `00123456` should be entered as `123456` (8 digits total):
- `09238471` → valid
- `0009238471` → invalid (too many digits)

### 3. Scottish Companies
`SC` prefix is valid but often mistyped as company numbers:
- `SC123456` → valid Scottish company
- `SC12345` → invalid (only 5 digits after SC)

### 4. Removed/Dissolved Charities
A charity can have `date_of_removal` set, meaning it's no longer active. Don't reject — just mark as "Removed from the register".

### 5. Excepted & Exempt Charities
These may not appear in the Charity Commission API (they're not registered):
- **Excepted:** Small groups (some churches, scout groups) — no CC registration
- **Exempt:** Regulated by another body (some universities, health trusts) — no CC registration

If you search for one, the API will return 404. Handle gracefully: "This charity number is not registered with the Charity Commission (may be excepted or exempt)."

---

## Test Cases

| Input | Expected | Type | Notes |
|-------|----------|------|-------|
| `220949` | ✅ Pass | Charity | British Red Cross |
| `1155899` | ✅ Pass | Charity | World Aid Convoy |
| `09238471` | ✅ Pass | Company | Example company |
| `SC123456` | ✅ Pass | Company | Scottish company |
| `NI123456` | ✅ Pass | Company | N.I. company |
| `1` | ✅ Pass | Charity | Edge case: 1-digit charity |
| `1234567` | ✅ Pass | Charity | Edge case: 7-digit charity |
| `12345678` | ✅ Pass | Company | 8-digit company |
| `123456789` | ❌ Fail | — | Too many digits (9) |
| `SC12345` | ❌ Fail | — | SC prefix but only 5 digits |
| `XX123456` | ❌ Fail | — | Invalid prefix |
| `` (empty) | ❌ Fail | — | Empty input |
| `ABC` | ❌ Fail | — | No digits |

---

## Implementation Checklist

- [ ] **Fix frontend detection** (`frontend/public/index.html` line ~599)
- [ ] **Update preview logic** (`frontend/api/preview_lookup.py`) to handle charity numbers correctly
- [ ] **Add input validation** to both charity and company APIs to reject invalid formats early
- [ ] **Add error messaging** on frontend:
  - "This charity number was not found" (404)
  - "This company number was not found" (404)
  - "Please enter a valid UK charity (1-7 digits) or company number"
- [ ] **Test with real data:**
  - Charity: `220949` (British Red Cross)
  - Charity: `1155899` (World Aid Convoy)
  - Company: `09238471` (example)
  - Scottish: `SC123456` (any valid Scottish company)
- [ ] **Document for users** which formats are accepted

---

## References

- Charity Commission API: https://api.charitycommission.gov.uk/register/api
- Companies House API: https://api.company-information.service.gov.uk
- Charity Search: https://register-of-charities.charitycommission.gov.uk
- Companies House Search: https://beta.companieshouse.gov.uk
