"""
core/financial_patterns.py — Advanced financial anomaly pattern detection.

Goes beyond simple volatility metrics to detect:
  - Unusual growth/decline rates vs sector norms
  - Expenditure mismatches (spending far above/below income)
  - Sudden structural changes (revenue mix shifts, new cost categories)
  - Benford's Law compliance for income figures
  - Ratio analysis across financial history

Public API:
    detect_advanced_patterns(financial_history, charity_data) → PatternReport
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class FinancialPattern(BaseModel):
    """A single detected financial pattern or anomaly."""
    pattern_type: str = Field(..., description="growth_spike|decline|expenditure_mismatch|ratio_shift|structural_change|benford_anomaly|reserve_concern")
    severity: str = Field("info", description="critical|high|medium|low|info")
    title: str = ""
    description: str = ""
    years_affected: list[str] = Field(default_factory=list)
    metric_name: str = ""
    metric_value: float = 0.0
    threshold: float = 0.0
    confidence: float = Field(0.7, ge=0, le=1)


class TrendAnalysis(BaseModel):
    """Multi-year trend summary."""
    metric: str = ""
    direction: str = Field("stable", description="growing|declining|stable|volatile|insufficient_data")
    avg_annual_change_pct: float = 0.0
    total_change_pct: float = 0.0
    volatility: float = 0.0
    years: list[str] = Field(default_factory=list)
    values: list[float] = Field(default_factory=list)


class PatternReport(BaseModel):
    """Complete financial pattern detection results."""
    patterns: list[FinancialPattern] = Field(default_factory=list)
    income_trend: TrendAnalysis = Field(default_factory=TrendAnalysis)
    expenditure_trend: TrendAnalysis = Field(default_factory=TrendAnalysis)
    surplus_trend: TrendAnalysis = Field(default_factory=TrendAnalysis)
    ratio_history: list[dict[str, Any]] = Field(default_factory=list)
    overall_health: str = Field("unknown", description="healthy|caution|concern|critical|unknown")
    health_score: float = Field(0.5, ge=0, le=1, description="0=critical, 1=healthy")
    summary: str = ""
    pattern_count: int = 0
    critical_count: int = 0
    high_count: int = 0


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _pct_change(old: float, new: float) -> float | None:
    if old == 0:
        return None
    return ((new - old) / abs(old)) * 100


def _compute_trend(years: list[str], values: list[float], metric: str) -> TrendAnalysis:
    """Compute trend analysis for a series of values."""
    if len(values) < 2:
        return TrendAnalysis(metric=metric, direction="insufficient_data", years=years, values=values)

    changes = []
    for i in range(1, len(values)):
        pct = _pct_change(values[i - 1], values[i])
        if pct is not None:
            changes.append(pct)

    avg_change = sum(changes) / len(changes) if changes else 0
    total_pct = _pct_change(values[0], values[-1])

    # Volatility = standard deviation of changes
    if len(changes) >= 2:
        mean = sum(changes) / len(changes)
        variance = sum((c - mean) ** 2 for c in changes) / len(changes)
        volatility = math.sqrt(variance)
    else:
        volatility = 0

    # Determine direction
    if volatility > 40:
        direction = "volatile"
    elif avg_change > 10:
        direction = "growing"
    elif avg_change < -10:
        direction = "declining"
    else:
        direction = "stable"

    return TrendAnalysis(
        metric=metric,
        direction=direction,
        avg_annual_change_pct=round(avg_change, 1),
        total_change_pct=round(total_pct, 1) if total_pct is not None else 0,
        volatility=round(volatility, 1),
        years=years,
        values=values,
    )


def _benford_check(values: list[float]) -> tuple[bool, float]:
    """Basic Benford's Law first-digit check.

    Returns (is_anomalous, deviation_score).
    Not reliable for small datasets (< 20 values).
    """
    if len(values) < 10:
        return False, 0.0

    # Expected Benford distribution for first digit
    expected = {d: math.log10(1 + 1 / d) for d in range(1, 10)}

    # Count first digits
    digit_counts: dict[int, int] = {d: 0 for d in range(1, 10)}
    total = 0
    for v in values:
        abs_v = abs(v)
        if abs_v >= 1:
            first_digit = int(str(abs_v).lstrip("0.")[0])
            if 1 <= first_digit <= 9:
                digit_counts[first_digit] += 1
                total += 1

    if total < 10:
        return False, 0.0

    # Chi-squared-like deviation
    deviation = 0
    for d in range(1, 10):
        observed_pct = digit_counts[d] / total
        expected_pct = expected[d]
        deviation += abs(observed_pct - expected_pct)

    # Deviation > 0.3 is suspicious
    is_anomalous = deviation > 0.30
    return is_anomalous, round(deviation, 3)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN DETECTION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def detect_advanced_patterns(
    financial_history: list[dict[str, Any]],
    charity_data: dict[str, Any] | None = None,
) -> PatternReport:
    """Run comprehensive financial pattern detection.

    Args:
        financial_history: List of dicts with {year, income, expenditure}
        charity_data: Optional charity data for context (latest figures, etc.)
    """
    patterns: list[FinancialPattern] = []
    charity_data = charity_data or {}

    if not financial_history:
        return PatternReport(
            summary="No financial history available for analysis.",
            overall_health="unknown",
        )

    # Sort by year ascending
    sorted_hist = sorted(financial_history, key=lambda x: str(x.get("year", "")))

    years = [str(h.get("year", "")) for h in sorted_hist]
    incomes = [_safe_float(h.get("income")) for h in sorted_hist]
    expenditures = [_safe_float(h.get("expenditure")) for h in sorted_hist]
    surpluses = [inc - exp for inc, exp in zip(incomes, expenditures)]

    # ── Trend analysis ──────────────────────────────────────────────
    income_trend = _compute_trend(years, incomes, "Income")
    expenditure_trend = _compute_trend(years, expenditures, "Expenditure")
    surplus_trend = _compute_trend(years, surpluses, "Surplus/Deficit")

    # ── Ratio history ───────────────────────────────────────────────
    ratio_history = []
    for i, h in enumerate(sorted_hist):
        inc = incomes[i]
        exp = expenditures[i]
        ratio_history.append({
            "year": years[i],
            "income": inc,
            "expenditure": exp,
            "surplus": surpluses[i],
            "spend_ratio": round(exp / inc * 100, 1) if inc > 0 else None,
            "reserve_months": round(surpluses[i] / (exp / 12), 1) if exp > 0 else None,
        })

    # ═══════════════════════════════════════════════════════════════════
    # PATTERN 1: Unusual growth spikes (>50% YoY)
    # ═══════════════════════════════════════════════════════════════════
    for i in range(1, len(incomes)):
        pct = _pct_change(incomes[i - 1], incomes[i])
        if pct is not None and pct > 50 and incomes[i - 1] > 10000:
            sev = "high" if pct > 100 else "medium"
            patterns.append(FinancialPattern(
                pattern_type="growth_spike",
                severity=sev,
                title=f"Income spike: {pct:+.0f}%",
                description=f"Income surged from £{incomes[i-1]:,.0f} to £{incomes[i]:,.0f} "
                            f"({pct:+.0f}%) between {years[i-1]} and {years[i]}. "
                            "Sudden large increases may indicate one-off grants, large donations, "
                            "or financial events that should be investigated.",
                years_affected=[years[i - 1], years[i]],
                metric_name="income_yoy_change_pct",
                metric_value=round(pct, 1),
                threshold=50.0,
                confidence=0.8,
            ))

    # ═══════════════════════════════════════════════════════════════════
    # PATTERN 2: Significant decline (>30% YoY)
    # ═══════════════════════════════════════════════════════════════════
    for i in range(1, len(incomes)):
        pct = _pct_change(incomes[i - 1], incomes[i])
        if pct is not None and pct < -30 and incomes[i - 1] > 10000:
            sev = "high" if pct < -50 else "medium"
            patterns.append(FinancialPattern(
                pattern_type="decline",
                severity=sev,
                title=f"Income decline: {pct:+.0f}%",
                description=f"Income dropped from £{incomes[i-1]:,.0f} to £{incomes[i]:,.0f} "
                            f"({pct:+.0f}%) between {years[i-1]} and {years[i]}. "
                            "Sustained declines may indicate loss of funding, "
                            "operational issues, or changing external conditions.",
                years_affected=[years[i - 1], years[i]],
                metric_name="income_yoy_change_pct",
                metric_value=round(pct, 1),
                threshold=-30.0,
                confidence=0.8,
            ))

    # ═══════════════════════════════════════════════════════════════════
    # PATTERN 3: Expenditure mismatches
    # ═══════════════════════════════════════════════════════════════════
    for i in range(len(incomes)):
        inc, exp = incomes[i], expenditures[i]
        if inc > 0:
            spend_ratio = exp / inc * 100
            if spend_ratio > 120 and exp > 50000:
                patterns.append(FinancialPattern(
                    pattern_type="expenditure_mismatch",
                    severity="high" if spend_ratio > 150 else "medium",
                    title=f"Spending exceeds income by {spend_ratio - 100:.0f}%",
                    description=f"In {years[i]}, expenditure (£{exp:,.0f}) exceeded income "
                                f"(£{inc:,.0f}) by {spend_ratio - 100:.0f}%. This may indicate "
                                "reserve draw-down, accounting timing, or financial distress.",
                    years_affected=[years[i]],
                    metric_name="spend_to_income_ratio",
                    metric_value=round(spend_ratio, 1),
                    threshold=120.0,
                    confidence=0.85,
                ))

    # ═══════════════════════════════════════════════════════════════════
    # PATTERN 4: Consecutive deficits
    # ═══════════════════════════════════════════════════════════════════
    consecutive_deficits = 0
    deficit_years = []
    for i in range(len(surpluses)):
        if surpluses[i] < 0:
            consecutive_deficits += 1
            deficit_years.append(years[i])
        else:
            if consecutive_deficits >= 3:
                patterns.append(FinancialPattern(
                    pattern_type="reserve_concern",
                    severity="high" if consecutive_deficits >= 4 else "medium",
                    title=f"{consecutive_deficits} consecutive years of deficit",
                    description=f"The entity ran deficits for {consecutive_deficits} consecutive "
                                f"years ({', '.join(deficit_years[-consecutive_deficits:])}). "
                                "Prolonged deficits may indicate going-concern risk or "
                                "unsustainable financial model.",
                    years_affected=deficit_years[-consecutive_deficits:],
                    metric_name="consecutive_deficit_years",
                    metric_value=float(consecutive_deficits),
                    threshold=3.0,
                    confidence=0.9,
                ))
            consecutive_deficits = 0
            deficit_years = []

    # Check if final years are still in deficit run
    if consecutive_deficits >= 3:
        patterns.append(FinancialPattern(
            pattern_type="reserve_concern",
            severity="high" if consecutive_deficits >= 4 else "medium",
            title=f"{consecutive_deficits} consecutive years of deficit (ongoing)",
            description=f"Currently in a {consecutive_deficits}-year deficit streak "
                        f"({', '.join(deficit_years[-consecutive_deficits:])}). "
                        "This is an ongoing concern.",
            years_affected=deficit_years[-consecutive_deficits:],
            metric_name="consecutive_deficit_years",
            metric_value=float(consecutive_deficits),
            threshold=3.0,
            confidence=0.9,
        ))

    # ═══════════════════════════════════════════════════════════════════
    # PATTERN 5: Spend ratio shift (structural change)
    # ═══════════════════════════════════════════════════════════════════
    spend_ratios = []
    for i in range(len(incomes)):
        if incomes[i] > 0:
            spend_ratios.append(expenditures[i] / incomes[i] * 100)
        else:
            spend_ratios.append(None)

    for i in range(1, len(spend_ratios)):
        if spend_ratios[i] is not None and spend_ratios[i - 1] is not None:
            shift = abs(spend_ratios[i] - spend_ratios[i - 1])
            if shift > 25:
                patterns.append(FinancialPattern(
                    pattern_type="ratio_shift",
                    severity="medium",
                    title=f"Spend ratio shifted by {shift:.0f} percentage points",
                    description=f"The expenditure-to-income ratio changed from "
                                f"{spend_ratios[i-1]:.0f}% to {spend_ratios[i]:.0f}% "
                                f"between {years[i-1]} and {years[i]}. This may indicate "
                                "a structural change in the entity's financial model.",
                    years_affected=[years[i - 1], years[i]],
                    metric_name="spend_ratio_shift_ppts",
                    metric_value=round(shift, 1),
                    threshold=25.0,
                    confidence=0.75,
                ))

    # ═══════════════════════════════════════════════════════════════════
    # PATTERN 6: Income scale mismatch with governance
    # ═══════════════════════════════════════════════════════════════════
    latest_income = _safe_float(charity_data.get("latest_income"))
    num_trustees = charity_data.get("num_trustees", 0) or 0
    employees = charity_data.get("employees", 0) or 0

    if latest_income > 1_000_000 and employees == 0:
        patterns.append(FinancialPattern(
            pattern_type="structural_change",
            severity="medium",
            title="£1M+ income with no paid staff",
            description=f"Annual income is £{latest_income:,.0f} with zero employees reported. "
                        "This is unusual at this scale and may indicate "
                        "reliance on outsourced management.",
            metric_name="income_per_employee",
            metric_value=latest_income,
            threshold=1_000_000,
            confidence=0.7,
        ))

    if latest_income > 500_000 and num_trustees == 1:
        patterns.append(FinancialPattern(
            pattern_type="structural_change",
            severity="high",
            title="Single trustee for £500K+ charity",
            description=f"Annual income is £{latest_income:,.0f} managed by a single trustee. "
                        "This creates single points of failure in governance oversight.",
            metric_name="trustees_count",
            metric_value=float(num_trustees),
            threshold=2.0,
            confidence=0.85,
        ))

    # ═══════════════════════════════════════════════════════════════════
    # PATTERN 7: Benford's Law check (experimental)
    # ═══════════════════════════════════════════════════════════════════
    all_financial_values = [v for v in incomes + expenditures if v > 0]
    is_anomalous, deviation = _benford_check(all_financial_values)
    if is_anomalous:
        patterns.append(FinancialPattern(
            pattern_type="benford_anomaly",
            severity="low",
            title="Unusual digit distribution in financial figures",
            description=f"First-digit distribution of financial values deviates from "
                        f"Benford's Law (deviation: {deviation:.3f}). This is a statistical "
                        "indicator only and has many innocent explanations.",
            metric_name="benford_deviation",
            metric_value=deviation,
            threshold=0.30,
            confidence=0.40,
        ))

    # ═══════════════════════════════════════════════════════════════════
    # OVERALL HEALTH ASSESSMENT
    # ═══════════════════════════════════════════════════════════════════
    critical_count = len([p for p in patterns if p.severity == "critical"])
    high_count = len([p for p in patterns if p.severity == "high"])
    medium_count = len([p for p in patterns if p.severity == "medium"])

    health_score = 1.0
    health_score -= critical_count * 0.25
    health_score -= high_count * 0.15
    health_score -= medium_count * 0.05
    health_score = max(0.0, min(1.0, health_score))

    if health_score >= 0.8:
        overall_health = "healthy"
    elif health_score >= 0.6:
        overall_health = "caution"
    elif health_score >= 0.3:
        overall_health = "concern"
    else:
        overall_health = "critical"

    # Summary
    if not patterns:
        summary = "No significant financial anomalies detected across available history."
    else:
        parts = []
        if critical_count:
            parts.append(f"{critical_count} critical")
        if high_count:
            parts.append(f"{high_count} high")
        if medium_count:
            parts.append(f"{medium_count} medium")
        summary = f"{len(patterns)} pattern(s) detected ({', '.join(parts)} severity). Overall health: {overall_health}."

    return PatternReport(
        patterns=patterns,
        income_trend=income_trend,
        expenditure_trend=expenditure_trend,
        surplus_trend=surplus_trend,
        ratio_history=ratio_history,
        overall_health=overall_health,
        health_score=round(health_score, 2),
        summary=summary,
        pattern_count=len(patterns),
        critical_count=critical_count,
        high_count=high_count,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# UI HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def render_health_badge(report: PatternReport) -> str:
    """HTML badge for financial health status."""
    color_map = {
        "healthy": "#28a745", "caution": "#ffc107",
        "concern": "#fd7e14", "critical": "#dc3545", "unknown": "#6c757d",
    }
    icon_map = {
        "healthy": "💚", "caution": "⚠️",
        "concern": "🟠", "critical": "🔴", "unknown": "❓",
    }
    color = color_map.get(report.overall_health, "#6c757d")
    icon = icon_map.get(report.overall_health, "❓")

    return f"""
    <div style="display:inline-flex;align-items:center;gap:8px;padding:8px 14px;
                border-radius:8px;background:{color}12;border:1px solid {color}30;margin:4px 0;">
        <span style="font-size:20px;">{icon}</span>
        <div>
            <div style="font-size:13px;font-weight:600;color:{color};">
                Financial Health: {report.overall_health.title()}
            </div>
            <div style="font-size:11px;color:#666;">
                {report.pattern_count} pattern(s) · Score: {int(report.health_score * 100)}/100
            </div>
        </div>
    </div>
    """


def render_patterns_table(report: PatternReport) -> str:
    """HTML table of detected financial patterns."""
    if not report.patterns:
        return "<p style='font-size:13px;color:#28a745;'>✅ No significant patterns detected.</p>"

    sev_colors = {
        "critical": "#dc3545", "high": "#fd7e14",
        "medium": "#ffc107", "low": "#17a2b8", "info": "#6c757d",
    }
    sev_icons = {
        "critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "ℹ️",
    }

    rows = ""
    for p in sorted(report.patterns, key=lambda x: ["critical", "high", "medium", "low", "info"].index(x.severity)):
        color = sev_colors.get(p.severity, "#6c757d")
        icon = sev_icons.get(p.severity, "")
        conf_bar = f'<div style="width:60px;height:4px;background:#eee;border-radius:2px;"><div style="width:{int(p.confidence*100)}%;height:4px;background:{color};border-radius:2px;"></div></div>'
        rows += f"""
        <tr style="border-bottom:1px solid #f0f0f0;">
            <td style="padding:8px 6px;font-size:12px;">{icon}</td>
            <td style="padding:8px 6px;font-size:12px;font-weight:600;">{p.title}</td>
            <td style="padding:8px 6px;font-size:11px;color:#555;max-width:400px;">{p.description[:200]}</td>
            <td style="padding:8px 6px;font-size:11px;color:#888;">{', '.join(p.years_affected) if p.years_affected else '—'}</td>
            <td style="padding:8px 6px;">{conf_bar}</td>
        </tr>"""

    return f"""
    <table style="width:100%;border-collapse:collapse;">
        <thead>
            <tr style="border-bottom:2px solid #ddd;">
                <th style="padding:6px;font-size:11px;text-align:left;width:30px;"></th>
                <th style="padding:6px;font-size:11px;text-align:left;">Pattern</th>
                <th style="padding:6px;font-size:11px;text-align:left;">Detail</th>
                <th style="padding:6px;font-size:11px;text-align:left;">Years</th>
                <th style="padding:6px;font-size:11px;text-align:left;">Confidence</th>
            </tr>
        </thead>
        <tbody>{rows}</tbody>
    </table>
    """
