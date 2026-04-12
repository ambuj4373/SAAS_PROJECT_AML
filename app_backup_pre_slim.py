import streamlit as st
import requests
import os
import json
import ssl
import time
import re
import io
import base64
import hashlib
import pandas as pd
from urllib.parse import urljoin, urlparse, quote
from concurrent.futures import ThreadPoolExecutor, as_completed
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import httpx
import pdfplumber
import fitz  # PyMuPDF — layout-aware PDF extraction
from bs4 import BeautifulSoup

# Optional OCR support — requires tesseract binary installed on system
try:
    import pytesseract
    from PIL import Image
    _OCR_AVAILABLE = bool(pytesseract.get_tesseract_version())
except Exception:
    _OCR_AVAILABLE = False
from google import genai
from google.genai.types import HttpOptions
from openai import OpenAI
from tavily import TavilyClient
from requests.exceptions import RequestException, SSLError
from datetime import datetime

# ─── Centralised configuration (API keys, clients, risk data, pricing) ───────
from config import (
    CH_API_KEY, CHARITY_COMMISSION_API_KEY, TAVILY_API_KEY,
    GEMINI_API_KEY, OPENAI_API_KEY, BASEL_AML_URL,
    COUNTRY_RISK_MATRIX, get_country_risk, is_elevated_risk,
    get_ssl_verify, _ssl_verify,
    gemini_client, openai_client, tavily,
    LLM_PROVIDERS, MODEL_PRICING, _calc_cost,
    STYLE_CSS_PATH,
)

# ─── STREAMLIT PAGE CONFIG ───────────────────────────────────────────────────
st.set_page_config(page_title="Know Your Charity UK", layout="wide", page_icon="🔍")

# ─── CUSTOM STYLING (loaded from style.css) ──────────────────────────────────
with open(STYLE_CSS_PATH) as _css_fh:
    st.markdown(f"<style>{_css_fh.read()}</style>", unsafe_allow_html=True)

# ============================================================================
# MODULE IMPORTS - business logic extracted from monolithic app.py
# ============================================================================
from core.pdf_parser import (
    parse_cc_printout, extract_pdf_text, extract_pdf_with_vision,
    extract_pdf_sections, extract_partners_from_text,
    compute_extraction_confidence,
)
from api_clients.charity_commission import (
    download_cc_accounts_pdf, download_cc_latest_tar,
    fetch_charity_data, build_cc_governance_intel,
    fetch_financial_history,
    _ORG_TYPE_INFO, _REG_EVENT_INFO,
)
from api_clients.companies_house import (
    fetch_ch_data, fetch_trustee_appointments,
)
from api_clients.tavily_search import (
    tavily_search, search_adverse_media, count_true_adverse,
    search_generic, search_website_projects, search_positive_media,
    extract_social_media_from_website, search_online_presence,
    search_policies, search_partnerships,
    search_country_risk_batch, search_country_kyc_profile,
    ADVERSE_TERMS, ADVERSE_CRIME_TERMS, SANCTIONED_JURISDICTIONS,
    POLICY_CHECKLIST,
)
from core.risk_engine import (
    assess_governance_indicators, assess_structural_governance,
    detect_financial_anomalies, generate_financial_trend_comment,
)

# ─── LLM HELPERS ─────────────────────────────────────────────────────────────

def _call_gemini(prompt, model_name):
    response = gemini_client.models.generate_content(model=model_name, contents=prompt)
    # Extract token counts from Gemini usage metadata
    usage = getattr(response, "usage_metadata", None)
    prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
    completion_tokens = getattr(usage, "candidates_token_count", 0) or 0
    cost = _calc_cost(model_name, prompt_tokens, completion_tokens)
    cost_info = {
        "model": model_name,
        "provider": "gemini",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_usd": cost,
    }
    return response.text, cost_info


def _call_openai(prompt, model_name):
    response = openai_client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": (
                "You are a professional KYC/AML compliance analyst. "
                "Write thorough, evidence-based due-diligence reports. "
                "Be analytical — interpret data, identify and contextualise risk indicators "
                "and control strengths, and make proportionate assessments. "
                "Use markdown: tables, bold, hyperlinks. "
                "Every sentence must add value. Never fabricate — if data is missing, "
                "say so explicitly."
            )},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=8000,
    )
    usage = response.usage
    prompt_tokens = usage.prompt_tokens if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0
    cost = _calc_cost(model_name, prompt_tokens, completion_tokens)
    cost_info = {
        "model": model_name,
        "provider": "openai",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_usd": cost,
    }
    return response.choices[0].message.content, cost_info


def llm_generate(prompt, max_retries=3):
    """Generate LLM report. Returns (text, cost_info) tuple."""
    selected_label = st.session_state.get("llm_model", list(LLM_PROVIDERS.keys())[0])
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
                        break
                else:
                    raise
    raise last_err


# ─── FORMAT / CHART HELPERS ──────────────────────────────────────────────────

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
        entry = {
            "title": (r.get("title") or "")[:120],
            "url": r.get("url", ""),
            "snippet": (r.get("content") or "")[:max_chars],
        }
        # Preserve adverse media relevance tag if present
        if "_relevant" in r:
            entry["verified_adverse"] = r["_relevant"]
        out.append(entry)
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


def make_pie_chart(data_dict, title):
    """Matplotlib pie chart."""
    fig, ax = plt.subplots(figsize=(6, 4))
    labels = list(data_dict.keys())
    values = list(data_dict.values())
    colors = plt.cm.Set3(range(len(labels)))
    wedges, texts, autotexts = ax.pie(
        values, labels=None, autopct='%1.1f%%', colors=colors,
        pctdistance=0.85, startangle=90
    )
    ax.legend(wedges, [f"{l}: £{v:,.0f}" for l, v in zip(labels, values)],
              loc="center left", bbox_to_anchor=(1, 0.5), fontsize=8)
    ax.set_title(title, fontweight='bold', fontsize=11)
    plt.tight_layout()
    return fig


def make_income_vs_expense_chart(income, expenditure):
    """Bar chart: income vs expenditure with surplus label."""
    fig, ax = plt.subplots(figsize=(5, 3))
    bars = ax.bar(["Income", "Expenditure"], [income or 0, expenditure or 0],
                  color=["#2ecc71", "#e74c3c"], width=0.5)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"£{x:,.0f}"))
    for bar, val in zip(bars, [income or 0, expenditure or 0]):
        ax.text(bar.get_x() + bar.get_width()/2,
                bar.get_height() + max(income or 0, expenditure or 0) * 0.02,
                f"£{val:,.0f}", ha='center', va='bottom', fontsize=9, fontweight='bold')
    surplus = (income or 0) - (expenditure or 0)
    color = "#2ecc71" if surplus >= 0 else "#e74c3c"
    ax.set_title(f"Income vs Expenditure  |  Surplus: £{surplus:,.0f}",
                 fontweight='bold', fontsize=10, color=color)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    plt.tight_layout()
    return fig


def make_financial_trend_chart(history):
    """Line chart: income & expenditure over multiple years.
    `history` is a list of {year, income, expenditure} dicts, sorted ascending."""
    years = [h["year"] for h in history]
    incomes = [h["income"] for h in history]
    expenditures = [h["expenditure"] for h in history]
    surpluses = [h["income"] - h["expenditure"] for h in history]

    fig, ax = plt.subplots(figsize=(7, 3.5))
    ax.plot(years, incomes, marker="o", markersize=4, linewidth=1.5,
            color="#2c7a4b", label="Income")
    ax.plot(years, expenditures, marker="s", markersize=4, linewidth=1.5,
            color="#a63d40", label="Expenditure")
    # Subtle surplus/deficit line
    ax.plot(years, surpluses, marker="^", markersize=3, linewidth=1,
            color="#6c757d", linestyle="--", alpha=0.7, label="Net Surplus / Deficit")
    ax.axhline(y=0, color="#999999", linewidth=0.5, linestyle=":")

    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"£{x:,.0f}"))
    ax.set_xlabel("Financial Year", fontsize=9)
    ax.set_ylabel("£", fontsize=9)
    ax.set_title("Income & Expenditure Trend", fontweight="bold", fontsize=10)
    ax.legend(fontsize=8, loc="best")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="both", labelsize=8)
    plt.tight_layout()
    return fig


# ═══════════════════════════════════════════════════════════════════════════════
# STREAMLIT UI
# ═══════════════════════════════════════════════════════════════════════════════

st.title("� Know Your Charity UK")
st.caption("Independent Risk Review of UK Charities · knowyourcharity@ambujshukla.com")

st.sidebar.header("🔧 Configuration")
charity_num = st.sidebar.text_input(
    "Charity Registration Number", value="",
    placeholder="e.g. 1234567")

_report_mode = st.sidebar.radio(
    "Report Mode",
    ["🔍 Donor Snapshot (Free)", "🛡️ Full Due-Diligence Report"],
    index=0,
    key="report_mode",
    help=(
        "**Donor Snapshot**: Quick, free overview using publicly available data. "
        "Ideal for individual donors checking a charity before giving.\n\n"
        "**Full Due-Diligence Report**: Comprehensive AI-powered KYC analysis "
        "including adverse media, policy discovery, and governance deep-dive. "
        "Uses API credits."
    ),
)
_is_donor_mode = _report_mode.startswith("🔍")

if not _is_donor_mode:
    st.sidebar.selectbox(
        "AI Model",
        list(LLM_PROVIDERS.keys()),
        index=0,
        key="llm_model",
        help="If the selected model is rate-limited, the app auto-falls back to the next available model.",
    )

    st.sidebar.toggle(
        "👁️ Vision PDF Extraction (OCR)",
        value=False,
        key="enable_vision_ocr",
        help=(
            "When ON: image-based / scanned PDFs are re-read using GPT-4.1-mini vision "
            "(~$0.01–0.05 per document). Produces better extraction but costs OpenAI credits.\n\n"
            "When OFF: only standard text extraction is used — faster and free, but may miss "
            "content in scanned / image-heavy PDFs. Recommended OFF for testing."
        ),
    )

    st.sidebar.markdown("---")
    st.sidebar.header("📄 Document Upload")

    # ── Section 1: CC Register Printout (Primary) ────────────────
    with st.sidebar.expander("🏛️ Charity Commission Printout", expanded=True):
        st.caption(
            "Upload the official register printout from the Charity Commission website. "
            "This provides verified primary data (trustees, policies, financials, "
            "objects, contact details) and significantly improves report accuracy."
        )
        cc_printout_file = st.file_uploader(
            "Upload CC Register Printout (PDF)",
            type=["pdf"],
            accept_multiple_files=False,
            key="cc_printout_uploader",
            help="Download from register-of-charities.charitycommission.gov.uk → "
                 "'Print charity details'. This single PDF contains structured data "
                 "that replaces multiple API calls and web searches.",
        )

    # ── Section 2: Supporting Documents ──────────────────────────
    with st.sidebar.expander("📁 Supporting Documents (Optional)", expanded=False):
        st.caption(
            "Upload annual reports, accounts, TAR, and any governance or "
            "policy documents provided by the charity."
        )
        uploaded_files = st.file_uploader(
            "Annual Reports / Accounts (PDF)",
            type=["pdf"],
            accept_multiple_files=True,
            key="annual_reports_uploader",
            help="Upload the charity's annual report, accounts, trustees' report, "
                 "or any other relevant PDF documents. "
                 "The text will be extracted and fed into the AI analysis.",
        )
        st.markdown("---")
        st.caption(
            "Upload policy documents provided directly by the charity. "
            "These take priority over web-scraped evidence for control assessment."
        )
        gov_doc_files = st.file_uploader(
            "Governance & Policy PDFs",
            type=["pdf"],
            accept_multiple_files=True,
            key="gov_doc_uploader",
            help="Safeguarding, AML, Risk Management, Due Diligence, "
                 "Bribery, Whistleblowing procedures, etc.",
        )

    with st.sidebar.expander("🌐 Official Domain & Social Links (Optional)", expanded=False):
        st.caption(
            "Override the Charity Commission website or provide verified social media URLs."
        )
        override_domain = st.text_input(
            "Official Website Domain",
            value="",
            placeholder="e.g. https://www.charityname.org.uk",
            help="If the CC API website field is incorrect or you want to scan an alternative domain.",
            key="override_domain",
        )
        st.markdown("**Verified Social Media Links**")
        manual_facebook = st.text_input("Facebook URL", value="", key="manual_fb",
                                         placeholder="https://facebook.com/...")
        manual_instagram = st.text_input("Instagram URL", value="", key="manual_ig",
                                          placeholder="https://instagram.com/...")
        manual_twitter = st.text_input("Twitter / X URL", value="", key="manual_tw",
                                        placeholder="https://x.com/...")
        manual_linkedin = st.text_input("LinkedIn URL", value="", key="manual_li",
                                         placeholder="https://linkedin.com/...")
        manual_youtube = st.text_input("YouTube URL", value="", key="manual_yt",
                                        placeholder="https://youtube.com/...")
        manual_other_social = st.text_input("Other social link", value="", key="manual_other",
                                             placeholder="https://...")
else:
    # Donor mode — minimal sidebar
    cc_printout_file = None
    uploaded_files = None
    gov_doc_files = None
    override_domain = ""
    manual_facebook = manual_instagram = manual_twitter = ""
    manual_linkedin = manual_youtube = manual_other_social = ""

_btn_label = "🔍 Check Charity" if _is_donor_mode else "🚀 Generate Full Report"
run_btn = st.sidebar.button(_btn_label, type="primary",
                            use_container_width=True)

st.sidebar.markdown("---")
st.sidebar.caption(
    "Powered by Charity Commission API, Companies House API, "
    "Tavily Search, OpenAI & Google Gemini"
)

# ─── Report History Sidebar ──────────────────────────────────────────────────
if "report_history" not in st.session_state:
    st.session_state["report_history"] = []

if st.session_state["report_history"]:
    st.sidebar.markdown("---")
    st.sidebar.header("📜 Report History")

    # Cumulative cost summary
    total_session_cost = sum(h["cost_usd"] for h in st.session_state["report_history"])
    total_session_tokens = sum(h["total_tokens"] for h in st.session_state["report_history"])
    st.sidebar.metric(
        "Session Total Cost",
        f"${total_session_cost:.4f}" if total_session_cost > 0 else "Free",
        help=f"Total tokens used: {total_session_tokens:,}",
    )

    for idx, entry in enumerate(reversed(st.session_state["report_history"])):
        cost_str = f"${entry['cost_usd']:.4f}" if entry["cost_usd"] > 0 else "Free"
        with st.sidebar.expander(
            f"#{len(st.session_state['report_history']) - idx} — "
            f"{entry['charity_name'][:25]} ({entry['charity_num']})",
            expanded=False,
        ):
            st.markdown(
                f"**Date:** {entry['timestamp']}  \n"
                f"**Model:** {entry['model']}  \n"
                f"**Tokens:** {entry['total_tokens']:,}  \n"
                f"**Cost:** {cost_str}"
            )
            if st.button(f"📄 View Report", key=f"view_hist_{idx}"):
                st.session_state["_view_history_idx"] = len(st.session_state["report_history"]) - 1 - idx

# ─── Show historical report if selected ──────────────────────────────────────
if "_view_history_idx" in st.session_state:
    hist_idx = st.session_state["_view_history_idx"]
    if 0 <= hist_idx < len(st.session_state["report_history"]):
        h = st.session_state["report_history"][hist_idx]
        st.markdown("---")
        st.markdown(f"# 📜 Historical Report: {h['charity_name']}")
        h_cost = f"${h['cost_usd']:.4f}" if h["cost_usd"] > 0 else "Free"
        st.markdown(
            f"**Charity No:** {h['charity_num']} &nbsp;|&nbsp; "
            f"**Generated:** {h['timestamp']} &nbsp;|&nbsp; "
            f"**Model:** {h['model']} &nbsp;|&nbsp; "
            f"**Cost:** {h_cost} &nbsp;|&nbsp; "
            f"**Tokens:** {h['total_tokens']:,}"
        )
        st.markdown("---")
        st.markdown(h["report_text"])
        st.markdown("---")
        if st.button("✖ Close Historical Report"):
            del st.session_state["_view_history_idx"]
            st.rerun()
        st.markdown("---")

# ══════════════════════════════════════════════════════════════════════
# DONOR SNAPSHOT MODE — free, lightweight, CC API only
# ══════════════════════════════════════════════════════════════════════
if run_btn and _is_donor_mode:
    if not CHARITY_COMMISSION_API_KEY:
        st.error("Missing CHARITY_COMMISSION_API_KEY. Set it in your .env file.")
        st.stop()
    if not charity_num or not charity_num.strip().isdigit():
        st.error("Please enter a valid charity registration number.")
        st.stop()

    charity_num = charity_num.strip()

    with st.status("🔍 Checking charity — this takes just a few seconds...",
                   expanded=True) as status:
        try:
            st.write("📡 Fetching Charity Commission data...")
            charity_data = fetch_charity_data(charity_num)
            entity_name = charity_data.get("charity_name", "Unknown Charity")
            inc = charity_data.get("latest_income") or 0
            exp = charity_data.get("latest_expenditure") or 0
            employees = charity_data.get("employees") or 0
            volunteers = charity_data.get("volunteers") or 0
            num_trustees = charity_data.get("num_trustees") or len(charity_data.get("trustees", []))
            reg_status = charity_data.get("reg_status", "")
            reg_date = charity_data.get("date_of_registration", "")
            website = charity_data.get("website", "")
            activities = charity_data.get("activities", "")
            what_it_does = charity_data.get("what_it_does", [])
            who_it_helps = charity_data.get("who_it_helps", [])
            how_it_operates = charity_data.get("how_it_operates", [])
            countries = charity_data.get("countries", [])
            _other_names = charity_data.get("other_names", [])
            fin_year = charity_data.get("fin_year_end", "")

            # Financial history
            st.write("📊 Fetching financial history...")
            financial_history = fetch_financial_history(charity_num)

            # Governance intel
            cc_governance = build_cc_governance_intel(charity_data)
            _sf = cc_governance.get("status_flags", {})

            # Social media from website
            _social = {}
            if website:
                st.write("🌐 Checking website for social media links...")
                try:
                    _social = extract_social_media_from_site(website)
                except Exception:
                    pass

            status.update(label="✅ Charity data retrieved!", state="complete")

        except Exception as e:
            status.update(label="Failed", state="error")
            st.error(f"Could not retrieve charity data. Verify the registration number is correct.")
            st.stop()

    # ── Persist donor snapshot to session state ──
    st.session_state["_donor_snapshot"] = {
        "charity_data": charity_data,
        "entity_name": entity_name,
        "charity_num": charity_num,
        "financial_history": financial_history,
        "cc_governance": cc_governance,
        "social_media": _social,
    }

# ── Render donor snapshot from session state ──
_ds = st.session_state.get("_donor_snapshot")
if _ds and _is_donor_mode:
    charity_data = _ds["charity_data"]
    entity_name = _ds["entity_name"]
    charity_num = _ds["charity_num"]
    financial_history = _ds["financial_history"]
    cc_governance = _ds["cc_governance"]
    _social = _ds.get("social_media", {})

    inc = charity_data.get("latest_income") or 0
    exp = charity_data.get("latest_expenditure") or 0
    employees = charity_data.get("employees") or 0
    volunteers = charity_data.get("volunteers") or 0
    num_trustees = charity_data.get("num_trustees") or len(charity_data.get("trustees", []))
    reg_status = charity_data.get("reg_status", "")
    reg_date = charity_data.get("date_of_registration", "")
    website = charity_data.get("website", "")
    activities = charity_data.get("activities", "")
    what_it_does = charity_data.get("what_it_does", [])
    who_it_helps = charity_data.get("who_it_helps", [])
    how_it_operates = charity_data.get("how_it_operates", [])
    countries = charity_data.get("countries", [])
    _other_names = charity_data.get("other_names", [])
    fin_year = charity_data.get("fin_year_end", "")
    _sf = cc_governance.get("status_flags", {})

    # ── Calculate transparency signals ──
    _transparency_signals = []
    _transparency_positive = []
    _org_type = cc_governance.get("organisation_type") or charity_data.get("charity_type", "")
    _org_info = _ORG_TYPE_INFO.get(_org_type, {})

    # Positive signals
    if reg_status == "R":
        _transparency_positive.append("Registered and active with the Charity Commission")
    if website:
        _transparency_positive.append("Has a public website")
    if inc > 0 and exp > 0:
        _spend_pct = exp / inc * 100
        if 75 <= _spend_pct <= 105:
            _transparency_positive.append(f"Healthy spend-to-income ratio ({_spend_pct:.0f}%)")
    if employees > 0:
        _transparency_positive.append(f"Has {employees} paid employee(s)")
    if volunteers and volunteers > 0:
        _transparency_positive.append(f"Engages {volunteers:,} volunteer(s)")
    if num_trustees >= 3:
        _transparency_positive.append(f"Board of {num_trustees} trustees")
    if financial_history and len(financial_history) >= 3:
        _transparency_positive.append(f"Financial reporting history of {len(financial_history)} years")
    _reporting = cc_governance.get("reporting_status", "")
    if _reporting and "received" in _reporting.lower():
        _transparency_positive.append("Charity Commission reporting is up to date")
    _ga = cc_governance.get("gift_aid")
    if _ga and ("recognised" in _ga.lower() or "active" in _ga.lower()):
        _transparency_positive.append("HMRC-recognised for Gift Aid")
    if _social:
        _social_found = [k for k, v in _social.items() if v]
        if _social_found:
            _transparency_positive.append(f"Active on {len(_social_found)} social media platform(s)")

    # Caution signals
    if reg_status and reg_status != "R":
        _transparency_signals.append("Charity is not currently registered (may be removed or excepted)")
    if _sf.get("insolvent"):
        _transparency_signals.append("Charity is marked as insolvent")
    if _sf.get("in_administration"):
        _transparency_signals.append("Charity is in administration")
    if _sf.get("interim_manager"):
        _transparency_signals.append("Charity Commission has appointed an interim manager")
    if _sf.get("removed"):
        _transparency_signals.append(f"Charity has been removed from the register: {_sf.get('removal_reason', '')}")
    if inc > 0 and exp > 0:
        _spend_pct = exp / inc * 100
        if _spend_pct > 120:
            _transparency_signals.append(f"Expenditure significantly exceeds income ({_spend_pct:.0f}%)")
    if num_trustees <= 2 and inc >= 100_000:
        _transparency_signals.append(f"Limited board oversight — only {num_trustees} trustee(s) for income of £{inc:,.0f}")
    if inc >= 500_000 and employees == 0:
        _transparency_signals.append("No paid employees despite significant income")
    if not website:
        _transparency_signals.append("No public website listed — limited online transparency")
    if _reporting and "received" not in _reporting.lower() and _reporting.lower() != "":
        _transparency_signals.append(f"Reporting status: {_reporting}")
    if _other_names and len(_other_names) >= 3:
        _transparency_signals.append(f"Multiple alternative names ({len(_other_names)}) — verify identity")

    # ── Donor-friendly transparency level ──
    if _sf.get("insolvent") or _sf.get("in_administration") or _sf.get("removed"):
        _donor_level = "Enhanced Due Diligence Recommended"
        _donor_color = "#dc2626"
        _donor_icon = "🔴"
    elif len(_transparency_signals) >= 3:
        _donor_level = "Limited Transparency — Ask Questions Before Donating"
        _donor_color = "#d97706"
        _donor_icon = "🟠"
    elif len(_transparency_signals) >= 1:
        _donor_level = "Mostly Transparent — Some Queries Recommended"
        _donor_color = "#ca8a04"
        _donor_icon = "🟡"
    else:
        _donor_level = "Transparent — Key Indicators Positive"
        _donor_color = "#16a34a"
        _donor_icon = "🟢"

    # ── Render Donor Snapshot ──
    st.markdown("---")

    # Banner
    st.markdown(f"""<div style="background:linear-gradient(135deg,#1e40af,#3b82f6);
    color:#fff;padding:1.5rem 2rem;border-radius:12px;margin-bottom:1rem;">
    <h1 style="color:#fff!important;margin:0;font-size:1.8rem;">🔍 Know Your Charity UK</h1>
    <div style="font-size:1.3rem;margin-top:0.3rem;font-weight:600;">{entity_name}</div>
    <div style="font-size:0.85rem;margin-top:0.5rem;opacity:0.9;">
        Charity No: <b>{charity_num}</b> &nbsp;·&nbsp;
        Snapshot Date: <b>{datetime.now().strftime('%d %B %Y')}</b> &nbsp;·&nbsp;
        Mode: <b>Donor Snapshot (Free)</b>
    </div>
    </div>""", unsafe_allow_html=True)

    # Transparency Assessment
    st.markdown(f"""<div style="border:2px solid {_donor_color};border-radius:10px;
    padding:1rem 1.5rem;margin-bottom:1rem;">
    <div style="font-size:1.1rem;font-weight:700;">
        {_donor_icon} Transparency Assessment: <span style="color:{_donor_color}">{_donor_level}</span>
    </div>
    <div style="font-size:0.78rem;color:#6b7280;margin-top:0.3rem;">
        Based on publicly available Charity Commission data. This is an automated assessment,
        not a formal audit or regulatory opinion.
    </div>
    </div>""", unsafe_allow_html=True)

    # Key numbers
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Annual Income", f"£{inc:,.0f}" if inc else "Not reported")
    c2.metric("Annual Expenditure", f"£{exp:,.0f}" if exp else "Not reported")
    c3.metric("Trustees", num_trustees or "Unknown")
    c4.metric("Employees", employees if employees else "Volunteer-run")

    # Two-column layout: positive signals + caution signals
    col_good, col_caution = st.columns(2)

    with col_good:
        st.markdown("#### ✅ Positive Indicators")
        if _transparency_positive:
            for _tp in _transparency_positive:
                st.markdown(f"- {_tp}")
        else:
            st.markdown("_No strong positive indicators identified._")

    with col_caution:
        st.markdown("#### ⚠️ Points to Consider")
        if _transparency_signals:
            for _ts in _transparency_signals:
                st.markdown(f"- {_ts}")
        else:
            st.markdown("_No concerns identified from available data._")

    # ── About This Charity ──
    st.markdown("---")
    st.markdown("### 📋 About This Charity")

    _about_html = '<div style="font-size:0.88rem;">'
    if activities:
        _about_html += f'<p><b>Activities:</b> {activities}</p>'
    if what_it_does:
        _about_html += f'<p><b>What it does:</b> {", ".join(what_it_does)}</p>'
    if who_it_helps:
        _about_html += f'<p><b>Who it helps:</b> {", ".join(who_it_helps)}</p>'
    if how_it_operates:
        _about_html += f'<p><b>How it operates:</b> {", ".join(how_it_operates)}</p>'
    if _org_type:
        _about_html += f'<p><b>Organisation type:</b> {_org_type}'
        if _org_info.get("full_name"):
            _about_html += f' ({_org_info["full_name"]})'
        _about_html += '</p>'
    if reg_date:
        _rd = str(reg_date)[:10]
        _about_html += f'<p><b>Registered:</b> {_rd}</p>'
    if _other_names:
        _about_html += f'<p><b>Also known as:</b> {", ".join(_other_names)}</p>'
    if countries:
        _country_names = [c.get("country", "") for c in countries if c.get("country")]
        if _country_names:
            _about_html += f'<p><b>Countries of operation:</b> {", ".join(_country_names[:10])}'
            if len(_country_names) > 10:
                _about_html += f' ...and {len(_country_names) - 10} more'
            _about_html += '</p>'
    _about_html += '</div>'
    st.markdown(_about_html, unsafe_allow_html=True)

    # ── Financial Trend ──
    if financial_history and len(financial_history) >= 2:
        st.markdown("### 📊 Financial Trend")
        _fh_df = pd.DataFrame(financial_history)
        _fh_df.columns = ["Year", "Income (£)", "Expenditure (£)"]
        st.line_chart(_fh_df.set_index("Year"))

        # Simple income direction
        _first_inc = financial_history[0].get("income", 0)
        _last_inc = financial_history[-1].get("income", 0)
        if _last_inc > _first_inc * 1.1:
            st.caption("📈 Income has been growing over the reporting period.")
        elif _last_inc < _first_inc * 0.9:
            st.caption("📉 Income has declined over the reporting period.")
        else:
            st.caption("➡️ Income has remained relatively stable.")

    # ── Where to Follow & Verify ──
    st.markdown("### 🌐 Where to Follow & Verify")

    _links_html = '<div style="font-size:0.88rem;">'
    if website:
        _links_html += (f'<p>🌍 <b>Official Website:</b> '
                       f'<a href="{website}" target="_blank">{website}</a> — '
                       f'Visit to read their latest reports and impact updates.</p>')

    # Social media links
    _social_platforms = {
        "facebook": ("📘", "Facebook"),
        "instagram": ("📸", "Instagram"),
        "twitter": ("🐦", "Twitter / X"),
        "linkedin": ("💼", "LinkedIn"),
        "youtube": ("📺", "YouTube"),
        "tiktok": ("🎵", "TikTok"),
    }
    _any_social = False
    for _plat, (_emoji, _label) in _social_platforms.items():
        _url = (_social or {}).get(_plat)
        if _url:
            _links_html += (f'<p>{_emoji} <b>{_label}:</b> '
                           f'<a href="{_url}" target="_blank">{_url}</a> — '
                           f'Follow to see their work and community engagement.</p>')
            _any_social = True
    if not _any_social and not website:
        _links_html += ('<p>⚠️ No public website or social media profiles were found. '
                       'Consider contacting the charity directly to request information.</p>')
    elif not _any_social:
        _links_html += ('<p>ℹ️ No social media profiles were detected on the charity\'s website. '
                       'The charity may maintain a social media presence not linked from their site.</p>')

    # Official register links
    _cc_org_num = charity_data.get("organisation_number") or charity_num
    _links_html += (f'<p>🏛️ <b>Charity Commission:</b> '
                   f'<a href="https://register-of-charities.charitycommission.gov.uk/'
                   f'charity-search/-/charity-details/{_cc_org_num}" target="_blank">'
                   f'View on CC Register</a> — '
                   f'Official registration, accounts, and trustee information.</p>')

    _co_num = (charity_data.get("company_number") or "").strip()
    if _co_num:
        _links_html += (f'<p>🏢 <b>Companies House:</b> '
                       f'<a href="https://find-and-update.company-information.service.gov.uk/'
                       f'company/{_co_num}" target="_blank">'
                       f'View company {_co_num}</a> — '
                       f'Company filings and officer details.</p>')

    _links_html += '</div>'
    st.markdown(_links_html, unsafe_allow_html=True)

    st.markdown("### 💡 What Can You Do as a Donor?")
    st.markdown("""
- **Ask questions**: Before donating, you have every right to ask a charity how your money will be used. A well-run charity will welcome this.
- **Check their accounts**: Registered charities must publish annual accounts. Look for these on the [Charity Commission register](https://register-of-charities.charitycommission.gov.uk/) or the charity's website.
- **Look for impact reports**: Transparent charities regularly share how donations translate into outcomes — look for this on their website and social media.
- **Verify Gift Aid claims**: If you're a UK taxpayer, Gift Aid adds 25p to every £1 you donate at no extra cost. But the charity must be HMRC-recognised.
- **Report concerns**: If something doesn't feel right, you can report concerns to the [Charity Commission](https://www.gov.uk/complain-about-charity).
""")

    # ── Charity Sector Insights ──
    st.markdown("### 📰 Understanding the UK Charity Sector")
    st.markdown("""
<div style="font-size:0.88rem;">

**The power of giving:** The UK charity sector comprises over 170,000 registered charities with a combined annual income exceeding £80 billion.
Donations help fund everything from local food banks and hospices to international disaster relief.
Research by the <a href="https://www.cafonline.org/about-us/research" target="_blank">Charities Aid Foundation</a>
consistently shows that charitable giving makes a measurable difference — funding medical research breakthroughs,
supporting people in crisis, and building community resilience.

**Real impact stories:**
- UK charities provided over 2.5 million emergency food parcels through the <a href="https://www.trussell.org.uk/" target="_blank">Trussell Trust</a> network alone in 2023/24.
- <a href="https://www.cancerresearchuk.org/" target="_blank">Cancer Research UK</a> has helped double cancer survival rates over 40 years — funded almost entirely by public donations.
- Small, local charities often achieve outsized impact: community groups, sports clubs, and faith organisations transform neighbourhoods with modest budgets.

**Staying informed:**
While the vast majority of charities operate with integrity, a small number have faced scrutiny for governance failings,
inappropriate spending, or lack of transparency. High-profile cases (such as
<a href="https://en.wikipedia.org/wiki/Kids_Company" target="_blank">Kids Company</a>)
have led to strengthened regulation and reporting requirements. This is why platforms like Know Your Charity UK exist —
to help donors make informed decisions, not to discourage giving, but to direct it wisely.

**Tips for confident giving:**
1. Check the <a href="https://register-of-charities.charitycommission.gov.uk/" target="_blank">Charity Commission register</a> to verify a charity is genuine.
2. Look at their spend-to-income ratio — a healthy charity typically spends 75–95% of income on charitable purposes.
3. A charity with 0% fundraising costs may sound efficient, but it could also indicate under-investment in sustainability.
4. Don't let a single metric define your view — context matters. A small charity might have higher admin ratios simply because of its size.

</div>
""", unsafe_allow_html=True)

    # Footer
    st.markdown("---")
    st.markdown(
        '<div style="text-align:center;font-size:0.7rem;color:#9ca3af;margin-top:0.5rem;">'
        'Built by Ambuj Shukla with the help of Co-Pilot · '
        '<a href="mailto:knowyourcharity@ambujshukla.com" style="color:#6b7280;">'
        'knowyourcharity@ambujshukla.com</a><br>'
        'Data sourced from the Charity Commission public API. This is not financial advice or a regulatory assessment.'
        '</div>',
        unsafe_allow_html=True,
    )

# ══════════════════════════════════════════════════════════════════════
# FULL DUE-DILIGENCE REPORT MODE
# ══════════════════════════════════════════════════════════════════════
if run_btn and not _is_donor_mode:
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

    with st.status("🔄 Generating KYC Report — typically 30-60 seconds...",
                   expanded=True) as status:
        try:
            if _ssl_verify is False:
                st.warning("⚠️ SSL verification disabled (ALLOW_INSECURE_SSL=true). "
                           "For local use only.")

            # ══════════════════════════════════════════════════════════════
            # PHASE 1: DATA COLLECTION  (10 steps)
            # ══════════════════════════════════════════════════════════════

            # ── Step 1: Charity Commission ───────────────────────────────
            st.write("🔍 **Step 1/7** — Fetching Charity Commission records...")
            charity_data = None
            try:
                charity_data = fetch_charity_data(charity_num)
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    st.error(f"Charity **{charity_num}** not found on Charity Commission.")
                    st.stop()
                elif e.response is not None and e.response.status_code == 429:
                    st.error("Charity Commission API rate limit reached. Please wait a few minutes and retry.")
                    st.stop()
                elif e.response is not None and e.response.status_code >= 500:
                    st.error("Charity Commission API is temporarily unavailable. Please retry shortly.")
                    st.stop()
                raise
            except requests.exceptions.ConnectionError:
                st.error("Unable to connect to Charity Commission API. Check your network connection.")
                st.stop()
            except requests.exceptions.Timeout:
                st.error("Charity Commission API request timed out. Please retry.")
                st.stop()

            if charity_data is None:
                st.error(f"No data returned for charity '{charity_num}'.")
                st.stop()

            entity_name = charity_data.get("charity_name", "Unknown Charity")
            website = charity_data.get("website", "")
            # Apply domain override if user provided one
            _user_domain = (override_domain or "").strip()
            if _user_domain:
                if not _user_domain.startswith("http"):
                    _user_domain = "https://" + _user_domain
                website = _user_domain
                st.info(f"Using user-provided domain: **{website}**")
            trustees = charity_data.get("trustees", [])
            st.success(f"Found: **{entity_name}** — {len(trustees)} trustees, "
                       f"{len(charity_data.get('countries', []))} countries of operation")

            # ── CC Printout enrichment (if uploaded) ─────────────────
            cc_printout_data = {}
            cc_printout_text = ""
            if cc_printout_file is not None:
                st.write("🏛️ Parsing Charity Commission printout...")
                _printout_bytes = cc_printout_file.read()
                cc_printout_data = parse_cc_printout(_printout_bytes)
                if cc_printout_data:
                    _po_fields = len([k for k in cc_printout_data if not k.startswith("_")])
                    st.success(f"✅ CC Printout parsed — {_po_fields} structured fields extracted")

                    # Extract raw text too for the LLM
                    cc_printout_text_raw, _ = extract_pdf_text(_printout_bytes, max_pages=15, max_chars=20000)
                    cc_printout_text = (
                        "\n\n=== CHARITY COMMISSION OFFICIAL REGISTER PRINTOUT ===\n"
                        "Source: Uploaded CC Register Printout (primary verified source)\n"
                        f"{cc_printout_text_raw}"
                    )

                    # Enrich charity_data with printout fields where API may be sparse
                    if cc_printout_data.get("declared_policies"):
                        charity_data["_cc_declared_policies"] = cc_printout_data["declared_policies"]
                        st.caption(f"   📋 {len(cc_printout_data['declared_policies'])} declared policies found")
                    if cc_printout_data.get("charitable_objects"):
                        charity_data["_charitable_objects"] = cc_printout_data["charitable_objects"]
                    if cc_printout_data.get("trustees_detailed"):
                        charity_data["_trustees_detailed"] = cc_printout_data["trustees_detailed"]
                        st.caption(f"   👤 {len(cc_printout_data['trustees_detailed'])} trustees with appointment dates")
                    if cc_printout_data.get("where_the_charity_operates"):
                        charity_data["_operating_locations"] = cc_printout_data["where_the_charity_operates"]
                    if cc_printout_data.get("address"):
                        charity_data["_registered_address"] = cc_printout_data["address"]
                    if cc_printout_data.get("phone"):
                        charity_data["_phone"] = cc_printout_data["phone"]
                    if cc_printout_data.get("email"):
                        charity_data["_email"] = cc_printout_data["email"]
                    if cc_printout_data.get("filing_history"):
                        charity_data["_filing_history"] = cc_printout_data["filing_history"]
                    if cc_printout_data.get("financial_breakdown"):
                        charity_data["_financial_breakdown"] = cc_printout_data["financial_breakdown"]
                    if cc_printout_data.get("what_the_charity_does"):
                        charity_data["_what_it_does"] = cc_printout_data["what_the_charity_does"]
                    if cc_printout_data.get("who_the_charity_helps"):
                        charity_data["_who_it_helps"] = cc_printout_data["who_the_charity_helps"]
                    if cc_printout_data.get("how_the_charity_helps"):
                        charity_data["_how_it_helps"] = cc_printout_data["how_the_charity_helps"]
                    if cc_printout_data.get("land_property") is not None:
                        charity_data["_land_property"] = cc_printout_data["land_property"]
                    if cc_printout_data.get("trustee_payments") is not None:
                        charity_data["_trustee_payments"] = cc_printout_data["trustee_payments"]
                    if cc_printout_data.get("trading_subsidiaries") is not None:
                        charity_data["_trading_subsidiaries"] = cc_printout_data["trading_subsidiaries"]
                    if cc_printout_data.get("main_purpose_method"):
                        charity_data["_main_purpose_method"] = cc_printout_data["main_purpose_method"]
                else:
                    st.warning("⚠️ Could not parse CC printout — will proceed with API data only.")

            # Fetch financial history (non-blocking, fail-safe)
            financial_history = fetch_financial_history(charity_num)
            financial_anomalies = {}
            if financial_history:
                st.caption(f"Retrieved {len(financial_history)} years of financial history.")
                financial_anomalies = detect_financial_anomalies(financial_history)
                if financial_anomalies.get("anomaly_count", 0) > 0:
                    st.caption(f"⚡ {financial_anomalies['anomaly_count']} financial observation(s) flagged.")

            # Build CC governance intelligence from API data
            cc_governance = build_cc_governance_intel(charity_data)
            _gov_items = []
            _org_type = cc_governance.get("organisation_type") or ""
            if _org_type:
                _ot_info = _ORG_TYPE_INFO.get(_org_type, {})
                _gov_items.append(f"Type: **{_org_type}** ({_ot_info.get('full_name', '')})")
            _ga = cc_governance.get("gift_aid")
            if _ga:
                _ga_icon = "✅" if "recognised" in _ga.lower() or "active" in _ga.lower() else "⚠️"
                _gov_items.append(f"Gift Aid: {_ga_icon}")
            _rh = cc_governance.get("registration_history", [])
            if _rh:
                _gov_items.append(f"History: {len(_rh)} event(s)")
            # Critical status flags
            _sf = cc_governance.get("status_flags", {})
            if _sf.get("insolvent"):
                st.error("🚨 **CRITICAL: Charity is marked as INSOLVENT**")
            if _sf.get("in_administration"):
                st.error("🚨 **CRITICAL: Charity is IN ADMINISTRATION**")
            if _sf.get("interim_manager"):
                st.warning("⚠️ **Charity Commission Interim Manager appointed** — "
                           "CC has intervened in this charity's governance.")
            if _sf.get("removed"):
                st.warning(f"⚠️ Charity has been REMOVED from register: {_sf.get('removal_reason', 'reason unknown')}")
            if _sf.get("cio_dissolution"):
                st.warning("⚠️ CIO dissolution indicator is set — charity may be winding down.")
            _reporting = cc_governance.get("reporting_status", "")
            if _reporting:
                _gov_items.append(f"Reporting: {_reporting}")
            if _gov_items:
                st.caption("🏛️ Governance: " + " · ".join(_gov_items))

            # ── Step 2: Companies House ──────────────────────────────────
            st.write("🏢 **Step 2/7** — Checking Companies House link...")
            ch_data = None
            linked_co = (charity_data.get("company_number") or "").strip()
            if linked_co and CH_API_KEY:
                try:
                    ch_data = fetch_ch_data(linked_co)
                    st.success(f"Linked company **{linked_co}** found.")
                    if not trustees and ch_data:
                        trustees = ch_data.get("officer_names", [])
                except Exception:
                    st.warning(f"Companies House lookup for {linked_co} failed (non-fatal).")
            elif linked_co:
                st.info(f"Linked company {linked_co} exists but CH_API_KEY not set.")
            else:
                # Provide context based on org type
                _org_type = cc_governance.get("organisation_type") or charity_data.get("charity_type", "")
                _ot_info = _ORG_TYPE_INFO.get(_org_type, {})
                if _ot_info.get("ch_required"):
                    st.warning(f"No linked Companies House registration — but organisation type "
                               f"'{_org_type}' typically requires CH registration. Verify.")
                elif _org_type:
                    st.info(f"No linked Companies House registration. "
                            f"({_org_type}: {_ot_info.get('risk_note', 'CH registration not required')})")
                else:
                    st.info("No linked Companies House registration.")

            # Build governance indicators (after ch_data is available)
            gov_indicators = assess_governance_indicators(cc_governance, charity_data, ch_data)

            # ── Structural Governance Analysis ──
            # Fetch trustee directorships from CH (only if CH data available)
            trustee_appointments = {}
            if ch_data and CH_API_KEY:
                try:
                    trustee_appointments = fetch_trustee_appointments(ch_data)
                    _total_appts = sum(len(v) for v in trustee_appointments.values())
                    if _total_appts > 0:
                        st.caption(f"🔗 Fetched {_total_appts} active appointment(s) across {len(trustee_appointments)} officer(s)")
                except Exception:
                    pass  # non-fatal

            structural_governance = assess_structural_governance(
                charity_data, ch_data, trustees, trustee_appointments)
            _sg_flags = structural_governance.get("total_flags", 0)
            if _sg_flags > 0:
                st.caption(f"🏗️ {_sg_flags} structural governance observation(s) noted")

            # ── Step 3: CC Accounts & Trustees' Annual Report ─────────
            st.write("📥 **Step 3/7** — Checking for CC Accounts & TAR...")
            _vision_enabled = st.session_state.get("enable_vision_ocr", False)
            if not _vision_enabled:
                st.caption("⚡ Vision PDF extraction is OFF — using text-only mode (no extra OpenAI cost)")
            cc_pdf_text = ""
            cc_pdf_url = ""
            cc_tar_doc = None          # single dict or None
            cc_all_docs = []           # kept for backward compat (list of 0-1)
            cc_all_docs_text = ""
            cc_tar_status = ""         # human-readable fetch outcome
            _all_pdf_meta = []         # PDF extraction metadata for confidence layer
            _vision_cost_total = 0.0  # Track extra cost from vision-based PDF extraction

            # Conditional trigger: only fetch if uploads don't already
            # include accounts / TAR / financial statements
            _acct_keywords = ["accounts", "annual report", "tar",
                              "trustees", "financial statement"]
            _uploads_contain_accounts = False
            for _uf_check in list(uploaded_files or []) + list(gov_doc_files or []):
                if any(kw in (_uf_check.name or "").lower()
                       for kw in _acct_keywords):
                    _uploads_contain_accounts = True
                    break

            if _uploads_contain_accounts:
                st.info("Accounts / TAR already provided in uploads — "
                        "skipping CC PDF download.")
                cc_tar_status = "Skipped — accounts provided in uploads"
            else:
                try:
                    cc_tar_doc = download_cc_latest_tar(
                        charity_num, company_number=linked_co,
                        organisation_number=charity_data.get("organisation_number"))
                    if cc_tar_doc:
                        yr_label = cc_tar_doc.get("year") or "Unknown Year"
                        title_label = cc_tar_doc.get("title") or "Accounts"
                        cc_pdf_text, _cc_pdf_meta = extract_pdf_text(
                            cc_tar_doc["bytes"], max_pages=25, max_chars=12000)

                        # Vision fallback for CC TAR
                        _vision_enabled = st.session_state.get("enable_vision_ocr", False)
                        if _cc_pdf_meta.get("extraction_quality") in ("low", "none") and openai_client and _vision_enabled:
                            st.write("&nbsp;&nbsp;&nbsp; 👁️ Re-reading CC TAR with AI vision...")
                            _cc_v_text, _cc_v_meta, _cc_v_cost = extract_pdf_with_vision(
                                cc_tar_doc["bytes"], filename="CC_TAR.pdf", max_pages=20)
                            _vision_cost_total += _cc_v_cost
                            if len(_cc_v_text.strip()) > len(cc_pdf_text.strip()):
                                cc_pdf_text = _cc_v_text
                                _cc_pdf_meta = _cc_v_meta
                                st.success(f"   ✅ Vision extracted {len(cc_pdf_text):,} chars "
                                           f"(+${_cc_v_cost:.4f})")
                        elif _cc_pdf_meta.get("extraction_quality") in ("low", "none") and not _vision_enabled:
                            st.caption("⚡ Vision OCR disabled — text extraction may be limited for this filing. "
                                       "Enable *Vision PDF Extraction* in sidebar for better results.")

                        _all_pdf_meta.append(_cc_pdf_meta)
                        cc_pdf_url = cc_tar_doc["url"]
                        cc_all_docs = [cc_tar_doc]
                        cc_all_docs_text = (
                            f"\n=== CHARITY COMMISSION OFFICIAL FILING: "
                            f"{title_label} (Reporting Year: {yr_label}) ===\n"
                            f"Source: Charity Commission Official Filing\n"
                            f"{cc_pdf_text}"
                        )
                        _recv = cc_tar_doc.get("date_received") or ""
                        _ontime = cc_tar_doc.get("on_time")
                        _meta_parts = [f"📄 {title_label} — Reporting Year {yr_label}"]
                        if _recv:
                            _meta_parts.append(f"Received: {_recv}")
                        if _ontime is not None:
                            _meta_parts.append(
                                "On time" if _ontime else "Late submission")
                        st.write("&nbsp;&nbsp;&nbsp; " + " · ".join(_meta_parts))
                        st.success(
                            f"Retrieved Accounts & TAR — "
                            f"{len(cc_pdf_text):,} chars extracted."
                        )
                        cc_tar_status = (
                            f"Retrieved — {title_label} (Reporting Year: {yr_label})")
                    else:
                        st.info(
                            "No downloadable accounts were available via the "
                            "Charity Commission portal for the latest "
                            "reporting year.")
                        cc_tar_status = (
                            "No downloadable accounts available via "
                            "Charity Commission portal")
                except Exception as e:
                    st.info(f"Could not fetch CC TAR: {e}")
                    cc_tar_status = f"Fetch error: {e}"

            # ── Step 4: User-uploaded documents ──────────────────────────
            st.write("📄 **Step 4/7** — Processing uploaded documents...")
            uploaded_text = ""
            _doc_partners = []  # partners extracted via NER
            if uploaded_files:
                for uf in uploaded_files:
                    st.write(f"&nbsp;&nbsp;&nbsp; 📎 Extracting: {uf.name}")
                    _uf_bytes = uf.read()
                    text, _uf_meta = extract_pdf_text(_uf_bytes, max_pages=40, max_chars=20000)

                    # Vision fallback: if text extraction is poor, use GPT-4.1-mini vision
                    _vision_enabled = st.session_state.get("enable_vision_ocr", False)
                    if _uf_meta.get("extraction_quality") in ("low", "none") and openai_client and _vision_enabled:
                        st.write(f"&nbsp;&nbsp;&nbsp; 👁️ Re-reading with AI vision: {uf.name}")
                        _v_text, _v_meta, _v_cost = extract_pdf_with_vision(
                            _uf_bytes, filename=uf.name, max_pages=20,
                            progress_callback=lambda done, total: st.caption(
                                f"   Vision: page {done}/{total}"))
                        _vision_cost_total += _v_cost
                        if len(_v_text.strip()) > len(text.strip()):
                            text = _v_text
                            _uf_meta = _v_meta
                            st.success(f"   ✅ Vision extracted {len(text):,} chars "
                                       f"(+${_v_cost:.4f})")

                    elif _uf_meta.get("extraction_quality") in ("low", "none") and not _vision_enabled:
                        st.caption(f"⚡ {uf.name}: Vision OCR disabled — enable in sidebar for better extraction.")

                    _all_pdf_meta.append(_uf_meta)
                    uploaded_text += f"\n\n=== UPLOADED: {uf.name} ===\n{text}"
                    # Run NER partner extraction on uploaded docs
                    _uf_partners = extract_partners_from_text(
                        text, charity_name=charity_data.get("charity_name", ""))
                    _doc_partners.extend(_uf_partners)
                    if _uf_meta.get("extraction_quality") == "low":
                        st.warning(f"⚠️ {uf.name}: Low text extraction "
                                   f"({_uf_meta.get('chars_extracted', 0):,} chars) — "
                                   f"possible scanned document.")
                    elif _uf_meta.get("extraction_quality") == "none":
                        st.warning(f"⚠️ {uf.name}: No text extracted — "
                                   f"appears to be image-based.")
                st.success(f"Extracted {len(uploaded_text):,} chars from "
                           f"{len(uploaded_files)} document(s).")
                if _doc_partners:
                    st.info(f"🔍 Auto-detected {len(_doc_partners)} potential "
                            f"partner organisation(s) from document text.")
            else:
                st.info("No documents uploaded — using API + web data only.")

            # ── Step 4b: Governance documents (optional) ─────────────────
            gov_doc_pages = []   # list of {"url": "PROVIDED", "snippet": text}
            gov_doc_links = []   # list of {"url": "PROVIDED", "text": filename, …}
            gov_doc_text = ""
            if gov_doc_files:
                st.write("📁 Processing governance documents provided by charity...")
                for gf in gov_doc_files:
                    _gf_bytes = gf.read()
                    gf_text, _gf_meta = extract_pdf_text(_gf_bytes, max_pages=40, max_chars=20000)

                    # Vision fallback for governance docs too
                    _vision_enabled = st.session_state.get("enable_vision_ocr", False)
                    if _gf_meta.get("extraction_quality") in ("low", "none") and openai_client and _vision_enabled:
                        st.write(f"&nbsp;&nbsp;&nbsp; 👁️ Re-reading with AI vision: {gf.name}")
                        _gv_text, _gv_meta, _gv_cost = extract_pdf_with_vision(
                            _gf_bytes, filename=gf.name, max_pages=20,
                            progress_callback=lambda done, total: st.caption(
                                f"   Vision: page {done}/{total}"))
                        _vision_cost_total += _gv_cost
                        if len(_gv_text.strip()) > len(gf_text.strip()):
                            gf_text = _gv_text
                            _gf_meta = _gv_meta
                            st.success(f"   ✅ Vision extracted {len(gf_text):,} chars "
                                       f"(+${_gv_cost:.4f})")

                    elif _gf_meta.get("extraction_quality") in ("low", "none") and not _vision_enabled:
                        st.caption(f"⚡ {gf.name}: Vision OCR disabled — enable in sidebar for better extraction.")

                    _all_pdf_meta.append(_gf_meta)
                    gov_doc_text += f"\n\n=== GOV DOC (PROVIDED): {gf.name} ===\n{gf_text}"
                    gov_doc_pages.append({
                        "url": "PROVIDED",
                        "snippet": gf_text[:4000],
                        "is_hub": False,
                        "source": "Provided by Charity",
                    })
                    gov_doc_links.append({
                        "url": "PROVIDED",
                        "text": gf.name,
                        "source": "Provided by Charity",
                        "is_document": True,
                    })
                    # Also extract partners from governance docs
                    _gf_partners = extract_partners_from_text(
                        gf_text, charity_name=charity_data.get("charity_name", ""))
                    _doc_partners.extend(_gf_partners)
                st.success(f"Processed {len(gov_doc_files)} governance document(s) "
                           f"({len(gov_doc_text):,} chars).")

            # ── Collect manual social media links ────────────────────────
            _manual_social = {}
            for _plat, _val in [
                ("facebook", manual_facebook), ("instagram", manual_instagram),
                ("twitter", manual_twitter), ("linkedin", manual_linkedin),
                ("youtube", manual_youtube), ("other", manual_other_social),
            ]:
                if (_val or "").strip():
                    _manual_social[_plat] = _val.strip()

            # ── Step 5-9: Parallel Web Searches ─────────────────────
            st.write("🔍 **Step 5/7** — Running all web searches in parallel...")

            website_results = []
            online_presence_results = []
            generic_org_results = []
            policy_results = []
            partnership_results = []
            adverse_org = []
            adverse_trustees = {}
            positive_results = []
            social_media_links = {}

            # Context terms for broader adverse searches — extract town/city
            _addr = charity_data.get("address", "")
            _location = ""
            if isinstance(_addr, dict):
                _location = _addr.get("town", "")
            elif isinstance(_addr, str) and _addr:
                # Address is often a comma-separated string; extract likely town
                parts = [p.strip() for p in _addr.split(",") if p.strip()]
                # Town is typically 2nd-to-last or last before postcode
                for part in reversed(parts):
                    if not re.match(r'^[A-Z]{1,2}\d', part.strip()):
                        _location = part.strip()
                        break
            _context = [c for c in [_location, "charity"] if c]

            # Build all search tasks
            search_tasks = {}
            with ThreadPoolExecutor(max_workers=10) as executor:
                # Organisation-level searches
                search_tasks["generic"] = executor.submit(search_generic, entity_name)
                search_tasks["adverse_org"] = executor.submit(
                    search_adverse_media, entity_name, _context)
                search_tasks["positive"] = executor.submit(
                    search_positive_media, entity_name, _location)
                search_tasks["online"] = executor.submit(
                    search_online_presence, entity_name, website)
                search_tasks["policy"] = executor.submit(
                    search_policies, entity_name, website)
                search_tasks["partnerships"] = executor.submit(
                    search_partnerships, entity_name, website)
                if website:
                    search_tasks["website"] = executor.submit(
                        search_website_projects, website, entity_name)

                # Trustee adverse media — all in parallel with context
                for t in trustees:
                    search_tasks[f"trustee:{t}"] = executor.submit(
                        search_adverse_media, t, [entity_name, "charity"])

                # Collect results as they complete
                for future in as_completed(search_tasks.values()):
                    pass  # just wait for all

            # Extract results with graceful degradation — each search
            # failing independently should not crash the entire report.
            _search_failures = []

            def _safe_result(key, default=None):
                """Extract a future's result, returning default on failure."""
                try:
                    return search_tasks[key].result()
                except Exception as e:
                    _search_failures.append(f"{key}: {e}")
                    return default if default is not None else []

            generic_org_results = _safe_result("generic", [])
            adverse_org = _safe_result("adverse_org", [])
            positive_results = _safe_result("positive", [])
            online_presence_results = _safe_result("online", [])

            _policy_result = _safe_result("policy", ([], [], [], [], {}, {
                "safeguarding": {"status": "not_located", "evidence": "", "source_url": "",
                                 "comment": "Policy search unavailable due to technical error",
                                 "status_icon": "ℹ️ Not Located in Public Materials"},
                "financial_crime": {"status": "not_located", "evidence": "", "source_url": "",
                                    "comment": "Policy search unavailable due to technical error",
                                    "status_icon": "ℹ️ Not Located in Public Materials"},
                "risk_management": {"status": "not_located", "evidence": "", "source_url": "",
                                    "comment": "Policy search unavailable due to technical error",
                                    "status_icon": "ℹ️ Not Located in Public Materials"},
                "hrcob_status": "Unable to Assess",
                "hrcob_narrative": "Policy search could not be completed due to a technical error. "
                                   "Manual review of the charity's website and governance documents is recommended.",
            }))
            if isinstance(_policy_result, tuple) and len(_policy_result) == 6:
                policy_results, policy_audit, policy_doc_links, policy_classification, social_media_links, hrcob_core_controls = _policy_result
            else:
                policy_results, policy_audit, policy_doc_links = [], [], []
                policy_classification, social_media_links = [], {}
                hrcob_core_controls = _policy_result if isinstance(_policy_result, dict) else {}

            if _search_failures:
                st.warning(f"⚠️ {len(_search_failures)} search(es) encountered errors "
                           f"and returned partial results: {', '.join(_search_failures)}")

            # ── Merge governance documents into classifiers ──────────────
            if gov_doc_pages or gov_doc_links:
                # Re-classify core controls with provided docs prepended
                # (provided docs take priority — they come first in the lists)
                _merged_pages = gov_doc_pages + [
                    p for p in policy_results
                    if p.get("url")
                ]
                # Also include the original crawled pages used by the classifier
                _all_crawled = []
                for r in policy_results:
                    if r.get("url"):
                        _all_crawled.append({"url": r["url"], "snippet": r.get("content", "")})
                _merged_all_pages = gov_doc_pages + _all_crawled
                _merged_doc_links = gov_doc_links + policy_doc_links
                _merged_search = policy_results

                # Re-run core control classification with merged data
                hrcob_core_controls = _classify_core_controls(
                    _merged_all_pages, _merged_doc_links, _merged_search)

                # Re-run policy classification with merged data
                policy_classification = _classify_policies(
                    _merged_all_pages, _merged_doc_links, _merged_search)

                # Add provided doc links to the policy doc links list
                policy_doc_links = _merged_doc_links

                policy_audit.append({
                    "method": "Governance documents provided by charity",
                    "documents": [gf.name for gf in gov_doc_files],
                    "results_count": len(gov_doc_files),
                })

            # ── Merge manual social media links ──────────────────────────
            if _manual_social:
                for plat, url in _manual_social.items():
                    social_media_links[plat] = url
                social_media_links["_manual_note"] = (
                    "Some social links were provided manually by the analyst "
                    "and take precedence over auto-detected links."
                )
            _partnership_result = _safe_result("partnerships", ([], []))
            if isinstance(_partnership_result, tuple) and len(_partnership_result) == 2:
                partnership_results, partnership_audit = _partnership_result
            else:
                partnership_results, partnership_audit = [], []
            if "website" in search_tasks:
                website_results = _safe_result("website", [])
            for t in trustees:
                adverse_trustees[t] = _safe_result(f"trustee:{t}", [])

            n_searches = 6 + (1 if website else 0) + len(trustees)
            org_verified = count_true_adverse(adverse_org)
            trustee_verified = sum(count_true_adverse(v) for v in adverse_trustees.values())
            trustee_total = sum(len(v) for v in adverse_trustees.values())
            n_found = sum(1 for pc in policy_classification if pc["status"] == "found")
            n_partial = sum(1 for pc in policy_classification if pc["status"] == "partial")
            n_not_located = sum(1 for pc in policy_classification if pc["status"] == "not_located")
            social_found = sum(1 for v in social_media_links.values() if v)
            hrcob_stat = hrcob_core_controls.get("hrcob_status", "Unknown")
            st.success(
                f"Completed **{n_searches} searches** in parallel: "
                f"{org_verified} org adverse (verified), "
                f"{trustee_verified}/{trustee_total} trustee hits (verified/total), "
                f"Policies: {n_found}✅ {n_partial}🔍 {n_not_located}⚠️, "
                f"HRCOB Core: **{hrcob_stat}**, "
                f"{len(policy_doc_links)} doc/hub links, "
                f"Social: {social_found}/5 platforms, "
                f"{len(partnership_results)} partnership, "
                f"{len(online_presence_results)} online presence"
            )

            # ── Step 6: Country Risk (parallel) ─────────────────────────
            st.write("🌍 **Step 6/7** — Cross-referencing country risk...")
            countries = charity_data.get("countries", [])
            country_names = [c.get("country", "") for c in countries if c.get("country")]

            country_risk_classified = []
            high_risk_countries = []
            for c in countries:
                cname = c.get("country", "")
                risk = get_country_risk(cname)
                entry = {"country": cname, "continent": c.get("continent", ""),
                         "risk_level": risk}
                country_risk_classified.append(entry)
                if is_elevated_risk(risk):
                    high_risk_countries.append(cname)

            # Parallel: batch + individual KYC profiles
            country_kyc_profiles = {}
            country_risk_results = []
            with ThreadPoolExecutor(max_workers=8) as executor:
                batch_future = executor.submit(
                    search_country_risk_batch, country_names)
                kyc_futures = {}
                for cname in high_risk_countries[:10]:
                    kyc_futures[cname] = executor.submit(
                        search_country_kyc_profile, cname)
                try:
                    country_risk_results = batch_future.result()
                except Exception:
                    country_risk_results = []
                for cname, fut in kyc_futures.items():
                    try:
                        country_kyc_profiles[cname] = fut.result()
                    except Exception:
                        country_kyc_profiles[cname] = []

            if high_risk_countries:
                st.warning(f"⚠️ {len(high_risk_countries)} elevated-risk countries")
            else:
                st.success("✅ No elevated-risk countries.")

            # ══════════════════════════════════════════════════════════════
            # PHASE 2: AI REPORT GENERATION
            # ══════════════════════════════════════════════════════════════
            selected_label = st.session_state.get(
                "llm_model", list(LLM_PROVIDERS.keys())[0])
            st.write(f"✍️ **Step 7/7** — Drafting report via **{selected_label}**...")

            # Build compact data payload
            doc_context = ""
            _extraction_limited = False
            _extraction_confidence = compute_extraction_confidence(_all_pdf_meta)
            if _extraction_confidence["overall_quality"] in ("low", "none", "mixed"):
                _extraction_limited = True
            if cc_all_docs_text:
                doc_context += ("\n\n" + cc_all_docs_text[:20000])
            elif cc_pdf_text:
                doc_context += ("\n\n=== CHARITY COMMISSION ACCOUNTS PDF ===\n"
                                + cc_pdf_text[:18000])
            if cc_printout_text:
                doc_context += cc_printout_text[:20000]
            if uploaded_text:
                doc_context += ("\n\n=== USER-UPLOADED DOCUMENTS ===\n"
                                + uploaded_text[:20000])
            if gov_doc_text:
                doc_context += ("\n\n=== GOVERNANCE DOCUMENTS (PROVIDED BY CHARITY) ===\n"
                                + gov_doc_text[:20000])

            # Also extract partners from CC TAR text
            if cc_pdf_text and not _doc_partners:
                _tar_partners = extract_partners_from_text(
                    cc_pdf_text, charity_name=charity_data.get("charity_name", ""))
                _doc_partners.extend(_tar_partners)

            all_data = json.dumps(_compact({
                "charity": charity_data,
                "cc_governance_intelligence": {
                    "registration_history": cc_governance.get("registration_history", []),
                    "organisation_type": cc_governance.get("organisation_type") or charity_data.get("charity_type"),
                    "organisation_type_detail": _ORG_TYPE_INFO.get(
                        cc_governance.get("organisation_type") or charity_data.get("charity_type", ""), {}),
                    "other_names": cc_governance.get("other_names", []),
                    "gift_aid_status": cc_governance.get("gift_aid"),
                    "other_regulators": cc_governance.get("other_regulators"),
                    "cc_declared_policies": cc_governance.get("cc_declared_policies", []),
                    "land_property": cc_governance.get("land_property"),
                    "governance_indicators": {
                        "ch_consistency": gov_indicators.get("ch_consistency"),
                        "gift_aid_flag": gov_indicators.get("gift_aid_flag"),
                        "name_change_flag": gov_indicators.get("name_change_flag"),
                        "policy_declared_count": gov_indicators.get("policy_declared_count", 0),
                        "reg_history_flags": gov_indicators.get("reg_history_flags", []),
                        "years_registered": gov_indicators.get("years_registered"),
                    },
                    "governance_url": cc_governance.get("governance_url", ""),
                },
                "cc_printout_data": {
                    "provided": bool(cc_printout_data),
                    "declared_policies": cc_printout_data.get("declared_policies", []),
                    "charitable_objects": cc_printout_data.get("charitable_objects", ""),
                    "trustees_detailed": cc_printout_data.get("trustees_detailed", []),
                    "operating_locations": cc_printout_data.get("where_the_charity_operates", []),
                    "what_it_does": cc_printout_data.get("what_the_charity_does", []),
                    "who_it_helps": cc_printout_data.get("who_the_charity_helps", []),
                    "how_it_helps": cc_printout_data.get("how_the_charity_helps", []),
                    "main_purpose_method": cc_printout_data.get("main_purpose_method", ""),
                    "address": cc_printout_data.get("address", ""),
                    "postcode": cc_printout_data.get("postcode", ""),
                    "phone": cc_printout_data.get("phone", ""),
                    "email": cc_printout_data.get("email", ""),
                    "website": cc_printout_data.get("website", ""),
                    "land_property": cc_printout_data.get("land_property"),
                    "trustee_payments": cc_printout_data.get("trustee_payments"),
                    "trading_subsidiaries": cc_printout_data.get("trading_subsidiaries"),
                    "employees_over_60k": cc_printout_data.get("employees_over_60k"),
                    "filing_history": cc_printout_data.get("filing_history", {}),
                    "financial_breakdown": cc_printout_data.get("financial_breakdown", {}),
                    "organisation_type": cc_printout_data.get("organisation_type", ""),
                    "gift_aid": cc_printout_data.get("gift_aid"),
                    "registration_history": cc_printout_data.get("registration_history", ""),
                    "governing_document_date": cc_printout_data.get("governing_document_date", ""),
                } if cc_printout_data else {"provided": False},
                "structural_governance": {
                    "capacity_flags": structural_governance.get("capacity_flags", []),
                    "trustee_directorships": structural_governance.get("trustee_directorships", {}),
                    "concentration_flags": structural_governance.get("concentration_flags", []),
                    "summary": structural_governance.get("summary", ""),
                    "total_flags": structural_governance.get("total_flags", 0),
                },
                "financial_history": financial_history or [],
                "financial_anomalies": financial_anomalies if financial_anomalies else {},
                "cc_tar_filing": {
                    "title": cc_tar_doc.get("title", "") if cc_tar_doc else "",
                    "year": cc_tar_doc.get("year", "") if cc_tar_doc else "",
                    "url": cc_tar_doc.get("url", "") if cc_tar_doc else "",
                    "date_received": cc_tar_doc.get("date_received", "") if cc_tar_doc else "",
                    "on_time": cc_tar_doc.get("on_time") if cc_tar_doc else None,
                    "source": "Charity Commission Official Filing",
                } if cc_tar_doc else None,
                "cc_tar_fetch_status": cc_tar_status,
                "companies_house": ch_data,
                "web_search": _slim_search(website_results),
                "generic_search": _slim_search(generic_org_results),
                "online_presence": _slim_search(online_presence_results, max_items=5),
                "policies_found": _slim_search(policy_results, max_items=10, max_chars=600),
                "policy_search_audit": policy_audit,
                "policy_doc_links": [{"url": l["url"], "text": l["text"], "source": l["source"],
                                       "is_document": l.get("is_document", False)}
                                      for l in policy_doc_links[:30]],
                "policy_classification": policy_classification,
                "hrcob_core_controls": {
                    "safeguarding": hrcob_core_controls["safeguarding"],
                    "financial_crime": hrcob_core_controls["financial_crime"],
                    "risk_management": hrcob_core_controls["risk_management"],
                    "hrcob_status": hrcob_core_controls["hrcob_status"],
                    "hrcob_narrative": hrcob_core_controls["hrcob_narrative"],
                },
                "partnerships": _slim_search(partnership_results, max_items=5),
                "partnership_search_audit": partnership_audit,
                "adverse_org": _slim_search(adverse_org),
                "adverse_trustees": {
                    k: _slim_search(v, max_items=3, max_chars=300)
                    for k, v in adverse_trustees.items()
                },
                "positive_media": _slim_search(positive_results),
                "social_media_links": social_media_links or {},
                "social_media_note": "Deterministically extracted from charity website HTML. Only verified profile links (not share buttons). null means not found on site.",
                "countries_classified": country_risk_classified,
                "high_risk_country_profiles": {
                    k: _slim_search(v, max_items=3, max_chars=600)
                    for k, v in country_kyc_profiles.items()
                },
                "country_risk_general": _slim_search(
                    country_risk_results, max_items=6, max_chars=500),
                "search_methods_used": [
                    "Charity Commission API (allcharitydetails + charityoverview)",
                    "CC Register governance page scrape (registration history, org type, gift aid, declared policies, land & property)",
                    "Companies House API (company profile + officers)",
                    f"Deep website crawl: {len(_POLICY_PATHS)} policy paths + hub-slug detection + nav/footer scan",
                    "Policy hub processing: document links (PDF/DOCX) extracted + filename/link-text keyword classification",
                    "Policy hub link following: all policy-related internal links from detected hubs crawled",
                    "3x Tavily site-specific keyword group searches for policies",
                    "Tavily broad web search for policy mentions",
                    "Tavily site-specific search for partners on charity website",
                    "Tavily broad web search for partnership mentions",
                    "Multi-strategy adverse media search for organisation (3 queries with de-duplication)",
                    f"Multi-strategy adverse media search for each of {len(trustees)} trustees",
                    "Multi-strategy positive media search (2 queries with location context)",
                    "Deterministic social media link extraction from charity website HTML",
                    "Tavily online presence, reviews & transparency search",
                    "Tavily generic organisation search",
                    "Tavily Know Your Country per-country KYC profiles",
                    f"CC Accounts & TAR: {'Retrieved (Reporting Year ' + cc_tar_doc.get('year', '?') + ')' if cc_tar_doc else 'Not available'}",
                    "PDF text extraction (CC filings + user uploads + governance docs)",
                ] + ([f"CC Register Printout uploaded — {len([k for k in cc_printout_data if not k.startswith('_')])} "
                      f"verified fields extracted (policies, trustees, financials, objects, contact)"]
                     if cc_printout_data else [])
                  + ([f"Governance documents provided by charity ({len(gov_doc_files)} docs)"]
                     if gov_doc_files else [])
                  + ([f"Manual social media links provided ({len(_manual_social)})"]
                     if _manual_social else [])
                  + ([f"Official domain override: {website}"]
                     if _user_domain else []),
                "cc_printout_provided": bool(cc_printout_data),
                "governance_docs_provided": bool(gov_doc_files),
                "manual_social_links_provided": bool(_manual_social),
                "domain_override_used": bool(_user_domain),
                "search_failures": _search_failures if _search_failures else [],
                "extraction_limited": _extraction_limited,
                "extraction_confidence": {
                    "overall_quality": _extraction_confidence["overall_quality"],
                    "total_pages": _extraction_confidence["total_pages"],
                    "total_chars": _extraction_confidence["total_chars"],
                    "ocr_pages": _extraction_confidence["ocr_pages"],
                    "sections_detected": _extraction_confidence["all_sections"],
                    "recommendation": _extraction_confidence["recommendation"],
                },
                "document_partners_extracted": [
                    {"name": p["name"], "context": p["context"],
                     "confidence": p["confidence"]}
                    for p in _doc_partners[:30]
                ] if _doc_partners else [],
                "policy_checklist": POLICY_CHECKLIST,
            }), default=str)

            master_prompt = f"""You are a professional KYC/AML compliance analyst writing a comprehensive HRCOB due-diligence report.

CRITICAL INSTRUCTIONS:
- Be ANALYTICAL, not just descriptive. Interpret data, identify and contextualise gaps, assess risks proportionately.
- Every claim must reference actual DATA provided. Do not fabricate.
- Use markdown: ## headers, **bold**, tables, bullet points, [hyperlinks](url).
- If document extracts are provided, mine them thoroughly for partner info, financial detail, policies.
- Maintain sector-neutral, evidence-based language throughout. Avoid assumptions about charity type, size, or mission.
- WRITE FOR DECISION-MAKERS: Be concise, lead with conclusions, use tables over prose. Aim for clarity over completeness.
- REDUCE TEXT DENSITY: Use bullet points and tables instead of long paragraphs. Keep analyst notes to 2-3 sentences max.
- CONSOLIDATE DATA LIMITATIONS: Do NOT repeat "No policies located" or "Website access restricted" in every section. Instead, state data availability limitations ONCE in a consolidated note at the start of the report, then reference it briefly where relevant (e.g. "See Data Limitations above").

CC REGISTER PRINTOUT — WHEN PROVIDED:
- If "cc_printout_data" is present with "provided": true, the user has uploaded the official Charity Commission register printout PDF.
- This is a PRIMARY VERIFIED SOURCE — data extracted from it (declared policies, charitable objects, trustee appointment dates, financial breakdown, contact details, operating locations) should be treated as authoritative and cited as "(Source: CC Register Printout — uploaded primary document)".
- Declared policies from the printout are what the charity has officially declared to the Charity Commission. Cross-reference these with the web crawl results to assess consistency.
- If the printout lists policies that the web crawl did not find publicly, note: "Declared to CC but not located in public web materials — may be internal documents."
- Use printout financial breakdown data (income by source, expenditure by category) for deeper financial analysis.
- Use trustee appointment dates from the printout to assess trustee tenure and board stability.

- Move the Overall Risk Rating and HRCOB Core Control Status to the VERY TOP of the report as a 1-paragraph executive summary before Section 1. Format:
  **Overall Risk Rating: [LOW/MEDIUM/HIGH/VERY HIGH]** — [1-2 sentence justification]
  **HRCOB Core Controls: [Satisfactory/Acceptable with Clarification/Clarification Recommended/Further Enquiry Recommended]**
  Then proceed with the 9 detailed sections.

EVIDENCE ANCHORING — CRITICAL:
- Every factual claim MUST cite its source immediately after the data point. Use this format:
  - Financial figures: "Income: £X (Source: CC API — allcharitydetails)" or "Income: £X (Source: TAR filing, Year YYYY)"
  - Policy findings: "Safeguarding policy identified (Source: Website crawl — [URL])" or "(Source: Uploaded governance document)"
  - Trustee information: "(Source: CC API — charitytrustees + Companies House officers)"
  - Adverse media: "(Source: Tavily web search, N results screened)"
  - Geographic presence: "(Source: CC API — areasofoperation)"
- Do NOT use phrasing like "AI reviewed and concluded" or "Our analysis suggests" or "The system determined".
- DO use phrasing like "Rule-based classification determined" or "Automated pattern matching identified" or "Cross-referencing CC API data with policy crawl results indicates".
- Present findings as outputs of a structured analytical process, not as opinions or intuitions.
- When a conclusion follows from specific data points, state the logic chain explicitly: "Given [fact A] + [fact B] → [conclusion C]"

ADVERSE MEDIA — IMPORTANT:
- Each adverse media result has a "verified_adverse" field (true/false).
- Only results with "verified_adverse": true are confirmed adverse hits where the content mentions BOTH the entity/person AND adverse terms.
- Results with "verified_adverse": false are FALSE POSITIVES — do NOT count or report them as adverse findings.
- State the exact number of VERIFIED hits. If 0 verified out of N results, say: "No verified adverse media found (N unrelated search results filtered out)."

POLICIES — IMPORTANT (THREE-STATE SYSTEM):
- The data includes "policy_classification" — a pre-computed three-state assessment for EACH policy type.
  Each entry has: policy name, status (found/partial/not_located), source_url, evidence, comment.
- ALSO includes "policies_found" (raw search results), "policy_doc_links" (document & policy links discovered on hubs/nav), and "policy_search_audit".
- We searched: (1) deep website crawl of {len(_POLICY_PATHS)} common policy paths with hub-slug detection, (2) policy hub detection (any page whose URL/title suggests policies AND contains ≥2 policy-relevant links), (3) all document links (PDF/DOCX) extracted from each hub, (4) homepage nav/header/footer scan, (5) keyword classification of every discovered link's filename + link text, (6) 3x Tavily domain-limited keyword searches, (7) Tavily broad web search.

USE THE THREE-STATE CLASSIFICATIONS from "policy_classification":
- ✅ **Found** = A document link (PDF/DOCX) or page whose filename/link text matches the policy type's keywords. Include the source URL and the evidence text.
- 🔍 **Partial** = Website page body text mentions relevant keywords, but no downloadable document or explicit policy page was found. State what was found and note: "standalone policy document not confirmed."
- ⚠️ **Not Located** = No evidence in any crawled page, document link, or web search result. Use CONSERVATIVE wording:
  - NEVER say "No safeguarding policy found" — say: "No safeguarding policy document could be located in public materials scanned (website, policy hub, uploaded documents). The policy may exist internally or under a different title."
  - If a policy hub exists, add: "A policy hub page is present at [URL]; the policy may be available there under a different name."
  - NEVER say "Does not exist" or "No policy found" — always frame as "not located in public materials scanned."
  - For GDPR specifically, distinguish between a privacy notice (common) and a standalone data-processing policy (less common).

DETECTION CONFIDENCE SCORING — IMPORTANT:
Each policy classification and core control result now includes a "detection_confidence" field:
- **high** = Policy keyword found in a document title/filename, OR keyword found in body text within close proximity to a policy anchor term ("policy", "procedure", "framework", "statement", "guidelines", etc.). This is a strong signal that a formal policy exists.
- **medium** = Policy keyword found in body text but NOT near a policy anchor term — the mention may be incidental or contextual rather than indicating a formal policy document. OR a Partial match where the keyword appeared near policy language but no standalone document was confirmed.
- **low** = Only indirect or generic mentions found. The evidence is weak; the policy may exist but was not clearly identified. Manual confirmation recommended.
- **none** = No relevant keywords found at all.

HOW TO USE detection_confidence:
- When confidence is "high": State the finding with assurance — "Safeguarding policy identified."
- When confidence is "medium": Add qualifier — "Safeguarding referenced in a policy context; standalone document not confirmed."
- When confidence is "low": Use cautious language — "Limited evidence of [policy]; automated extraction may have been insufficient. Manual confirmation recommended."
- When confidence is "none" and extraction_confidence quality is also "low" or "mixed": Write "Automated extraction was limited; [policy] could not be assessed. Manual review of provided documents recommended." Do NOT say "No [policy] found" when the documents were unreadable.
- NEVER escalate risk based solely on low-confidence detection. Low confidence means uncertain evidence, not evidence of absence.

HRCOB CORE CONTROLS — CRITICAL (HIGHEST PRIORITY):
The data includes "hrcob_core_controls" — a pre-computed assessment of the three MANDATORY HRCOB control areas:
1. **Safeguarding** — Found if standalone document or procedural detail (DBS, designated lead, abuse reporting); Partial if only high-level mention.
2. **Financial Crime** (Bribery + AML combined) — Found if coverage of BOTH bribery/corruption AND money laundering (in one document or separate), OR if a broader fraud/financial crime framework is present; Partial if only one side present without broader coverage.
3. **Risk Management** — Found if standalone document or structured risk review (risk register, principal risks); Partial if generic mention only.

FINANCIAL CRIME CLASSIFICATION — CONSISTENCY:
- If any of these are present: Anti-Bribery policy, Fraud policy, Anti-Corruption policy, combined financial crime policy — then Financial Crime should be classified as Found (note if AML coverage not independently confirmed).
- Only classify as "Not Located" if NO financial crime indicators exist at all.
- Do NOT mark Financial Crime as "Not Located" in Section 8A while simultaneously showing Anti-Bribery & Corruption as "Found" in Section 8B. This is an inconsistency. If any financial crime-related policy exists, the core control has partial or full coverage.

The overall "hrcob_status" is pre-computed:
- **Satisfactory** = All three core controls Found.
- **Acceptable with Clarification** = One or more Partial but none Not Located.
- **Clarification Recommended** = One core control Not Located.
- **Material Control Concern** / **Further Enquiry Recommended** = Two or more core controls Not Located.

USE the pre-computed hrcob_core_controls data directly. Present it as the FIRST and most prominent part of Section 8.

RISK WEIGHTING — PROPORTIONAL REASONING:
- Core controls are ONE significant input to governance risk — not a deterministic override. Weigh them alongside charity size, operating geography, financial health, adverse media, and years of operation.
- Presence of all three core controls provides strong governance assurance, particularly for smaller charities where formal documented policies may be less common.
- A single missing control should prompt a clarification request, not an automatic HIGH risk rating. Consider whether the charity's scale, complexity, and geographic reach make the absence more or less material.
- Multiple missing controls are a stronger signal but should still be assessed in context — a small UK-only charity with clean financials and no adverse media is different from a large international operation.
- Large scale alone should NOT escalate risk. High-risk geography alone should NOT escalate risk. A single missing public document should NOT escalate risk. Risk should only escalate when MULTIPLE risk indicators align (financial instability + governance gaps + adverse media + weak controls).
- Secondary policy gaps (whistleblowing, GDPR, social media, etc.) should be noted for completeness but should NOT drive the overall HRCOB outcome or inflate governance risk.
- Think like an analyst writing a proportionate assessment, not a rules engine issuing verdicts.

SOURCE ATTRIBUTION — IMPORTANT:
When reporting on policies and controls, always attribute the source clearly:
- If evidence came from a document provided directly by the charity (source contains "Provided by Charity"), write: "Identified in documentation provided directly by the charity."
- If evidence came from the public website, write: "Identified on charity website." or "Identified in publicly available documentation."
- If BOTH provided documents AND public website contain evidence, write: "Identified in both public website and provided documentation."
- If no evidence found anywhere: "No [policy] located in public materials scanned and none provided for review."
- Check the "governance_docs_provided" flag in the data. If true, governance documents were uploaded by the analyst — factor this into your source attribution.
- Check the "manual_social_links_provided" flag. If true, some social media links were verified manually by the analyst.

For the "policy_doc_links" data: these are document links (PDFs, DOCX files) and policy-related page links discovered on policy hubs, navigation menus, and footers. Each has link text (often the document title) and a source label. Use the link text to identify what policy each document represents. Reference these in your commentary.

Also check document extracts for any policy references buried in annual reports or trustees' reports.

HANDLING MISSING INFORMATION — CRITICAL:
When information is NOT found after exhaustive searching, you must follow this exact format:

**⚠️ [Item] — NOT FOUND**
- **What we searched**: [List the specific methods used — e.g. "Direct fetch of 10 policy page URLs on charity website, Tavily site-specific search, Tavily broad web search, uploaded document analysis"]
- **Why this matters**: [1-2 sentences on why this is important for KYC/AML compliance]
- **Recommended next steps**:
  1. Request directly from the charity: "[Specific question to ask the charity, e.g. 'Please provide your current AML/CTF policy document']"
  2. Check [specific alternative source, e.g. "Charity Commission annual return filing for policy declarations"]
  3. [Any other practical step the analyst can take]

Do NOT simply say "NOT FOUND" without this context. The analyst reading this report needs to understand that the system genuinely exhausted all automated avenues and they must now investigate manually.

ONLY mark something as "not found" if there is genuinely NO evidence in ANY of the data provided (search results, document extracts, API data). If there is even partial evidence, note what was found and what gaps remain.

ACCOUNTS & TAR FILING — IMPORTANT:
- The system fetches the MOST RECENT Accounts & Trustees' Annual Report (TAR) from the Charity Commission,
  but ONLY when uploaded documents do not already include accounts.
- Check "cc_tar_filing" in the structured data for the downloaded filing metadata (title, year, URL,
  date_received, on_time) and "cc_tar_fetch_status" for the outcome.
- If a TAR was retrieved, state clearly: "Accounts and Trustees' Annual Report (TAR) retrieved directly
  from Charity Commission filings (Reporting Year: XXXX)."
- If NO TAR was available, state: "No downloadable accounts were available via the Charity Commission
  portal for the latest reporting year." — NEVER say "Accounts missing" or imply governance failure.
- The absence of a downloadable TAR PDF does NOT constitute a governance concern. Many charities file
  accounts that are not immediately available as downloadable PDFs. Write: "Limited financial disclosure
  available via public filings; clarification may be required." — NOT "Material governance concern."
- Source: always attribute as "Charity Commission Official Filing" when referencing TAR content.

DOCUMENT EXTRACTS — IMPORTANT:
- Extract ALL financial figures, partner names, employee counts, programme details from document text.
- If the document mentions specific restricted funds, name each fund and its purpose.
- If the document contains an independent examiner's report, note who examined and their opinion.
- Cross-reference document figures with API summary data.
- Look for governance statements, risk management mentions, internal controls.

EXTRACTION LIMITATIONS — TRANSPARENCY:
- Check "extraction_confidence" in the data for quality assessment:
  - "overall_quality": good | partial | low | none | mixed
  - "total_pages", "total_chars", "ocr_pages" — quantitative metrics
  - "sections_detected" — list of document sections the parser identified (e.g. "governance", "risks", "partners")
  - "recommendation" — pre-computed analyst guidance text
- If quality is "low", "none", or "mixed": state in the report: "Uploaded accounts appear to be image-based or non-text extractable; detailed automated extraction was limited."
- Use the "recommendation" text from extraction_confidence in the Analyst Note for Section 4.
- If quality is "good" or "partial", state what sections were detected: "Document sections detected: [list]."
- Do NOT claim "Detailed analysis conducted" when extraction was limited. Instead write: "Analysis based primarily on structured financial summaries and available extracted content."
- Do NOT escalate risk because extraction was limited.
- Do NOT mark accounts as missing — they were provided but could not be fully machine-read.
- This is a transparency note, not a risk factor.

DOCUMENT-EXTRACTED PARTNERS (NER):
- The data may include "document_partners_extracted" — a list of organisation names automatically detected in uploaded documents using contextual pattern matching.
- Each entry has: name, context (the phrase where it appeared), confidence (high/medium).
- Use these to enrich Section 2 (Partner Organisations). List them alongside any partners found via web search.
- Attribute as: "Identified in uploaded document text" or "Referenced in [document name]."
- Cross-reference with web search partnership results — if the same partner appears in both, note corroboration.
- If search-based partner detection failed (SSL errors, etc.) but document-extracted partners exist, USE the document partners and state: "Partner organisations identified from document analysis (automated search was limited due to technical restrictions)."
- This significantly reduces false "NOT FOUND" results for partnerships.

DATA DISCREPANCY HANDLING:
- If figures extracted from the TAR conflict with the API summary financial data, flag internally
  and state in the report: "Minor discrepancies observed between summary financial data and filed
  accounts; refer to official filing for definitive figures."
- Do NOT escalate risk automatically because of data discrepancies between sources.
- If discrepancies are significant (>10% variance), note them factually and recommend the analyst
  verify against the original filing document.

Write the complete report with these 9 sections:

## 1. Overview — What They Do
- Charity's stated objects, aims, mission statement
- Website link and assessment of web presence quality
- List projects/programs found (from web search AND documents)
- Summarise Trustees' Annual Report if document data available
- **Analyst Note**: Are activities consistent with income level and geography?

## 2. How the Charity Operates
- Donation methods (cash, online, bank transfer, goods-in-kind, crypto?)
- Funding model: donations, grants, government funding, trading — with £ amounts
- **Partner Organisations**: List ALL partners found in documents/search. For each: name, country, relationship type
- **Due diligence on partners**: What checks? Vetting criteria? MoU?
- **Fund oversight**: Who has decision-making power over funds?
- **3rd party KYC**: Does the charity verify partner identities? Sanctions screening?
- Reference partnerships search data + document extracts
- If partnership info not found, use the "NOT FOUND" format above with specific guidance
- IMPORTANT: If the partnership search failed due to technical errors (SSL, timeout, crawl failure), do NOT write "No partner information found" or "Partner Organisations — NOT FOUND". Instead write: "Automated search was limited due to technical access restrictions. Partner frameworks likely exist given the charity's scale and international operations. Direct confirmation is recommended." Check "partnership_search_audit" for error indicators and "search_failures" for technical issues.

### Cross-Border Disbursement & Sanctions Risk Assessment
If the charity operates in or disburses funds to ANY 🔴 Very High Risk or 🟠 High Risk jurisdiction, you MUST include this sub-section:

**Cross-Border Disbursement Risk:**
- How are funds transferred to operating countries? (bank wire, hawala/money service businesses, cash couriers, local agents?)
- What controls exist over fund transfers? (dual authorisation, audit trail, reconciliation?)
- Are local implementing partners used? If so, what due diligence framework applies?
- For EACH high-risk country, describe the disbursement mechanism if identifiable from documents/data.

**Sanctions Exposure:**
- Cross-reference operating countries against: UK HMT sanctions list, UN sanctions, OFAC SDN list, EU restrictive measures.
- For countries under active sanctions regimes (Syria, Yemen, Afghanistan, Myanmar, North Korea, Iran, Somalia, Sudan, South Sudan, Libya, DRC, CAR, Mali, etc.), flag: "Operations in [country] require ongoing sanctions compliance monitoring under [applicable regime]."
- Note whether the charity has stated sanctions screening procedures.
- If no AML/sanctions policy was located (check hrcob_core_controls → financial_crime status), flag this as a compounding risk factor for sanctioned jurisdictions.

**Diversion Risk:**
- For conflict-affected or fragile states (Yemen, Syria, Somalia, Afghanistan, South Sudan, Myanmar, DRC, etc.), assess risk of diversion to non-state armed groups, proscribed organisations, or designated entities.
- Consider: Is there active armed conflict? Are non-state actors controlling territory where the charity operates? Does the charity's operational model (e.g. cash distributions, food aid) carry elevated diversion risk?
- If operating in territory controlled or contested by proscribed organisations, flag: "Operations in [area] carry elevated risk of resource diversion. Direct engagement with the charity to verify monitoring controls is recommended."
- Keep language proportionate — operating in conflict zones is legitimate for humanitarian organisations. Flag the risk, do not assume wrongdoing.

## 3. Where They Operate
Summary table: Country | Continent | Risk Level.
Labels: 🔴 Very High Risk, 🟠 High Risk, 🟡 Medium Risk, 🟢 Low Risk, ⚪ Unclassified (not in internal matrix — analyst should verify against Basel AML Index).

For EACH 🔴/🟠 country, write a **"Know Your Country" profile**:
- **Country Summary**: 2-3 sentence AML risk overview
- **Risk Indicators**: Sanctions | FATF | Terrorism | Corruption | Criminal Markets | EU Tax Blacklist — ✅ or ➖
- **Key Concerns**: bullet points
Cite: [Know Your Country](https://www.knowyourcountry.com/), [Basel AML Index](https://index.baselgovernance.org/).

GEOGRAPHIC RISK CONTEXTUALISATION (for large humanitarian NGOs):
- If the charity has income > £50m, operates in > 10 countries, and has > 5 elevated-risk jurisdictions, add a contextual note: "High-risk geographic exposure is consistent with humanitarian mandate and does not independently indicate elevated governance risk. Risk assessment should focus on control framework rather than geographic presence alone."
- Do NOT reduce risk counts or change numeric classifications.
- Do NOT assume absence of controls because of geography.
- Large humanitarian NGOs (Red Cross, MSF, Oxfam, etc.) operate in high-risk jurisdictions by mandate — this is expected and should be contextualised, not penalised.

## 4. Entity Details & Financial Analysis
- Registration, HQ, years active, charity type
- **CC Governance Intelligence** (NEW — use "cc_governance_intelligence" data):
  - **Organisation Type**: State the type (CIO, Charitable Company, Trust, etc.) and explain what it means for liability, regulation, and transparency. Use "organisation_type_detail" for the description and risk note.
  - **Registration History**: List all registration events with dates. Flag notable events (removals, mergers, re-registrations, conversions). If a charity was removed and re-registered, this warrants scrutiny. State years active since registration.
  - **Gift Aid Status**: State whether recognised by HMRC. If NOT recognised, flag as: "Gift Aid not recognised — verify with charity whether this is administrative or indicative of a compliance issue."
  - **Other Names**: List any trading names, former names, or working names. Multiple name changes may warrant verification.
  - **Companies House Consistency**: Use "governance_indicators.ch_consistency" — state whether the CH link status matches what's expected for the organisation type. If a charitable company lacks CH registration, flag it.
  - **CC Declared Policies**: State the count and list policies declared to the Charity Commission. Cross-reference with the policy_classification findings — note any discrepancies (e.g. charity declares a safeguarding policy to CC but none was found on their website).
  - **Land & Property**: State whether the charity owns/leases land or property. Property ownership is relevant for asset-based risk assessment.
  - **Other Regulators**: State if the charity is regulated by any body other than the Charity Commission.
  - Source: Always attribute as "(Source: CC Register — Governance page)".
- **Trustees Table**: Name | Role | Other Active Directorships (from "structural_governance.trustee_directorships" — show count and key entity names if available; if no CH data, state "N/A — no Companies House link")
- **Financial Table**: Income by source, Expenditure by category (£)
- Surplus/deficit, reserves, year-on-year trends (if documents available)
- If "financial_history" data is present (multi-year income/expenditure), include a **Financial Trend Summary**:
  - State the direction of income over the period (growth / decline / stable)
  - State the direction of expenditure over the period
  - Note whether deficits are structural (recurring) or one-off
  - If income is declining while expenditure is rising, flag this clearly
  - Keep observations factual — do not forecast or predict
- If "financial_anomalies" data is present and has flags, include a **Financial Anomaly Analysis** sub-section:
  - Reproduce each flag verbatim from the "flags" list — do NOT rephrase or escalate language
  - Report income volatility (CV) and expenditure volatility (CV) as percentages
  - If ratio shifts are present, describe them factually (e.g., "Expenditure-to-income ratio shifted from X to Y")
  - Use neutral, analytical language throughout. Phrases like "Significant year-on-year variation observed" are appropriate; phrases like "Alarming" or "Concerning" are NOT
  - Do NOT forecast, speculate, or impute causation — state observed patterns only
  - If anomaly_count is 0, state "No significant financial anomalies were detected across available reporting periods."
- Cost-to-income ratio, fundraising efficiency
- Employees, volunteers, >£60k earners, trading subsidiary
- **Financial Health Indicators** (include this sub-section):
  - **Spend-to-Income %**: total expenditure ÷ total income × 100. Values >100% indicate a deficit year. Benchmark: most charities operate between 85-105%.
  - **Deficit Ratio**: (expenditure − income) ÷ income × 100. Negative values = surplus. Flag if >10% or recurring.
  - **Financial Stress Indicator**: Composite assessment based on deficit ratio, anomaly count, and income trend direction. Rate as Low / Moderate / Elevated / High.
  - **Governance Risk Multiplier**: State whether any of these amplifying factors apply: (a) ≥3 high-risk jurisdictions, (b) AML/financial crime policy not located, (c) verified adverse media hits. If none apply, state "baseline (1.0×)". If factors apply, state the multiplier and contributing factors.
- **Analyst Note**: Financial red flags? Unusual ratios?

### 4B. Structural Governance Observations (use "structural_governance" data)
If "structural_governance" data is present and has total_flags > 0, include this sub-section:

**Oversight Capacity**:
- If "capacity_flags" are present, reproduce each observation verbatim. These flag situations where income is high relative to management capacity (e.g., income above £1m with ≤3 trustees or no employees).
- Frame as neutral observations: "The analyst may wish to verify..." — NOT as compliance failures.
- Do NOT escalate language beyond what the flags state.

**Trustee Directorships**:
- If "trustee_directorships" data is present, include a table:

| Trustee / Director | Other Active Appointments | Notable Entities |
|--------------------|--------------------------|------------------|

- For each trustee with 3+ other active directorships, note: "Multiple directorships are noted for time-capacity consideration."
- Do NOT imply conflict of interest, misconduct, or wrongdoing. Simply note the factual observation.
- If trustees share directorships at the same external entity (see "concentration_flags"), note this factually: "X and Y are both directors of Z — shared external relationships noted for context."

**Governance Concentration**:
- If "concentration_flags" are present, reproduce each observation. These highlight patterns such as overlapping appointments between trustees.
- State clearly: "These observations are structural in nature and do not indicate misconduct."

If structural_governance.total_flags is 0, state: "No structural governance anomalies detected."

## 5. Online Presence & Digital Footprint
- Website quality, transparency (published accounts/policies?)
- **Social Media Verification Table** (from "social_media_links" data — these were deterministically extracted from the charity's website HTML, NOT guessed):

| Platform | Verified URL | Source |
|----------|-------------|--------|

  For each platform in social_media_links: show the URL as a clickable link and note "Extracted from charity website".
  For major platforms (Facebook, Twitter/X, LinkedIn, Instagram, YouTube) NOT found in the data: state "No official [platform] link detected on the charity's public website." Do NOT guess URLs or follower counts.
  If social_media_links is empty/null: state "No social media profile links were detected on the charity's public website."
- Charity review sites, Fundraising Regulator registration
- **Analyst Note**: Online presence consistent with stated size? Social media gaps for a charity of this scale?

## 6. Adverse Media Search
### Organisation
State search query used. Report ONLY verified hits. If 0 verified, state clearly.
### Trustee-by-Trustee
For EACH trustee: verified hit count. If clear, state "No verified adverse media."
### **Analyst Note**: Overall adverse media risk: Low / Medium / High.

## 7. Positive Media & Partnerships
- Awards, press, public recognition
- Government grants/contracts
- Reputable partnerships (UN, DFID, WHO, etc.)
- **Analyst Note**: Do positives offset negatives?

## 8. Policies & Compliance Framework

### 8A. HRCOB Core Controls (Mandatory Assessment)

This is the MOST IMPORTANT governance section. Present the pre-computed "hrcob_core_controls" data FIRST.

Present this table prominently:

| Core Control | Status | Evidence |
|-------------|--------|----------|
| Safeguarding | [status_icon from hrcob_core_controls.safeguarding] | [evidence + comment] |
| Financial Crime (Bribery + AML) | [status_icon from hrcob_core_controls.financial_crime] | [evidence + comment] |
| Risk Management | [status_icon from hrcob_core_controls.risk_management] | [evidence + comment] |

**HRCOB Core Control Status: [hrcob_status]**

Use the pre-computed "hrcob_narrative" text. Then add analyst commentary:
- If **Satisfactory**: State the narrative, then note: "All three HRCOB core control areas are documented in publicly available materials. The governance framework appears structured and proportionate to the charity's size and operations."
- If **Acceptable with Clarification**: State the narrative, identify which control(s) are partial, what exactly was found vs. missing, and recommend specific clarification questions. Frame this as advisory: "Clarification would strengthen assurance" — not as a compliance failure.
- If **Clarification Recommended**: State the narrative, identify which control was not located, explain what was searched, and provide specific next steps. Frame proportionally: "Requesting documentation directly from the charity is recommended. This finding should be considered alongside other risk factors."
- If **Further Enquiry Recommended** (or "Material Control Concern"): State the narrative, identify which controls were not located, and recommend direct engagement. Note: "The absence of multiple core control documents in public materials warrants further enquiry, though the policies may exist internally. This reflects a gap in publicly available evidence and should not be interpreted as a confirmed governance failure. The analyst should weigh this alongside the charity's size, geography, and overall risk profile."

For Financial Crime specifically:
- If both bribery AND money laundering are covered (in one or separate documents) → state "Combined financial crime coverage confirmed"
- If only one side is present → state which side was found and which is missing
- Accept combined "Anti-Corruption, Bribery & Money Laundering" policies as fully satisfying this control

### 8B. Secondary Policies (Contextual)

The following policies are secondary and should NOT drive the overall HRCOB outcome. They provide context but are not mandatory for compliance determination.

Use the pre-computed "policy_classification" data for the FULL policy table.
Also cross-check against "policies_found" search results, "policy_doc_links", and document extracts.

### Full Policy Discovery Table
Present EVERY policy from the checklist in this table:

| Policy | Status | Evidence | Source / URL |
|--------|--------|----------|--------------|

Populate Evidence with the best short description: document title and where it was found (policy hub, footer, resources page).
If multiple docs match (e.g. separate staff vs. volunteer safeguarding policies), list the strongest or summarise: "multiple safeguarding policies detected".

Status values (use from policy_classification):
- ✅ **Found** — A document (PDF/DOCX) or dedicated page matched. Include clickable URL and the evidence text.
- 🔍 **Partial** — Relevant keywords mentioned on website text, but no downloadable document link found. Explain what was seen and note: "Standalone policy document not confirmed."
- ⚠️ **Not Located** — Not located in public materials scanned. Use CONSERVATIVE wording:
  - Say: "No [policy] document could be located in public materials scanned (website, policy hub, uploaded documents). The policy may exist internally or under a different title."
  - If a policy hub page exists, add: "A policy hub page is present at [URL]; the policy may be available there under a different name."
  - NEVER say "Does not exist" or "No policy found" — always frame as "not located in public materials scanned."

Note: Secondary policy gaps (whistleblowing, GDPR, social media, etc.) should be noted for completeness but explicitly stated as NOT affecting the HRCOB core compliance determination.

### Policy Hub Summary
If policy hub pages were detected, list them here:
- Hub URL, number of policy-related document links discovered
- List titles of documents/links found on the hub (from "policy_doc_links" where is_document=true)
- Note any PDF/DOCX links that couldn't be parsed automatically
- If no policy hub was detected, state: "No dedicated policy hub page was identified on the charity's website."

**Analyst Note**: Focus on core controls first. Are the three HRCOB core controls proportionate to the charity's risk profile and operational geography?

## 9. Risk Assessment & Mitigants
### Risks Identified
Present as a summary risk table at the start of this section:

| Risk Category | Rating | Key Driver |
|--------------|--------|------------|
| Geographic Risk | [LOW/MEDIUM/HIGH] | [1-line reason] |
| Financial Risk | [LOW/MEDIUM/HIGH] | [1-line reason] |
| Governance Risk | [LOW/MEDIUM/HIGH] | [1-line reason] |
| Media Risk | [LOW/MEDIUM/HIGH] | [1-line reason] |
| Sanctions & Disbursement Risk | [LOW/MEDIUM/HIGH/N/A] | [1-line reason] |
| Operational Risk | [LOW/MEDIUM/HIGH] | [1-line reason] |

Then provide narrative for EACH risk category below the table.
If any data source was unavailable (search failure, API error, crawl timeout), note it here as: "[Data source] was unavailable — unable to assess [risk area]. This should not be interpreted as a risk indicator."

SANCTIONS & DISBURSEMENT RISK:
- Rate as N/A if the charity operates only in low/medium-risk jurisdictions with no sanctions exposure.
- Rate as LOW if charity operates in sanctioned jurisdictions but has documented AML/sanctions policies and due diligence frameworks.
- Rate as MEDIUM if charity operates in sanctioned jurisdictions with partial or unclear AML controls.
- Rate as HIGH if charity operates in multiple sanctioned jurisdictions with no located AML/financial crime policy and no evidence of sanctions screening.
- Always consider whether the charity's sector (humanitarian, development, peacebuilding) makes sanctioned-jurisdiction operations expected and legitimate.

GOVERNANCE RISK — proportional contextual assessment:
- Governance risk should emerge from a holistic view of controls, charity size, operational complexity, geographic exposure, financial health, and trustee track record — not from a single missing document.
- All three core controls Found with clean financials and no adverse media → Governance Risk is typically LOW.
- Partial controls or a single missing control → consider the charity's scale and complexity. For a small, UK-only charity this may still be LOW-MEDIUM. For a large international operation it may warrant MEDIUM.
- Multiple missing controls combined with other risk factors (elevated geography, financial concerns, adverse media) → Governance Risk may be MEDIUM-HIGH depending on cumulative picture.
- A single missing control should NOT automatically produce HIGH governance risk. HIGH should reflect a convergence of multiple concerning factors.
- Missing secondary policies (whistleblowing, GDPR, social media, etc.) should NOT by themselves push Governance Risk above LOW. Note them as minor observations only.

TAR / ACCOUNTS AVAILABILITY — RISK NON-ESCALATION:
- The presence or absence of a downloadable Accounts & TAR PDF must NOT directly affect the risk rating.
- A successfully retrieved TAR improves evidence quality only — it does not lower risk by itself.
- If the TAR was not available (see "cc_tar_fetch_status"), write: "Limited financial disclosure available via public filings; clarification may be required." Do NOT write "Material governance concern" or "Accounts missing."
- The absence of a downloadable PDF ≠ governance failure. Many charities file accounts that are processed but not yet available as downloadable PDFs, or the filing may be under review.

SEARCH FAILURES — NON-ESCALATION:
- If any search component failed (see "_search_failures" if present in data), the missing data must NOT inflate the risk assessment.
- State clearly: "[Component] data was unavailable due to a technical issue. This gap does not indicate risk."
- A search failure or crawl timeout is a technical limitation, not a governance concern.

INTERNAL CONSISTENCY CHECK:
Before finalising the report, review your own output for internal consistency:
- Does the risk rating in Section 9 align with the narrative in Sections 1-8?
- Does the governance commentary match the detected core control statuses?
- Does the media section accurately reflect the verified adverse hit count?
- Is the tone proportionate throughout — no section more alarmist than the evidence warrants?
- If a control was "Not Located" but the TAR or other documents contain governance statements, acknowledge this.
- If the overall risk is LOW but a sub-section uses alarming language, soften the sub-section to be consistent.
If you detect an inconsistency in your own output, adjust the wording to ensure coherence.

### Mitigating Factors
Specific mitigants with evidence references.
### Overall Risk Rating
**LOW / MEDIUM / HIGH / VERY HIGH** — 2-3 sentence justification.
The overall risk should be a contextual synthesis of ALL factors: geography, financials, governance (including core controls), adverse media, partnerships, and charity maturity. No single factor should mechanically determine the rating. Reference the HRCOB Core Control Status as one input among several. A charity with strong controls but elevated geography is different from one with weak controls and elevated geography.

### HRCOB Core Control Assessment
**HRCOB Core Control Status: [Satisfactory / Acceptable with Clarification / Clarification Recommended / Further Enquiry Recommended]**
Restate the hrcob_narrative. This assessment is analytical and advisory — it informs the analyst's judgement but does not mechanically determine the overall risk rating.

### Recommended Actions for Analyst
Numbered list of specific next steps the human analyst should take, prioritised by risk level.

--- STRUCTURED DATA ---
{all_data}

--- DOCUMENT EXTRACTS ---
{doc_context if doc_context else "[No documents available — report based on API + web data only]"}
"""

            full_report, cost_info = llm_generate(master_prompt)

            # Add vision extraction cost to total
            if _vision_cost_total > 0:
                cost_info["cost_usd"] = cost_info.get("cost_usd", 0) + _vision_cost_total
                cost_info["vision_cost_usd"] = _vision_cost_total

            status.update(label="✅ Report Complete!", state="complete")

            # ── Persist display data to session state ────────────────
            # This ensures interactive widgets (filters, tabs) don't
            # blank the page on Streamlit reruns.
            st.session_state["_display"] = {
                "charity_data": charity_data,
                "entity_name": entity_name,
                "charity_num": charity_num,
                "trustees": trustees,
                "financial_history": financial_history,
                "financial_anomalies": financial_anomalies,
                "ch_data": ch_data,
                "linked_co": linked_co,
                "cc_pdf_text": cc_pdf_text,
                "cc_tar_doc": cc_tar_doc,
                "cc_all_docs_text": cc_all_docs_text,
                "uploaded_text": uploaded_text,
                "_extraction_confidence": _extraction_confidence,
                "countries": countries,
                "high_risk_countries": high_risk_countries,
                "country_risk_classified": country_risk_classified,
                "adverse_org": adverse_org,
                "adverse_trustees": adverse_trustees,
                "social_media_links": social_media_links,
                "_manual_social": _manual_social,
                "policy_classification": policy_classification,
                "policy_doc_links": policy_doc_links,
                "hrcob_core_controls": hrcob_core_controls,
                "full_report": full_report,
                "cost_info": cost_info,
                "uploaded_files_count": len(uploaded_files or []),
                "gov_doc_files_count": len(gov_doc_files or []),
                "vision_mode": st.session_state.get("enable_vision_ocr", False),
                "vision_cost_usd": _vision_cost_total,
                "cc_governance": cc_governance,
                "gov_indicators": gov_indicators,
                "structural_governance": structural_governance,
                "trustee_appointments": trustee_appointments,
                "cc_printout_data": cc_printout_data,
            }

        except SSLError as err:
            status.update(label="Failed", state="error")
            st.error("SSL handshake failed. This typically indicates a corporate proxy or certificate issue.")
            st.info("Set CORP_CA_BUNDLE or ALLOW_INSECURE_SSL=true in .env.")
            st.stop()
        except RequestException as err:
            status.update(label="Failed", state="error")
            st.error("A network or API request failed during report generation. Please check your connection and retry.")
            st.stop()
        except Exception as err:
            status.update(label="Failed", state="error")
            st.error("An unexpected error occurred during report generation. Please check your configuration and retry.")
            import traceback
            with st.expander("Technical details (for debugging)", expanded=False):
                st.code(traceback.format_exc())
            st.stop()

# ══════════════════════════════════════════════════════════════════════
# PHASE 3: RENDER THE REPORT
# Loads display data from session state so interactive widgets
# (filters, sorting, tab switches) don't blank the page on reruns.
# ══════════════════════════════════════════════════════════════════════
_dp = st.session_state.get("_display")
if _dp and not _is_donor_mode:
    # ── Unpack all display variables from session state ───────────────
    charity_data = _dp["charity_data"]
    entity_name = _dp["entity_name"]
    charity_num = _dp["charity_num"]
    trustees = _dp["trustees"]
    financial_history = _dp["financial_history"]
    financial_anomalies = _dp.get("financial_anomalies", {})
    ch_data = _dp["ch_data"]
    linked_co = _dp["linked_co"]
    cc_pdf_text = _dp["cc_pdf_text"]
    cc_tar_doc = _dp["cc_tar_doc"]
    cc_all_docs_text = _dp["cc_all_docs_text"]
    uploaded_text = _dp["uploaded_text"]
    _extraction_confidence = _dp["_extraction_confidence"]
    countries = _dp["countries"]
    high_risk_countries = _dp["high_risk_countries"]
    country_risk_classified = _dp["country_risk_classified"]
    adverse_org = _dp["adverse_org"]
    adverse_trustees = _dp["adverse_trustees"]
    social_media_links = _dp["social_media_links"]
    _manual_social = _dp["_manual_social"]
    policy_classification = _dp["policy_classification"]
    policy_doc_links = _dp["policy_doc_links"]
    hrcob_core_controls = _dp["hrcob_core_controls"]
    full_report = _dp["full_report"]
    cost_info = _dp["cost_info"]
    _uploaded_files_count = _dp.get("uploaded_files_count", 0)
    _gov_doc_files_count = _dp.get("gov_doc_files_count", 0)
    _vision_mode = _dp.get("vision_mode", False)
    _vision_cost = _dp.get("vision_cost_usd", 0.0)
    cc_governance = _dp.get("cc_governance", {})
    gov_indicators = _dp.get("gov_indicators", {})
    structural_governance = _dp.get("structural_governance", {})
    trustee_appointments = _dp.get("trustee_appointments", {})

    st.markdown("---")

    # ── Report Header Banner ─────────────────────────────────────────────
    cost_usd = cost_info.get("cost_usd", 0)
    cost_display = f"${cost_usd:.4f}" if cost_usd > 0 else "Free"
    actual_model = cost_info.get("model", "unknown")
    _report_date = datetime.now().strftime('%d %B %Y')
    _reg_status = charity_data.get('reg_status', 'Unknown')

    # ── Re-run Consistency Hash ──────────────────────────────────────
    # Deterministic hash of input data (NOT LLM output) — same inputs → same hash
    _hash_payload = json.dumps({
        "charity_num": charity_num,
        "date": _report_date,
        "income": charity_data.get("latest_income"),
        "expenditure": charity_data.get("latest_expenditure"),
        "trustees_count": len(trustees),
        "countries_count": len(countries),
        "high_risk_count": len(high_risk_countries),
        "hrcob_status": hrcob_core_controls.get("hrcob_status"),
        "docs_count": (1 if cc_tar_doc else 0) + _uploaded_files_count + _gov_doc_files_count,
        "model": actual_model,
    }, sort_keys=True)
    _data_hash = hashlib.sha256(_hash_payload.encode()).hexdigest()[:8].upper()
    _hash_display = f"{_data_hash[:4]}-CC-{charity_num}-{datetime.now().strftime('%Y-%m-%d')}"
    _vision_badge = (
        '👁️ Vision ON' if _vision_mode
        else '⚡ Vision OFF (text-only)'
    )

    st.markdown(f"""<div class="report-banner">
<h1>🛡️ Know Your Charity UK — Due-Diligence Report</h1>
<div class="subtitle">{entity_name}</div>
<div class="meta">
    Charity No: <strong>{charity_num}</strong> &nbsp;·&nbsp;
    Report Date: <strong>{_report_date}</strong> &nbsp;·&nbsp;
    Status: <strong>{_reg_status}</strong> &nbsp;·&nbsp;
    Model: <strong>{actual_model}</strong> &nbsp;·&nbsp;
    Cost: <strong>{cost_display}</strong> &nbsp;·&nbsp;
    PDF Mode: <strong>{_vision_badge}</strong> &nbsp;·&nbsp;
    Data Hash: <strong><span class="data-hash" title="Reproducibility hash — same inputs produce the same hash">{_hash_display}</span></strong>
</div>
</div>""", unsafe_allow_html=True)

    # ── Executive Summary & Visual Risk Dashboard ─────────────────────
    # Extract overall risk rating from LLM report
    _risk_match = re.search(
        r'(?:Overall\s+Risk\s+Rating)[:\s*#]*\*{0,2}\s*'
        r'(LOW|MEDIUM|HIGH|VERY\s*HIGH|MEDIUM[\s-]*HIGH|MEDIUM[\s-]*LOW)',
        full_report,
        re.IGNORECASE,
    )
    _overall_risk = _risk_match.group(1).strip().upper().replace("  ", " ") if _risk_match else "UNRATED"

    # Compute sub-risk RAG levels
    inc = charity_data.get("latest_income") or 0
    exp = charity_data.get("latest_expenditure") or 0
    _n_hr = len(high_risk_countries)
    _total_adverse = sum(count_true_adverse(v) for v in adverse_trustees.values()) + count_true_adverse(adverse_org)
    _hrcob_stat = hrcob_core_controls.get("hrcob_status", "Unknown")
    _anomaly_ct = financial_anomalies.get("anomaly_count", 0) if financial_anomalies else 0

    # Geography RAG
    if _n_hr == 0:
        _geo_rag, _geo_label = "green", "LOW"
    elif _n_hr <= 2:
        _geo_rag, _geo_label = "amber", "MEDIUM"
    else:
        _geo_rag, _geo_label = "red", "HIGH"

    # Financial RAG
    _deficit_ratio = ((exp - inc) / inc * 100) if inc > 0 else 0
    if _anomaly_ct == 0 and _deficit_ratio < 5:
        _fin_rag, _fin_label = "green", "LOW"
    elif _anomaly_ct <= 2 and _deficit_ratio < 20:
        _fin_rag, _fin_label = "amber", "MEDIUM"
    else:
        _fin_rag, _fin_label = "red", "HIGH"

    # Governance RAG
    if _hrcob_stat == "Satisfactory":
        _gov_rag, _gov_label = "green", "LOW"
    elif _hrcob_stat in ("Acceptable with Clarification", "Clarification Recommended"):
        _gov_rag, _gov_label = "amber", "MEDIUM"
    else:
        _gov_rag, _gov_label = "red", "HIGH"

    # Media RAG
    if _total_adverse == 0:
        _med_rag, _med_label = "green", "LOW"
    elif _total_adverse <= 2:
        _med_rag, _med_label = "amber", "MEDIUM"
    else:
        _med_rag, _med_label = "red", "HIGH"

    # Operational RAG (composite: docs quality + controls)
    _eq = _extraction_confidence.get("overall_quality", "n/a") if _extraction_confidence else "n/a"
    _core_statuses = [
        hrcob_core_controls.get("safeguarding", {}).get("status", "not_located"),
        hrcob_core_controls.get("financial_crime", {}).get("status", "not_located"),
        hrcob_core_controls.get("risk_management", {}).get("status", "not_located"),
    ]
    _n_missing = _core_statuses.count("not_located")
    if _n_missing == 0 and _eq in ("good", "partial"):
        _ops_rag, _ops_label = "green", "LOW"
    elif _n_missing <= 1:
        _ops_rag, _ops_label = "amber", "MEDIUM"
    else:
        _ops_rag, _ops_label = "red", "HIGH"

    # Overall risk badge class
    _risk_upper = _overall_risk.replace("-", " ").replace("  ", " ")
    if "LOW" in _risk_upper and "MEDIUM" not in _risk_upper:
        _risk_css = "risk-badge-low"
    elif "HIGH" in _risk_upper:
        _risk_css = "risk-badge-high"
    else:
        _risk_css = "risk-badge-medium"

    # Build RAG tiles HTML
    _rag_tiles = ""
    for _rlabel, _rrag, _rval in [
        ("Geography", _geo_rag, _geo_label),
        ("Financial", _fin_rag, _fin_label),
        ("Governance", _gov_rag, _gov_label),
        ("Media", _med_rag, _med_label),
        ("Operational", _ops_rag, _ops_label),
    ]:
        _rag_tiles += (
            f'<div class="rag-tile rag-{_rrag}">'
            f'<div class="rag-label">{_rlabel}</div>'
            f'<div class="rag-value">{_rval}</div></div>'
        )

    # Key findings summary bullets
    _findings = []
    _hrcob_icon = {"Satisfactory": "✅", "Acceptable with Clarification": "🔍",
                   "Clarification Recommended": "⚠️"}.get(_hrcob_stat, "🔴")
    _findings.append(f"{_hrcob_icon} <strong>HRCOB Core Controls:</strong> {_hrcob_stat}")
    if _n_hr > 0:
        _hr_names = ", ".join(high_risk_countries[:5])
        _findings.append(f"🌍 <strong>{_n_hr} high-risk jurisdiction{'s' if _n_hr != 1 else ''}:</strong> {_hr_names}")
    else:
        _findings.append("🌍 <strong>Geography:</strong> No high-risk jurisdictions identified")
    if _total_adverse > 0:
        _findings.append(f"🔍 <strong>{_total_adverse} verified adverse media hit{'s' if _total_adverse != 1 else ''}</strong> — review required")
    else:
        _findings.append("✅ <strong>Adverse Media:</strong> No verified hits")
    if inc > 0:
        _spend_pct = exp / inc * 100
        _surplus = inc - exp
        _findings.append(
            f"💰 <strong>Financial:</strong> Income {fmt_money(inc)}, "
            f"Spend-to-Income {_spend_pct:.0f}%, "
            f"{'Surplus' if _surplus >= 0 else 'Deficit'} {fmt_money(abs(_surplus))}"
        )
    if _anomaly_ct > 0:
        _findings.append(f"📊 <strong>{_anomaly_ct} financial anomal{'ies' if _anomaly_ct != 1 else 'y'}</strong> flagged")

    _findings_html = "".join(f"<li>{f}</li>" for f in _findings)

    st.markdown(f"""<div class="exec-summary">
<h3>📋 Executive Summary</h3>
<p style="margin-bottom:8px;">
    <span class="risk-badge {_risk_css}">OVERALL RISK: {_overall_risk}</span>
</p>
<div class="rag-grid">{_rag_tiles}</div>
<ul style="margin:0.5rem 0 0 0; padding-left:1.4rem; line-height:1.8;">{_findings_html}</ul>
</div>""", unsafe_allow_html=True)

    # ── Confidence Meter ──────────────────────────────────────────────
    _conf_score = 100
    _conf_reductions = []
    # Deduct for data quality gaps
    if _eq in ("low", "none"):
        _conf_score -= 20
        _conf_reductions.append("−20% document extraction limited/failed")
    elif _eq == "mixed":
        _conf_score -= 10
        _conf_reductions.append("−10% mixed document extraction quality")
    if not cc_tar_doc and not cc_pdf_text:
        _conf_score -= 15
        _conf_reductions.append("−15% no CC accounts PDF available")
    if _uploaded_files_count == 0 and _gov_doc_files_count == 0:
        _conf_score -= 10
        _conf_reductions.append("−10% no uploaded governance documents")
    # Deduct for missing controls evidence
    if _n_missing >= 2:
        _conf_score -= 15
        _conf_reductions.append(f"−15% {_n_missing} core controls not located")
    elif _n_missing == 1:
        _conf_score -= 8
        _conf_reductions.append("−8% 1 core control not located")
    # Deduct for search/access failures
    _search_fail_count = len(_dp.get("charity_data", {}).get("search_failures", []))
    if _search_fail_count > 0:
        _conf_score -= min(10, _search_fail_count * 5)
        _conf_reductions.append(f"−{min(10, _search_fail_count * 5)}% data source access failures")
    # Deduct if no website could be crawled
    if not social_media_links and not policy_doc_links:
        _conf_score -= 8
        _conf_reductions.append("−8% website crawl yielded no links")
    _conf_score = max(10, _conf_score)

    if _conf_score >= 75:
        _conf_css = "conf-bar-high"
    elif _conf_score >= 50:
        _conf_css = "conf-bar-med"
    else:
        _conf_css = "conf-bar-low"

    _conf_factors_html = ""
    if _conf_reductions:
        _conf_factors_html = (
            '<div class="conf-factors">Reduced by: '
            + " · ".join(_conf_reductions)
            + "</div>"
        )

    st.markdown(f"""<div class="conf-meter">
<div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:3px;">
    <span style="font-size:0.78rem; font-weight:600;">Assessment Confidence</span>
    <span style="font-size:0.85rem; font-weight:700;">{_conf_score}%</span>
</div>
<div class="conf-bar-bg">
    <div class="conf-bar-fill {_conf_css}" style="width:{_conf_score}%;">{_conf_score}%</div>
</div>
{_conf_factors_html}
</div>""", unsafe_allow_html=True)

    # ── Variance Simulation (What-If Scenarios) ──────────────────────
    _scenarios = []
    # Scenario 1: If missing core controls were verified
    if _n_missing > 0:
        _missing_names = [
            k.replace("_", " ").title()
            for k in ("safeguarding", "financial_crime", "risk_management")
            if hrcob_core_controls.get(k, {}).get("status") == "not_located"
        ]
        # What would governance RAG become?
        _sim_gov = "LOW" if (_n_missing == _n_missing) else _gov_label  # all fixed → all found
        _sim_risk = "LOW-MEDIUM" if _overall_risk in ("MEDIUM", "HIGH", "MEDIUM-HIGH") else _overall_risk
        _scenarios.append(
            f"If <strong>{', '.join(_missing_names)}</strong> "
            f"{'policy was' if len(_missing_names) == 1 else 'policies were'} "
            f"verified → Governance risk reduces to <strong>LOW</strong>, "
            f"overall risk may reduce to <strong>{_sim_risk}</strong>"
        )
    # Scenario 2: If adverse media were cleared
    if _total_adverse > 0:
        _scenarios.append(
            f"If <strong>{_total_adverse} adverse media hit{'s' if _total_adverse != 1 else ''}</strong> "
            f"{'are' if _total_adverse != 1 else 'is'} investigated and cleared → "
            f"Media risk reduces to <strong>LOW</strong>"
        )
    # Scenario 3: If governance docs were uploaded
    if _uploaded_files_count == 0 and _gov_doc_files_count == 0:
        _conf_gain = 10 + (_n_missing * 8)
        _scenarios.append(
            f"If charity provides governance documents → Confidence increases by "
            f"<strong>~{_conf_gain}%</strong> (currently {_conf_score}%)"
        )

    if _scenarios:
        _scen_html = "".join(f"<div>🔮 {s}</div>" for s in _scenarios)
        st.markdown(
            f'<div class="variance-box">'
            f'<div class="var-header">📐 Scenario Modelling</div>'
            f'{_scen_html}</div>',
            unsafe_allow_html=True,
        )

    # ── Risk Processing Flow Diagram ─────────────────────────────────
    _flow_steps = [
        ("🗄️", "Data Sources", f"{3 + _total_docs} sources"),
        ("⚙️", "Extraction", f"Vision + API"),
        ("🏷️", "Classification", f"{len(policy_classification)} policies"),
        ("📏", "Rule Engine", f"{3} core controls"),
        ("⚖️", "Risk Weighting", "5 dimensions"),
        ("📄", "Report", actual_model),
    ]
    _flow_html = ""
    for i, (icon, label, sub) in enumerate(_flow_steps):
        if i > 0:
            _flow_html += '<span class="flow-arrow">→</span>'
        _flow_html += (
            f'<div class="flow-step">'
            f'<span class="flow-icon">{icon}</span>{label}'
            f'<span class="flow-sub">{sub}</span></div>'
        )
    st.markdown(
        f'<div class="flow-diagram">{_flow_html}</div>',
        unsafe_allow_html=True,
    )

    # ── Data Limitations (consolidated) ───────────────────────────────
    _limitations = []
    if _eq in ("low", "none", "mixed"):
        _limitations.append("Document extraction was limited (image-based or non-text PDF) — some policy and financial details may be incomplete.")
    if not cc_tar_doc and not cc_pdf_text:
        _limitations.append("No downloadable accounts were available via the Charity Commission portal — financial analysis relies on API summary data.")
    _n_not_located = sum(1 for p in policy_classification.values()
                        if isinstance(p, dict) and p.get("status") == "not_located")
    if _n_not_located > 0:
        _limitations.append(f"{_n_not_located} polic{'ies' if _n_not_located != 1 else 'y'} not located in public materials scanned — may exist internally or under different titles.")
    if not countries:
        _limitations.append("No operating countries identified from available data sources.")

    if _limitations:
        _lim_html = " · ".join(_limitations)
        st.markdown(
            f'<div class="data-limits-box">⚠️ <strong>Data Limitations:</strong> {_lim_html}</div>',
            unsafe_allow_html=True,
        )

    # ── Store in report history (only on fresh run, not reruns) ────────
    if run_btn:
        if "report_history" not in st.session_state:
            st.session_state["report_history"] = []
        report_entry = {
            "charity_num": charity_num,
            "charity_name": entity_name,
            "timestamp": datetime.now().strftime("%d %b %Y %H:%M"),
            "model": actual_model,
            "provider": cost_info.get("provider", "unknown"),
            "prompt_tokens": cost_info.get("prompt_tokens", 0),
            "completion_tokens": cost_info.get("completion_tokens", 0),
            "total_tokens": cost_info.get("total_tokens", 0),
            "cost_usd": cost_usd,
            "report_text": full_report,
        }
        existing_keys = {(h["charity_num"], h["timestamp"]) for h in st.session_state["report_history"]}
        if (report_entry["charity_num"], report_entry["timestamp"]) not in existing_keys:
            st.session_state["report_history"].append(report_entry)

    # ── Quick Facts ────────────────────────────────────────────────────────
    st.subheader("Quick Facts")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Income", fmt_money(charity_data.get("latest_income")))
    c2.metric("Expenditure", fmt_money(charity_data.get("latest_expenditure")))
    inc = charity_data.get("latest_income") or 0
    exp = charity_data.get("latest_expenditure") or 0
    surplus = inc - exp
    c3.metric("Surplus / Deficit", fmt_money(surplus) if inc else "N/A",
              delta=f"{surplus/inc*100:.1f}%" if inc else None)
    c4.metric("Employees", charity_data.get("employees", "N/A"))
    c5.metric("Trustees", charity_data.get("num_trustees", len(trustees)))

    c6, c7, c8, c9, c10 = st.columns(5)
    c6.metric("Volunteers", charity_data.get("volunteers", "N/A"))
    c7.metric("Countries", len(countries))
    c8.metric("High-Risk Geo", len(high_risk_countries))
    total_adverse = sum(count_true_adverse(v) for v in adverse_trustees.values()) + count_true_adverse(adverse_org)
    c9.metric("Adverse Hits", total_adverse,
             help="Verified adverse media hits (name + adverse keyword match)")
    _cc_doc_count = 1 if cc_tar_doc else (1 if cc_pdf_text else 0)
    _total_docs = _cc_doc_count + _uploaded_files_count + _gov_doc_files_count
    _eq = _extraction_confidence.get("overall_quality", "n/a") if _extraction_confidence else "n/a"
    _eq_label = {"good": "✅ Good", "partial": "⚠️ Partial", "low": "⚠️ Low",
                 "none": "❌ None", "mixed": "⚠️ Mixed"}.get(_eq, _eq)
    c10.metric("Docs Analysed",
               f"{_total_docs}" if _total_docs > 0 else "0 (API + web)",
               help=f"{'1 CC TAR' if cc_tar_doc else '0 CC TAR'}, "
                    f"{_uploaded_files_count} uploaded, "
                    f"{_gov_doc_files_count} governance · "
                    f"Extraction: {_eq_label}")

    # ── Evidence Source Annotations ──────────────────────────────────
    _fin_source = "CC API (allcharitydetails)"
    if cc_tar_doc:
        _tar_yr = cc_tar_doc.get("year", "?")
        _fin_source += f" + TAR filing ({_tar_yr})"
    _ev_html = (
        '<div style="font-size:0.68rem; color:#6b7280; margin-top:0.3rem; line-height:1.6;">'
        f'<span class="ev-tag ev-tag-api">API</span> Financial data: {_fin_source} · '
        f'<span class="ev-tag ev-tag-api">API</span> Trustees: CC API (charitytrustees) · '
        f'<span class="ev-tag ev-tag-api">API</span> Governance: CC Register (governance page) · '
        f'<span class="ev-tag ev-tag-web">WEB</span> Adverse media: Tavily search · '
        f'<span class="ev-tag ev-tag-web">WEB</span> Policies: Website crawl + Tavily'
    )
    if cc_tar_doc:
        _ev_html += f' · <span class="ev-tag ev-tag-doc">DOC</span> Accounts: CC TAR ({_tar_yr})'
    if _uploaded_files_count > 0:
        _ev_html += f' · <span class="ev-tag ev-tag-doc">DOC</span> {_uploaded_files_count} uploaded doc{"s" if _uploaded_files_count != 1 else ""}'
    if _gov_doc_files_count > 0:
        _ev_html += f' · <span class="ev-tag ev-tag-doc">DOC</span> {_gov_doc_files_count} governance doc{"s" if _gov_doc_files_count != 1 else ""}'
    _ev_html += "</div>"
    st.markdown(_ev_html, unsafe_allow_html=True)

    # ── Governance Intelligence Panel ────────────────────────────────────
    if cc_governance:
        _org_type = cc_governance.get("organisation_type") or charity_data.get("charity_type", "")
        _org_info = _ORG_TYPE_INFO.get(_org_type, {})
        _ga = cc_governance.get("gift_aid")
        _rh = cc_governance.get("registration_history", [])
        _other_names = cc_governance.get("other_names", [])
        _status_flags = cc_governance.get("status_flags", {})
        _funding = cc_governance.get("funding_model", {})
        _trustee_gov = cc_governance.get("trustee_governance", {})
        _reporting = cc_governance.get("reporting_status", "")
        _years_reg = gov_indicators.get("years_registered")
        _cc_gov_url = cc_governance.get("governance_url", "")

        # Build HTML panel
        _gi_html = '<div class="gov-intel"><h4>🏛️ Governance Intelligence <span style="font-size:0.7rem;color:#6b7280;">(Source: CC API)</span></h4>'

        # ── Critical status alerts ──
        if _status_flags:
            for flag, val in _status_flags.items():
                if flag == "insolvent" and val:
                    _gi_html += '<div class="gov-chip gov-chip-bad" style="width:100%;margin-bottom:0.3rem;">🚨 <b>INSOLVENT</b> — Charity is marked as insolvent</div>'
                elif flag == "in_administration" and val:
                    _gi_html += '<div class="gov-chip gov-chip-bad" style="width:100%;margin-bottom:0.3rem;">🚨 <b>IN ADMINISTRATION</b></div>'
                elif flag == "interim_manager" and val:
                    _gi_html += '<div class="gov-chip gov-chip-bad" style="width:100%;margin-bottom:0.3rem;">⚠️ <b>INTERIM MANAGER APPOINTED</b> — CC has intervened in charity governance</div>'
                elif flag == "cio_dissolution" and val:
                    _gi_html += '<div class="gov-chip gov-chip-warn" style="width:100%;margin-bottom:0.3rem;">⚠️ <b>CIO DISSOLUTION</b> — Charity may be winding down</div>'
                elif flag == "removed" and val:
                    _reason = _status_flags.get("removal_reason", "Unknown")
                    _gi_html += f'<div class="gov-chip gov-chip-warn" style="width:100%;margin-bottom:0.3rem;">⚠️ <b>REMOVED</b> from register: {_reason}</div>'

        # ── Row 1: Key indicator chips ──
        _gi_html += '<div class="gov-intel-grid">'

        # Org type chip
        if _org_type:
            _full = _org_info.get("full_name", _org_type)
            _desc = _org_info.get("description", "")
            _gi_html += f'<div class="gov-chip gov-chip-ok" title="{_desc}">🏢 <b>{_org_type}</b> — {_full}</div>'

        # Registration date + years
        _reg_date = charity_data.get("date_of_registration", "")
        if _reg_date:
            _rd_display = str(_reg_date)[:10]
            _yrs_text = f" ({_years_reg} yrs)" if _years_reg else ""
            _gi_html += f'<div class="gov-chip gov-chip-ok">📅 Registered: {_rd_display}{_yrs_text}</div>'

        # Gift Aid chip
        if _ga:
            if "recognised" in _ga.lower() or "active" in _ga.lower():
                _gi_html += f'<div class="gov-chip gov-chip-ok">🎁 {_ga}</div>'
            elif "not" in _ga.lower() or "removed" in _ga.lower() or "revoked" in _ga.lower():
                _gi_html += f'<div class="gov-chip gov-chip-warn">🎁 ⚠️ {_ga}</div>'
            else:
                _gi_html += f'<div class="gov-chip">🎁 {_ga}</div>'

        # Reporting status
        if _reporting:
            _rep_cls = "gov-chip-ok" if "received" in _reporting.lower() else "gov-chip-warn"
            _gi_html += f'<div class="gov-chip {_rep_cls}">📄 Reporting: {_reporting}</div>'

        # CH consistency
        _ch_note = gov_indicators.get("ch_consistency", "")
        if _ch_note:
            _ch_cls = "gov-chip-ok" if ("confirmed" in _ch_note.lower() or
                                        "not required" in _ch_note.lower()) else "gov-chip-warn"
            _gi_html += f'<div class="gov-chip {_ch_cls}">🏢 {_ch_note}</div>'

        # Trustee governance
        if _trustee_gov.get("trustee_benefits"):
            _gi_html += '<div class="gov-chip gov-chip-warn">💰 Trustee benefits declared</div>'
        if _trustee_gov.get("trustee_payments_acting_as_trustee"):
            _gi_html += '<div class="gov-chip gov-chip-warn">💰 Trustee payments for acting as trustee</div>'
        if _trustee_gov.get("trustee_payments_services"):
            _gi_html += '<div class="gov-chip">💼 Trustee payments for services</div>'
        if _trustee_gov.get("trading_subsidiary"):
            _gi_html += '<div class="gov-chip">🏪 Has trading subsidiary</div>'

        _gi_html += '</div>'  # close grid

        # ── Other names ──
        if _other_names:
            _names_str = ", ".join(_other_names)
            _gi_html += f'<div style="font-size:0.78rem;margin-top:0.4rem;">📝 <b>Other Names:</b> {_names_str}</div>'

        # ── Funding model ──
        _fund_parts = []
        if _funding.get("raises_from_public"):
            _fund_parts.append("Raises funds from public")
        if _funding.get("professional_fundraiser"):
            _fund_parts.append("Uses professional fundraiser")
        if _funding.get("govt_contracts"):
            _gc = _funding["govt_contracts"]
            _fund_parts.append(f"Govt contracts: {_gc.get('count', 0)}")
        if _funding.get("govt_grants"):
            _gg = _funding["govt_grants"]
            _fund_parts.append(f"Govt grants: {_gg.get('count', 0)}")
        if _funding.get("grant_making"):
            _fund_parts.append("Grant-making charity")
        if _fund_parts:
            _gi_html += f'<div style="font-size:0.78rem;margin-top:0.3rem;">💰 <b>Funding:</b> {" · ".join(_fund_parts)}</div>'

        # ── Registration History Timeline ──
        if _rh:
            _gi_html += '<div class="gov-timeline" style="margin-top:0.5rem;"><b>Registration History:</b>'
            for ev in _rh:
                _ev_date = ev.get("date", "")
                _ev_type = ev.get("event", "")
                _ev_interp = ev.get("interpretation", "")
                _ev_note = f' — <i>{_ev_interp}</i>' if _ev_interp else ""
                _gi_html += f'<div class="event">{_ev_date}: <b>{_ev_type}</b>{_ev_note}</div>'
            _gi_html += '</div>'

            # Flag notable events
            _notable = gov_indicators.get("reg_history_flags", [])
            if _notable:
                _gi_html += '<div style="font-size:0.75rem;color:#b45309;margin-top:0.3rem;">'
                _gi_html += f'⚠️ Notable: {", ".join(_notable)}'
                _gi_html += '</div>'

        # ── Org type risk note ──
        _risk_note = _org_info.get("risk_note", "")
        if _risk_note:
            _gi_html += f'<div style="font-size:0.72rem;color:#6b7280;margin-top:0.4rem;">ℹ️ {_risk_note}</div>'

        # ── CC declared policies note ──
        _pol_note = cc_governance.get("cc_declared_policies_note", "")
        if _pol_note:
            _gi_html += (f'<div style="font-size:0.70rem;color:#6b7280;margin-top:0.2rem;">'
                         f'📋 {_pol_note}</div>')

        # Link to governance page
        if _cc_gov_url:
            _gi_html += (f'<div style="font-size:0.7rem;margin-top:0.3rem;">'
                         f'<a href="{_cc_gov_url}" target="_blank">View CC Governance Page ↗ (includes declared policies, land & property)</a></div>')

        _gi_html += '</div>'  # close gov-intel
        st.markdown(_gi_html, unsafe_allow_html=True)

    # ── Structural Governance Observations Panel ─────────────────────────
    if structural_governance and structural_governance.get("total_flags", 0) > 0:
        _sg_html = ('<div class="gov-intel" style="border-image:linear-gradient(135deg,#d97706,#dc2626) 1;">'
                    '<h4>🏗️ Structural Governance Observations '
                    '<span style="font-size:0.7rem;color:#6b7280;">'
                    f'({structural_governance["total_flags"]} observation(s))</span></h4>')

        # Capacity flags
        _cap_flags = structural_governance.get("capacity_flags", [])
        if _cap_flags:
            _sg_html += '<div style="margin-bottom:0.5rem;">'
            _sg_html += '<b style="font-size:0.82rem;">📊 Oversight Capacity</b>'
            for _cf in _cap_flags:
                _sg_html += (f'<div class="gov-chip gov-chip-warn" '
                             f'style="width:100%;margin:0.25rem 0;white-space:normal;">'
                             f'⚠️ {_cf}</div>')
            _sg_html += '</div>'

        # Trustee directorships
        _td = structural_governance.get("trustee_directorships", {})
        if _td:
            _sg_html += '<div style="margin-bottom:0.5rem;">'
            _sg_html += '<b style="font-size:0.82rem;">🔗 Trustee / Director Appointments</b>'
            for _tname, _tdata in _td.items():
                _count = _tdata.get("count", 0)
                _color = "#b45309" if _count >= 3 else "#6b7280"
                _icon = "⚠️" if _count >= 3 else "ℹ️"
                _sg_html += (f'<div style="font-size:0.78rem;margin:0.3rem 0;padding:0.3rem 0.5rem;'
                             f'border-left:3px solid {_color};background:#fefce8;">'
                             f'{_icon} <b>{_tname}</b>: {_count} other active appointment(s)')
                _entities = _tdata.get("entities", [])
                if _entities:
                    _sg_html += '<div style="font-size:0.72rem;color:#6b7280;margin-top:0.15rem;">'
                    for _ent in _entities[:5]:
                        _ename = _ent.get("company_name", "")
                        _erole = _ent.get("officer_role", "")
                        _estatus = _ent.get("company_status", "")
                        _sg_html += f'• {_ename} ({_erole}'
                        if _estatus and _estatus != "active":
                            _sg_html += f', {_estatus}'
                        _sg_html += ')<br>'
                    if len(_entities) > 5:
                        _sg_html += f'<i>...and {len(_entities) - 5} more</i>'
                    _sg_html += '</div>'
                _sg_html += '</div>'
            _sg_html += '</div>'

        # Concentration flags
        _conc = structural_governance.get("concentration_flags", [])
        if _conc:
            _sg_html += '<div style="margin-bottom:0.5rem;">'
            _sg_html += '<b style="font-size:0.82rem;">🔀 Governance Concentration</b>'
            for _cc in _conc:
                _sg_html += (f'<div class="gov-chip" '
                             f'style="width:100%;margin:0.25rem 0;white-space:normal;">'
                             f'🔗 {_cc}</div>')
            _sg_html += '</div>'

        # Disclaimer
        _sg_html += ('<div style="font-size:0.68rem;color:#9ca3af;margin-top:0.4rem;'
                     'font-style:italic;">These observations highlight structural '
                     'patterns for analyst review. They do not imply misconduct '
                     'or regulatory non-compliance.</div>')

        _sg_html += '</div>'
        st.markdown(_sg_html, unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════
    # TABBED DASHBOARD
    # ══════════════════════════════════════════════════════════════════════
    tab_finance, tab_screening, tab_geo, tab_controls, tab_report, tab_data = st.tabs([
        "📊 Financials",
        "🔍 Screening",
        "🌍 Geography",
        "🛡️ Controls & Policies",
        "📝 Analyst Report",
        "📦 Raw Data & Audit",
    ])

    # ── TAB 1: Financials ────────────────────────────────────────────────
    with tab_finance:
        income_data = {
            "Donations & Legacies": charity_data.get("inc_donations", 0) or 0,
            "Charitable Activities": charity_data.get("inc_charitable", 0) or 0,
            "Trading": charity_data.get("inc_trading", 0) or 0,
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

        # ── Multi-year trend chart (if ≥3 years available) ────────────
        if len(financial_history) >= 3:
            st.markdown("**Income & Expenditure Trend**")
            trend_fig = make_financial_trend_chart(financial_history)
            st.pyplot(trend_fig)
            plt.close(trend_fig)
            _trend_comment = generate_financial_trend_comment(financial_history)
            if _trend_comment:
                st.caption(_trend_comment)
            if len(financial_history) < 5:
                st.caption(
                    "ℹ️ Historical financial data limited; "
                    "trend analysis based on available years only."
                )
            st.markdown("---")

        # ── Financial Anomaly Detection ──────────────────────────────
        if financial_anomalies and financial_anomalies.get("anomaly_count", 0) > 0:
            st.markdown("**Financial Anomaly Detection**")
            st.caption(
                "Automated analysis of year-on-year income/expenditure changes, "
                "volatility, and ratio shifts. Thresholds: >30% YoY change, "
                ">25% coefficient of variation, >15pp ratio shift."
            )
            # Summary metrics
            _fa_c1, _fa_c2, _fa_c3 = st.columns(3)
            _fa_c1.metric("Observations", financial_anomalies["anomaly_count"])
            _fa_c2.metric(
                "Income Volatility",
                f"{financial_anomalies['income_volatility']:.0%}",
                help="Coefficient of variation across reporting periods"
            )
            _fa_c3.metric(
                "Expenditure Volatility",
                f"{financial_anomalies['expenditure_volatility']:.0%}",
                help="Coefficient of variation across reporting periods"
            )
            # Individual flags
            for _flag in financial_anomalies["flags"]:
                st.info(f"📊 {_flag}")
            st.caption(financial_anomalies.get("summary", ""))
            st.markdown("---")
        elif financial_anomalies:
            st.success("✅ No significant financial anomalies detected.")
            st.markdown("---")

        # ── Single-year breakdown charts ─────────────────────────────
        chart_cols = st.columns(3)
        if inc or exp:
            with chart_cols[0]:
                st.markdown("**Income vs Expenditure (Latest Year)**")
                fig = make_income_vs_expense_chart(inc, exp)
                st.pyplot(fig)
                plt.close(fig)
        if income_data:
            with chart_cols[1]:
                st.markdown("**Income Breakdown**")
                fig = make_pie_chart(income_data, "Income Sources")
                st.pyplot(fig)
                plt.close(fig)
        if exp_data:
            with chart_cols[2]:
                st.markdown("**Expenditure Breakdown**")
                fig = make_pie_chart(exp_data, "Expenditure Categories")
                st.pyplot(fig)
                plt.close(fig)

        # Fundraising efficiency
        exp_raising = charity_data.get("exp_raising", 0) or 0
        exp_charitable = charity_data.get("exp_charitable", 0) or 0
        has_breakdown = (exp_raising > 0 or exp_charitable > 0) and inc > 0
        if has_breakdown:
            efficiency = exp_charitable / inc * 100
            fundraising_cost = exp_raising / inc * 100
            admin = 100 - efficiency - fundraising_cost

            st.markdown("---")
            st.markdown("**Fundraising Efficiency**")
            st.caption(
                "Charitable Spending = charitable expenditure ÷ total income · "
                "Fundraising Cost = cost of raising funds ÷ total income · "
                "Admin Overhead = remainder"
            )
            eff_cols = st.columns(3)
            eff_cols[0].metric("Charitable Spending", f"{efficiency:.1f}%")
            eff_cols[1].metric("Fundraising Cost", f"{fundraising_cost:.1f}%")
            eff_cols[2].metric("Admin Overhead", f"{admin:.1f}%")
        elif inc > 0:
            st.info(
                "Fundraising efficiency ratios unavailable — the Charity Commission "
                "API did not return an expenditure breakdown."
            )

        # ── Financial Health Indicators ──────────────────────────────
        if inc > 0:
            st.markdown("---")
            st.markdown("**Financial Health Indicators**")
            st.caption(
                "Computed risk-relevant ratios for analyst review. "
                "Thresholds are indicative and should be interpreted in context."
            )

            _spend_to_inc = exp / inc * 100
            _deficit_ratio_pct = (exp - inc) / inc * 100
            _surplus_val = inc - exp

            # Financial stress indicator (composite)
            _stress_score = 0
            _stress_factors = []
            if _deficit_ratio_pct > 10:
                _stress_score += 2
                _stress_factors.append("significant deficit")
            elif _deficit_ratio_pct > 0:
                _stress_score += 1
                _stress_factors.append("minor deficit")
            if _anomaly_ct >= 3:
                _stress_score += 2
                _stress_factors.append(f"{_anomaly_ct} financial anomalies")
            elif _anomaly_ct >= 1:
                _stress_score += 1
                _stress_factors.append(f"{_anomaly_ct} financial anomal{'ies' if _anomaly_ct != 1 else 'y'}")
            # Check for declining income trend
            if len(financial_history) >= 3:
                _recent = financial_history[-1]["income"]
                _oldest = financial_history[0]["income"]
                if _oldest > 0 and _recent < _oldest * 0.7:
                    _stress_score += 1
                    _stress_factors.append("declining income trend")

            if _stress_score == 0:
                _stress_label = "🟢 Low"
            elif _stress_score <= 2:
                _stress_label = "🟡 Moderate"
            elif _stress_score <= 3:
                _stress_label = "🟠 Elevated"
            else:
                _stress_label = "🔴 High"

            # Governance risk multiplier
            _gov_mult = 1.0
            _gov_mult_factors = []
            if _n_hr >= 3:
                _gov_mult += 0.5
                _gov_mult_factors.append(f"{_n_hr} high-risk jurisdictions")
            elif _n_hr >= 1:
                _gov_mult += 0.25
                _gov_mult_factors.append(f"{_n_hr} high-risk jurisdiction{'s' if _n_hr != 1 else ''}")
            _fc_status = hrcob_core_controls.get("financial_crime", {}).get("status", "not_located")
            if _fc_status == "not_located":
                _gov_mult += 0.5
                _gov_mult_factors.append("AML/financial crime policy not located")
            elif _fc_status == "partial":
                _gov_mult += 0.25
                _gov_mult_factors.append("AML/financial crime policy partial")
            if _total_adverse > 0:
                _gov_mult += 0.25
                _gov_mult_factors.append(f"{_total_adverse} adverse media hit{'s' if _total_adverse != 1 else ''}")

            if _gov_mult <= 1.0:
                _gov_mult_label = "🟢 1.0× (baseline)"
            elif _gov_mult <= 1.5:
                _gov_mult_label = f"🟡 {_gov_mult:.2f}×"
            elif _gov_mult <= 2.0:
                _gov_mult_label = f"🟠 {_gov_mult:.2f}×"
            else:
                _gov_mult_label = f"🔴 {_gov_mult:.2f}×"

            # Display metrics
            _fh1, _fh2, _fh3, _fh4 = st.columns(4)
            _fh1.metric(
                "Spend-to-Income",
                f"{_spend_to_inc:.1f}%",
                help="Total expenditure as a percentage of total income. "
                     "Values >100% indicate a deficit year."
            )
            _fh2.metric(
                "Deficit Ratio",
                f"{_deficit_ratio_pct:+.1f}%",
                delta="surplus" if _surplus_val >= 0 else "deficit",
                delta_color="normal" if _surplus_val >= 0 else "inverse",
                help="(Expenditure − Income) ÷ Income × 100. "
                     "Negative = surplus, Positive = deficit."
            )
            _fh3.metric(
                "Financial Stress",
                _stress_label,
                help="Composite indicator based on deficit ratio, "
                     "financial anomaly count, and income trend."
            )
            _fh4.metric(
                "Governance Risk Multiplier",
                _gov_mult_label,
                help="Risk multiplier combining high-risk geography, "
                     "AML policy gaps, and adverse media. "
                     "1.0× = baseline (no amplifying factors)."
            )
            # Show factor breakdown
            if _stress_factors:
                st.caption(f"Stress factors: {', '.join(_stress_factors)}")
            if _gov_mult_factors:
                st.caption(f"Multiplier factors: {', '.join(_gov_mult_factors)}")

        # Validation links
        st.markdown("---")
        _cc_org_num = charity_data.get("organisation_number") or charity_num
        _cc_url = (
            f"https://register-of-charities.charitycommission.gov.uk"
            f"/charity-search/-/charity-details/{_cc_org_num}"
        )
        st.markdown(
            f'<a class="val-link" href="{_cc_url}" target="_blank">'
            f'🔗 Charity Commission Record</a>',
            unsafe_allow_html=True,
        )
        if linked_co:
            _ch_filing_url = (
                f"https://find-and-update.company-information.service.gov.uk"
                f"/company/{linked_co}/filing-history"
            )
            st.markdown(
                f'<a class="val-link" href="{_ch_filing_url}" target="_blank">'
                f'🔗 Companies House Filing History</a>',
                unsafe_allow_html=True,
            )

    # ── TAB 2: Screening ────────────────────────────────────────────────
    with tab_screening:
        # Trustees adverse media
        if trustees:
            st.markdown("**Trustees / Directors — Adverse Media Screening**")
            st.caption(
                "Verified hits = person's name AND adverse keywords both present. "
                "Unverified = generic mentions filtered out."
            )
            trustee_rows = []
            for i, t in enumerate(trustees):
                results = adverse_trustees.get(t, [])
                true_hits = count_true_adverse(results)
                total_results = len(results)
                if true_hits > 0:
                    flag = f"🔴 {true_hits} verified hit(s)"
                elif total_results > 0:
                    flag = f"🟢 0 verified ({total_results} unrelated)"
                else:
                    flag = "🟢 Clear"
                trustee_rows.append({"#": i + 1, "Name": t, "Adverse Media": flag})
            st.dataframe(pd.DataFrame(trustee_rows), use_container_width=True, hide_index=True)

        # Social media
        if social_media_links:
            st.markdown("---")
            st.markdown("**Social Media Verification**")
            _has_manual = social_media_links.get("_manual_note")
            if _has_manual:
                st.caption(
                    "Links include manually verified URLs provided by the analyst "
                    "and auto-detected links from the charity's website HTML."
                )
            else:
                st.caption(
                    "Extracted from the charity's website HTML. "
                    "Only official profile links — share buttons excluded."
                )
            sm_rows = []
            for platform, url in sorted(social_media_links.items()):
                if platform.startswith("_"):
                    continue  # skip internal flags
                _is_manual = platform in _manual_social if _manual_social else False
                sm_rows.append({
                    "Platform": platform.title(),
                    "URL": url if url else "—",
                    "Status": ("📋 Manually Verified" if _is_manual
                               else ("✅ Detected" if url else "⚠️ Not on site")),
                })
            st.dataframe(pd.DataFrame(sm_rows), use_container_width=True, hide_index=True)

        # Adverse media validation links
        st.markdown("---")
        with st.expander("🔎 Adverse Media — Validate on Google News", expanded=False):
            _adv_query_org = f'"{entity_name}" ({ADVERSE_TERMS})'
            _adv_gn_org = f"https://www.google.com/search?q={quote(_adv_query_org)}&tbm=nws"
            st.markdown(
                f'**Organisation**: <a class="val-link" href="{_adv_gn_org}" target="_blank">'
                f'🔎 {entity_name}</a>',
                unsafe_allow_html=True,
            )
            st.caption(f"Search string: `{_adv_query_org[:200]}`")

            _trading = charity_data.get("charity_name", "")
            if _trading and _trading != entity_name:
                _tq = f'"{_trading}" ({ADVERSE_TERMS})'
                _tgn = f"https://www.google.com/search?q={quote(_tq)}&tbm=nws"
                st.markdown(
                    f'**Trading name**: <a class="val-link" href="{_tgn}" target="_blank">'
                    f'🔎 {_trading}</a>',
                    unsafe_allow_html=True,
                )

            st.markdown("**Trustees:**")
            for _ti, _tn in enumerate(trustees):
                _tq = f'"{_tn}" ({ADVERSE_TERMS})'
                _tgn = f"https://www.google.com/search?q={quote(_tq)}&tbm=nws"
                st.markdown(
                    f'<a class="val-link" href="{_tgn}" target="_blank">'
                    f'🔎 {_tn}</a>',
                    unsafe_allow_html=True,
                )
            if trustees:
                st.caption(f"Search string pattern: `\"{trustees[0]}\" ({ADVERSE_TERMS[:80]}…)`")

    # ── TAB 3: Geography ─────────────────────────────────────────────────
    with tab_geo:
        if country_risk_classified:
            _risk_icons = {
                "Very High Risk": "🔴 Very High Risk",
                "High Risk": "🟠 High Risk",
                "Medium Risk": "🟡 Medium Risk",
                "Low Risk": "🟢 Low Risk",
                "Unclassified": "⚪ Unclassified — refer to Basel AML Index",
                "Unknown": "⚪ Unknown",
            }
            for entry in country_risk_classified:
                entry["risk_display"] = _risk_icons.get(
                    entry["risk_level"], entry["risk_level"])

            # ── Summary Counters ─────────────────────────────────────
            _geo_risk_counts = {}
            for e in country_risk_classified:
                _geo_risk_counts[e["risk_level"]] = _geo_risk_counts.get(e["risk_level"], 0) + 1
            _gc1, _gc2, _gc3, _gc4, _gc5 = st.columns(5)
            _gc1.metric("Total Countries", len(country_risk_classified))
            _gc2.metric("🔴 Very High", _geo_risk_counts.get("Very High Risk", 0))
            _gc3.metric("🟠 High", _geo_risk_counts.get("High Risk", 0))
            _gc4.metric("🟡 Medium", _geo_risk_counts.get("Medium Risk", 0))
            _gc5.metric("🟢 Low", _geo_risk_counts.get("Low Risk", 0))

            # ── Quick Filter Buttons ─────────────────────────────────
            _qf1, _qf2, _qf3, _qf_spacer = st.columns([1, 1, 1, 3])
            if "geo_quick_filter" not in st.session_state:
                st.session_state["geo_quick_filter"] = "all"
            with _qf1:
                if st.button("🔴🟠 High & Very High", key="geo_qf_high",
                             use_container_width=True):
                    st.session_state["geo_quick_filter"] = "high"
            with _qf2:
                if st.button("🟢 Low Risk Only", key="geo_qf_low",
                             use_container_width=True):
                    st.session_state["geo_quick_filter"] = "low"
            with _qf3:
                if st.button("↩️ Reset Filters", key="geo_qf_reset",
                             use_container_width=True):
                    st.session_state["geo_quick_filter"] = "all"

            # ── Filter Controls ──────────────────────────────────────
            _all_risk_levels = sorted(set(e["risk_level"] for e in country_risk_classified),
                                      key=lambda r: ["Very High Risk", "High Risk",
                                                      "Medium Risk", "Low Risk",
                                                      "Unclassified", "Unknown"].index(r)
                                      if r in ["Very High Risk", "High Risk", "Medium Risk",
                                               "Low Risk", "Unclassified", "Unknown"] else 99)
            _all_continents = sorted(set(e.get("continent", "Unknown")
                                         for e in country_risk_classified))

            _fc1, _fc2, _fc3 = st.columns(3)
            with _fc1:
                _sel_risk = st.multiselect("Filter by Risk Level",
                                           options=_all_risk_levels,
                                           default=None,
                                           key="geo_filter_risk",
                                           placeholder="All risk levels")
            with _fc2:
                _sel_cont = st.multiselect("Filter by Continent",
                                           options=_all_continents,
                                           default=None,
                                           key="geo_filter_continent",
                                           placeholder="All continents")
            with _fc3:
                _search_country = st.text_input("Search Country",
                                                key="geo_search_country",
                                                placeholder="Type country name…")

            # ── Apply Filters ────────────────────────────────────────
            _filtered = list(country_risk_classified)

            # Quick filter override
            _qf = st.session_state.get("geo_quick_filter", "all")
            if _qf == "high":
                _filtered = [e for e in _filtered
                             if e["risk_level"] in ("Very High Risk", "High Risk")]
            elif _qf == "low":
                _filtered = [e for e in _filtered
                             if e["risk_level"] == "Low Risk"]

            # Multi-select filters (applied on top of quick filter)
            if _sel_risk:
                _filtered = [e for e in _filtered if e["risk_level"] in _sel_risk]
            if _sel_cont:
                _filtered = [e for e in _filtered
                             if e.get("continent", "Unknown") in _sel_cont]
            if _search_country:
                _sq = _search_country.lower()
                _filtered = [e for e in _filtered if _sq in e["country"].lower()]

            # ── Sort by Risk Severity then Country ───────────────────
            _risk_order = {"Very High Risk": 0, "High Risk": 1, "Medium Risk": 2,
                           "Low Risk": 3, "Unclassified": 4, "Unknown": 5}
            _filtered.sort(key=lambda e: (_risk_order.get(e["risk_level"], 9),
                                          e["country"]))

            # ── Display Table ────────────────────────────────────────
            if _filtered:
                risk_df = pd.DataFrame(_filtered)
                display_df = risk_df[["country", "continent", "risk_display"]].rename(
                    columns={"country": "Country", "continent": "Continent",
                             "risk_display": "Risk Level"}
                )
                st.dataframe(display_df, use_container_width=True, hide_index=True)
                if len(_filtered) < len(country_risk_classified):
                    st.caption(f"Showing {len(_filtered)} of "
                               f"{len(country_risk_classified)} countries "
                               f"(filtered)")
            else:
                st.info("No countries match the current filters.")

            # ── Elevated Risk Warning ────────────────────────────────
            elevated = [e for e in country_risk_classified
                        if is_elevated_risk(e["risk_level"])]
            if elevated:
                st.warning(
                    f"⚠️ **{len(elevated)} elevated-risk jurisdiction(s):** "
                    + ", ".join(f"**{e['country']}** ({e['risk_level']})"
                               for e in elevated)
                )
                # Contextual note for large humanitarian charities
                _charity_income = charity_data.get("latest_income") or 0
                _n_countries = len(country_risk_classified)
                if _charity_income > 50_000_000 and _n_countries > 10 and len(elevated) > 5:
                    st.info(
                        "ℹ️ **Context:** High-risk geographic exposure is consistent with a "
                        "large-scale humanitarian or international development mandate and does not "
                        "independently indicate elevated governance risk. Risk assessment should "
                        "focus on the control framework rather than geographic presence alone."
                    )
            else:
                st.success("✅ No High Risk or Very High Risk jurisdictions.")

            # Risk pie
            risk_counts = _geo_risk_counts
            if risk_counts:
                fig_r, ax_r = plt.subplots(figsize=(5, 3))
                rc = {"Very High Risk": "#e74c3c", "High Risk": "#e67e22",
                      "Medium Risk": "#f1c40f", "Low Risk": "#2ecc71",
                      "Unknown": "#bdc3c7"}
                labels_r = list(risk_counts.keys())
                vals_r = list(risk_counts.values())
                colors_r = [rc.get(l, "#95a5a6") for l in labels_r]
                ax_r.pie(vals_r, labels=labels_r, colors=colors_r,
                         autopct='%1.0f%%', startangle=90)
                ax_r.set_title("Geographic Risk Distribution", fontweight='bold')
                plt.tight_layout()
                st.pyplot(fig_r)
                plt.close(fig_r)
        else:
            st.info("No countries of operation recorded.")

    # ── TAB 4: Controls & Policies ───────────────────────────────────────
    with tab_controls:
        # HRCOB Core Controls
        if hrcob_core_controls:
            st.markdown("**HRCOB Core Controls**")
            hrcob_status = hrcob_core_controls.get("hrcob_status", "Unknown")
            hrcob_narrative = hrcob_core_controls.get("hrcob_narrative", "")

            _status_colors = {
                "Satisfactory": ("success", "✅"),
                "Acceptable with Clarification": ("warning", "🔍"),
                "Clarification Recommended": ("info", "📋"),
                "Material Control Concern": ("warning", "⚠️"),
                "Further Enquiry Recommended": ("warning", "⚠️"),
            }
            _method, _icon = _status_colors.get(hrcob_status, ("info", "ℹ️"))
            getattr(st, _method)(f"{_icon} **HRCOB Core Control Status: {hrcob_status}**")
            st.caption(hrcob_narrative)

            _labels = {
                "safeguarding": "Safeguarding",
                "financial_crime": "Financial Crime (Bribery + AML)",
                "risk_management": "Risk Management",
            }
            core_rows = []
            for key in ["safeguarding", "financial_crime", "risk_management"]:
                ctrl = hrcob_core_controls.get(key, {})
                _src_url = ctrl.get("source_url", "") or ""
                _source_label = "Provided by Charity" if _src_url == "PROVIDED" else (_src_url or "—")
                core_rows.append({
                    "Core Control": _labels[key],
                    "Status": ctrl.get("status_icon", "⚠️ Not Located"),
                    "Evidence": ctrl.get("evidence", "") or "—",
                    "Source": _source_label,
                })
            st.dataframe(pd.DataFrame(core_rows), use_container_width=True, hide_index=True)

            # Inline policy links (skip PROVIDED pseudo-URLs)
            _link_parts = []
            for key in ["safeguarding", "financial_crime", "risk_management"]:
                ctrl = hrcob_core_controls.get(key, {})
                _url = ctrl.get("source_url", "")
                if _url and _url != "PROVIDED":
                    _link_parts.append(
                        f'{_labels[key]}: '
                        f'<a class="val-link" href="{_url}" target="_blank">🔗 View Policy</a>'
                    )
                elif _url == "PROVIDED":
                    _link_parts.append(f'{_labels[key]}: 📁 Provided by Charity')
            if _link_parts:
                st.markdown(" &nbsp; ".join(_link_parts), unsafe_allow_html=True)

            # Three metric cards
            cc1, cc2, cc3 = st.columns(3)
            sg_s = hrcob_core_controls.get("safeguarding", {}).get("status", "not_located")
            fc_s = hrcob_core_controls.get("financial_crime", {}).get("status", "not_located")
            rm_s = hrcob_core_controls.get("risk_management", {}).get("status", "not_located")
            _si = {"found": "✅", "partial": "🔍", "not_located": "⚠️"}
            cc1.metric("Safeguarding", f"{_si.get(sg_s, '?')} {sg_s.replace('_', ' ').title()}")
            cc2.metric("Financial Crime", f"{_si.get(fc_s, '?')} {fc_s.replace('_', ' ').title()}")
            cc3.metric("Risk Management", f"{_si.get(rm_s, '?')} {rm_s.replace('_', ' ').title()}")

        # Policy Discovery
        if policy_classification:
            st.markdown("---")
            st.markdown("**Policy Discovery Dashboard**")
            st.caption(
                "Three-state classification: "
                "**Found** = document/page title matched · "
                "**Partial** = keywords on site, no document · "
                "**Not Located** = not in public materials (may exist internally)"
            )
            policy_rows = []
            for pc in policy_classification:
                _pc_src = pc.get("source_url", "") or ""
                policy_rows.append({
                    "Policy": pc["policy"],
                    "Status": pc["status_icon"],
                    "Evidence": pc.get("evidence", "") or "—",
                    "Source": "Provided by Charity" if _pc_src == "PROVIDED" else (_pc_src or "—"),
                })
            st.dataframe(pd.DataFrame(policy_rows), use_container_width=True, hide_index=True)

            n_found = sum(1 for pc in policy_classification if pc["status"] == "found")
            n_partial = sum(1 for pc in policy_classification if pc["status"] == "partial")
            n_missing = sum(1 for pc in policy_classification if pc["status"] == "not_located")
            pc1, pc2, pc3 = st.columns(3)
            pc1.metric("Found", n_found)
            pc2.metric("Partial", n_partial)
            pc3.metric("Not Located", n_missing)

            if policy_doc_links:
                doc_only = [d for d in policy_doc_links if d.get("is_document")]
                page_links = [d for d in policy_doc_links if not d.get("is_document")]
                with st.expander(
                    f"📄 {len(doc_only)} documents + {len(page_links)} page links discovered",
                    expanded=False,
                ):
                    dl_rows = []
                    for dl in policy_doc_links[:35]:
                        dl_rows.append({
                            "Link Text": dl.get("text", "—"),
                            "URL": dl.get("url", "—"),
                            "Source": dl.get("source", "—"),
                            "Type": "📄 Doc" if dl.get("is_document") else "🔗 Page",
                        })
                    st.dataframe(pd.DataFrame(dl_rows), use_container_width=True, hide_index=True)

    # ── TAB 5: AI Report ─────────────────────────────────────────────────
    with tab_report:
        st.markdown(full_report)

    # ── TAB 6: Raw Data ──────────────────────────────────────────────────
    with tab_data:
        # Token usage
        st.markdown("**Token Usage & Cost**")
        tc1, tc2, tc3, tc4 = st.columns(4)
        tc1.metric("Prompt Tokens", f"{cost_info.get('prompt_tokens', 0):,}")
        tc2.metric("Completion Tokens", f"{cost_info.get('completion_tokens', 0):,}")
        tc3.metric("Total Tokens", f"{cost_info.get('total_tokens', 0):,}")
        tc4.metric("Cost (USD)", cost_display)

        pricing = MODEL_PRICING.get(actual_model, {})
        if pricing.get("input", 0) > 0:
            st.caption(
                f"Pricing for **{actual_model}**: "
                f"${pricing['input']:.2f}/1M input, "
                f"${pricing['output']:.2f}/1M output"
            )
        else:
            st.caption(f"**{actual_model}** — free tier, no cost.")

        # Validation links
        st.markdown("---")
        st.markdown("**Entity Validation Links**")
        _cc_org_num = charity_data.get("organisation_number") or charity_num
        _cc_url = (
            f"https://register-of-charities.charitycommission.gov.uk"
            f"/charity-search/-/charity-details/{_cc_org_num}"
        )
        st.markdown(
            f'<a class="val-link" href="{_cc_url}" target="_blank">'
            f'🔗 Charity Commission Record</a>',
            unsafe_allow_html=True,
        )
        if linked_co:
            _ch_url = (
                f"https://find-and-update.company-information.service.gov.uk"
                f"/company/{linked_co}"
            )
            st.markdown(
                f'<a class="val-link" href="{_ch_url}" target="_blank">'
                f'🔗 Companies House Filing History</a>',
                unsafe_allow_html=True,
            )

        # Document extracts
        _cc_po = _dp.get("cc_printout_data", {})
        if cc_all_docs_text or cc_pdf_text or uploaded_text or _cc_po:
            st.markdown("---")
            st.markdown("**Extracted Document Text**")

            # CC Printout data (if provided)
            if _cc_po:
                with st.expander("🏛️ CC Register Printout — Parsed Data", expanded=False):
                    st.caption("Source: Uploaded Charity Commission Register Printout (primary verified document)")
                    _po_policies = _cc_po.get("declared_policies", [])
                    if _po_policies:
                        st.markdown(f"**Declared Policies ({len(_po_policies)}):** " +
                                    ", ".join(f"✅ {p}" for p in _po_policies))
                    _po_trustees = _cc_po.get("trustees_detailed", [])
                    if _po_trustees:
                        st.markdown("**Trustees:**")
                        for t in _po_trustees:
                            _appt = t.get("appointment_date", "")
                            st.markdown(f"- {t.get('name', 'Unknown')} — appointed {_appt}")
                    _po_objects = _cc_po.get("charitable_objects", "")
                    if _po_objects:
                        st.markdown("**Charitable Objects:**")
                        st.caption(_po_objects[:1000])
                    _po_locs = _cc_po.get("where_the_charity_operates", [])
                    if _po_locs:
                        st.markdown(f"**Operating Locations:** {', '.join(_po_locs)}")
                    _po_filing = _cc_po.get("filing_history", {})
                    if _po_filing:
                        _on = _po_filing.get("on_time_count", 0)
                        _late = _po_filing.get("late_count", 0)
                        st.markdown(f"**Filing Record:** {_on} on time, {_late} late")
                    st.json(_cc_po)

            if cc_tar_doc:
                _yr = cc_tar_doc.get('year', '?')
                _title = cc_tar_doc.get('title', 'Accounts')
                _recv = cc_tar_doc.get('date_received', '')
                _src = cc_tar_doc.get('source', 'Charity Commission Official Filing')
                with st.expander(
                    f"Charity Commission: {_title} (Reporting Year {_yr})",
                    expanded=False,
                ):
                    st.caption(f"Source: {_src}")
                    if _recv:
                        st.caption(f"Date received: {_recv}")
                    st.text_area(
                        "CC TAR", cc_all_docs_text[:8000], height=400,
                        disabled=True, key="cc_tar_display",
                    )
            elif cc_pdf_text:
                with st.expander("Charity Commission Accounts PDF", expanded=False):
                    st.text_area("CC PDF", cc_pdf_text[:5000], height=300,
                                 disabled=True, key="cc_pdf_display")
            if uploaded_text:
                with st.expander("User-Uploaded Documents", expanded=False):
                    st.text_area("Uploaded", uploaded_text[:5000], height=300,
                                 disabled=True, key="uploaded_display")

        # Raw API data
        st.markdown("---")
        with st.expander("Charity Commission API Response", expanded=False):
            st.json(charity_data)
        if ch_data:
            with st.expander("Companies House API Response", expanded=False):
                st.json(ch_data)

    _vision_footer = (f"Vision ON (+${_vision_cost:.4f})" if _vision_mode
                      else "Vision OFF (text-only)")
    st.markdown("---")
    st.caption(
        f"Report generated on {datetime.now().strftime('%d %B %Y at %H:%M')} · "
        f"Know Your Charity UK · "
        f"Data Hash: {_hash_display} · "
        f"Assessment Confidence: {_conf_score}% · "
        f"Model: {actual_model} · Cost: {cost_display} · "
        f"PDF Extraction: {_vision_footer} · "
        f"Tokens: {cost_info.get('total_tokens', 0):,} · "
        f"Rule Engine: {len(policy_classification)} policies, 3 core controls, "
        f"{len(country_risk_classified)} countries classified · "
        f"Sources: Charity Commission API, Companies House API, Tavily Web Search · "
        f"Documents analysed: {_cc_doc_count + _uploaded_files_count + _gov_doc_files_count} "
        f"({'incl. CC TAR' if cc_tar_doc else 'no CC TAR'})"
    )
    st.markdown(
        '<div style="text-align:center;font-size:0.7rem;color:#9ca3af;margin-top:1rem;">'
        'Built by Ambuj Shukla with the help of Co-Pilot · '
        '<a href="mailto:knowyourcharity@ambujshukla.com" style="color:#6b7280;">'
        'knowyourcharity@ambujshukla.com</a></div>',
        unsafe_allow_html=True,
    )
