"""
core/risk_engine.py — Governance assessment, structural anomaly detection,
and financial anomaly analysis for Know Your Charity UK.
"""

from datetime import datetime

from config import get_country_risk, is_elevated_risk
from api_clients.charity_commission import _ORG_TYPE_INFO

def assess_governance_indicators(cc_gov: dict, charity_data: dict,
                                 ch_data: dict | None) -> dict:
    """Produce risk indicators from governance intelligence.

    Returns dict with:
      - org_type_info: dict with full_name, description, risk_note
      - ch_consistency: whether CH link status matches org type expectation
      - gift_aid_flag: 'ok' | 'warning' | 'unknown'
      - name_change_flag: 'ok' | 'multiple' | 'none'
      - policy_declared_count: int
      - reg_history_flags: list of notable events
      - years_registered: int | None
    """
    indicators = {
        "org_type_info": {},
        "ch_consistency": None,
        "gift_aid_flag": "unknown",
        "name_change_flag": "none",
        "policy_declared_count": 0,
        "reg_history_flags": [],
        "years_registered": None,
    }

    # Organisation type analysis
    org_type = cc_gov.get("organisation_type") or charity_data.get("charity_type", "")
    org_info = _ORG_TYPE_INFO.get(org_type, {})
    indicators["org_type_info"] = org_info

    # CH consistency check
    ch_required = org_info.get("ch_required", False)
    has_ch = bool(ch_data) or bool((charity_data.get("company_number") or "").strip())
    if ch_required and not has_ch:
        indicators["ch_consistency"] = "Charitable company should have CH registration but none found"
    elif not ch_required and has_ch:
        indicators["ch_consistency"] = "Unexpected CH registration for this org type — verify"
    elif ch_required and has_ch:
        indicators["ch_consistency"] = "CH registration confirmed (expected for charitable company)"
    else:
        indicators["ch_consistency"] = f"No CH registration (not required for {org_type or 'this type'})"

    # Gift Aid
    ga = cc_gov.get("gift_aid", "")
    if ga and "recognised" in ga.lower() and "not" not in ga.lower():
        indicators["gift_aid_flag"] = "ok"
    elif ga and ("not recognised" in ga.lower() or "removed" in ga.lower()):
        indicators["gift_aid_flag"] = "warning"
    else:
        indicators["gift_aid_flag"] = "unknown"

    # Other names
    names_list = cc_gov.get("other_names", [])
    if len(names_list) >= 3:
        indicators["name_change_flag"] = "multiple"
    elif names_list:
        indicators["name_change_flag"] = "some"
    else:
        indicators["name_change_flag"] = "none"

    # Declared policies count
    indicators["policy_declared_count"] = len(cc_gov.get("cc_declared_policies", []))

    # Registration history flags
    for ev in cc_gov.get("registration_history", []):
        event_type = ev.get("event", "")
        if any(kw in event_type.lower() for kw in ["removed", "merged", "restoration",
                                                     "re-registered"]):
            indicators["reg_history_flags"].append(event_type)

    # Years registered
    reg_date = charity_data.get("date_of_registration")
    if reg_date:
        try:
            # Handle various date formats
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d %B %Y"):
                try:
                    dt = datetime.strptime(str(reg_date)[:19], fmt)
                    indicators["years_registered"] = (datetime.now() - dt).days // 365
                    break
                except ValueError:
                    continue
        except Exception:
            pass

    return indicators


def assess_structural_governance(charity_data, ch_data, trustees,
                                  trustee_appointments=None):
    """Detect structural governance anomalies.

    Returns dict with:
      - capacity_flags: list of observation strings
      - trustee_directorships: dict of name -> {count, entities}
      - concentration_flags: list of observation strings
      - summary: overall assessment string
    """
    flags = []
    concentration = []
    trustee_directorships = {}

    inc = charity_data.get("latest_income") or 0
    exp = charity_data.get("latest_expenditure") or 0
    num_trustees = charity_data.get("num_trustees") or len(trustees or [])
    employees = charity_data.get("employees") or 0
    volunteers = charity_data.get("volunteers") or 0
    entity_name = charity_data.get("charity_name", "")

    # ── Income vs Trustee capacity ──
    if inc >= 1_000_000 and num_trustees <= 3:
        flags.append(
            f"Annual income is £{inc:,.0f} with only {num_trustees} trustee(s). "
            f"For charities of this income scale, a small trustee board may "
            f"limit the breadth of oversight, skills diversity, and succession "
            f"planning capacity. This is an observation for the analyst to "
            f"explore — it does not imply wrongdoing."
        )
    elif inc >= 500_000 and num_trustees <= 2:
        flags.append(
            f"Annual income is £{inc:,.0f} with only {num_trustees} trustee(s). "
            f"A board of 2 or fewer limits quorum capacity and independent "
            f"oversight. Consider whether the charity has adequate governance "
            f"structures for its financial scale."
        )

    # ── Income vs Employees ──
    if inc >= 1_000_000 and employees == 0:
        flags.append(
            f"Annual income is £{inc:,.0f} with no paid employees reported. "
            f"A purely volunteer-run charity at this income level may face "
            f"capacity constraints in financial management, compliance, and "
            f"operational delivery. Verify whether the charity uses external "
            f"service providers or seconded staff."
        )
    elif inc >= 500_000 and employees == 0:
        flags.append(
            f"Annual income is £{inc:,.0f} with no paid employees. "
            f"Consider whether volunteer-only governance and operations "
            f"are proportionate to income."
        )

    # ── Expenditure vs Income with tiny board ──
    if inc > 0 and exp > 0:
        spend_pct = exp / inc * 100
        if spend_pct > 95 and num_trustees <= 3 and inc >= 500_000:
            flags.append(
                f"High spend-to-income ratio ({spend_pct:.0f}%) combined with a "
                f"small board ({num_trustees} trustees). Financial headroom is "
                f"limited, increasing dependency on continuous income flow."
            )

    # ── Trustee directorships analysis ──
    if trustee_appointments:
        for name, appts in trustee_appointments.items():
            # Exclude the current charity's own company from count
            co_num = (charity_data.get("company_number") or "").strip()
            other = [a for a in appts if a.get("company_number") != co_num]
            if other:
                trustee_directorships[name] = {
                    "count": len(other),
                    "entities": [
                        {
                            "company_name": a.get("company_name", ""),
                            "company_number": a.get("company_number", ""),
                            "company_status": a.get("company_status", ""),
                            "officer_role": a.get("officer_role", ""),
                        }
                        for a in other[:15]  # cap output size
                    ],
                }
                if len(other) >= 3:
                    entity_names = [a.get("company_name", "?") for a in other[:5]]
                    concentration.append(
                        f"{name} holds {len(other)} other active directorship(s)/officership(s) "
                        f"(including: {', '.join(entity_names)}"
                        f"{'...' if len(other) > 5 else ''}). "
                        f"Multiple directorships may indicate governance concentration "
                        f"or time-capacity considerations — this is noted for the analyst "
                        f"to assess contextually, not as an indication of misconduct."
                    )

    # ── Cross-trustee overlap ──
    if trustee_appointments and len(trustee_appointments) >= 2:
        # Check if multiple trustees share directorships at the same entity
        entity_to_trustees = {}
        co_num = (charity_data.get("company_number") or "").strip()
        for name, appts in trustee_appointments.items():
            for a in appts:
                aco = a.get("company_number", "")
                if aco and aco != co_num:
                    entity_to_trustees.setdefault(aco, set()).add(name)
        for aco, tset in entity_to_trustees.items():
            if len(tset) >= 2:
                co_name = ""
                for name in tset:
                    for a in trustee_appointments.get(name, []):
                        if a.get("company_number") == aco:
                            co_name = a.get("company_name", aco)
                            break
                    if co_name:
                        break
                concentration.append(
                    f"Trustees {', '.join(sorted(tset))} share a directorship at "
                    f"{co_name} ({aco}). Shared external relationships between "
                    f"multiple trustees are noted for context."
                )

    # Summary
    total_flags = len(flags) + len(concentration)
    if total_flags == 0:
        summary = "No structural governance anomalies detected."
    elif total_flags <= 2:
        summary = f"{total_flags} governance observation(s) noted — low structural concern."
    else:
        summary = f"{total_flags} governance observation(s) noted — analyst review recommended."

    return {
        "capacity_flags": flags,
        "trustee_directorships": trustee_directorships,
        "concentration_flags": concentration,
        "summary": summary,
        "total_flags": total_flags,
    }


def generate_financial_trend_comment(history):
    """Deterministic 1-2 sentence summary of financial trajectory."""
    if len(history) < 2:
        return ""
    n = len(history)
    incomes = [h["income"] for h in history]
    expenditures = [h["expenditure"] for h in history]
    surpluses = [h["income"] - h["expenditure"] for h in history]

    parts = []

    # Income direction
    inc_first, inc_last = incomes[0], incomes[-1]
    if inc_first > 0:
        inc_change = (inc_last - inc_first) / inc_first
    else:
        inc_change = 0

    if abs(inc_change) < 0.10:
        parts.append(f"Income has remained broadly stable over the past {n} years.")
    elif inc_change > 0:
        parts.append(f"Income has shown growth over the past {n} years "
                     f"(+{inc_change:.0%}).")
    else:
        parts.append(f"Income has declined over the past {n} years "
                     f"({inc_change:.0%}).")

    # Expenditure vs income growth comparison
    exp_first, exp_last = expenditures[0], expenditures[-1]
    if exp_first > 0:
        exp_change = (exp_last - exp_first) / exp_first
    else:
        exp_change = 0

    if exp_change > inc_change + 0.05 and exp_change > 0.10:
        parts.append("Expenditure growth has outpaced income growth in recent years.")

    # Persistent deficits
    deficit_years = sum(1 for s in surpluses if s < 0)
    if deficit_years >= n - 1 and n >= 3:
        parts = [f"Recurring deficits observed across {deficit_years} of the past {n} financial years."]
    elif deficit_years >= 2:
        parts.append(f"Deficits recorded in {deficit_years} of {n} years.")

    return " ".join(parts[:2])


# ─── FINANCIAL ANOMALY DETECTION ─────────────────────────────────────────────
# Thresholds (proportional — expressed as fractions)
_ANOMALY_YOY_JUMP = 0.30        # 30 % year-on-year change
_ANOMALY_RATIO_SHIFT = 0.15     # 15 pp shift in expenditure-to-income ratio
_ANOMALY_VOLATILITY = 0.25      # CV (coefficient of variation) threshold


def detect_financial_anomalies(history):
    """Analyse multi-year financial data and return structured anomaly flags.

    Parameters
    ----------
    history : list[dict]
        Each dict has keys ``year``, ``income``, ``expenditure``.
        Must be sorted ascending by year.

    Returns
    -------
    dict with:
        ``flags``           – list of human-readable observation strings
        ``income_volatility``  – float (coefficient of variation, 0-1+)
        ``expenditure_volatility`` – float
        ``yoy_income``      – list of {year, pct_change} dicts
        ``yoy_expenditure`` – list of {year, pct_change} dicts
        ``ratio_shifts``    – list of {year, ratio, prev_ratio, shift_pp} dicts
        ``anomaly_count``   – int (total flags)
        ``summary``         – 1-sentence plain-English summary
    """
    result = {
        "flags": [],
        "income_volatility": 0.0,
        "expenditure_volatility": 0.0,
        "yoy_income": [],
        "yoy_expenditure": [],
        "ratio_shifts": [],
        "anomaly_count": 0,
        "summary": "",
    }

    if not history or len(history) < 2:
        result["summary"] = "Insufficient multi-year data for anomaly analysis."
        return result

    n = len(history)
    incomes = [h["income"] for h in history]
    expenditures = [h["expenditure"] for h in history]
    years = [h["year"] for h in history]

    # ── Helper: coefficient of variation ─────────────────────────────
    def _cv(values):
        if not values:
            return 0.0
        mean = sum(values) / len(values)
        if mean == 0:
            return 0.0
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        return (variance ** 0.5) / abs(mean)

    # ── 1. Income volatility ────────────────────────────────────────
    inc_cv = _cv(incomes)
    result["income_volatility"] = round(inc_cv, 4)
    if inc_cv > _ANOMALY_VOLATILITY and n >= 3:
        result["flags"].append(
            f"Income volatility is elevated (CV = {inc_cv:.0%}) across "
            f"{n} reporting periods — significant year-on-year variation observed."
        )

    # ── 2. Expenditure volatility ───────────────────────────────────
    exp_cv = _cv(expenditures)
    result["expenditure_volatility"] = round(exp_cv, 4)
    if exp_cv > _ANOMALY_VOLATILITY and n >= 3:
        result["flags"].append(
            f"Expenditure volatility is elevated (CV = {exp_cv:.0%}) across "
            f"{n} reporting periods — significant year-on-year variation observed."
        )

    # ── 3. Year-on-year jumps ───────────────────────────────────────
    for i in range(1, n):
        # Income
        prev_inc = incomes[i - 1]
        curr_inc = incomes[i]
        if prev_inc > 0:
            pct = (curr_inc - prev_inc) / prev_inc
        elif curr_inc > 0:
            pct = 1.0  # from zero to something = 100 %
        else:
            pct = 0.0
        result["yoy_income"].append({"year": years[i], "pct_change": round(pct, 4)})
        if abs(pct) >= _ANOMALY_YOY_JUMP:
            direction = "increase" if pct > 0 else "decrease"
            result["flags"].append(
                f"Significant income {direction} of {abs(pct):.0%} "
                f"between {years[i-1]} and {years[i]}."
            )

        # Expenditure
        prev_exp = expenditures[i - 1]
        curr_exp = expenditures[i]
        if prev_exp > 0:
            pct_e = (curr_exp - prev_exp) / prev_exp
        elif curr_exp > 0:
            pct_e = 1.0
        else:
            pct_e = 0.0
        result["yoy_expenditure"].append({"year": years[i], "pct_change": round(pct_e, 4)})
        if abs(pct_e) >= _ANOMALY_YOY_JUMP:
            direction = "increase" if pct_e > 0 else "decrease"
            result["flags"].append(
                f"Significant expenditure {direction} of {abs(pct_e):.0%} "
                f"between {years[i-1]} and {years[i]}."
            )

    # ── 4. Expenditure-to-income ratio shifts ──────────────────────
    ratios = []
    for i in range(n):
        inc_val = incomes[i] if incomes[i] > 0 else 1
        ratio = expenditures[i] / inc_val
        ratios.append(ratio)

    for i in range(1, n):
        shift = ratios[i] - ratios[i - 1]  # positive = spending grew relative to income
        entry = {
            "year": years[i],
            "ratio": round(ratios[i], 4),
            "prev_ratio": round(ratios[i - 1], 4),
            "shift_pp": round(shift * 100, 1),  # percentage points
        }
        result["ratio_shifts"].append(entry)
        if abs(shift) >= _ANOMALY_RATIO_SHIFT:
            direction = "increased" if shift > 0 else "decreased"
            result["flags"].append(
                f"Expenditure-to-income ratio {direction} by "
                f"{abs(shift)*100:.0f} percentage points between "
                f"{years[i-1]} ({ratios[i-1]:.0%}) and {years[i]} ({ratios[i]:.0%})."
            )

    # ── 5. Persistent deficit check ────────────────────────────────
    surpluses = [incomes[i] - expenditures[i] for i in range(n)]
    deficit_years = sum(1 for s in surpluses if s < 0)
    if deficit_years >= 3 and deficit_years >= n - 1:
        result["flags"].append(
            f"Recurring deficit pattern: expenditure exceeded income in "
            f"{deficit_years} of {n} reporting periods."
        )
    elif deficit_years >= 2:
        result["flags"].append(
            f"Deficits recorded in {deficit_years} of {n} reporting periods."
        )

    # ── 6. Income approaching zero ─────────────────────────────────
    if incomes[-1] > 0 and incomes[-1] < incomes[0] * 0.25 and incomes[0] > 10000:
        result["flags"].append(
            f"Latest income (£{incomes[-1]:,.0f}) is less than 25 % of "
            f"earliest recorded income (£{incomes[0]:,.0f}) — significant decline."
        )

    # ── De-duplicate & summarise ───────────────────────────────────
    # Remove near-duplicate messages
    seen = set()
    unique_flags = []
    for f in result["flags"]:
        key = f[:60]
        if key not in seen:
            seen.add(key)
            unique_flags.append(f)
    result["flags"] = unique_flags
    result["anomaly_count"] = len(unique_flags)

    if not unique_flags:
        result["summary"] = (
            f"No significant financial anomalies detected across {n} reporting periods. "
            "Income and expenditure patterns appear stable."
        )
    elif len(unique_flags) <= 2:
        result["summary"] = (
            f"{len(unique_flags)} observation(s) noted in financial trend analysis. "
            "See individual flags for detail."
        )
    else:
        result["summary"] = (
            f"{len(unique_flags)} observations noted in financial trend analysis across "
            f"{n} reporting periods — review recommended."
        )

    return result


