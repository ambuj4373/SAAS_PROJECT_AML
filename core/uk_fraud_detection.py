"""
uk_fraud_detection.py

Lightweight Fraud Detection for UK Companies (Companies House)

Fraud detection engines for UK company data:
  ✅ Shell Company Detection (from officer analysis, filings, accounts)
  ✅ Behaviour Monitoring (director changes, filing patterns)
  ✅ Director Network Intelligence (officer clustering, circular ownership)
  ✅ Financial Risk Engine (accounts analysis)
  ✅ Legal Event Intelligence (filing history, status changes)
  ✅ Geo & Address Risk (virtual office detection)

Non-breaking: Added as optional "fraud_detection" key in company_check result.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 1. SHELL COMPANY DETECTION (UK)
# ═══════════════════════════════════════════════════════════════════════════

class UKShellCompanyDetector:
    """Detect shell company patterns using Companies House data."""
    
    @staticmethod
    def detect(
        company_name: str,
        incorporation_date: Optional[str],
        status: str,
        officers: List[Dict],
        filing_history: List[Dict],
        accounts_data: Dict,
        virtual_office: Dict,
    ) -> Dict[str, Any]:
        """
        Detect shell company characteristics.
        
        Returns:
            {
                "is_shell_company": bool,
                "shell_risk_score": 0-100,
                "indicators": [str],
                "evidence": [str],
                "company_type": "Startup"|"Established"|"Unknown"
            }
        """
        shell_score = 0
        indicators = []
        evidence = []
        
        # Determine company age
        company_age_months = UKShellCompanyDetector._get_age_months(incorporation_date)
        company_type = "Startup" if company_age_months and company_age_months <= 36 else "Established"
        
        # SIGNAL 1: No officers or only 1 director
        if len(officers) == 0:
            shell_score += 25
            indicators.append("🔴 No officers on file")
            evidence.append("Zero officers recorded")
        elif len(officers) == 1:
            shell_score += 10
            indicators.append("⚠️ Only 1 officer (minimal governance)")
        
        # SIGNAL 2: No accounts filed (for established companies)
        if company_type == "Established" and not accounts_data.get("accounts_filed"):
            shell_score += 20
            indicators.append("🔴 No accounts filed (established company)")
            evidence.append("No financial records in Companies House")
        
        # SIGNAL 3: Virtual office
        if virtual_office.get("is_virtual_office"):
            shell_score += 15
            indicators.append("⚠️ Virtual office address")
            evidence.append(f"Address type: {virtual_office.get('office_type', 'Virtual')}")
        
        # SIGNAL 4: No recent filings (except for startups)
        recent_filings = len([f for f in filing_history if f.get("days_old", 999) < 365])
        if company_type == "Established" and recent_filings == 0:
            shell_score += 20
            indicators.append("🔴 No filings in past 12 months")
            evidence.append("No recent filing activity")
        elif recent_filings < 2 and company_type == "Established":
            shell_score += 10
            indicators.append("⚠️ Minimal filing activity")
        
        # SIGNAL 5: Company age (startup adjustment)
        if company_age_months and company_age_months < 3:
            if company_type == "Startup":
                indicators.append("✓ Very new company (acceptable for startup)")
            else:
                shell_score += 15
                indicators.append("🔴 Very new company (< 3 months)")
        
        # Multiple signals = higher risk
        signal_count = len([i for i in indicators if i.startswith("🔴")])
        is_shell = (company_type == "Startup" and signal_count >= 3) or \
                   (company_type == "Established" and signal_count >= 2) or \
                   shell_score >= 70
        
        return {
            "is_shell_company": is_shell,
            "shell_risk_score": min(100, shell_score),
            "company_type": company_type,
            "age_months": company_age_months,
            "indicators": indicators,
            "evidence": evidence,
            "signal_count": signal_count,
        }
    
    @staticmethod
    def _get_age_months(incorporation_date: Optional[str]) -> Optional[int]:
        """Calculate company age in months."""
        if not incorporation_date:
            return None
        try:
            inc_date = datetime.strptime(incorporation_date[:10], "%Y-%m-%d")
            months = (datetime.now() - inc_date).days // 30
            return months
        except:
            return None


# ═══════════════════════════════════════════════════════════════════════════
# 2. BEHAVIOUR MONITORING (UK)
# ═══════════════════════════════════════════════════════════════════════════

class UKBehaviourMonitoring:
    """Detect behavioral anomalies in filing patterns and officer changes."""
    
    @staticmethod
    def detect(
        filing_history: List[Dict],
        director_analysis: Dict,
        status: str,
    ) -> Dict[str, Any]:
        """
        Detect unusual behavior patterns.
        
        Returns:
            {
                "director_changes": {...},
                "filing_anomalies": [...],
                "behaviour_risk_score": 0-100,
                "alerts": [str]
            }
        """
        alerts = []
        risk_score = 0
        
        # ANALYSIS 1: Director appointment/removal frequency
        director_changes = director_analysis.get("recent_appointments", 0)
        director_removals = director_analysis.get("recent_removals", 0)
        
        if director_changes > 2:
            alerts.append(f"🟡 Multiple recent director appointments ({director_changes})")
            risk_score += 10
        
        if director_removals > 1:
            alerts.append(f"🔴 Multiple recent director removals ({director_removals})")
            risk_score += 15
        
        # ANALYSIS 2: Rapid director turnover
        rapid_turnover = (director_changes + director_removals) > 2
        if rapid_turnover:
            alerts.append("⚠️ Rapid officer turnover")
            risk_score += 12
        
        # ANALYSIS 3: Filing frequency spikes
        filings_90d = len([f for f in filing_history if f.get("days_old", 999) < 90])
        filings_avg = len(filing_history) / max(1, (datetime.now().year - 2000))  # Rough average per year
        
        if filings_90d > filings_avg * 2:
            alerts.append(f"⚠️ Filing spike: {filings_90d} filings in 90 days")
            risk_score += 8
        
        # ANALYSIS 4: Status changes (dissolution, strike-off, etc)
        if status.lower() in ("dissolved", "liquidation", "administration"):
            alerts.append(f"🔴 Company status: {status}")
            risk_score += 25
        
        return {
            "director_changes": director_changes,
            "director_removals": director_removals,
            "rapid_turnover": rapid_turnover,
            "filings_90d": filings_90d,
            "behaviour_risk_score": min(100, risk_score),
            "alerts": alerts,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 3. DIRECTOR NETWORK INTELLIGENCE (UK)
# ═══════════════════════════════════════════════════════════════════════════

class UKDirectorNetwork:
    """Analyze director networks using Companies House officer data."""
    
    @staticmethod
    def detect(
        officers: List[Dict],
        director_analysis: Dict,
        company_num: str,
    ) -> Dict[str, Any]:
        """
        Detect director clustering and network anomalies.
        
        Returns:
            {
                "director_count": int,
                "common_director_count": int,
                "network_risk_score": 0-100,
                "flags": [str]
            }
        """
        flags = []
        risk_score = 0
        
        # Extract common directors (already calculated in company_check)
        common_directors = director_analysis.get("common_director_count", 0)
        total_directors = len(officers)
        
        # SIGNAL 1: Director clustering (same person in many companies)
        if total_directors > 0:
            director_concentration = common_directors / total_directors
            
            if director_concentration > 0.5:
                flags.append(f"🟡 High director concentration: {common_directors}/{total_directors}")
                risk_score += 20
            elif director_concentration > 0.3:
                flags.append(f"⚠️ Some director overlap ({common_directors} common)")
                risk_score += 8
        
        # SIGNAL 2: Single director with many appointments (already in analysis)
        if director_analysis.get("director_max_appointments", 0) > 10:
            flags.append(
                f"🟡 Director with {director_analysis['director_max_appointments']} appointments"
            )
            risk_score += 15
        
        return {
            "director_count": total_directors,
            "common_director_count": common_directors,
            "network_risk_score": min(100, risk_score),
            "flags": flags,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 4. FINANCIAL RISK ENGINE (UK)
# ═══════════════════════════════════════════════════════════════════════════

class UKFinancialRisk:
    """Analyze financial patterns from Companies House accounts."""
    
    @staticmethod
    def detect(accounts_data: Dict) -> Dict[str, Any]:
        """
        Analyze financial risk from accounts.
        
        Returns:
            {
                "revenue_trend": str,
                "profitability": str,
                "accounts_overdue": bool,
                "financial_risk_score": 0-100,
                "anomalies": [str]
            }
        """
        anomalies = []
        risk_score = 0
        
        # Check if accounts filed
        if not accounts_data.get("accounts_filed"):
            return {
                "revenue_trend": "unknown",
                "profitability": "unknown",
                "accounts_overdue": accounts_data.get("accounts_overdue", False),
                "financial_risk_score": 0,
                "anomalies": ["No accounts filed"],
            }
        
        # Accounts overdue
        if accounts_data.get("accounts_overdue"):
            anomalies.append("🔴 Accounts filing overdue")
            risk_score += 20
        
        # Check for losses
        latest_profit = accounts_data.get("latest_profit")
        if latest_profit is not None and latest_profit < 0:
            anomalies.append(f"⚠️ Latest year loss: £{abs(latest_profit):,.0f}")
            risk_score += 10
        
        # Check for declining revenue
        revenue_trend = accounts_data.get("revenue_trend", "unknown")
        if revenue_trend == "declining":
            anomalies.append("⚠️ Revenue declining year-on-year")
            risk_score += 8
        
        # Determine profitability from accounts
        profitability = "unknown"
        if latest_profit is not None:
            profitability = "profitable" if latest_profit > 0 else "losses"
        
        return {
            "revenue_trend": revenue_trend,
            "profitability": profitability,
            "accounts_overdue": accounts_data.get("accounts_overdue", False),
            "financial_risk_score": min(100, risk_score),
            "anomalies": anomalies,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 5. LEGAL EVENT INTELLIGENCE (UK)
# ═══════════════════════════════════════════════════════════════════════════

class UKLegalEvents:
    """Analyze legal events from filing history."""
    
    @staticmethod
    def detect(filing_history: List[Dict]) -> Dict[str, Any]:
        """
        Analyze legal events and status changes.
        
        Returns:
            {
                "status_changes": int,
                "restructuring_events": int,
                "legal_risk_score": 0-100,
                "anomalies": [str]
            }
        """
        anomalies = []
        risk_score = 0
        
        status_changes = 0
        restructuring_events = 0
        
        for filing in filing_history:
            action = filing.get("action", "").lower()
            
            # Count status changes
            if "dissolution" in action or "strike-off" in action:
                status_changes += 1
                anomalies.append(f"🔴 {filing.get('action')}")
            elif "liquidation" in action or "administration" in action:
                status_changes += 1
                anomalies.append(f"🔴 {filing.get('action')}")
            
            # Count restructuring
            if any(x in action for x in ["merger", "transfer", "conversion", "change of name"]):
                restructuring_events += 1
        
        # Risk scoring
        if status_changes > 0:
            risk_score += min(30, status_changes * 15)
        
        if restructuring_events > 3:
            anomalies.append(f"⚠️ Frequent restructuring ({restructuring_events} events)")
            risk_score += 12
        
        return {
            "status_changes": status_changes,
            "restructuring_events": restructuring_events,
            "legal_risk_score": min(100, risk_score),
            "anomalies": anomalies,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 6. GEO & ADDRESS RISK (UK)
# ═══════════════════════════════════════════════════════════════════════════

class UKGeoAddressRisk:
    """Analyze geographic and address patterns."""
    
    @staticmethod
    def detect(
        registered_office: Dict,
        virtual_office: Dict,
        director_analysis: Dict,
    ) -> Dict[str, Any]:
        """
        Analyze geographic risk patterns.
        
        Returns:
            {
                "virtual_office_risk": bool,
                "director_diversity": str,
                "geo_risk_score": 0-100,
                "flags": [str]
            }
        """
        flags = []
        risk_score = 0
        
        # SIGNAL 1: Virtual office
        if virtual_office.get("is_virtual_office"):
            risk_score += 15
            flags.append(f"⚠️ Virtual office: {virtual_office.get('office_type')}")
        
        # SIGNAL 2: High-risk postcode (if available)
        postcode = registered_office.get("postal_code", "")
        if postcode:
            # Check for known high-risk postcode patterns (e.g., serviced office buildings)
            if any(x in postcode.lower() for x in ["sw1a", "ec1a", "sw1p"]):
                # These are government areas, less concerning
                pass
            elif virtual_office.get("is_virtual_office"):
                risk_score += 10
                flags.append("⚠️ Virtual office in high-density business area")
        
        # SIGNAL 3: Director geographic concentration (all same country)
        director_countries = director_analysis.get("director_countries", [])
        if len(director_countries) == 1 and "United Kingdom" not in director_countries:
            risk_score += 12
            flags.append(f"⚠️ All directors from: {director_countries[0]}")
        
        # SIGNAL 4: Multiple directors at same address (not registered office)
        director_diversity = director_analysis.get("director_address_diversity", "Unknown")
        if director_diversity == "Low":
            flags.append("⚠️ Multiple directors at same residential address")
            risk_score += 10
        
        return {
            "virtual_office_risk": virtual_office.get("is_virtual_office", False),
            "director_diversity": director_diversity,
            "geo_risk_score": min(100, risk_score),
            "flags": flags,
        }


# ═══════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════

def run_uk_fraud_detection_suite(
    company_num: str,
    company_name: str,
    incorporation_date: Optional[str],
    status: str,
    officers: List[Dict],
    filing_history: List[Dict],
    accounts_data: Dict,
    virtual_office: Dict,
    director_analysis: Dict,
    registered_office: Dict,
) -> Dict[str, Any]:
    """
    Run UK fraud detection suite using Companies House data.
    
    Non-breaking: Returns fraud analysis dict that can be added to result.
    """
    
    logger.info(f"🔍 Running UK fraud detection for {company_name} ({company_num})")
    
    try:
        # Engine 1: Shell Company Detection
        shell_detection = UKShellCompanyDetector.detect(
            company_name=company_name,
            incorporation_date=incorporation_date,
            status=status,
            officers=officers,
            filing_history=filing_history,
            accounts_data=accounts_data,
            virtual_office=virtual_office,
        )
        
        # Engine 2: Behaviour Monitoring
        behaviour_detection = UKBehaviourMonitoring.detect(
            filing_history=filing_history,
            director_analysis=director_analysis,
            status=status,
        )
        
        # Engine 3: Director Network Intelligence
        director_network = UKDirectorNetwork.detect(
            officers=officers,
            director_analysis=director_analysis,
            company_num=company_num,
        )
        
        # Engine 4: Financial Risk
        financial_risk = UKFinancialRisk.detect(accounts_data=accounts_data)
        
        # Engine 5: Legal Events
        legal_events = UKLegalEvents.detect(filing_history=filing_history)
        
        # Engine 6: Geo & Address Risk
        geo_address_risk = UKGeoAddressRisk.detect(
            registered_office=registered_office,
            virtual_office=virtual_office,
            director_analysis=director_analysis,
        )
        
        # AGGREGATE RESULTS
        fraud_score = (
            (shell_detection["shell_risk_score"] * 0.20) +
            (behaviour_detection["behaviour_risk_score"] * 0.15) +
            (director_network["network_risk_score"] * 0.20) +
            (financial_risk["financial_risk_score"] * 0.15) +
            (legal_events["legal_risk_score"] * 0.15) +
            (geo_address_risk["geo_risk_score"] * 0.15)
        )
        
        # Determine risk level
        if fraud_score >= 70:
            risk_level = "Critical"
        elif fraud_score >= 50:
            risk_level = "High"
        elif fraud_score >= 30:
            risk_level = "Medium"
        else:
            risk_level = "Low"
        
        # Collect all alerts
        all_alerts = (
            shell_detection.get("indicators", []) +
            behaviour_detection.get("alerts", []) +
            director_network.get("flags", []) +
            financial_risk.get("anomalies", []) +
            legal_events.get("anomalies", []) +
            geo_address_risk.get("flags", [])
        )
        
        return {
            "shell_company_detection": shell_detection,
            "behaviour_monitoring": behaviour_detection,
            "director_network": director_network,
            "financial_risk": financial_risk,
            "legal_events": legal_events,
            "geo_address_risk": geo_address_risk,
            "overall_fraud_score": min(100, int(fraud_score)),
            "fraud_risk_level": risk_level,
            "alerts": all_alerts,
            "detailed_breakdown": {
                "shell_weight": round(shell_detection["shell_risk_score"] * 0.20, 1),
                "behaviour_weight": round(behaviour_detection["behaviour_risk_score"] * 0.15, 1),
                "network_weight": round(director_network["network_risk_score"] * 0.20, 1),
                "financial_weight": round(financial_risk["financial_risk_score"] * 0.15, 1),
                "legal_weight": round(legal_events["legal_risk_score"] * 0.15, 1),
                "geo_weight": round(geo_address_risk["geo_risk_score"] * 0.15, 1),
            },
        }
    
    except Exception as e:
        logger.warning(f"UK fraud detection error: {e}")
        return {
            "overall_fraud_score": 0,
            "fraud_risk_level": "Low",
            "alerts": [],
            "error": str(e),
        }
