"""
core/pdf_parser.py — PDF extraction, CC printout parsing, OCR, and
vision-based extraction for Know Your Charity UK.
"""

import re
import io
import os
import base64

import fitz          # PyMuPDF
import pdfplumber

try:
    import pytesseract
    from PIL import Image
    _OCR_AVAILABLE = bool(pytesseract.get_tesseract_version())
except Exception:
    _OCR_AVAILABLE = False

from config import openai_client, _calc_cost


# ═══════════════════════════════════════════════════════════════════════════════
# PDF EXTRACTION — Layout-aware (PyMuPDF) with OCR fallback & section parsing
# ═══════════════════════════════════════════════════════════════════════════════

# Section heading patterns for annual reports / governance documents
_SECTION_HEADINGS = re.compile(
    r"^(?:\d+[\.\)]\s*)?("
    r"foreword|chair(?:'?s|man'?s|woman'?s)?\s+(?:report|statement|introduction)"
    r"|chief executive(?:'s)?\s+(?:report|statement|review)"
    r"|director(?:'s)?\s+(?:report|statement|review)"
    r"|governance|corporate governance"
    r"|trustee(?:s)?(?:'s|')?\s+(?:report|annual report|statement)"
    r"|strategic report"
    r"|risk(?:s)?(?:\s+management)?"
    r"|principal risks"
    r"|financial(?:\s+(?:statements?|review|summary|highlights))?"
    r"|statement of financial activit"
    r"|balance sheet"
    r"|notes? to the (?:financial\s+)?(?:accounts?|statements?)"
    r"|independent (?:examiner|auditor)(?:'s)?\s+report"
    r"|partner(?:s|ship)?"
    r"|our (?:work|impact|programmes?|projects?)"
    r"|safeguard"
    r"|how we (?:work|operate|raise funds)"
    r"|where we (?:work|operate)"
    r"|fund(?:raising|s)"
    r"|remuneration"
    r"|employ(?:ee|ment)"
    r"|volunteer"
    r"|reserves?"
    r"|grant(?:\s+making)?(?:\s+polic)?"
    r"|related part(?:y|ies)"
    r"|objects? (?:and|&) activit"
    r"|structure(?:,?\s*governance)?"
    r"|achievements? (?:and|&) performance"
    r"|plans? for (?:the )?future"
    r")\b",
    re.IGNORECASE | re.MULTILINE,
)

# Partnership context phrases for heuristic NER
_PARTNER_CONTEXT_PHRASES = [
    "worked with", "working with", "partner with", "partnered with",
    "partnering with", "in partnership with", "in collaboration with",
    "in cooperation with", "supported by", "grant to", "grants to",
    "funding to", "support to", "delivered by", "implemented by",
    "sub-grant", "sub-recipient", "implementing partner",
    "delivery partner", "local partner", "national society",
    "national societies", "memorandum of understanding", "mou with",
    "consortium", "alliance with", "joint programme with",
    "contract with", "agreement with", "awarded to",
]
_PARTNER_CONTEXT_RE = re.compile(
    r"(?:" + "|".join(re.escape(p) for p in _PARTNER_CONTEXT_PHRASES) + r")\s+",
    re.IGNORECASE,
)


def _ocr_page(page_pixmap):
    """Run OCR on a PyMuPDF pixmap. Returns text or empty string."""
    if not _OCR_AVAILABLE:
        return ""
    try:
        img = Image.frombytes("RGB",
                              (page_pixmap.width, page_pixmap.height),
                              page_pixmap.samples)
        return pytesseract.image_to_string(img, lang="eng") or ""
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════════════════
#  CC Printout Parser — extract structured fields from CC register PDF
# ═══════════════════════════════════════════════════════════════════════

def parse_cc_printout(file_bytes: bytes) -> dict:
    """Parse a Charity Commission register printout PDF into structured data.

    Returns a dict with keys mirroring what the CC API provides, plus extras
    only available in the printout (declared policies, trustee details with
    appointment dates, financial history breakdown, charitable objects, etc.).
    Returns empty dict on failure.
    """
    import re as _re

    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception:
        return {}

    # Concatenate all page text
    full_text = ""
    for page in doc:
        full_text += page.get_text("text", sort=True) + "\n"
    doc.close()

    if len(full_text.strip()) < 100:
        return {}

    result: dict = {"_source": "cc_printout", "_raw_length": len(full_text)}

    # ── Charity name & number ──────────────────────────────────────
    m = _re.search(r"Charity number:\s*(\d{6,8})", full_text)
    if m:
        result["charity_number"] = m.group(1)

    # Name is typically the first substantial line after "Search" / header
    lines = [l.strip() for l in full_text.split("\n") if l.strip()]
    for line in lines:
        if (len(line) > 3 and line.upper() == line and
                "CHARITY" not in line and "SEARCH" not in line and
                "HOME" not in line and "LOG IN" not in line and
                "COMMISSION" not in line and
                not line.startswith("http") and
                not _re.match(r"\d+/\d+/\d+", line) and
                not _re.match(r"\d+\s*$", line) and
                "PAGE" not in line):
            result["charity_name"] = line.title()
            break

    # ── Reporting status ───────────────────────────────────────────
    if "reporting is" in full_text.lower() and "up to date" in full_text.lower():
        result["reporting_up_to_date"] = True
    elif "overdue" in full_text.lower():
        result["reporting_up_to_date"] = False

    # ── Income & expenditure (overview) ────────────────────────────
    m = _re.search(r"Total income:\s*£([\d,\.]+(?:k|m)?)", full_text, _re.IGNORECASE)
    if m:
        result["total_income_display"] = "£" + m.group(1)
        result["total_income"] = _parse_cc_amount(m.group(1))
    m = _re.search(r"Total expenditure:\s*£([\d,\.]+(?:k|m)?)", full_text, _re.IGNORECASE)
    if m:
        result["total_expenditure_display"] = "£" + m.group(1)
        result["total_expenditure"] = _parse_cc_amount(m.group(1))

    # ── Activities description ─────────────────────────────────────
    m = _re.search(
        r"(?:Activities\s*[-–—]\s*how the charity spends its money|"
        r"What the charity does)(.*?)(?:Income and expenditure|People|$)",
        full_text, _re.DOTALL | _re.IGNORECASE)
    if m:
        desc = m.group(1).strip()
        # Clean up
        desc = _re.sub(r"\n{2,}", "\n", desc).strip()
        if len(desc) > 10:
            result["activities_description"] = desc

    # ── People: trustees, employees, volunteers ────────────────────
    m = _re.search(r"(\d+)\s*Trustee\(s\)", full_text)
    if m:
        result["trustee_count"] = int(m.group(1))
    m = _re.search(r"(\d+)\s*Volunteer\(s\)", full_text)
    if m:
        result["volunteer_count"] = int(m.group(1))
    m = _re.search(r"(\d+)\s*Employee\(s\)", full_text)
    if m:
        result["employee_count"] = int(m.group(1))

    # Employees over £60k
    if "no employees have total benefits over £60k" in full_text.lower():
        result["employees_over_60k"] = False
    elif _re.search(r"employees? with total benefits over £60", full_text, _re.IGNORECASE):
        result["employees_over_60k"] = True

    # ── Fundraising ────────────────────────────────────────────────
    if "no information available" in full_text.lower():
        result["fundraising_info"] = "No information available"
    m = _re.search(r"Fundraising\s+(.*?)(?:Trading|Trustee payment)", full_text, _re.DOTALL)
    if m:
        fr_text = m.group(1).strip()
        if fr_text and "no information" not in fr_text.lower():
            result["fundraising_info"] = fr_text[:500]

    # ── Trading subsidiaries ───────────────────────────────────────
    if "does not have any trading subsidiaries" in full_text.lower():
        result["trading_subsidiaries"] = False
    elif "trading subsidiar" in full_text.lower():
        result["trading_subsidiaries"] = True

    # ── Trustee payments ───────────────────────────────────────────
    if "no trustees receive" in full_text.lower() and "remuneration" in full_text.lower():
        result["trustee_payments"] = False
    else:
        result["trustee_payments"] = True

    # ── What / Who / How / Where ───────────────────────────────────
    _classification_fields = {
        "what_the_charity_does": r"What the charity\s*does:\s*(.*?)(?:Who the charity|How the charity|Where the charity|$)",
        "who_the_charity_helps": r"Who the charity\s*helps:\s*(.*?)(?:How the charity|Where the charity|$)",
        "how_the_charity_helps": r"How the charity\s*helps:\s*(.*?)(?:Where the charity|Main way|$)",
        "where_the_charity_operates": r"Where the charity\s*operates:\s*(.*?)(?:Governance|Trustees|Financial|People|$)",
    }
    # Footer / header lines injected by browser print-to-PDF
    _junk_line_re = _re.compile(
        r"^\d+/\d+/\d+|^https?://|^\d+/\d+$|charitycommission\.gov\.uk",
        _re.IGNORECASE,
    )
    for key, pattern in _classification_fields.items():
        m = _re.search(pattern, full_text, _re.DOTALL | _re.IGNORECASE)
        if m:
            items = [
                l.strip() for l in m.group(1).strip().split("\n")
                if l.strip() and not _junk_line_re.search(l.strip())
            ]
            result[key] = items

    # Main purpose method
    m = _re.search(r"Main way of carrying out purposes is\s*(.*?)(?:What the charity|$)",
                   full_text, _re.DOTALL | _re.IGNORECASE)
    if m:
        result["main_purpose_method"] = m.group(1).strip().split("\n")[0].strip()

    # ── Governance ─────────────────────────────────────────────────
    # Registration history — the CC printout splits label across lines:
    # "Registration        26 July 2016: CIO registration\nhistory:"
    # So we search for the date+event near "Registration" keyword
    _gov_block = _re.search(
        r"Governance(.*?)(?:Trustees\b|Financial history|Contact information|$)",
        full_text, _re.DOTALL | _re.IGNORECASE)
    _gov_text = _gov_block.group(1) if _gov_block else full_text

    rh_entries = _re.findall(
        r"(\d{1,2}\s+\w+\s+\d{4})\s*:\s*([A-Za-z][\w\s]+?)(?:\n|$)",
        _gov_text)
    if rh_entries:
        result["registration_history"] = [
            {"date": d.strip(), "event": e.strip()} for d, e in rh_entries
        ]

    m = _re.search(r"Organisation\s*type:\s*(\w[\w\s\-]*?)(?:\n|Other names)",
                   full_text, _re.IGNORECASE)
    if m:
        result["organisation_type"] = m.group(1).strip()

    m = _re.search(r"Other names:\s*(.*?)(?:Gift aid|Policies|$)",
                   full_text, _re.DOTALL | _re.IGNORECASE)
    if m:
        names_text = m.group(1).strip()
        if "no other names" not in names_text.lower():
            result["other_names"] = [n.strip() for n in names_text.split("\n") if n.strip()]

    # Gift aid
    if "recognised by hmrc for gift aid" in full_text.lower():
        result["gift_aid"] = True
    elif "not recognised" in full_text.lower():
        result["gift_aid"] = False

    # Other regulators
    m = _re.search(r"Other regulators:\s*(.*?)(?:Policies|Land|$)",
                   full_text, _re.DOTALL | _re.IGNORECASE)
    if m:
        reg_text = m.group(1).strip()
        if "no information" not in reg_text.lower():
            result["other_regulators"] = reg_text

    # ── Declared Policies (critical for compliance) ────────────────
    m = _re.search(r"Policies:\s*(.*?)(?:Land and|Contact information|Trustees\b|Financial history|$)",
                   full_text, _re.DOTALL | _re.IGNORECASE)
    if m:
        policies_block = m.group(1).strip()
        # Join lines that are continuations (e.g. "Bullying and harassment policy and\nprocedures")
        raw_lines = []
        for line in policies_block.split("\n"):
            line = line.strip()
            if not line or len(line) <= 2:
                continue
            if "this charity" in line.lower():
                continue
            raw_lines.append(line)
        # Merge continuation lines: if a line is lowercase-starting or is a
        # short word like "procedures", merge with previous
        policies = []
        for line in raw_lines:
            if (policies and
                (line[0].islower() or
                 line.lower() in ("procedures", "and procedures", "policy",
                                   "and policy", "controls", "framework"))):
                policies[-1] = policies[-1] + " " + line
            else:
                policies.append(line)
        # Clean up trailing fragments
        policies = [p.strip() for p in policies if len(p.strip()) > 5]
        if policies:
            result["declared_policies"] = policies

    # ── Land & Property ────────────────────────────────────────────
    if "does not own and/or lease land or property" in full_text.lower():
        result["land_property"] = False
    elif "land and property" in full_text.lower():
        result["land_property"] = True

    # ── Trustees detailed list ─────────────────────────────────────
    # With sort=True the trustee table renders as single lines:
    #   "Anawar ul Hassan            Trustee  22 July 2021      None on record"
    # Search the entire text for lines matching this pattern instead
    # of trying to isolate a section block.
    trustees_list = []
    _trustee_line_re = _re.compile(
        r"^\s*(.+?)\s{2,}Trustee\s+(\d{1,2}\s+\w+\s+\d{4})",
        _re.MULTILINE | _re.IGNORECASE)
    seen_names: set[str] = set()
    for tm in _trustee_line_re.finditer(full_text):
        raw_name = tm.group(1).strip()
        appt_date = tm.group(2).strip()
        # Check the line immediately after for name continuation.
        # In the sorted text, wrapped names (e.g. "AMIN ALI OLIVER" /
        # "DESMOND BUXTON") appear on the next line without a "Trustee" marker.
        # The continuation line may also contain OTHER table columns separated
        # by 2+ spaces, so take only the first column.
        after_pos = full_text.find("\n", tm.end())
        if after_pos > 0:
            next_line = full_text[after_pos + 1:].split("\n", 1)[0].strip()
            # Split by 2+ spaces to isolate the first table column
            col_parts = _re.split(r"\s{2,}", next_line)
            next_name_part = col_parts[0].strip() if col_parts else ""
            # Accept as continuation if it's a short uppercase/titlecase
            # fragment and doesn't contain "Trustee", dates, or known junk.
            if (next_name_part and len(next_name_part) < 40
                    and not _re.search(r"\bTrustee\b", next_name_part, _re.IGNORECASE)
                    and not _re.search(r"\d{1,2}\s+\w+\s+\d{4}", next_name_part)
                    and not next_name_part.startswith("http")
                    and not _re.match(r"\d+/\d+", next_name_part)
                    and "none on" not in next_name_part.lower()
                    and "record" not in next_name_part.lower()
                    and "received" not in next_name_part.lower()
                    and "on time" not in next_name_part.lower()
                    and "trustees are" not in next_name_part.lower()
                    and next_name_part[0].isupper()):
                raw_name = raw_name + " " + next_name_part
        full_name = " ".join(raw_name.split()).title()
        if full_name not in seen_names and len(full_name) > 2:
            seen_names.add(full_name)
            trustees_list.append({
                "name": full_name,
                "appointment_date": appt_date,
                "role": "Trustee",
            })

    if trustees_list:
        result["trustees_detailed"] = trustees_list

    # ── Financial history table ────────────────────────────────────
    # Extract year columns and amounts per category.
    # Parse line-by-line to avoid region-overlap contamination between
    # adjacent categories.
    fin_section = _re.search(
        r"Financial history(.*?)(?:Accounts and annual|Contact information|$)",
        full_text, _re.DOTALL | _re.IGNORECASE)
    if fin_section:
        fin_block = fin_section.group(1)
        # Collapse whitespace within each line for easier matching
        fin_clean = _re.sub(r"[ \t]+", " ", fin_block)
        # Find year-end dates
        years = _re.findall(r"(\d{2}/\d{2}/\d{4})", fin_clean)
        if years:
            result["financial_years_available"] = list(dict.fromkeys(years))[:10]

        # ── Line-by-line category → amounts mapping ──
        amount_re = _re.compile(r"£([\d,\.]+(?:k|m)?)", _re.IGNORECASE)
        na_re = _re.compile(r"^N/?A$", _re.IGNORECASE)
        date_re = _re.compile(r"^\d{2}/\d{2}/\d{4}$")

        # Category label → canonical key  (order matters — first match wins)
        _cat_labels = [
            ("total_gross_income",           ["total gross income"]),
            ("total_expenditure",            ["total expenditure"]),
            ("govt_contracts",               ["income from government contracts"]),
            ("govt_grants",                  ["income from government grants"]),
            ("income_donations",             ["income - donations", "donations and legacies"]),
            ("income_trading",               ["income - other trading", "other trading activities"]),
            ("income_charitable",            ["income - charitable"]),
            ("income_endowments",            ["income - endowments"]),
            ("income_investment",            ["income - investment"]),
            ("income_other",                 ["income - other"]),
            ("income_legacies",              ["income - legacies"]),
            ("expenditure_charitable",       ["expenditure - charitable"]),
            ("expenditure_raising_funds",    ["expenditure - raising"]),
            ("expenditure_governance",       ["expenditure - governance"]),
            ("expenditure_grants",           ["expenditure - grants"]),
            ("expenditure_investment_mgmt",  ["expenditure - investment management"]),
            ("expenditure_other",            ["expenditure - other"]),
        ]

        def _match_cat_label(text_low: str):
            """Return canonical key if *text_low* matches a category label."""
            for cat_key, labels in _cat_labels:
                for lab in labels:
                    if lab in text_low:
                        return cat_key
            return None

        # With sort=True, each category line often contains both the label
        # AND all of its £-amounts / N/A values on a single line, e.g.
        #   "Total gross income £447.45k £697.82k £683.05k £558.55k £463.31k"
        # Some labels wrap across two lines ("Expenditure - Charitable" / "activities").
        fin_lines = [l.strip() for l in fin_clean.split("\n") if l.strip()]
        financial_breakdown: dict[str, list[float]] = {}
        pending_label = ""  # accumulate multi-line category labels

        for fl in fin_lines:
            # Skip header / date-only rows / footer junk
            if date_re.match(fl) or fl.lower() in (
                    "financial period end date", "income / expenditure",
                    "financial history"):
                continue
            if (_re.match(r"\d+/\d+/\d+,", fl) or fl.startswith("http")
                    or _re.match(r"\d+/\d+$", fl)):
                continue

            # Pull out all £-amounts from the line
            amounts_on_line = amount_re.findall(fl)
            # Strip amounts and N/A tokens to isolate the label portion
            label_part = amount_re.sub("", fl)
            label_part = _re.sub(r"\bN/?A\b", "", label_part, flags=_re.IGNORECASE)
            label_part = label_part.strip()

            # Try to identify the category (possibly combining with pending)
            combined = (pending_label + " " + label_part).strip().lower() if pending_label else label_part.lower()
            cat = _match_cat_label(combined)

            if cat:
                if amounts_on_line:
                    financial_breakdown[cat] = [
                        _parse_cc_amount(a) for a in amounts_on_line
                    ]
                pending_label = ""
            elif label_part:
                # Could be a continuation word ("contracts", "activities", etc.)
                pending_label = combined
            # If only amounts/N/A with no label text, skip

        if financial_breakdown:
            result["financial_breakdown"] = financial_breakdown

    # ── Accounts & annual returns filing history ───────────────────
    filing_section = _re.search(
        r"Accounts and annual returns(.*?)(?:Contact information|Governing document|$)",
        full_text, _re.DOTALL | _re.IGNORECASE)
    if filing_section:
        fblock = filing_section.group(1)
        on_time_count = fblock.lower().count("on time")
        late_count = fblock.lower().count("late")
        result["filing_history"] = {
            "on_time_count": on_time_count,
            "late_count": late_count,
            "all_on_time": late_count == 0 and on_time_count > 0,
        }

    # ── Contact information ────────────────────────────────────────
    m = _re.search(r"Address:\s*(.*?)(?:Phone:|Email:|Website:|$)",
                   full_text, _re.DOTALL | _re.IGNORECASE)
    if m:
        addr_lines = [l.strip() for l in m.group(1).strip().split("\n") if l.strip()]
        result["address"] = ", ".join(addr_lines)
        # Extract postcode
        pc = _re.search(r"[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}", " ".join(addr_lines))
        if pc:
            result["postcode"] = pc.group(0)

    m = _re.search(r"Phone:\s*([\d\s+]+)", full_text)
    if m:
        result["phone"] = m.group(1).strip()

    m = _re.search(r"Email:\s*(\S+@\S+)", full_text)
    if m:
        result["email"] = m.group(1).strip()

    m = _re.search(r"Website:\s*(https?://\S+)", full_text)
    if m:
        result["website"] = m.group(1).strip()

    # ── Governing document ─────────────────────────────────────────
    m = _re.search(r"(?:CIO|Trust|Company|Unincorporated)\s*[-–—]?\s*(?:Foundation|Association)?\s*Registered\s+(\d{2}\s+\w+\s+\d{4})",
                   full_text, _re.IGNORECASE)
    if m:
        result["governing_document_date"] = m.group(1)

    # ── Charitable objects ─────────────────────────────────────────
    m = _re.search(
        r"Charitable objects\s*(.*?)(?:Print charity details|Was this page"
        r"|Contact information|\bEmail:|\bPhone:|\bAddress:|\bWebsite:"
        r"|\d+/\d+/\d+,\s*\d+:\d+|https?://register-of-charities|$)",
        full_text, _re.DOTALL | _re.IGNORECASE)
    if m:
        objects_text = m.group(1).strip()
        # Remove stray "..." ellipsis the CC site adds
        objects_text = _re.sub(r"\.\.\.", "", objects_text)
        # Clean excess whitespace
        objects_text = _re.sub(r"\s+", " ", objects_text).strip()
        if len(objects_text) > 20:
            result["charitable_objects"] = objects_text

    return result


def _parse_cc_amount(amount_str: str) -> float:
    """Convert CC printout amount strings like '447.45k', '1.2m', '275,280' to float."""
    import re as _re
    s = amount_str.replace(",", "").strip()
    m = _re.match(r"([\d.]+)\s*(k|m)?", s, _re.IGNORECASE)
    if not m:
        return 0.0
    val = float(m.group(1))
    suffix = (m.group(2) or "").lower()
    if suffix == "k":
        val *= 1000
    elif suffix == "m":
        val *= 1_000_000
    return val


def extract_pdf_text(file_bytes, max_pages=30, max_chars=15000):
    """Extract text from a PDF using PyMuPDF (layout-aware).

    Falls back to pdfplumber if PyMuPDF fails, and optionally runs OCR
    on pages with very low text content (if tesseract is installed).

    Returns tuple (text, metadata) where metadata is a dict:
        pages_total, pages_extracted, chars_extracted,
        ocr_pages, extraction_quality, sections_detected
    """
    text_parts = []
    total_chars = 0
    ocr_page_count = 0
    page_count = 0
    sections_found = []

    # ── Primary: PyMuPDF (fitz) ──────────────────────────────────────
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        page_count = len(doc)
        for i, page in enumerate(doc[:max_pages]):
            # Extract text with layout preservation (sort=True keeps reading order)
            page_text = page.get_text("text", sort=True) or ""

            # OCR fallback: if page has very little text, try OCR
            if len(page_text.strip()) < 100 and _OCR_AVAILABLE:
                pix = page.get_pixmap(dpi=200)
                ocr_text = _ocr_page(pix)
                if len(ocr_text.strip()) > len(page_text.strip()):
                    page_text = ocr_text
                    ocr_page_count += 1

            text_parts.append(f"--- Page {i+1} ---\n{page_text}")
            total_chars += len(page_text)
            if total_chars > max_chars:
                break
        doc.close()
    except Exception:
        # Fallback: pdfplumber
        text_parts = []
        total_chars = 0
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                page_count = len(pdf.pages)
                for i, page in enumerate(pdf.pages[:max_pages]):
                    page_text = page.extract_text() or ""
                    text_parts.append(f"--- Page {i+1} ---\n{page_text}")
                    total_chars += len(page_text)
                    if total_chars > max_chars:
                        break
        except Exception as e:
            meta = {"pages_total": 0, "pages_extracted": 0,
                    "chars_extracted": 0, "ocr_pages": 0,
                    "extraction_quality": "error",
                    "sections_detected": []}
            return f"[PDF extraction error: {e}]", meta

    # ── Detect total-blank PDFs ──────────────────────────────────────
    raw_text_only = [t for t in text_parts if not t.startswith("--- Page")]
    if not any(t.strip() for t in raw_text_only):
        meta = {"pages_total": page_count,
                "pages_extracted": min(max_pages, page_count),
                "chars_extracted": 0, "ocr_pages": ocr_page_count,
                "extraction_quality": "none",
                "sections_detected": []}
        msg = "[PDF appears to be scanned or image-based — text extraction returned no content."
        if not _OCR_AVAILABLE:
            msg += " OCR not available (install tesseract for OCR support)."
        msg += "]"
        return msg, meta

    full = "\n".join(text_parts)

    # ── Section detection ────────────────────────────────────────────
    for m in _SECTION_HEADINGS.finditer(full):
        heading = m.group(0).strip()[:80]
        if heading not in sections_found:
            sections_found.append(heading)

    # ── Extraction quality assessment ────────────────────────────────
    raw_chars = sum(len(t) for t in raw_text_only)
    pages_extracted = min(max_pages, page_count)
    chars_per_page = raw_chars / max(pages_extracted, 1)

    if chars_per_page > 1000:
        quality = "good"
    elif chars_per_page > 300:
        quality = "partial"
    elif raw_chars > 0:
        quality = "low"
    else:
        quality = "none"

    # ── Low-text warning header ──────────────────────────────────────
    if quality == "low":
        header = (f"[LOW EXTRACTABLE TEXT WARNING: Only {raw_chars:,} characters "
                  f"extracted from {pages_extracted} pages — possible scanned or "
                  f"image-based document. Detailed automated extraction was limited.")
        if not _OCR_AVAILABLE:
            header += " Install tesseract for OCR support."
        header += "]\n\n"
        full = header + full

    if len(full) > max_chars:
        full = full[:max_chars] + "\n[...truncated...]"

    meta = {
        "pages_total": page_count,
        "pages_extracted": pages_extracted,
        "chars_extracted": raw_chars,
        "ocr_pages": ocr_page_count,
        "extraction_quality": quality,
        "sections_detected": sections_found[:30],
    }
    return full, meta


# ═══════════════════════════════════════════════════════════════════════════════
# VISION-BASED PDF EXTRACTION — Sends page images to GPT-4.1-mini vision
# Used as enhanced fallback when text extraction fails (scanned/image PDFs)
# ═══════════════════════════════════════════════════════════════════════════════

_VISION_EXTRACT_PROMPT = (
    "You are an expert document analyst. Extract ALL text content from this "
    "document page image. Preserve the original structure: headings, paragraphs, "
    "bullet points, tables (render as markdown tables), and numbered lists. "
    "Include ALL numbers, names, dates, and financial figures exactly as shown. "
    "If this is a policy document, capture the full policy title, scope, "
    "definitions, and all numbered clauses. "
    "If this is an annual report or accounts page, capture all financial "
    "figures, trustee names, partner organisations, programme details, and "
    "geographic references. "
    "Output ONLY the extracted text — no commentary or explanation."
)


def _render_pdf_page_to_base64(doc, page_idx, dpi=150):
    """Render a single PDF page as a base64-encoded JPEG."""
    page = doc[page_idx]
    pix = page.get_pixmap(dpi=dpi)
    img_bytes = pix.tobytes("jpeg")
    return base64.b64encode(img_bytes).decode("utf-8")


def extract_pdf_with_vision(file_bytes, filename="document.pdf",
                            max_pages=15, progress_callback=None):
    """Extract text from a PDF by sending page images to GPT-4.1-mini vision.

    Args:
        file_bytes: Raw PDF bytes
        filename: Name of the file (for logging)
        max_pages: Max pages to process (cost control)
        progress_callback: Optional callable(page_num, total) for UI updates

    Returns:
        tuple (extracted_text, metadata_dict, vision_cost_usd)
    """
    if not openai_client:
        return "", {"extraction_quality": "error", "reason": "no_openai_client"}, 0.0

    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as e:
        return f"[Vision PDF error: {e}]", {"extraction_quality": "error"}, 0.0

    total_pages = len(doc)
    pages_to_process = min(max_pages, total_pages)
    text_parts = []
    total_prompt_tokens = 0
    total_completion_tokens = 0
    sections_found = []

    # Process pages in batches of up to 4 images per API call (cost efficient)
    _BATCH_SIZE = 4
    for batch_start in range(0, pages_to_process, _BATCH_SIZE):
        batch_end = min(batch_start + _BATCH_SIZE, pages_to_process)
        batch_images = []

        for pg in range(batch_start, batch_end):
            try:
                b64_img = _render_pdf_page_to_base64(doc, pg, dpi=150)
                batch_images.append((pg, b64_img))
            except Exception:
                text_parts.append(f"--- Page {pg + 1} ---\n[Page render failed]")

        if not batch_images:
            continue

        # Build vision message with multiple images
        content_parts = []
        if len(batch_images) > 1:
            content_parts.append({
                "type": "text",
                "text": (f"Extract all text from these {len(batch_images)} document "
                         f"pages (pages {batch_images[0][0]+1}-{batch_images[-1][0]+1}). "
                         f"Separate each page with '--- Page N ---' headers. "
                         + _VISION_EXTRACT_PROMPT),
            })
        else:
            content_parts.append({
                "type": "text",
                "text": (f"Extract all text from this document page "
                         f"(page {batch_images[0][0]+1}). " + _VISION_EXTRACT_PROMPT),
            })

        for pg, b64 in batch_images:
            content_parts.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}",
                    "detail": "high",
                },
            })

        try:
            resp = openai_client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": (
                        "You are a precise document OCR system. Extract text "
                        "faithfully from document images. Preserve structure, "
                        "formatting, tables, and all content accurately."
                    )},
                    {"role": "user", "content": content_parts},
                ],
                temperature=0.1,
                max_tokens=4000,
            )
            batch_text = resp.choices[0].message.content or ""
            usage = resp.usage
            if usage:
                total_prompt_tokens += usage.prompt_tokens
                total_completion_tokens += usage.completion_tokens

            # If batch didn't include page headers, add them
            if len(batch_images) == 1 and not batch_text.startswith("--- Page"):
                batch_text = f"--- Page {batch_images[0][0]+1} ---\n{batch_text}"
            text_parts.append(batch_text)
        except Exception as e:
            for pg, _ in batch_images:
                text_parts.append(f"--- Page {pg + 1} ---\n[Vision extraction failed: {e}]")

        if progress_callback:
            progress_callback(batch_end, pages_to_process)

    doc.close()

    full_text = "\n\n".join(text_parts)

    # Detect sections
    for m in _SECTION_HEADINGS.finditer(full_text):
        heading = m.group(0).strip()[:80]
        if heading not in sections_found:
            sections_found.append(heading)

    # Cost calculation (GPT-4.1-mini pricing)
    vision_cost = _calc_cost("gpt-4.1-mini", total_prompt_tokens,
                             total_completion_tokens)

    raw_chars = len(full_text)
    if raw_chars > 500:
        quality = "good"
    elif raw_chars > 100:
        quality = "partial"
    elif raw_chars > 0:
        quality = "low"
    else:
        quality = "none"

    meta = {
        "pages_total": total_pages,
        "pages_extracted": pages_to_process,
        "chars_extracted": raw_chars,
        "ocr_pages": pages_to_process,  # all pages were vision-processed
        "extraction_quality": quality,
        "sections_detected": sections_found[:30],
        "vision_used": True,
        "vision_prompt_tokens": total_prompt_tokens,
        "vision_completion_tokens": total_completion_tokens,
    }

    return full_text, meta, vision_cost


def extract_pdf_sections(full_text):
    """Split extracted PDF text into named sections based on heading detection.

    Returns dict: {section_name_lower: section_text, ...}
    Also returns '_full' key with the complete text.
    """
    sections = {"_full": full_text}
    # Split on heading-like lines (all-caps or mixed-case headings)
    parts = _SECTION_HEADINGS.split(full_text)
    if len(parts) < 2:
        return sections

    # parts[0] is text before first heading, parts[1] is first heading match,
    # parts[2] is text after first heading, etc.
    for i in range(1, len(parts) - 1, 2):
        heading = parts[i].strip().lower()
        body = parts[i + 1] if (i + 1) < len(parts) else ""
        # Truncate body at next heading or at 8000 chars
        sections[heading] = body[:8000]

    return sections


def extract_partners_from_text(full_text, charity_name=""):
    """Extract potential partner organisations from document text using
    heuristic NER (context phrase + capitalised entity extraction).

    Returns list of dicts: {name, context, confidence}
    """
    if not full_text or len(full_text) < 200:
        return []

    partners = []
    seen_names = set()
    charity_lower = charity_name.lower() if charity_name else ""

    # Pattern: extract capitalised multi-word entity after context phrases
    # e.g. "in partnership with International Federation of Red Cross"
    _entity_after_context = re.compile(
        r"(?:" + "|".join(re.escape(p) for p in _PARTNER_CONTEXT_PHRASES)
        + r")\s+((?:[A-Z][a-zA-Z&''.-]+(?:\s+(?:of|the|and|for|de|du|des|la|le|al|el|di)\s+)?)*"
        r"[A-Z][a-zA-Z&''.-]+)",
        re.MULTILINE,
    )

    for m in _entity_after_context.finditer(full_text):
        name = m.group(1).strip().rstrip(".,;:)")
        # Filter out noise
        if len(name) < 4 or len(name) > 120:
            continue
        if name.lower() in seen_names:
            continue
        if charity_lower and name.lower() in charity_lower:
            continue
        # Skip common false positives
        _skip = {"the", "this", "that", "these", "their", "our", "its",
                 "all", "each", "such", "other", "both", "many", "some",
                 "most", "any", "several", "various", "different"}
        if name.lower().split()[0] in _skip:
            continue

        context_phrase = m.group(0)[:120]
        partners.append({
            "name": name,
            "context": context_phrase,
            "confidence": "high" if len(name.split()) >= 2 else "medium",
        })
        seen_names.add(name.lower())

    # Also look for "National Society/Societies" patterns (common in RCRC)
    _ns_pattern = re.compile(
        r"(?:national societ(?:y|ies)|red cross|red crescent)\s+(?:of\s+|in\s+)?"
        r"((?:[A-Z][a-zA-Z]+\s*){1,4})",
        re.IGNORECASE,
    )
    for m in _ns_pattern.finditer(full_text):
        name = m.group(0).strip().rstrip(".,;:)")
        if name.lower() not in seen_names and len(name) > 5:
            partners.append({
                "name": name,
                "context": m.group(0)[:120],
                "confidence": "medium",
            })
            seen_names.add(name.lower())

    # Deduplicate & limit
    return partners[:50]


def compute_extraction_confidence(metadata_list):
    """Compute overall extraction confidence from a list of PDF metadata dicts.

    Returns dict:
        overall_quality: good | partial | low | none | mixed
        total_pages, total_chars, ocr_pages, all_sections
        recommendation: str (what to tell the analyst)
    """
    if not metadata_list:
        return {
            "overall_quality": "none",
            "total_pages": 0, "total_chars": 0,
            "ocr_pages": 0, "all_sections": [],
            "recommendation": "No documents were processed.",
        }

    total_pages = sum(m.get("pages_total", 0) for m in metadata_list)
    total_chars = sum(m.get("chars_extracted", 0) for m in metadata_list)
    ocr_pages = sum(m.get("ocr_pages", 0) for m in metadata_list)
    all_sections = []
    for m in metadata_list:
        all_sections.extend(m.get("sections_detected", []))
    all_sections = list(dict.fromkeys(all_sections))  # dedupe preserving order

    qualities = [m.get("extraction_quality", "none") for m in metadata_list]

    if all(q == "good" for q in qualities):
        overall = "good"
        rec = "Full text extraction achieved. Analysis reflects comprehensive document content."
    elif all(q == "none" for q in qualities):
        overall = "none"
        rec = ("All uploaded documents appear to be scanned or image-based. "
               "Analysis is based primarily on structured financial summaries and API data. "
               "Manual review of document content is recommended.")
    elif any(q == "low" for q in qualities) or any(q == "none" for q in qualities):
        overall = "low" if all(q in ("low", "none") for q in qualities) else "mixed"
        rec = ("Some documents had limited text extraction (possible scanned or image-based content). "
               "Analysis based on available extracted content and structured financial summaries. "
               "Manual confirmation of governance details is recommended.")
    else:
        overall = "partial"
        rec = ("Text extraction was partially successful. Some content may have been missed. "
               "Analysis reflects available extracted content.")

    return {
        "overall_quality": overall,
        "total_pages": total_pages,
        "total_chars": total_chars,
        "ocr_pages": ocr_pages,
        "all_sections": all_sections[:40],
        "recommendation": rec,
    }


