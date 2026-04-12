# 🏛️ HRCOB-V4.0 - Complete System Documentation

**Comprehensive Company & Charity Risk Assessment Platform**

Last Updated: April 12, 2026
Status: ✅ PRODUCTION READY

---

## 📋 Table of Contents

1. [System Overview](#system-overview)
2. [Features](#features)
3. [Architecture](#architecture)
4. [French Company Analysis](#french-company-analysis)
5. [UK Company Analysis](#uk-company-analysis)
6. [Comprehensive Screening](#comprehensive-screening)
7. [Installation & Setup](#installation--setup)
8. [Quick Start](#quick-start)
9. [API Integration](#api-integration)
10. [Troubleshooting](#troubleshooting)

---

## System Overview

**HRCOB-V4.0** is a sophisticated dual-country company and charity risk assessment platform that provides:

- ✅ **Real-time company analysis** (UK & France)
- ✅ **Risk scoring with hard stops** (0-100 scale)
- ✅ **Ultimate Beneficial Owner (UBO) tracing**
- ✅ **Multi-entity screening** (company + directors + entities)
- ✅ **French language support** (French terms alongside English)
- ✅ **Fraud detection engines** (6 advanced detection rules)
- ✅ **Adverse media & sanctions screening**
- ✅ **FCA industry classification** (UK companies)
- ✅ **PDF report generation**

### Tech Stack

- **Backend**: Python 3.10+
- **Frontend**: Streamlit (interactive web interface)
- **APIs**: Companies House, INPI, Charity Commission, Tavily, Gemini/OpenAI
- **Data**: Real-time from official registries

---

## Features

### UK Company Checks
- ✅ Companies House API integration
- ✅ Director analysis & PSC screening
- ✅ Address credibility assessment
- ✅ Dormancy detection
- ✅ FCA compliance screening
- ✅ Sanctions & FATF checks
- ✅ Adverse media detection
- ✅ Risk matrix (0-100)

### French Company Checks (NEW)
- ✅ INPI registry API integration
- ✅ Director extraction from `composition.pouvoirs`
- ✅ **Recursive UBO tracing** (up to 3 levels)
- ✅ Company name variants in French
- ✅ Multi-entity screening (company + all directors + UBO chain)
- ✅ Role code mapping (French titles)
- ✅ Address credibility in French context
- ✅ High-risk industry detection (APE codes)
- ✅ Risk matrix (0-100)

### Charity Checks (UK)
- ✅ Charity Commission API integration
- ✅ Trustee analysis
- ✅ Registration status verification
- ✅ Adverse media screening
- ✅ Risk assessment

### Advanced Features
- ✅ **6 Fraud Detection Engines** (UK & French)
- ✅ **Recursive UBO Chain Tracing** (French companies)
- ✅ **Dual-language Screening** (English + French)
- ✅ **Multi-entity Risk Aggregation**
- ✅ **PDF Report Export**
- ✅ **AI Narrative Generation** (Gemini/OpenAI)

---

## Architecture

### Module Structure

```
hrcob/
├── app.py                          # Main Streamlit application
├── config.py                       # Configuration & API keys
├── requirements.txt                # Python dependencies
│
├── core/                           # Core analysis modules
│   ├── company_check.py           # UK company analysis (UNCHANGED)
│   ├── french_company_check.py    # French company analysis (NEW)
│   ├── french_screening.py        # Multi-entity screening (NEW)
│   ├── charity_check.py           # Charity analysis
│   ├── fatf_screener.py          # Sanctions/FATF screening
│   ├── risk_scorer.py            # Risk scoring engine
│   ├── fraud_detection.py        # UK fraud detection
│   ├── french_fraud_detection.py # French fraud detection (NEW)
│   ├── high_risk_industries.py   # Industry classification
│   └── ... (other modules)
│
├── api_clients/                    # External API integrations
│   ├── companies_house.py         # UK registry (Companies House)
│   ├── french_registry.py         # French registry (INPI) (NEW)
│   ├── charity_commission.py      # Charity Commission
│   ├── tavily_search.py          # Adverse media search
│   └── ... (other clients)
│
└── data/                           # Reference data
    ├── country_risk_matrix.json
    ├── country_aliases.json
    └── ... (other data)
```

### Data Flow

```
USER INPUT (Company Number or SIREN)
    ↓
DETECTION (UK or French?)
    ↓
┌─────────────────────────────────────┐
│ UK COMPANY                          │
│ (UNCHANGED)                         │
│ ├─ Companies House API              │
│ ├─ Director Analysis                │
│ ├─ PSC Screening                    │
│ ├─ Risk Scoring                     │
│ └─ Fraud Detection (6 engines)      │
└─────────────────────────────────────┘
    OR
┌─────────────────────────────────────┐
│ FRENCH COMPANY (NEW)                │
│ ├─ INPI API Fetch                   │
│ ├─ Recursive UBO Tracing            │
│ ├─ Multi-Entity Screening           │
│ │  ├─ Company name variants         │
│ │  ├─ Director names (EN + FR)      │
│ │  ├─ UBO chain entities            │
│ │  └─ Trading names                 │
│ ├─ Risk Aggregation                 │
│ └─ Fraud Detection (6 engines)      │
└─────────────────────────────────────┘
    ↓
COMPREHENSIVE RESULTS
├─ Risk Score (0-100)
├─ Risk Level (Low/Medium/High)
├─ Detailed Findings
├─ Screening Alerts
└─ PDF Export
```

---

## French Company Analysis

### What's New

The system now provides **comprehensive French company analysis** equivalent to UK checks:

#### 1. **INPI API Integration**
- Real-time data from Institut National de la Propriété Industrielle
- Requires credentials: `FRENCH_REGISTRY_EMAIL` + `FRENCH_REGISTRY_PASSWORD`
- Endpoints:
  - `/api/companies/{siren}` - Company details
  - `/api/sso/login` - Authentication (JWT tokens)

#### 2. **Director Extraction**
- Extracts from `composition.pouvoirs` (official powers/roles)
- Handles both physical persons (INDIVIDU) and legal entities (PERSONNE_MORALE)
- Maps INPI role codes to French titles:
  ```
  70 = Président
  73 = Directeur Général / Gérant
  53 = Représentant légal / Dirigeant
  (... 20+ more codes)
  ```

#### 3. **Recursive UBO Tracing**
When a director is a company (legal entity):
- Automatically fetches that company's directors
- Traces up to 3 levels deep (prevents infinite loops)
- Identifies ultimate physical person owners
- Shows full chain in UI with expandable sections

**Example:**
```
Company A (your company)
  └─ Director: Company B (legal entity)
     └─ Director: JEAN DUPONT (physical person) ✅
```

#### 4. **Multi-Entity Screening**
Screens ALL associated entities for sanctions/adverse media:
- Company legal name
- Company French variants (e.g., "Société par Actions Simplifiée")
- All directors (first name + last name, both EN + FR)
- All UBO chain entities
- Trading names if available

#### 5. **French Language Support**
- Screens in both English and French
- French term variants captured:
  - "SARL" (Société à Responsabilité Limitée)
  - "SAS" (Société par Actions Simplifiée)
  - "SA" (Société Anonyme)
  - Role French names (Gérant, Directeur, etc.)

### Example: French Company Check

```python
from core.french_company_check import run_french_company_check

result = run_french_company_check(
    siren="793437518",  # SIREN number
    website_url="https://example.fr",
    tavily_search_fn=search_with_tavily,
    adverse_search_fn=search_adverse_media,
    fatf_screen_fn=screen_entity
)

# Result includes:
# - company_profile (SIREN, denomination, APE code, etc.)
# - directors (extracted with recursive UBO chain)
# - comprehensive_screening (multi-entity results)
# - risk_matrix (0-100 score)
# - fraud_detection (6 advanced rules)
# - adverse_media (all associated entities)
```

---

## UK Company Analysis

### Unchanged Features

✅ **All UK company analysis remains UNCHANGED**:
- Companies House API integration
- Director & PSC analysis
- Address credibility
- Dormancy detection
- FCA classification
- Risk scoring
- Fraud detection
- Adverse media screening

The UK check (`core/company_check.py`) is completely separate and unaffected by French enhancements.

---

## Comprehensive Screening

### Multi-Entity Screening (French Companies)

The new `french_screening.py` module screens:

1. **Company Entity**
   - Legal name (English + French variants)
   - SIREN/SIRET numbers
   - Trading names
   - Previous names

2. **All Directors**
   - Full names (prenoms + nom)
   - Birth dates
   - Addresses
   - In both English and French

3. **UBO Chain**
   - Parent companies
   - Parent company directors
   - Recursively up to 3 levels

4. **French Variants**
   - "Société à Responsabilité Limitée (SARL)"
   - "Société par Actions Simplifiée (SAS)"
   - "Société Anonyme (SA)"
   - Role titles: Gérant, Directeur, Président, etc.

### Screening Results

Each entity screening returns:
```python
{
    "query": "JEAN DUPONT",
    "type": "individual",
    "language_variant": "en",  # or "fr"
    "status": "not_found",     # or "match"
    "matches": [
        {
            "name": "Jean Dupont",
            "title": "Sanctions List Entry",
            "risk_level": "high",
            "country": "FR"
        }
    ],
    "adverse_media": [...],
    "risk_aggregate": "high"
}
```

### Risk Aggregation

Final screening aggregates across all entities:
```python
{
    "total_entities_screened": 8,
    "high_risk_count": 1,
    "medium_risk_count": 2,
    "low_risk_count": 5,
    "overall_risk_level": "high",
    "screening_alerts": [
        "Company XYZ found in sanctions list",
        "Director JEAN DUPONT has adverse media mentions"
    ]
}
```

---

## Installation & Setup

### Prerequisites

- Python 3.10 or higher
- pip package manager
- API keys (see below)

### Step 1: Clone Repository

```bash
git clone <repository-url>
cd hrcob-v4.0
```

### Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 3: Set Up Environment Variables

Create `.env` file in project root:

```bash
# Required
CH_API_KEY=your_companies_house_key
CHARITY_COMMISSION_API_KEY=your_charity_key
FRENCH_REGISTRY_EMAIL=your_inpi_email
FRENCH_REGISTRY_PASSWORD=your_inpi_password

# Recommended
TAVILY_API_KEY=your_tavily_key
OPENAI_API_KEY=your_openai_key
# OR
GEMINI_API_KEY=your_gemini_key

# Optional
ALLOW_INSECURE_SSL=true
```

### Step 4: Verify Setup

```bash
python3 -c "
import sys
sys.path.insert(0, '.')
from core.company_check import run_company_check
from core.french_company_check import run_french_company_check
print('✅ All imports successful')
"
```

---

## Quick Start

### Run the Streamlit App

```bash
streamlit run app.py
```

Opens at: `http://localhost:8501`

### UK Company Check

1. Select "UK Company Check" tab
2. Enter company number (8 digits): e.g., `08354128`
3. Click "Check Company"
4. View results:
   - Company profile
   - Directors analysis
   - Risk score (0-100)
   - Detailed findings
   - PDF export

### French Company Check

1. Select "French Company Check" tab
2. Enter SIREN (9 digits): e.g., `793437518`
3. Click "Check Company"
4. View results:
   - Company profile (SIREN, denomination, APE)
   - Directors with roles (French titles)
   - UBO chain (if applicable)
   - Multi-entity screening results
   - Risk score (0-100)
   - Detailed findings
   - PDF export

### Example Outputs

**UK Company (08354128)**:
```
✅ Company: WISE
├─ Status: Active
├─ Directors: 4 found
├─ Risk Score: 23/100 (Low)
└─ Adverse Media: 0 mentions
```

**French Company (793437518)**:
```
✅ Company: PELLENC ENERGY
├─ Status: Active
├─ Directors: 2 found
│  ├─ ROGER PELLENC (Directeur Général)
│  └─ JEAN-LOUIS FERRANDIS (Représentant légal)
├─ UBO Chain: Traced (depth 0, no companies)
├─ Screening: 8 entities checked
├─ Risk Score: 45/100 (Medium)
└─ Adverse Media: 0 mentions
```

---

## API Integration

### Companies House (UK)

**Endpoint**: `https://api.companieshouse.gov.uk/`

**Key Operations**:
- Get company details: `/company/{company_number}`
- Get officers: `/company/{company_number}/officers`
- Get charges: `/company/{company_number}/charges`

**Authentication**: Basic auth with API key

### INPI (France)

**Endpoint**: `https://registre-national-entreprises.inpi.fr/api/`

**Key Operations**:
- Login (SSO): `POST /sso/login`
- Get company: `GET /companies/{siren}`
- Get actes: `GET /actes?siren={siren}` (⚠️ Currently returns 500)

**Authentication**: JWT tokens (86400s expiry)

### Charity Commission (UK)

**Endpoint**: `https://api.charitycommission.gov.uk/`

**Key Operations**:
- Search charities: `/v1/charities`
- Get details: `/v1/charities/{charity_no}`

### Tavily Search

**Endpoint**: `https://api.tavily.com/search`

**Purpose**: Adverse media screening, news detection

### Gemini / OpenAI

**Purpose**: AI narrative generation for risk reports

---

## Troubleshooting

### UK Company Check

**Issue**: "Company not found"
- **Cause**: Invalid company number
- **Fix**: Verify 8-digit format from Companies House

**Issue**: "Directors missing"
- **Cause**: API permission level
- **Fix**: Check API key has officer endpoint access

### French Company Check

**Issue**: "INPI authentication failed"
- **Cause**: Wrong credentials or expired
- **Fix**: Verify `FRENCH_REGISTRY_EMAIL` and `FRENCH_REGISTRY_PASSWORD` in `.env`

**Issue**: "No directors found"
- **Cause**: Company hasn't filed composition data
- **Fix**: Check INPI directly - some companies don't file structured director data

**Issue**: "UBO chain not traced"
- **Cause**: Parent company director has no SIREN
- **Fix**: Recursion stops when legal entity has no SIREN (normal)

### Screening Issues

**Issue**: "Adverse media screening timeout"
- **Cause**: Tavily API rate limit
- **Fix**: Check Tavily account quota; retry after delay

**Issue**: "Sanctions screening not working"
- **Cause**: Missing `fatf_screen_fn` parameter
- **Fix**: Pass screening function to check function

---

## Code Examples

### Python: UK Company Check

```python
from core.company_check import run_company_check
from core.fatf_screener import screen_entity
from api_clients.tavily_search import search_with_tavily

result = run_company_check(
    company_number="08354128",
    website_url="https://wise.com",
    tavily_search_fn=lambda q: search_with_tavily(q, api_key),
    adverse_search_fn=lambda q: search_with_tavily(q, api_key),
    fatf_screen_fn=screen_entity
)

print(f"Company: {result['company_name']}")
print(f"Risk Score: {result['risk_matrix']['risk_score']}/100")
print(f"Directors: {len(result['directors'])}")
```

### Python: French Company Check

```python
from core.french_company_check import run_french_company_check
from core.fatf_screener import screen_entity
from api_clients.tavily_search import search_with_tavily

result = run_french_company_check(
    siren="793437518",
    website_url="https://company.fr",
    tavily_search_fn=lambda q: search_with_tavily(q, api_key),
    adverse_search_fn=lambda q: search_with_tavily(q, api_key),
    fatf_screen_fn=screen_entity
)

print(f"Company: {result['company_name']}")
print(f"Risk Score: {result['risk_matrix']['risk_score']}/100")
print(f"Directors: {len(result['directors'])}")
print(f"Screening Results:")
for alert in result['comprehensive_screening']['screening_alerts']:
    print(f"  - {alert}")
```

### Python: Custom Screening

```python
from core.french_screening import (
    run_comprehensive_screening,
    collect_screening_identities
)

# Collect all identities to screen
identities = collect_screening_identities(
    company_name="PELLENC ENERGY",
    company_siren="793437518",
    company_trading_names=["Pellenc", "Pellenc Group"],
    directors=[
        {"name": "ROGER PELLENC", "birth_date": "1944-09"},
        {"name": "JEAN-LOUIS FERRANDIS", "birth_date": "1959-10"}
    ],
    ubo_entities=[...]
)

# Run comprehensive screening
screening_result = run_comprehensive_screening(
    identities=identities,
    fatf_screen_fn=screen_entity,
    adverse_search_fn=search_with_tavily
)

print(f"Total screened: {screening_result['total_entities_screened']}")
print(f"High risk: {screening_result['high_risk_count']}")
```

---

## File Structure

### Key Files Modified/Added

```
✅ NEW FILES:
  - core/french_company_check.py        (457 lines)
  - core/french_screening.py            (new module)
  - api_clients/french_registry.py      (552 lines, with recursive UBO)
  - core/french_fraud_detection.py      (1,472 lines)
  - core/french_dashboard.py            (new module)
  - core/french_company_analysis.py     (new module)
  - core/high_risk_industries.py        (French APE codes)

✅ MODIFIED (French support only):
  - app.py                              (Added French tab + UI)
  - core/company_check.py               (UNCHANGED for UK)

✅ UNCHANGED:
  - All UK company check logic
  - All charity check logic
  - All core risk scoring
  - All API client code (except French additions)
```

---

## Support & Maintenance

### Regular Updates
- INPI API changes: Monitor `/api/actes` endpoint recovery
- FCA SIC code updates: Check annually
- Sanctions lists: Real-time via FATF screener

### Performance Metrics
- UK company check: ~2-3 seconds
- French company check: ~2-4 seconds (includes UBO recursion)
- Screening 8 entities: ~5-10 seconds (Tavily API dependent)

### Known Limitations
- INPI `/api/actes` endpoint currently returns 500 (PDF documents unavailable)
- Some French companies don't file structured director data
- UBO recursion limited to 3 levels (configurable)
- French screening requires English + French variants (comprehensive but slower)

---

## Summary

**HRCOB-V4.0** is a **production-ready** comprehensive risk assessment platform that:

✅ Checks **UK & French companies** with equivalent rigor
✅ Traces **ultimate beneficial owners** through corporate chains
✅ Screens **ALL associated entities** in English & French
✅ Detects **fraud patterns** with 6 advanced engines
✅ Aggregates **multi-source risk signals**
✅ Generates **AI-powered narratives**
✅ Exports **professional PDF reports**

**Ready for deployment.**
