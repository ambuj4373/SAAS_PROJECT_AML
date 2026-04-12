"""
french_fraud_detection.py

Advanced Fraud Detection Suite for French Companies

Implements 6 intelligent engines to detect:
  ✅ Shell Company Structures
  ✅ Behavior Anomalies (director changes, activity spikes)
  ✅ Director Network Clusters & Circular Ownership
  ✅ Financial Anomalies (revenue spikes, consistent losses)
  ✅ Legal Event Anomalies (rapid changes, restructuring)
  ✅ Geo & Address Risk Patterns

Key Features:
  • Confidence scoring (not binary flags)
  • Context-aware thresholds (startup vs established)
  • Fuzzy matching for director names (nicknames, suffixes)
  • Circular ownership flagged with risk levels
  • Detailed evidence trails
  • False positive mitigation
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Set, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)

# Fuzzy matching library for director name comparison
try:
    from difflib import SequenceMatcher
    HAS_FUZZY = True
except ImportError:
    HAS_FUZZY = False


# ═══════════════════════════════════════════════════════════════════════════
# 1. SHELL COMPANY DETECTION ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class ShellCompanyDetector:
    """
    Detect shell company characteristics with startup-aware thresholds.
    
    Signals:
      • Very new company (age < 12 months)
      • No financial records or filings
      • Zero employee count (or data unavailable)
      • Frequent address changes
      • Director clustering (same people in many entities)
      • Low operational substance
    
    Key Feature: Different risk thresholds for startups vs established companies
    """
    
    # Thresholds vary by company age
    THRESHOLDS = {
        "startup": {  # < 3 years old
            "max_age_months": 36,
            "min_employees": 0,  # Startups can have no employees
            "max_address_changes": 3,
            "min_filings": 0,  # Acceptable for new company
        },
        "established": {  # >= 3 years old
            "max_age_months": 36,
            "min_employees": 1,
            "max_address_changes": 1,
            "min_filings": 1,
        }
    }
    
    # Risk indicators with weightings
    INDICATORS = {
        "age_critical": 30,  # < 3 months old
        "age_high": 20,  # 3-12 months old
        "no_employees": 15,  # 0 employees (if not startup)
        "no_financials": 20,  # No filed records
        "no_filings": 25,  # No compliance filings
        "address_instability": 15,  # Multiple address changes
        "director_concentration": 20,  # Same director in many companies
    }
    
    @staticmethod
    def detect(
        company_name: str,
        creation_date: Optional[str],
        employees: Optional[int],
        financial_records: List[Dict],
        formality_records: List[Dict],
        address_history: List[str],
        director_count: int,
        shared_directors: int = 0,
    ) -> Dict[str, Any]:
        """
        Detect shell company characteristics.
        
        Args:
            company_name: Company denomination
            creation_date: YYYYMMDD or ISO format
            employees: Employee count
            financial_records: List of financial records
            formality_records: List of RCS records
            address_history: List of addresses (chronological)
            director_count: Total active directors
            shared_directors: Directors in multiple companies
        
        Returns:
            {
                "is_shell_company": bool,
                "shell_risk_score": 0-100,
                "confidence_level": "High"|"Medium"|"Low",
                "company_type": "Startup"|"Established"|"Unknown",
                "indicators": [...],
                "evidence": [...]
            }
        """
        shell_score = 0
        indicators = []
        evidence = []
        
        # DETERMINE COMPANY TYPE (age-based)
        company_age_months = ShellCompanyDetector._get_age_months(creation_date)
        if company_age_months is None:
            company_type = "Unknown"
            confidence = "Low"
            company_age_months = 0
        elif company_age_months <= 36:
            company_type = "Startup"
            confidence = "High" if company_age_months <= 12 else "Medium"
        else:
            company_type = "Established"
            confidence = "High"
        
        # Select appropriate thresholds
        thresholds = ShellCompanyDetector.THRESHOLDS.get(
            "startup" if company_type == "Startup" else "established",
            ShellCompanyDetector.THRESHOLDS["established"]
        )
        
        # SIGNAL 1: Age-based risk
        if company_age_months is not None:
            if company_age_months < 3:
                shell_score += ShellCompanyDetector.INDICATORS["age_critical"]
                indicators.append("🔴 Critical: Very new company (< 3 months)")
                evidence.append(f"Company created {company_age_months} months ago")
            elif company_age_months < 12:
                shell_score += ShellCompanyDetector.INDICATORS["age_high"]
                indicators.append("🟡 High: Recent company (3-12 months)")
                evidence.append(f"Company created {company_age_months} months ago")
        
        # SIGNAL 2: Employee count (context-aware)
        if employees == 0 and company_type == "Established":
            shell_score += ShellCompanyDetector.INDICATORS["no_employees"]
            indicators.append("⚠️ Zero employee count (established company)")
            evidence.append("No employees recorded in INPI")
        
        # SIGNAL 3: Financial records
        if len(financial_records) == 0:
            if company_type == "Startup":
                indicators.append("✓ No financials (normal for startup)")
            else:
                shell_score += ShellCompanyDetector.INDICATORS["no_financials"]
                indicators.append("🔴 No financial records filed")
                evidence.append("No financial history in INPI")
        
        # SIGNAL 4: Compliance filings
        if len(formality_records) == 0:
            if company_type == "Startup":
                indicators.append("✓ No filings (normal for startup)")
            else:
                shell_score += ShellCompanyDetector.INDICATORS["no_filings"]
                indicators.append("🔴 No compliance filings")
                evidence.append("No RCS records in INPI")
        
        # SIGNAL 5: Address instability
        if len(address_history) > thresholds["max_address_changes"]:
            shell_score += ShellCompanyDetector.INDICATORS["address_instability"]
            indicators.append(f"⚠️ Multiple address changes ({len(address_history)})")
            evidence.append(f"{len(address_history)} different addresses in {company_age_months} months")
        
        # SIGNAL 6: Director concentration
        if director_count > 0 and shared_directors > 0:
            concentration_ratio = shared_directors / director_count
            if concentration_ratio > 0.5:  # More than 50% shared directors
                shell_score += ShellCompanyDetector.INDICATORS["director_concentration"]
                indicators.append(f"⚠️ High director concentration ({shared_directors}/{director_count})")
                evidence.append(f"{shared_directors} of {director_count} directors also manage other companies")
        
        # Normalize score
        shell_score = min(100, max(0, shell_score))
        
        # Determine if "shell company"
        # Startup: only flag if MULTIPLE signals
        # Established: flag if any 2+ signals
        signal_count = len([i for i in indicators if i.startswith("🔴") or i.startswith("⚠️")])
        is_shell = (company_type == "Startup" and signal_count >= 3) or \
                   (company_type == "Established" and signal_count >= 2) or \
                   shell_score >= 70
        
        return {
            "is_shell_company": is_shell,
            "shell_risk_score": shell_score,
            "confidence_level": confidence,
            "company_type": company_type,
            "age_months": company_age_months,
            "indicators": indicators,
            "evidence": evidence,
            "signal_count": signal_count,
        }
    
    @staticmethod
    def _get_age_months(creation_date: Optional[str]) -> Optional[int]:
        """Calculate company age in months."""
        if not creation_date:
            return None
        try:
            # Try ISO format (YYYY-MM-DD)
            date_obj = datetime.strptime(creation_date[:10], "%Y-%m-%d")
            months = (datetime.now() - date_obj).days // 30
            return months
        except:
            try:
                # Try YYYYMMDD format
                date_obj = datetime.strptime(creation_date[:8], "%Y%m%d")
                months = (datetime.now() - date_obj).days // 30
                return months
            except:
                return None


# ═══════════════════════════════════════════════════════════════════════════
# 2. BEHAVIOUR MONITORING ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class BehaviourMonitoringEngine:
    """
    Detect behavioral anomalies in company operations.
    
    Monitors:
      • Director changes (additions, removals, rapid turnover)
      • Activity spikes (sudden filing activity, status changes)
      • Status transitions (Active → Liquidation, etc.)
      • Filing frequency changes
    """
    
    @staticmethod
    def detect(
        formality_records: List[Dict],
        management_records: List[Dict],
        current_status: str,
        last_modification_date: Optional[str],
    ) -> Dict[str, Any]:
        """
        Detect behavioral anomalies.
        
        Returns:
            {
                "director_changes": {...},
                "activity_spikes": [...],
                "status_transitions": [...],
                "behaviour_risk_score": 0-100,
                "alerts": [...]
            }
        """
        alerts = []
        risk_score = 0
        
        # ANALYSIS 1: Director Changes
        director_changes = BehaviourMonitoringEngine._analyze_director_changes(
            management_records
        )
        if director_changes["rapid_turnover"]:
            alerts.append("🔴 Rapid director turnover detected")
            risk_score += 20
        if director_changes["recent_additions"] > 0:
            alerts.append(f"🟡 {director_changes['recent_additions']} recent director addition(s)")
            risk_score += 5
        
        # ANALYSIS 2: Activity Spikes
        activity_spikes = BehaviourMonitoringEngine._detect_activity_spikes(
            formality_records
        )
        if activity_spikes["is_spike"]:
            alerts.append(f"⚠️ Activity spike: {activity_spikes['filings_30d']} filings in last 30 days")
            risk_score += 10
        
        # ANALYSIS 3: Status Transitions
        status_alerts = BehaviourMonitoringEngine._detect_status_changes(
            formality_records, current_status
        )
        alerts.extend(status_alerts)
        if status_alerts:
            risk_score += 15
        
        # ANALYSIS 4: Filing Frequency
        filing_frequency = BehaviourMonitoringEngine._analyze_filing_frequency(
            formality_records
        )
        if filing_frequency["frequency_change"] == "sudden_increase":
            alerts.append("⚠️ Sudden increase in filing frequency")
            risk_score += 8
        elif filing_frequency["frequency_change"] == "sudden_stop":
            alerts.append("🟡 Sudden stop in filing activity")
            risk_score += 15
        
        return {
            "director_changes": director_changes,
            "activity_spikes": activity_spikes,
            "filing_frequency": filing_frequency,
            "behaviour_risk_score": min(100, risk_score),
            "alerts": alerts,
        }
    
    @staticmethod
    def _analyze_director_changes(management_records: List[Dict]) -> Dict[str, Any]:
        """Analyze director appointment/removal patterns."""
        if not management_records:
            return {
                "total_changes": 0,
                "rapid_turnover": False,
                "recent_additions": 0,
                "recent_removals": 0,
            }
        
        # Count recent changes (last 30 days)
        cutoff = datetime.now() - timedelta(days=30)
        recent_adds = 0
        recent_removes = 0
        
        for record in management_records:
            start_date = record.get("start_date")
            end_date = record.get("end_date")
            
            try:
                if start_date:
                    start_dt = datetime.strptime(start_date[:10], "%Y-%m-%d")
                    if start_dt > cutoff:
                        recent_adds += 1
                
                if end_date:
                    end_dt = datetime.strptime(end_date[:10], "%Y-%m-%d")
                    if end_dt > cutoff:
                        recent_removes += 1
            except:
                pass
        
        # Rapid turnover: > 2 changes in 90 days
        total_changes_90d = recent_adds + recent_removes
        rapid_turnover = total_changes_90d > 2
        
        return {
            "total_changes": len(management_records),
            "rapid_turnover": rapid_turnover,
            "recent_additions": recent_adds,
            "recent_removals": recent_removes,
            "changes_90d": total_changes_90d,
        }
    
    @staticmethod
    def _detect_activity_spikes(formality_records: List[Dict]) -> Dict[str, Any]:
        """Detect unusual filing activity patterns."""
        if not formality_records:
            return {"is_spike": False, "filings_30d": 0}
        
        cutoff = datetime.now() - timedelta(days=30)
        filings_30d = 0
        
        for record in formality_records:
            reg_date = record.get("registered_date")
            try:
                if reg_date:
                    reg_dt = datetime.strptime(reg_date[:10], "%Y-%m-%d")
                    if reg_dt > cutoff:
                        filings_30d += 1
            except:
                pass
        
        # Spike: > 5 filings in 30 days (normally 1-2)
        is_spike = filings_30d > 5
        
        return {
            "is_spike": is_spike,
            "filings_30d": filings_30d,
            "spike_threshold": 5,
        }
    
    @staticmethod
    def _detect_status_changes(
        formality_records: List[Dict],
        current_status: str
    ) -> List[str]:
        """Detect significant status transitions."""
        alerts = []
        
        # Look for termination events
        for record in formality_records:
            event_type = record.get("event_type", "").lower()
            
            if "radiation" in event_type:
                alerts.append("🔴 Company has undergone radiation (closure)")
            elif "liquidation" in event_type:
                alerts.append("🔴 Liquidation proceedings detected")
            elif "merger" in event_type:
                alerts.append("⚠️ Merger or acquisition detected")
        
        return alerts
    
    @staticmethod
    def _analyze_filing_frequency(formality_records: List[Dict]) -> Dict[str, Any]:
        """Analyze if filing frequency has changed dramatically."""
        if len(formality_records) < 3:
            return {"frequency_change": "insufficient_data"}
        
        # Compare first 6 months to last 6 months
        cutoff_recent = datetime.now() - timedelta(days=180)
        cutoff_old = datetime.now() - timedelta(days=360)
        
        recent_filings = 0
        old_filings = 0
        
        for record in formality_records:
            reg_date = record.get("registered_date")
            try:
                if reg_date:
                    reg_dt = datetime.strptime(reg_date[:10], "%Y-%m-%d")
                    if reg_dt > cutoff_recent:
                        recent_filings += 1
                    elif cutoff_old < reg_dt <= cutoff_recent:
                        old_filings += 1
            except:
                pass
        
        # Detect changes
        if old_filings == 0:
            return {"frequency_change": "insufficient_history"}
        
        ratio = recent_filings / max(1, old_filings)
        
        if ratio > 3:
            return {"frequency_change": "sudden_increase", "ratio": ratio}
        elif ratio < 0.3 and old_filings > 2:
            return {"frequency_change": "sudden_stop", "ratio": ratio}
        else:
            return {"frequency_change": "normal", "ratio": ratio}


# ═══════════════════════════════════════════════════════════════════════════
# 3. DIRECTOR NETWORK INTELLIGENCE ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class DirectorNetworkIntelligence:
    """
    Build and analyze director networks to detect:
      • Clusters (same directors managing many companies)
      • Circular ownership patterns
      • Hidden networks (shell structures)
      • Cross-border director networks
    
    Key Feature: Fuzzy matching to handle name variations
    """
    
    @staticmethod
    def detect(
        company_siren: str,
        company_name: str,
        management_roles: List[Dict],
        related_companies: List[Dict],
    ) -> Dict[str, Any]:
        """
        Analyze director network for suspicious patterns.
        
        Args:
            company_siren: SIREN of current company
            company_name: Company name
            management_roles: List of management role dicts
            related_companies: List of related company dicts
                (format: {"siren": "...", "name": "...", "directors": [...]})
        
        Returns:
            {
                "director_clusters": [...],
                "circular_ownership": [...],
                "network_risk_score": 0-100,
                "network_complexity": "Simple"|"Moderate"|"Complex",
                "flags": [...]
            }
        """
        flags = []
        risk_score = 0
        
        # ANALYSIS 1: Build director name list with fuzzy matching
        director_names = DirectorNetworkIntelligence._extract_director_names(
            management_roles
        )
        
        # ANALYSIS 2: Find clusters (same directors in multiple companies)
        clusters = DirectorNetworkIntelligence._find_director_clusters(
            company_siren,
            director_names,
            related_companies
        )
        
        if clusters["cluster_count"] > 0:
            risk_score += min(30, clusters["cluster_count"] * 10)
            flags.append(
                f"🔴 {clusters['cluster_count']} director cluster(s) detected"
            )
            for cluster in clusters["clusters"][:3]:  # Show top 3
                flags.append(
                    f"   - {cluster['director_name']} in {cluster['company_count']} companies"
                )
        
        # ANALYSIS 3: Detect circular ownership
        circular = DirectorNetworkIntelligence._detect_circular_ownership(
            company_siren,
            management_roles,
            related_companies
        )
        
        if circular["has_circular"]:
            risk_score += 25
            flags.append(f"🟡 Potential circular ownership detected")
            for link in circular["circular_links"]:
                flags.append(f"   {link}")
        
        # ANALYSIS 4: Network complexity assessment
        complexity = DirectorNetworkIntelligence._assess_complexity(
            len(management_roles),
            len(related_companies),
            len(clusters["clusters"])
        )
        
        return {
            "director_clusters": clusters,
            "circular_ownership": circular,
            "network_risk_score": min(100, risk_score),
            "network_complexity": complexity,
            "flags": flags,
        }
    
    @staticmethod
    def _extract_director_names(management_roles: List[Dict]) -> List[str]:
        """Extract director names from management roles."""
        names = []
        for role in management_roles:
            if role.get("person_type") == "Physical Person":
                full_name = role.get("full_name") or \
                           f"{role.get('first_name', '')} {role.get('last_name', '')}".strip()
                if full_name:
                    names.append(full_name.upper())  # Normalize to uppercase
        return names
    
    @staticmethod
    def _find_director_clusters(
        company_siren: str,
        director_names: List[str],
        related_companies: List[Dict],
    ) -> Dict[str, Any]:
        """Find directors appearing in multiple companies."""
        clusters = []
        
        for director_name in director_names:
            matching_companies = []
            
            for related in related_companies:
                related_directors = related.get("directors", [])
                
                for rel_director in related_directors:
                    rel_name = rel_director.get("name", "").upper()
                    
                    # Fuzzy matching: similar names
                    similarity = DirectorNetworkIntelligence._fuzzy_match(
                        director_name, rel_name
                    )
                    
                    if similarity > 0.85:  # 85% match threshold
                        matching_companies.append({
                            "siren": related.get("siren"),
                            "name": related.get("name"),
                            "similarity": similarity,
                        })
                        break
            
            if len(matching_companies) >= 2:  # Appears in 2+ companies
                clusters.append({
                    "director_name": director_name,
                    "company_count": len(matching_companies) + 1,  # +1 for current
                    "companies": matching_companies,
                    "risk_level": "high" if len(matching_companies) >= 5 else "medium",
                })
        
        return {
            "cluster_count": len(clusters),
            "clusters": sorted(
                clusters,
                key=lambda x: x["company_count"],
                reverse=True
            ),
        }
    
    @staticmethod
    def _detect_circular_ownership(
        company_siren: str,
        management_roles: List[Dict],
        related_companies: List[Dict],
    ) -> Dict[str, Any]:
        """Detect circular ownership patterns (A owns B owns A)."""
        circular_links = []
        has_circular = False
        
        # Check if any management is a corporate representative
        for role in management_roles:
            if role.get("person_type") == "Legal Entity":
                entity_siren = role.get("company_siren")
                entity_name = role.get("company_name")
                
                # Check if that entity has current company as shareholder
                for related in related_companies:
                    if related.get("siren") == entity_siren:
                        # Check related company's shareholders
                        shareholders = related.get("shareholders", [])
                        for shareholder in shareholders:
                            if shareholder.get("siren") == company_siren:
                                has_circular = True
                                circular_links.append(
                                    f"{company_siren} → {entity_siren} → {company_siren}"
                                )
        
        return {
            "has_circular": has_circular,
            "circular_links": circular_links,
            "risk_level": "high" if has_circular else "low",
        }
    
    @staticmethod
    def _assess_complexity(
        director_count: int,
        related_company_count: int,
        cluster_count: int,
    ) -> str:
        """Assess network complexity."""
        complexity_score = (
            (director_count * 0.3) +
            (related_company_count * 0.4) +
            (cluster_count * 0.3)
        )
        
        if complexity_score < 5:
            return "Simple"
        elif complexity_score < 15:
            return "Moderate"
        else:
            return "Complex"
    
    @staticmethod
    def _fuzzy_match(name1: str, name2: str) -> float:
        """
        Fuzzy match two names with threshold handling.
        
        Returns: 0-1 similarity score
        """
        if not HAS_FUZZY:
            # Fallback: exact match only
            return 1.0 if name1 == name2 else 0.0
        
        # Import here to avoid issues if not available
        try:
            from difflib import SequenceMatcher
            matcher = SequenceMatcher(None, name1, name2)
            return matcher.ratio()
        except:
            return 1.0 if name1 == name2 else 0.0


# ═══════════════════════════════════════════════════════════════════════════
# 4. FINANCIAL RISK ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class FinancialRiskEngine:
    """
    Analyze financial patterns to detect:
      • Revenue spikes/anomalies
      • Consistent losses without explanation
      • Filing pattern anomalies
      • Sudden financial status changes
    """
    
    @staticmethod
    def detect(financial_records: List[Dict]) -> Dict[str, Any]:
        """
        Analyze financial records for anomalies.
        
        Returns:
            {
                "revenue_trend": "stable"|"growing"|"declining"|"anomalous",
                "profitability": "profitable"|"losses"|"mixed"|"unknown",
                "filing_status": "complete"|"gaps"|"missing",
                "anomalies": [...],
                "financial_risk_score": 0-100,
                "confidence": "High"|"Medium"|"Low"
            }
        """
        anomalies = []
        risk_score = 0
        confidence = "Low"
        
        if not financial_records or len(financial_records) == 0:
            return {
                "revenue_trend": "unknown",
                "profitability": "unknown",
                "filing_status": "missing",
                "anomalies": ["No financial records available"],
                "financial_risk_score": 0,
                "confidence": "Low",
            }
        
        confidence = "High"
        
        # Sort by year (descending)
        sorted_records = sorted(
            financial_records,
            key=lambda x: x.get("year", 0),
            reverse=True
        )
        
        # ANALYSIS 1: Revenue trends
        revenues = [
            r.get("revenue", 0) for r in sorted_records
            if r.get("revenue") is not None and r.get("revenue", 0) > 0
        ]
        
        if len(revenues) >= 2:
            trend = FinancialRiskEngine._detect_revenue_trend(revenues)
            
            if trend == "anomalous":
                risk_score += 15
                anomalies.append("🟡 Anomalous revenue pattern detected")
        else:
            trend = "unknown"
        
        # ANALYSIS 2: Profitability
        profits = [
            r.get("net_profit", 0) for r in sorted_records
            if r.get("net_profit") is not None
        ]
        
        if len(profits) >= 2:
            loss_count = sum(1 for p in profits if p < 0)
            if loss_count == len(profits):
                risk_score += 10
                anomalies.append("⚠️ Consistent losses across all years")
            elif loss_count > len(profits) / 2:
                anomalies.append("⚠️ Multiple loss-making years")
            
            profitability = FinancialRiskEngine._classify_profitability(profits)
        else:
            profitability = "unknown"
        
        # ANALYSIS 3: Filing gaps
        filing_status = FinancialRiskEngine._check_filing_gaps(sorted_records)
        if filing_status == "gaps":
            risk_score += 10
            anomalies.append("🟡 Gaps in financial filing history")
        
        # ANALYSIS 4: Sudden changes
        if len(sorted_records) >= 2:
            sudden_change = FinancialRiskEngine._detect_sudden_changes(sorted_records)
            if sudden_change:
                risk_score += 8
                anomalies.append(f"⚠️ {sudden_change}")
        
        return {
            "revenue_trend": trend,
            "profitability": profitability,
            "filing_status": filing_status,
            "records_count": len(financial_records),
            "anomalies": anomalies,
            "financial_risk_score": min(100, risk_score),
            "confidence": confidence,
        }
    
    @staticmethod
    def _detect_revenue_trend(revenues: List[float]) -> str:
        """Detect revenue trend pattern."""
        if len(revenues) < 2:
            return "unknown"
        
        # Calculate year-over-year changes
        changes = []
        for i in range(len(revenues) - 1):
            if revenues[i+1] > 0:
                change = (revenues[i] - revenues[i+1]) / revenues[i+1]
                changes.append(change)
        
        if not changes:
            return "stable"
        
        # Classify
        avg_change = sum(changes) / len(changes)
        
        # Check for spikes (sudden large increase)
        max_change = max(changes)
        if max_change > 2:  # > 200% increase in one year
            return "anomalous"
        
        if avg_change > 0.1:
            return "growing"
        elif avg_change < -0.1:
            return "declining"
        else:
            return "stable"
    
    @staticmethod
    def _classify_profitability(profits: List[float]) -> str:
        """Classify profitability."""
        if not profits:
            return "unknown"
        
        profit_count = sum(1 for p in profits if p > 0)
        loss_count = sum(1 for p in profits if p < 0)
        
        if loss_count == 0:
            return "profitable"
        elif profit_count == 0:
            return "losses"
        else:
            return "mixed"
    
    @staticmethod
    def _check_filing_gaps(records: List[Dict]) -> str:
        """Check for gaps in filing history."""
        if len(records) < 2:
            return "unknown"
        
        years = sorted([r.get("year", 0) for r in records if r.get("year")])
        
        for i in range(len(years) - 1):
            if years[i] - years[i+1] > 1:  # Gap > 1 year
                return "gaps"
        
        return "complete"
    
    @staticmethod
    def _detect_sudden_changes(records: List[Dict]) -> Optional[str]:
        """Detect sudden financial changes."""
        if len(records) < 2:
            return None
        
        current = records[0]
        previous = records[1]
        
        current_rev = current.get("revenue", 0)
        previous_rev = previous.get("revenue", 0)
        
        if previous_rev > 0:
            change_ratio = current_rev / previous_rev
            if change_ratio > 3:
                return f"Revenue spike: {change_ratio:.1f}x increase"
            elif change_ratio < 0.3:
                return f"Revenue collapse: {change_ratio:.1f}x"
        
        return None


# ═══════════════════════════════════════════════════════════════════════════
# 5. LEGAL EVENT INTELLIGENCE ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class LegalEventIntelligence:
    """
    Analyze legal and compliance events to detect:
      • Rapid ownership changes
      • Frequent restructuring
      • Unusual legal activity
      • Capital changes
    """
    
    @staticmethod
    def detect(formality_records: List[Dict]) -> Dict[str, Any]:
        """
        Analyze legal events for anomalies.
        
        Returns:
            {
                "ownership_change_frequency": "stable"|"active"|"volatile",
                "legal_activity_level": "low"|"moderate"|"high",
                "restructuring_count": int,
                "capital_changes": [...],
                "anomalies": [...],
                "legal_risk_score": 0-100
            }
        """
        if not formality_records:
            return {
                "ownership_change_frequency": "unknown",
                "legal_activity_level": "unknown",
                "restructuring_count": 0,
                "capital_changes": [],
                "anomalies": [],
                "legal_risk_score": 0,
            }
        
        risk_score = 0
        anomalies = []
        
        # ANALYSIS 1: Ownership changes
        ownership_changes = LegalEventIntelligence._count_ownership_changes(
            formality_records
        )
        
        if ownership_changes["frequency"] == "volatile":
            risk_score += 20
            anomalies.append(
                f"🔴 Volatile ownership: {ownership_changes['change_count']} changes"
            )
        elif ownership_changes["frequency"] == "active":
            risk_score += 10
            anomalies.append(
                f"⚠️ Active ownership changes: {ownership_changes['change_count']} in 2 years"
            )
        
        # ANALYSIS 2: Restructuring
        restructuring = LegalEventIntelligence._detect_restructuring(
            formality_records
        )
        
        if restructuring["count"] > 2:
            risk_score += 15
            anomalies.append(f"⚠️ {restructuring['count']} restructuring events")
        
        # ANALYSIS 3: Capital changes
        capital_changes = LegalEventIntelligence._track_capital_changes(
            formality_records
        )
        
        if capital_changes["has_increases"] and capital_changes["increase_count"] > 2:
            anomalies.append(f"🟡 Frequent capital increases ({capital_changes['increase_count']})")
            risk_score += 8
        
        # Overall legal activity level
        activity_level = "low" if len(formality_records) < 3 else \
                        "moderate" if len(formality_records) < 10 else "high"
        
        return {
            "ownership_change_frequency": ownership_changes["frequency"],
            "legal_activity_level": activity_level,
            "restructuring_count": restructuring["count"],
            "capital_changes": capital_changes,
            "anomalies": anomalies,
            "legal_risk_score": min(100, risk_score),
        }
    
    @staticmethod
    def _count_ownership_changes(formality_records: List[Dict]) -> Dict[str, Any]:
        """Count ownership-related changes."""
        changes = []
        
        for record in formality_records:
            event_type = record.get("event_type", "").lower()
            if any(x in event_type for x in ["shareholder", "capital", "modification"]):
                registered_date = record.get("registered_date")
                if registered_date:
                    changes.append(registered_date)
        
        # Determine frequency based on concentration in time
        if len(changes) <= 1:
            frequency = "stable"
        else:
            # Calculate density
            oldest = min(changes)
            newest = max(changes)
            try:
                oldest_dt = datetime.strptime(oldest[:10], "%Y-%m-%d")
                newest_dt = datetime.strptime(newest[:10], "%Y-%m-%d")
                days_span = (newest_dt - oldest_dt).days
                
                if days_span > 0:
                    density = len(changes) / (days_span / 365)  # Changes per year
                    if density > 2:
                        frequency = "volatile"
                    elif density > 0.5:
                        frequency = "active"
                    else:
                        frequency = "stable"
                else:
                    frequency = "stable"
            except:
                frequency = "unknown"
        
        return {
            "frequency": frequency,
            "change_count": len(changes),
            "changes": changes,
        }
    
    @staticmethod
    def _detect_restructuring(formality_records: List[Dict]) -> Dict[str, Any]:
        """Detect restructuring events (mergers, divisions, etc)."""
        restructuring_events = []
        
        for record in formality_records:
            event_type = record.get("event_type", "").lower()
            if any(x in event_type for x in ["merger", "fusion", "division", "scission", "apport"]):
                restructuring_events.append({
                    "type": record.get("event_type"),
                    "date": record.get("registered_date"),
                    "description": record.get("description", ""),
                })
        
        return {
            "count": len(restructuring_events),
            "events": restructuring_events,
        }
    
    @staticmethod
    def _track_capital_changes(formality_records: List[Dict]) -> Dict[str, Any]:
        """Track capital increase/decrease events."""
        capital_increases = 0
        capital_decreases = 0
        capital_events = []
        
        for record in formality_records:
            event_type = record.get("event_type", "").lower()
            description = record.get("description", "").lower()
            
            if "capital" in event_type or "capital" in description:
                if "increase" in description or "augment" in description:
                    capital_increases += 1
                    capital_events.append(("increase", record.get("registered_date")))
                elif "decrease" in description or "reduction" in description:
                    capital_decreases += 1
                    capital_events.append(("decrease", record.get("registered_date")))
        
        return {
            "increase_count": capital_increases,
            "decrease_count": capital_decreases,
            "has_increases": capital_increases > 0,
            "has_decreases": capital_decreases > 0,
            "events": capital_events,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 6. GEO & ADDRESS RISK ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class GeoAddressRiskEngine:
    """
    Analyze geographic and address patterns to detect:
      • Multiple companies at same address
      • Virtual office clusters
      • Cross-border anomalies
      • Director nationality mismatches
    """
    
    @staticmethod
    def detect(
        company_address: str,
        company_postal_code: str,
        company_city: str,
        establishment_addresses: List[Dict],
        directors: List[Dict],
        related_companies: List[Dict],
    ) -> Dict[str, Any]:
        """
        Analyze geographic risk patterns.
        
        Returns:
            {
                "address_concentration": "low"|"medium"|"high",
                "virtual_office_risk": bool,
                "companies_at_address": int,
                "cross_border_flags": [...],
                "geo_risk_score": 0-100,
                "flags": [...]
            }
        """
        flags = []
        risk_score = 0
        
        # ANALYSIS 1: Companies at same address
        address_concentration = GeoAddressRiskEngine._analyze_address_concentration(
            company_address,
            related_companies
        )
        
        if address_concentration["concentration_level"] == "high":
            risk_score += 25
            flags.append(
                f"🔴 {address_concentration['company_count']} companies at same address"
            )
        elif address_concentration["concentration_level"] == "medium":
            risk_score += 12
            flags.append(
                f"⚠️ {address_concentration['company_count']} companies share address"
            )
        
        # ANALYSIS 2: Virtual office detection
        virtual_risk = GeoAddressRiskEngine._detect_virtual_offices(
            company_address,
            establishment_addresses
        )
        
        if virtual_risk["is_virtual"]:
            risk_score += 15
            flags.append("⚠️ Virtual office address pattern detected")
        
        # ANALYSIS 3: Cross-border analysis
        cross_border = GeoAddressRiskEngine._analyze_cross_border(
            company_city,
            establishment_addresses,
            directors
        )
        
        if cross_border["has_anomalies"]:
            risk_score += 10
            for anomaly in cross_border["anomalies"]:
                flags.append(f"🟡 {anomaly}")
        
        # ANALYSIS 4: Director nationality mismatch
        nationality_analysis = GeoAddressRiskEngine._analyze_nationality_mismatch(
            company_city,
            directors
        )
        
        if nationality_analysis["mismatch_ratio"] > 0.7:
            risk_score += 10
            flags.append(
                f"⚠️ High foreign director ratio: {nationality_analysis['mismatch_ratio']:.0%}"
            )
        
        return {
            "address_concentration": address_concentration["concentration_level"],
            "companies_at_address": address_concentration["company_count"],
            "virtual_office_risk": virtual_risk["is_virtual"],
            "cross_border": cross_border,
            "nationality_mismatch": nationality_analysis,
            "geo_risk_score": min(100, risk_score),
            "flags": flags,
        }
    
    @staticmethod
    def _analyze_address_concentration(
        company_address: str,
        related_companies: List[Dict]
    ) -> Dict[str, Any]:
        """Find multiple companies at same address."""
        matching_companies = 1  # Current company
        
        for related in related_companies:
            related_address = related.get("address", "")
            
            # Normalize and compare addresses
            if GeoAddressRiskEngine._addresses_match(company_address, related_address):
                matching_companies += 1
        
        if matching_companies > 5:
            concentration = "high"
        elif matching_companies > 2:
            concentration = "medium"
        else:
            concentration = "low"
        
        return {
            "concentration_level": concentration,
            "company_count": matching_companies,
        }
    
    @staticmethod
    def _detect_virtual_offices(
        company_address: str,
        establishments: List[Dict]
    ) -> Dict[str, Any]:
        """Detect virtual office patterns."""
        virtual_keywords = [
            "coworking", "business center", "virtual", "pépinière",
            "incubateur", "technopole", "parc technologique",
            "boîte postale", "bp", "poste", "mailbox"
        ]
        
        address_lower = company_address.lower()
        is_virtual = any(keyword in address_lower for keyword in virtual_keywords)
        
        # Check if all establishments are virtual too
        all_virtual = is_virtual
        for etab in establishments:
            etab_address = etab.get("address", "").lower()
            if not any(k in etab_address for k in virtual_keywords):
                all_virtual = False
                break
        
        return {
            "is_virtual": is_virtual,
            "all_virtual": all_virtual,
            "keywords_found": [
                k for k in virtual_keywords
                if k in address_lower
            ]
        }
    
    @staticmethod
    def _analyze_cross_border(
        company_city: str,
        establishments: List[Dict],
        directors: List[Dict]
    ) -> Dict[str, Any]:
        """Analyze cross-border establishment and director patterns."""
        anomalies = []
        
        # Check establishment distribution
        cities = [e.get("city", "") for e in establishments if e.get("city")]
        countries = set([e.get("country", "FR") for e in establishments])
        
        if len(countries) > 3:
            anomalies.append(f"Wide geographic spread: {len(countries)} countries")
        
        # Check director countries
        director_countries = set()
        for director in directors:
            nationality = director.get("nationality", "")
            if nationality and nationality != "French":
                director_countries.add(nationality)
        
        if len(director_countries) > 2:
            anomalies.append(
                f"Multiple foreign nationalities among directors: {len(director_countries)}"
            )
        
        return {
            "has_anomalies": len(anomalies) > 0,
            "anomalies": anomalies,
            "country_count": len(countries),
            "director_country_count": len(director_countries),
        }
    
    @staticmethod
    def _analyze_nationality_mismatch(
        company_city: str,
        directors: List[Dict]
    ) -> Dict[str, Any]:
        """Analyze director nationality vs company location."""
        if not directors:
            return {
                "mismatch_ratio": 0.0,
                "foreign_count": 0,
                "total_count": 0,
            }
        
        foreign_count = 0
        for director in directors:
            if director.get("person_type") == "Physical Person":
                nationality = director.get("nationality", "French")
                if nationality and nationality != "French":
                    foreign_count += 1
        
        mismatch_ratio = foreign_count / len(directors) if directors else 0
        
        return {
            "mismatch_ratio": mismatch_ratio,
            "foreign_count": foreign_count,
            "total_count": len(directors),
            "is_concerning": mismatch_ratio > 0.7,
        }
    
    @staticmethod
    def _addresses_match(addr1: str, addr2: str) -> bool:
        """Check if two addresses match (fuzzy)."""
        if not addr1 or not addr2:
            return False
        
        # Normalize
        a1 = re.sub(r"\s+", " ", addr1.lower().strip())
        a2 = re.sub(r"\s+", " ", addr2.lower().strip())
        
        # Exact match
        if a1 == a2:
            return True
        
        # Fuzzy: if addresses share significant substring
        if len(a1) > 10 and len(a2) > 10:
            if HAS_FUZZY:
                try:
                    from difflib import SequenceMatcher
                    matcher = SequenceMatcher(None, a1, a2)
                    return matcher.ratio() > 0.8
                except:
                    pass
        
        return False


# ═══════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════

def run_fraud_detection_suite(
    company: Any,
    dashboard_data: Dict[str, Any],
    financial_records: List[Dict],
    formality_records: List[Dict],
    management_roles: Optional[List[Dict]] = None,
    related_companies: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """
    Run complete fraud detection suite.
    
    Args:
        company: FrenchCompanyBasic object
        dashboard_data: Dashboard data from FrenchDashboardBuilder
        financial_records: Financial records from INPI
        formality_records: RCS formality records from INPI
        management_roles: Management roles (extracted from dashboard if not provided)
        related_companies: Related company data (optional)
    
    Returns:
        {
            "shell_company_detection": {...},
            "behaviour_monitoring": {...},
            "director_network": {...},
            "financial_risk": {...},
            "legal_events": {...},
            "geo_address_risk": {...},
            "overall_fraud_score": 0-100,
            "fraud_risk_level": "Low"|"Medium"|"High"|"Critical",
            "confidence": "Low"|"Medium"|"High",
            "alerts": [...]
        }
    """
    
    logger.info(f"🔍 Running fraud detection suite for {company.name}")
    
    # Extract data from dashboard
    if management_roles is None:
        dashboard_mgmt = dashboard_data.get("management_network", {})
        active_roles = dashboard_mgmt.get("roles", {}).get("active", [])
        management_roles = active_roles
    else:
        management_roles = management_roles if management_roles is not None else []
    
    if related_companies is None:
        related_companies = []
    
    # Ensure management_roles is never None
    management_roles = management_roles if management_roles is not None else []
    
    # Extract addresses
    address_history = [company.address] if company.address else []
    establishments = dashboard_data.get("establishments", {}).get("establishments", [])
    for etab in establishments:
        if etab.get("address") and etab.get("address") not in address_history:
            address_history.append(etab["address"])
    
    # RUN 6 DETECTION ENGINES
    # ─────────────────────────────────────────────────────────────────────
    
    # Engine 1: Shell Company Detection
    shell_detection = ShellCompanyDetector.detect(
        company_name=company.name,
        creation_date=company.creation_date,
        employees=company.employee_count,
        financial_records=financial_records,
        formality_records=formality_records,
        address_history=address_history,
        director_count=len(management_roles),
        shared_directors=0,  # Would need to calculate from related_companies
    )
    
    # Engine 2: Behaviour Monitoring
    behaviour_detection = BehaviourMonitoringEngine.detect(
        formality_records=formality_records,
        management_records=management_roles,
        current_status=company.status,
        last_modification_date=company.creation_date,
    )
    
    # Engine 3: Director Network Intelligence
    director_network = DirectorNetworkIntelligence.detect(
        company_siren=company.siren,
        company_name=company.name,
        management_roles=management_roles,
        related_companies=related_companies,
    )
    
    # Engine 4: Financial Risk
    financial_risk = FinancialRiskEngine.detect(
        financial_records=financial_records
    )
    
    # Engine 5: Legal Event Intelligence
    legal_events = LegalEventIntelligence.detect(
        formality_records=formality_records
    )
    
    # Engine 6: Geo & Address Risk
    establishment_list = [
        {
            "address": e.get("address", ""),
            "city": e.get("city", ""),
            "country": "France",
        }
        for e in establishments
    ]
    
    geo_address_risk = GeoAddressRiskEngine.detect(
        company_address=company.address,
        company_postal_code=company.postal_code,
        company_city=company.city,
        establishment_addresses=establishment_list,
        directors=management_roles,
        related_companies=related_companies,
    )
    
    # AGGREGATE RESULTS
    # ─────────────────────────────────────────────────────────────────────
    
    fraud_score = (
        (shell_detection["shell_risk_score"] * 0.20) +
        (behaviour_detection["behaviour_risk_score"] * 0.15) +
        (director_network["network_risk_score"] * 0.20) +
        (financial_risk["financial_risk_score"] * 0.15) +
        (legal_events["legal_risk_score"] * 0.15) +
        (geo_address_risk["geo_risk_score"] * 0.15)
    )
    
    # Determine overall risk level
    if fraud_score >= 70:
        risk_level = "Critical"
    elif fraud_score >= 50:
        risk_level = "High"
    elif fraud_score >= 30:
        risk_level = "Medium"
    else:
        risk_level = "Low"
    
    # Aggregate confidence
    min_confidence = min([
        shell_detection.get("confidence_level", "Low"),
        financial_risk.get("confidence", "Low"),
    ])
    confidence_map = {"High": 3, "Medium": 2, "Low": 1}
    confidence_level = "High" if confidence_map.get(min_confidence, 1) >= 2 else "Medium"
    
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
        "confidence": confidence_level,
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
