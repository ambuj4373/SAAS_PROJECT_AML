import streamlit as st
import requests
import os
import json
import ssl
import time
import pandas as pd
import httpx
from google import genai
from google.genai.types import HttpOptions
from openai import OpenAI
from tavily import TavilyClient
from requests.exceptions import RequestException, SSLError
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# ─── API KEYS ────────────────────────────────────────────────────────────────
CH_API_KEY = os.getenv("CH_API_KEY", "")
CHARITY_COMMISSION_API_KEY = os.getenv("CHARITY_COMMISSION_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

BASEL_AML_URL = "https://index.baselgovernance.org/api/v1/countries"

# ─── INTERNAL COUNTRY RISK MATRIX ────────────────────────────────────────────
# Risk classifications based on Basel AML Index, FATF, sanctions lists,
# Know Your Country, and internal due-diligence standards.
# Only High Risk and Very High Risk countries trigger detailed KYC lookups.
COUNTRY_RISK_MATRIX = {
    # ── Very High Risk ───────────────────────────────────────────────────
    "Afghanistan":       "Very High Risk",
    "Iran":              "Very High Risk",
    "North Korea":       "Very High Risk",
    "Syria":             "Very High Risk",
    "Yemen":             "Very High Risk",
    "Myanmar":           "Very High Risk",
    "Libya":             "Very High Risk",
    "Somalia":           "Very High Risk",
    "South Sudan":       "Very High Risk",
    "Iraq":              "Very High Risk",
    "Democratic Republic of the Congo": "Very High Risk",
    "Sudan":             "Very High Risk",
    "Cuba":              "Very High Risk",
    "Belarus":           "Very High Risk",
    "Russia":            "Very High Risk",
    "Crimea":            "Very High Risk",
    "Donetsk":           "Very High Risk",
    "Luhansk":           "Very High Risk",
    "Venezuela":         "Very High Risk",
    "Eritrea":           "Very High Risk",
    # ── High Risk ────────────────────────────────────────────────────────
    "Pakistan":          "High Risk",
    "Nigeria":           "High Risk",
    "Lebanon":           "High Risk",
    "Mali":              "High Risk",
    "Mozambique":        "High Risk",
    "Central African Republic": "High Risk",
    "Chad":              "High Risk",
    "Guinea-Bissau":     "High Risk",
    "Haiti":             "High Risk",
    "Cambodia":          "High Risk",
    "Burkina Faso":      "High Risk",
    "Uganda":            "High Risk",
    "Niger":             "High Risk",
    "Cameroon":          "High Risk",
    "Zimbabwe":          "High Risk",
    "Ethiopia":          "High Risk",
    "Tanzania":          "High Risk",
    "Bangladesh":        "High Risk",
    "Laos":              "High Risk",
    "Palestine":         "High Risk",
    "Congo":             "High Risk",
    "Guinea":            "High Risk",
    "Burundi":           "High Risk",
    "Tajikistan":        "High Risk",
    "Turkmenistan":      "High Risk",
    "Nicaragua":         "High Risk",
    "South Africa":      "High Risk",
    # ── Medium Risk (partial list — most countries default here) ──────────
    "India":             "Medium Risk",
    "Turkey":            "Medium Risk",
    "Kenya":             "Medium Risk",
    "Egypt":             "Medium Risk",
    "Philippines":       "Medium Risk",
    "Indonesia":         "Medium Risk",
    "Mexico":            "Medium Risk",
    "Brazil":            "Medium Risk",
    "Colombia":          "Medium Risk",
    "Thailand":          "Medium Risk",
    "Vietnam":           "Medium Risk",
    "Morocco":           "Medium Risk",
    "Jordan":            "Medium Risk",
    "Tunisia":           "Medium Risk",
    "Algeria":           "Medium Risk",
    "Sri Lanka":         "Medium Risk",
    "Ghana":             "Medium Risk",
    "Senegal":           "Medium Risk",
    "Nepal":             "Medium Risk",
    "Rwanda":            "Medium Risk",
    "Sierra Leone":      "Medium Risk",
    "Malawi":            "Medium Risk",
    "Zambia":            "Medium Risk",
    "China":             "Medium Risk",
    "Saudi Arabia":      "Medium Risk",
    "United Arab Emirates": "Medium Risk",
    "Qatar":             "Medium Risk",
    "Kuwait":            "Medium Risk",
    "Oman":              "Medium Risk",
    "Bahrain":           "Medium Risk",
    # ── Low Risk (selected — unlisted countries default to Medium) ───────
    "United Kingdom":    "Low Risk",
    "United States":     "Low Risk",
    "Canada":            "Low Risk",
    "Australia":         "Low Risk",
    "New Zealand":       "Low Risk",
    "Germany":           "Low Risk",
    "France":            "Low Risk",
    "Netherlands":       "Low Risk",
    "Sweden":            "Low Risk",
    "Norway":            "Low Risk",
    "Denmark":           "Low Risk",
    "Finland":           "Low Risk",
    "Switzerland":       "Low Risk",
    "Ireland":           "Low Risk",
    "Japan":             "Low Risk",
    "Singapore":         "Low Risk",
}

def get_country_risk(country_name):
    """Look up risk level from internal matrix.  Default to Medium if unknown."""
    if not country_name:
        return "Unknown"
    # Try exact match first, then case-insensitive
    if country_name in COUNTRY_RISK_MATRIX:
        return COUNTRY_RISK_MATRIX[country_name]
    for k, v in COUNTRY_RISK_MATRIX.items():
        if k.lower() == country_name.strip().lower():
            return v
    return "Medium Risk"

def is_elevated_risk(risk_level):
    """Return True if risk level is High or Very High."""
    return risk_level in ("High Risk", "Very High Risk")


def get_ssl_verify():
    ca = os.getenv("CORP_CA_BUNDLE", "").strip()
    insecure = os.getenv("ALLOW_INSECURE_SSL", "false").strip().lower() in {"1", "true", "yes", "y"}
    if ca:
        return ca
    if insecure:
        return False
    return True


# If insecure SSL is enabled, disable verification globally for requests/urllib3
_ssl_verify = get_ssl_verify()
if _ssl_verify is False:
    ssl._create_default_https_context = ssl._create_unverified_context
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ─── CONFIGURATION ───────────────────────────────────────────────────────────
st.set_page_config(page_title="HRCOB KYC Automation", layout="wide", page_icon="🛡️")

# Build Gemini client — inject httpx.Client(verify=False) when behind proxy
if GEMINI_API_KEY:
    _genai_opts = {}
    if _ssl_verify is False:
        _genai_opts["http_options"] = HttpOptions(
            httpx_client=httpx.Client(verify=False),
        )
    gemini_client = genai.Client(api_key=GEMINI_API_KEY, **_genai_opts)
else:
    gemini_client = None

# Build OpenAI client — also disable SSL when behind proxy
if OPENAI_API_KEY:
    _oai_kwargs = {}
    if _ssl_verify is False:
        _oai_kwargs["http_client"] = httpx.Client(verify=False)
    openai_client = OpenAI(api_key=OPENAI_API_KEY, **_oai_kwargs)
else:
    openai_client = None

tavily = TavilyClient(api_key=TAVILY_API_KEY) if TAVILY_API_KEY else None

# Available LLM providers and models  (OpenAI first — paid credit)
LLM_PROVIDERS = {}
if openai_client:
    LLM_PROVIDERS["GPT-4.1 mini  [$0.014/report]"] = ("openai", "gpt-4.1-mini")
    LLM_PROVIDERS["GPT-4.1 nano  [$0.003/report]"] = ("openai", "gpt-4.1-nano")
    LLM_PROVIDERS["GPT-4o mini"]                    = ("openai", "gpt-4o-mini")
if gemini_client:
    LLM_PROVIDERS["Gemini 2.0 Flash  [free tier]"]      = ("gemini", "gemini-2.0-flash")
    LLM_PROVIDERS["Gemini 2.0 Flash-Lite [free tier]"]   = ("gemini", "gemini-2.0-flash-lite")
    LLM_PROVIDERS["Gemini 1.5 Flash  [free tier]"]       = ("gemini", "gemini-1.5-flash")


# ═══════════════════════════════════════════════════════════════════════════════
# DATA ENGINES
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_charity_data(charity_num):
    """Charity Commission: core details + financial overview."""
    v = get_ssl_verify()
    h = {"Ocp-Apim-Subscription-Key": CHARITY_COMMISSION_API_KEY}
    base = "https://api.charitycommission.gov.uk/register/api"

    r1 = requests.get(f"{base}/allcharitydetails/{charity_num}/0", headers=h, timeout=20, verify=v)
    r1.raise_for_status()
    d = r1.json()

    overview = {}
    try:
        r2 = requests.get(f"{base}/charityoverview/{charity_num}/0", headers=h, timeout=20, verify=v)
        r2.raise_for_status()
        overview = r2.json()
    except Exception:
        pass

    trustees = [t["trustee_name"] for t in (d.get("trustee_names") or []) if t.get("trustee_name")]

    classifications = d.get("who_what_where") or []
    what_list = [c["classification_desc"] for c in classifications if c.get("classification_type") == "What"]
    who_list = [c["classification_desc"] for c in classifications if c.get("classification_type") == "Who"]
    how_list = [c["classification_desc"] for c in classifications if c.get("classification_type") == "How"]

    countries_raw = d.get("CharityAoOCountryContinent") or []
    countries = [{"country": a.get("country"), "continent": a.get("continent")} for a in countries_raw]

    return {
        "charity_name": d.get("charity_name"),
        "charity_number": d.get("reg_charity_number"),
        "company_number": d.get("charity_co_reg_number"),
        "charity_type": d.get("charity_type"),
        "reg_status": d.get("reg_status"),
        "date_of_registration": d.get("date_of_registration"),
        "address": ", ".join(filter(None, [
            d.get("address_line_one"), d.get("address_line_two"),
            d.get("address_line_three"), d.get("address_post_code"),
        ])),
        "phone": d.get("phone"),
        "email": d.get("email"),
        "website": d.get("web"),
        "trustees": trustees,
        "what_it_does": what_list,
        "who_it_helps": who_list,
        "how_it_operates": how_list,
        "countries": countries,
        "activities": overview.get("activities"),
        "latest_income": overview.get("latest_income"),
        "latest_expenditure": overview.get("latest_expenditure"),
        "employees": overview.get("employees"),
        "num_trustees": overview.get("trustees"),
        "fin_year_end": overview.get("latest_acc_fin_year_end_date"),
        "inc_donations": overview.get("inc_donations_legacies"),
        "inc_charitable": overview.get("inc_charitable_activities"),
        "inc_trading": overview.get("inc_other_trading_activities"),
        "inc_investments": overview.get("inc_investments"),
        "inc_other": overview.get("inc_other"),
        "exp_raising": overview.get("exp_raising_funds"),
        "exp_charitable": overview.get("exp_charitable_activities"),
        "exp_other": overview.get("exp_other"),
        "investment_gains": overview.get("investment_gains_losses"),
        "trading_subsidiary": overview.get("trading_subsidiary"),
        "trustee_benefits": overview.get("any_trustee_benefit"),
        "grant_making": overview.get("grant_making_main_activity"),
        "employees_over_60k": overview.get("employees_over_60k"),
        "volunteers": overview.get("volunteers"),
    }


def fetch_ch_data(company_num):
    """Companies House: company profile + officers."""
    url = f"https://api.company-information.service.gov.uk/company/{company_num}"
    auth = requests.auth.HTTPBasicAuth(CH_API_KEY, "")
    v = get_ssl_verify()

    p = requests.get(url, auth=auth, timeout=20, verify=v)
    p.raise_for_status()
    profile = p.json()
    o = requests.get(f"{url}/officers", auth=auth, timeout=20, verify=v)
    o.raise_for_status()
    officers = o.json()
    active = [off for off in officers.get("items", []) if off.get("resigned_on") is None]
    return {
        "name": profile.get("company_name"),
        "status": profile.get("company_status"),
        "type": profile.get("type"),
        "date_of_creation": profile.get("date_of_creation"),
        "registered_office": profile.get("registered_office_address"),
        "sic_codes": profile.get("sic_codes"),
        "officers": active,
        "officer_names": [off.get("name") for off in active],
    }


# ─── TAVILY SEARCH HELPERS ──────────────────────────────────────────────────

ADVERSE_TERMS = (
    '"Money Launder" OR "sanction" OR "corrupt" OR "bribe" OR "criminal" '
    'OR "crime" OR "illicit" OR "terror" OR "fraud" OR "scam" OR "found guilty" '
    'OR "Iran" OR "Syria" OR "Crimea" OR "North Korea" OR "DPRK" OR "Russia" '
    'OR "Cuba" OR "Belarus" OR "Donetsk" OR "Luhansk"'
)


def tavily_search(query, depth="advanced", max_results=5):
    if tavily is None:
        return []
    try:
        res = tavily.search(query=query, search_depth=depth, max_results=max_results)
        return res.get("results", [])
    except Exception as e:
        return [{"title": "Search error", "url": "", "content": str(e)}]


def search_adverse_media(name):
    """Detailed adverse media search for a single subject."""
    query = f'"{name}" AND ({ADVERSE_TERMS})'
    return tavily_search(query)


def search_generic(name):
    """Generic Google-style search for a subject."""
    return tavily_search(name, depth="basic")


def search_website_projects(website_url, charity_name):
    """Search the charity website for projects and activities."""
    return tavily_search(f"site:{website_url} projects activities programs {charity_name}")


def search_positive_media(charity_name):
    """Search for positive media, partnerships, grants, safeguarding."""
    return tavily_search(
        f'"{charity_name}" AND ("partner" OR "grant" OR "award" OR "government" OR "UN" OR "safeguarding" OR "fundraising regulator")',
        depth="advanced",
    )


def search_country_risk_batch(country_list):
    """Search Know Your Country / Basel AML Index for operating countries."""
    if not country_list:
        return []
    names = ", ".join(country_list[:20])
    return tavily_search(
        f"Know Your Country risk profile Basel AML Index for: {names}. "
        f"Include risk rating, key concerns (terrorism, corruption, sanctions), and brief country profile.",
        depth="advanced", max_results=8,
    )


def search_country_kyc_profile(country_name):
    """Fetch a detailed Know Your Country profile for a single high-risk country.
    Returns sanctions, FATF status, terrorism, corruption, criminal markets info."""
    return tavily_search(
        f"site:knowyourcountry.com {country_name} country summary sanctions FATF "
        f"terrorism corruption criminal markets AML risk rating",
        depth="advanced", max_results=3,
    )


# ─── LLM HELPERS ─────────────────────────────────────────────────────────────

def _call_gemini(prompt, model_name):
    """Call Gemini API and return text."""
    response = gemini_client.models.generate_content(
        model=model_name,
        contents=prompt,
    )
    return response.text


def _call_openai(prompt, model_name):
    """Call OpenAI API and return text.  max_tokens caps cost."""
    response = openai_client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": "You are a senior KYC compliance analyst. Write concise, professional reports using markdown. Never pad with filler."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=4096,     # cap output to ~3-4K tokens — plenty for a full report
    )
    return response.choices[0].message.content


def llm_generate(prompt, max_retries=3):
    """Call the selected LLM with automatic retry + provider fallback on 429."""
    selected_label = st.session_state.get("llm_model", list(LLM_PROVIDERS.keys())[0])

    # Build fallback order: selected first, then all others
    all_labels = list(LLM_PROVIDERS.keys())
    ordered = [selected_label] + [l for l in all_labels if l != selected_label]

    last_err = None
    for label in ordered:
        provider, model = LLM_PROVIDERS[label]
        for attempt in range(max_retries):
            try:
                if provider == "gemini":
                    return _call_gemini(prompt, model)
                else:
                    return _call_openai(prompt, model)
            except Exception as e:
                last_err = e
                err_str = str(e)
                is_rate_limit = ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str
                                 or "rate" in err_str.lower())
                if is_rate_limit:
                    wait = min(2 ** attempt * 5, 60)
                    if attempt < max_retries - 1:
                        st.toast(f"⏳ Rate limited on {label}, retrying in {wait}s ({attempt+2}/{max_retries})…")
                        time.sleep(wait)
                    else:
                        st.toast(f"🔄 {label} quota exhausted — trying next model…")
                        break  # next model
                else:
                    raise  # non-rate-limit error, propagate

    # All models exhausted
    raise last_err


# ─── FORMAT HELPERS ──────────────────────────────────────────────────────────

def _compact(obj):
    """Recursively strip None/empty values to reduce token count."""
    if isinstance(obj, dict):
        return {k: _compact(v) for k, v in obj.items()
                if v is not None and v != "" and v != [] and v != {}}
    if isinstance(obj, list):
        return [_compact(i) for i in obj if i is not None and i != "" and i != {} and i != []]
    return obj


def _slim_search(results, max_items=5, max_chars=400):
    """Trim Tavily search results to essential fields only."""
    out = []
    for r in (results or [])[:max_items]:
        out.append({
            "title": (r.get("title") or "")[:120],
            "url": r.get("url", ""),
            "snippet": (r.get("content") or "")[:max_chars],
        })
    return out


def fmt_money(val):
    if val is None:
        return "N/A"
    return f"£{val:,.0f}"


def fmt_date(iso_str):
    if not iso_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%d %B %Y")
    except Exception:
        return iso_str


# ═══════════════════════════════════════════════════════════════════════════════
# STREAMLIT UI
# ═══════════════════════════════════════════════════════════════════════════════

st.title("🛡️ HRCOB: Automated KYC Due-Diligence Reporter")

st.sidebar.header("Input")
charity_num = st.sidebar.text_input("Charity Registration Number", value="1195672")

st.sidebar.selectbox(
    "AI Model",
    list(LLM_PROVIDERS.keys()),
    index=0,
    key="llm_model",
    help="If the selected model is rate-limited, the app auto-falls back to the next available model.",
)

run_btn = st.sidebar.button("🚀 Generate Full Report", type="primary")

st.sidebar.markdown("---")
st.sidebar.caption("Powered by Charity Commission API, Companies House API, Tavily Search, Google Gemini & OpenAI")

if run_btn:
    # ─── Validate keys ──────────────────────────────────────────────────
    missing = []
    if not CHARITY_COMMISSION_API_KEY:
        missing.append("CHARITY_COMMISSION_API_KEY")
    if not GEMINI_API_KEY and not OPENAI_API_KEY:
        missing.append("GEMINI_API_KEY or OPENAI_API_KEY")
    if not TAVILY_API_KEY:
        missing.append("TAVILY_API_KEY")
    if missing:
        st.error(f"Missing API key(s): {', '.join(missing)}. Set them in your .env file.")
        st.stop()

    with st.status("🔄 Generating Full KYC Report — this takes 1-2 minutes...", expanded=True) as status:
        try:
            if get_ssl_verify() is False:
                st.warning("⚠️ SSL verification disabled (ALLOW_INSECURE_SSL=true). For local use only.")

            # ══════════════════════════════════════════════════════════════
            # PHASE 1: DATA COLLECTION
            # ══════════════════════════════════════════════════════════════

            # ── 1a. Charity Commission ───────────────────────────────────
            st.write("🔍 **Step 1/7** — Fetching Charity Commission records...")
            charity_data = None
            try:
                charity_data = fetch_charity_data(charity_num)
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    st.error(f"Charity number **{charity_num}** not found on Charity Commission.")
                    st.stop()
                raise

            if charity_data is None:
                st.error(f"No data returned for charity '{charity_num}'.")
                st.stop()

            entity_name = charity_data.get("charity_name", "Unknown Charity")
            website = charity_data.get("website", "")
            trustees = charity_data.get("trustees", [])
            st.success(f"Found: **{entity_name}**")

            # ── 1b. Companies House (if linked) ─────────────────────────
            st.write("🏢 **Step 2/7** — Checking Companies House link...")
            ch_data = None
            linked_co = (charity_data.get("company_number") or "").strip()
            if linked_co and CH_API_KEY:
                try:
                    ch_data = fetch_ch_data(linked_co)
                    st.success(f"Linked company **{linked_co}** found on Companies House.")
                    if not trustees and ch_data:
                        trustees = ch_data.get("officer_names", [])
                except Exception:
                    st.warning(f"Companies House lookup for {linked_co} failed (non-fatal).")
            elif linked_co:
                st.info(f"Linked company {linked_co} exists but CH_API_KEY not set.")
            else:
                st.info("No linked Companies House registration.")

            # ── 1c. Website & Projects Search ────────────────────────────
            st.write("🌐 **Step 3/7** — Reviewing charity website & projects...")
            website_results = []
            if website:
                website_results = search_website_projects(website, entity_name)
            generic_org_results = search_generic(entity_name)

            # ── 1d. Adverse Media Search ─────────────────────────────────
            st.write("📰 **Step 4/7** — Running adverse media searches...")
            adverse_org = search_adverse_media(entity_name)

            # Cap at first 5 trustees to avoid excessive API calls
            adverse_trustees = {}
            trustees_to_search = trustees[:5]
            for i, t in enumerate(trustees_to_search):
                st.write(f"&nbsp;&nbsp;&nbsp; Searching trustee {i+1}/{len(trustees_to_search)}: {t}")
                adverse_trustees[t] = search_adverse_media(t)
            if len(trustees) > 5:
                st.info(f"Searched first 5 of {len(trustees)} trustees. Remaining trustees not searched.")

            # ── 1e. Positive Media / Partnerships ────────────────────────
            st.write("✅ **Step 5/7** — Searching for positive media & partnerships...")
            positive_results = search_positive_media(entity_name)

            # ── 1f. Country Risk Cross-Reference ────────────────────────
            st.write("🌍 **Step 6/7** — Cross-referencing country risk scores...")
            countries = charity_data.get("countries", [])
            country_names = [c.get("country", "") for c in countries if c.get("country")]

            # Classify every country using internal risk matrix
            country_risk_classified = []
            high_risk_countries = []
            for c in countries:
                cname = c.get("country", "")
                risk = get_country_risk(cname)
                entry = {
                    "country": cname,
                    "continent": c.get("continent", ""),
                    "risk_level": risk,
                }
                country_risk_classified.append(entry)
                if is_elevated_risk(risk):
                    high_risk_countries.append(cname)

            # For each High/Very High Risk country → fetch Know Your Country profile
            country_kyc_profiles = {}
            if high_risk_countries:
                st.write(f"&nbsp;&nbsp;&nbsp; ⚠️ Found **{len(high_risk_countries)}** elevated-risk "
                         f"countries — fetching Know Your Country profiles...")
                for cname in high_risk_countries[:10]:  # cap at 10
                    st.write(f"&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; 🔎 {cname}...")
                    kyc_results = search_country_kyc_profile(cname)
                    country_kyc_profiles[cname] = kyc_results
            else:
                st.write("&nbsp;&nbsp;&nbsp; ✅ No High Risk or Very High Risk countries detected.")

            # Also do a batch search for general context
            country_risk_results = search_country_risk_batch(country_names)
            st.write(f"&nbsp;&nbsp;&nbsp; Checked risk data for {len(country_names)} countries.")

            # ══════════════════════════════════════════════════════════════
            # PHASE 2: AI REPORT GENERATION (single LLM call)
            # ══════════════════════════════════════════════════════════════
            selected_label = st.session_state.get("llm_model", list(LLM_PROVIDERS.keys())[0])
            st.write(f"✍️ **Step 7/7** — **{selected_label}** is drafting the full report...")

            # ── Token-efficient data payload ─────────────────────────────
            # Strip nulls, trim search results, use compact JSON (no indent)
            all_data = json.dumps(_compact({
                "charity": charity_data,
                "companies_house": ch_data,
                "web_search": _slim_search(website_results),
                "generic_search": _slim_search(generic_org_results),
                "adverse_org": _slim_search(adverse_org),
                "adverse_trustees": {k: _slim_search(v, max_items=3, max_chars=300)
                                     for k, v in adverse_trustees.items()},
                "positive_media": _slim_search(positive_results),
                "countries_classified": country_risk_classified,
                "high_risk_country_profiles": {k: _slim_search(v, max_items=3, max_chars=600)
                                               for k, v in country_kyc_profiles.items()},
                "country_risk_general": _slim_search(country_risk_results, max_items=6, max_chars=500),
            }), default=str)

            master_prompt = f"""Write a formal HRCOB KYC due-diligence report using the DATA below.
Rules: professional neutral tone, markdown, hyperlinks where URLs exist, tables for structured data.

## 1. Overview — What They Do
Activities, aims, website link, projects/programs from web search.

## 2. How the Charity Operates
Donation methods, funding sources, 3rd-party relationships & KYC checks, fund decision-makers.

## 3. Where They Operate
First, a summary table of ALL countries: Country | Continent | Risk Level (from countries_classified).
Use colour-coded labels: 🔴 Very High Risk, 🟠 High Risk, 🟡 Medium Risk, 🟢 Low Risk.

Then, for EACH High Risk or Very High Risk country, write a **detailed "Know Your Country" profile box** including:
- **Country Summary**: 2-3 sentence overview of AML risk landscape
- **Risk Indicators Table**: Sanctions | FATF Status | Terrorism | Corruption | Criminal Markets | EU Tax Blacklist | Offshore Finance Centre — mark each as ✅ flagged or ➖ not flagged
- **Sanctions Detail**: which authorities impose sanctions (UN, US/OFAC, EU, UK, etc.), scope (nuclear, human rights, etc.)
- **FATF Status**: whether on FATF grey/black list, mutual evaluation status
- **Key Concerns**: bullet points of specific risks (terrorism financing, corruption, illicit flows, etc.)
Use data from high_risk_country_profiles. Cite source: [Know Your Country](https://www.knowyourcountry.com/).
If no high-risk countries exist, state: "No elevated-risk jurisdictions identified."

## 4. Entity Details
Registration, HQ, years active, employees, volunteers.
Trustees/Directors table. Financial summary table (income/expenditure breakdown). Cost vs profit.

## 5. Adverse Media Search
Search string: `"[name]" AND ({ADVERSE_TERMS})`
Results per subject: Title (hyperlinked), excerpt, date. State clearly if none found.
Generic search highlights.

## 6. Positive Media Search
Awards, partnerships, government grants, safeguarding registration.

## 7. Risks and Mitigants
**Risks**: high-risk geographies, negative media, governance gaps, financial red flags.
**Mitigants**: UK-only payouts, governance strengths, reputable partners, track record.
Reference actual data.

--- DATA ---
{all_data}"""

            full_report = llm_generate(master_prompt)

            status.update(label="✅ Report Complete!", state="complete")

        except SSLError as err:
            status.update(label="Failed", state="error")
            st.error(f"SSL handshake failed: {err}")
            st.info("Set CORP_CA_BUNDLE or ALLOW_INSECURE_SSL=true in .env.")
            st.stop()
        except RequestException as err:
            status.update(label="Failed", state="error")
            st.error(f"Network/API request failed: {err}")
            st.stop()
        except Exception as err:
            status.update(label="Failed", state="error")
            st.error(f"Report generation failed: {err}")
            st.stop()

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 3: RENDER THE REPORT
    # ══════════════════════════════════════════════════════════════════════

    st.markdown("---")
    st.markdown(f"# 🛡️ HRCOB Due-Diligence Report: {entity_name}")
    st.markdown(f"**Charity Commission No:** {charity_num} &nbsp;|&nbsp; "
                f"**Report Date:** {datetime.now().strftime('%d %B %Y')} &nbsp;|&nbsp; "
                f"**Status:** {charity_data.get('reg_status')}")
    st.markdown("---")

    # ── Quick Facts Card ─────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Latest Income", fmt_money(charity_data.get("latest_income")))
    col2.metric("Latest Expenditure", fmt_money(charity_data.get("latest_expenditure")))
    col3.metric("Employees", charity_data.get("employees", "N/A"))
    col4.metric("Trustees", charity_data.get("num_trustees", len(trustees)))

    st.markdown("---")

    # ── Income Breakdown Chart ───────────────────────────────────────────
    income_data = {
        "Donations & Legacies": charity_data.get("inc_donations", 0) or 0,
        "Charitable Activities": charity_data.get("inc_charitable", 0) or 0,
        "Trading Activities": charity_data.get("inc_trading", 0) or 0,
        "Investments": charity_data.get("inc_investments", 0) or 0,
        "Other": charity_data.get("inc_other", 0) or 0,
    }
    income_data = {k: v for k, v in income_data.items() if v > 0}

    exp_data = {
        "Raising Funds": charity_data.get("exp_raising", 0) or 0,
        "Charitable Activities": charity_data.get("exp_charitable", 0) or 0,
        "Other": charity_data.get("exp_other", 0) or 0,
    }
    exp_data = {k: v for k, v in exp_data.items() if v > 0}

    if income_data or exp_data:
        chart_col1, chart_col2 = st.columns(2)
        if income_data:
            with chart_col1:
                st.subheader("Income Breakdown")
                df_inc = pd.DataFrame({"Source": income_data.keys(), "Amount (£)": income_data.values()})
                st.bar_chart(df_inc.set_index("Source"))
        if exp_data:
            with chart_col2:
                st.subheader("Expenditure Breakdown")
                df_exp = pd.DataFrame({"Category": exp_data.keys(), "Amount (£)": exp_data.values()})
                st.bar_chart(df_exp.set_index("Category"))

    st.markdown("---")

    # ── Trustees Table ───────────────────────────────────────────────────
    if trustees:
        st.subheader("🧑‍💼 Trustees / Directors")
        trustee_df = pd.DataFrame({"#": range(1, len(trustees) + 1), "Name": trustees})
        st.dataframe(trustee_df, use_container_width=True, hide_index=True)
        st.markdown("")

    # ── Countries of Operation — Risk-Classified Table ─────────────
    if country_risk_classified:
        st.subheader("🌍 Countries of Operation — Risk Assessment")

        # Build colour-coded risk labels
        _risk_icons = {
            "Very High Risk": "🔴 Very High Risk",
            "High Risk": "🟠 High Risk",
            "Medium Risk": "🟡 Medium Risk",
            "Low Risk": "🟢 Low Risk",
            "Unknown": "⚪ Unknown",
        }
        for entry in country_risk_classified:
            entry["risk_display"] = _risk_icons.get(entry["risk_level"], entry["risk_level"])

        risk_df = pd.DataFrame(country_risk_classified)
        display_df = risk_df[["country", "continent", "risk_display"]].rename(
            columns={"country": "Country", "continent": "Continent", "risk_display": "Risk Level"}
        )
        st.dataframe(display_df, use_container_width=True, hide_index=True)

        # Highlight elevated-risk countries
        elevated = [e for e in country_risk_classified if is_elevated_risk(e["risk_level"])]
        if elevated:
            st.warning(f"⚠️ **{len(elevated)} elevated-risk jurisdiction(s) detected:** "
                       + ", ".join(f"**{e['country']}** ({e['risk_level']})" for e in elevated))
        else:
            st.success("✅ No High Risk or Very High Risk jurisdictions detected.")
        st.markdown("")

    st.markdown("---")

    # ── Render Full AI Report ────────────────────────────────────────
    st.markdown(full_report)

    # ── Raw Data (collapsible) ───────────────────────────────────────────
    st.markdown("---")
    with st.expander("📦 Raw Data (Charity Commission + Companies House)", expanded=False):
        st.json(charity_data)
        if ch_data:
            st.json(ch_data)

    st.markdown("---")
    st.caption(f"Report generated on {datetime.now().strftime('%d %B %Y at %H:%M')} "
               f"by HRCOB Automated KYC Engine. Data sources: Charity Commission, "
               f"Companies House, Tavily Web Search, OpenAI / Google Gemini.")