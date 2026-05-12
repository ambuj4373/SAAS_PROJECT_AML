"""
config.py — Centralised configuration for Know Your Charity UK.

Exports API keys, LLM clients, country-risk helpers, SSL settings,
model pricing and cost helpers.  Imported by app.py at startup.
"""

import os
import json
import re
import ssl

# Inject system certificate store so Python trusts the same CAs as curl/browsers.
# Required when a VPN or corporate proxy performs SSL inspection — without this,
# every outbound HTTPS call fails with CERTIFICATE_VERIFY_FAILED.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass  # truststore optional; pip install truststore to enable

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai.types import HttpOptions
from openai import OpenAI
from tavily import TavilyClient

# ─── Environment ─────────────────────────────────────────────────────────────
load_dotenv()

_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_PROJECT_DIR, "data")

# ─── API KEYS ────────────────────────────────────────────────────────────────
CH_API_KEY = os.getenv("CH_API_KEY", "")
CHARITY_COMMISSION_API_KEY = os.getenv("CHARITY_COMMISSION_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
SERPER_API_KEY_2 = os.getenv("SERPER_API_KEY_2", "")
# Ordered list of Serper keys — first valid key is tried first, fallback to next on credits exhausted
SERPER_API_KEYS: list[str] = [k for k in [SERPER_API_KEY, SERPER_API_KEY_2] if k]
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

BASEL_AML_URL = "https://index.baselgovernance.org/api/v1/countries"

# ─── COUNTRY RISK DATA (from JSON) ──────────────────────────────────────────
try:
    with open(os.path.join(_DATA_DIR, "country_risk_matrix.json")) as _f:
        COUNTRY_RISK_MATRIX = {k: v for k, v in json.load(_f).items() if k != "_comment"}
except (FileNotFoundError, json.JSONDecodeError) as _e:
    print(f"[config] WARNING: could not load country_risk_matrix.json — {_e}")
    COUNTRY_RISK_MATRIX = {}

try:
    with open(os.path.join(_DATA_DIR, "country_aliases.json")) as _f:
        _COUNTRY_ALIASES = {k: v for k, v in json.load(_f).items() if k != "_comment"}
except (FileNotFoundError, json.JSONDecodeError) as _e:
    print(f"[config] WARNING: could not load country_aliases.json — {_e}")
    _COUNTRY_ALIASES = {}


def get_country_risk(country_name):
    """Look up country risk from internal matrix, resolving aliases.

    Handles Charity Commission area-of-operation names like
    'Throughout England And Wales', 'Throughout England', etc.
    """
    if not country_name:
        return "Unknown"

    # Built-in Charity Commission area-of-operation aliases
    _CC_AREA_ALIASES = {
        "throughout england and wales": "United Kingdom",
        "throughout england": "United Kingdom",
        "throughout wales": "United Kingdom",
        "throughout london": "United Kingdom",
        "throughout scotland": "United Kingdom",
        "throughout northern ireland": "United Kingdom",
        "england": "United Kingdom",
        "wales": "United Kingdom",
        "scotland": "United Kingdom",
        "northern ireland": "United Kingdom",
        "england and wales": "United Kingdom",
    }

    if country_name in COUNTRY_RISK_MATRIX:
        return COUNTRY_RISK_MATRIX[country_name]
    _lower = country_name.strip().lower()
    # Check CC area aliases first
    _cc_canonical = _CC_AREA_ALIASES.get(_lower)
    if _cc_canonical and _cc_canonical in COUNTRY_RISK_MATRIX:
        return COUNTRY_RISK_MATRIX[_cc_canonical]
    for k, v in COUNTRY_RISK_MATRIX.items():
        if k.lower() == _lower:
            return v
    canonical = _COUNTRY_ALIASES.get(_lower)
    if canonical and canonical in COUNTRY_RISK_MATRIX:
        return COUNTRY_RISK_MATRIX[canonical]
    _clean = re.sub(r"\s*\(.*?\)\s*", " ", _lower).strip()
    if _clean != _lower:
        for k, v in COUNTRY_RISK_MATRIX.items():
            if k.lower() == _clean:
                return v
        canonical = _COUNTRY_ALIASES.get(_clean)
        if canonical and canonical in COUNTRY_RISK_MATRIX:
            return COUNTRY_RISK_MATRIX[canonical]
    return "Unclassified"


def is_elevated_risk(risk_level):
    """Return True for High Risk or Very High Risk only.

    'Unclassified' means the system couldn't resolve the name — it should
    NOT auto-flag as elevated (e.g. 'British' would previously trigger this).
    """
    return risk_level in ("High Risk", "Very High Risk")


# ─── SSL CONFIGURATION ──────────────────────────────────────────────────────
def get_ssl_verify():
    ca = os.getenv("CORP_CA_BUNDLE", "").strip()
    insecure = os.getenv("ALLOW_INSECURE_SSL", "false").strip().lower() in {"1", "true", "yes", "y"}
    if ca:
        return ca
    if insecure:
        return False
    return True


_ssl_verify = get_ssl_verify()
if _ssl_verify is False:
    ssl._create_default_https_context = ssl._create_unverified_context
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─── LLM CLIENTS ────────────────────────────────────────────────────────────
if GEMINI_API_KEY:
    _genai_opts = {}
    if _ssl_verify is False:
        _genai_opts["http_options"] = HttpOptions(httpx_client=httpx.Client(verify=False))
    gemini_client = genai.Client(api_key=GEMINI_API_KEY, **_genai_opts)
else:
    gemini_client = None

if OPENAI_API_KEY:
    _oai_kwargs = {}
    if _ssl_verify is False:
        _oai_kwargs["http_client"] = httpx.Client(verify=False)
    openai_client = OpenAI(api_key=OPENAI_API_KEY, **_oai_kwargs)
else:
    openai_client = None

tavily = TavilyClient(api_key=TAVILY_API_KEY) if TAVILY_API_KEY else None

LLM_PROVIDERS = {}
if openai_client:
    LLM_PROVIDERS["GPT-4.1 mini  [$0.014/report]"] = ("openai", "gpt-4.1-mini")
    LLM_PROVIDERS["GPT-4.1 nano  [$0.003/report]"] = ("openai", "gpt-4.1-nano")
    LLM_PROVIDERS["GPT-4o mini"] = ("openai", "gpt-4o-mini")
if gemini_client:
    LLM_PROVIDERS["Gemini 2.0 Flash  [free tier]"] = ("gemini", "gemini-2.0-flash")
    LLM_PROVIDERS["Gemini 2.0 Flash-Lite [free tier]"] = ("gemini", "gemini-2.0-flash-lite")
    LLM_PROVIDERS["Gemini 1.5 Flash  [free tier]"] = ("gemini", "gemini-1.5-flash")

# ─── MODEL PRICING (USD per 1 M tokens) ─────────────────────────────────────
MODEL_PRICING = {
    "gpt-4.1-mini":          {"input": 0.40,  "output": 1.60},
    "gpt-4.1-nano":          {"input": 0.10,  "output": 0.40},
    "gpt-4o-mini":           {"input": 0.15,  "output": 0.60},
    "gemini-2.0-flash":      {"input": 0.0,   "output": 0.0},
    "gemini-2.0-flash-lite": {"input": 0.0,   "output": 0.0},
    "gemini-1.5-flash":      {"input": 0.0,   "output": 0.0},
}


def _calc_cost(model_name, prompt_tokens, completion_tokens):
    """Return cost in USD for a single LLM call."""
    p = MODEL_PRICING.get(model_name, {"input": 0.0, "output": 0.0})
    return (prompt_tokens * p["input"] + completion_tokens * p["output"]) / 1_000_000

# ─── CSS PATH (for app.py to load) ──────────────────────────────────────────
STYLE_CSS_PATH = os.path.join(_PROJECT_DIR, "style.css")
