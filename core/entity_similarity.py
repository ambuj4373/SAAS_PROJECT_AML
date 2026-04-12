"""
core/entity_similarity.py — Fuzzy entity matching and similarity detection.

Detects possible overlaps or relationships between entities (charities,
companies, trustees, directors) based on name similarity, shared addresses,
or overlapping personnel.

Uses SequenceMatcher for fuzzy name matching (no external dependency needed)
with configurable thresholds.

Public API:
    find_similar_names(target, candidates, threshold)  → list[NameMatch]
    detect_entity_overlaps(entity_data)                → OverlapReport
    name_similarity(a, b)                              → float (0-1)
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class NameMatch(BaseModel):
    """A single name match result."""
    source_name: str
    matched_name: str
    similarity: float = Field(0.0, ge=0, le=1)
    match_type: str = Field("fuzzy", description="exact|fuzzy|partial|initials")
    context: str = Field("", description="Where this match was found")
    risk_note: str = ""


class EntityOverlap(BaseModel):
    """A detected overlap between entities."""
    entity_a: str
    entity_b: str
    overlap_type: str = Field("name", description="name|address|personnel|role")
    detail: str = ""
    similarity: float = 0.0
    significance: str = Field("low", description="high|medium|low")


class OverlapReport(BaseModel):
    """Complete overlap detection result."""
    overlaps: list[EntityOverlap] = Field(default_factory=list)
    name_matches: list[NameMatch] = Field(default_factory=list)
    total_entities_checked: int = 0
    overlap_count: int = 0
    high_significance_count: int = 0
    summary: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# NAME NORMALISATION
# ═══════════════════════════════════════════════════════════════════════════════

_COMPANY_SUFFIXES = re.compile(
    r"\b(limited|ltd|plc|llp|cic|cio|inc|corp|company|co|"
    r"foundation|trust|charity|association|society|group|"
    r"holdings|international|uk|trading)\b",
    re.IGNORECASE,
)

_HONORIFICS = re.compile(
    r"\b(mr|mrs|ms|miss|dr|prof|sir|dame|lord|lady|rev|"
    r"reverend|hon|cbe|obe|mbe|phd|ba|ma|msc|fca|aca)\b\.?",
    re.IGNORECASE,
)


def _normalise_entity_name(name: str) -> str:
    """Normalise a company/charity name for comparison."""
    n = name.lower().strip()
    n = _COMPANY_SUFFIXES.sub("", n)
    n = re.sub(r"[^\w\s]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def _normalise_person_name(name: str) -> str:
    """Normalise a person name for comparison."""
    n = name.lower().strip()
    n = _HONORIFICS.sub("", n)
    n = re.sub(r"[^\w\s]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def _extract_initials(name: str) -> str:
    """Extract first letters of each word."""
    return "".join(w[0] for w in name.split() if w).lower()


# ═══════════════════════════════════════════════════════════════════════════════
# SIMILARITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

# ── Simple phonetic encoding (Double Metaphone–lite) ────────────────────────
def _metaphone_simple(word: str) -> str:
    """Minimal Metaphone-like phonetic encoding for English names.

    Maps common phonetic equivalences (ph→f, ck→k, ght→t, etc.) and strips
    vowels after the first letter to create a phonetic fingerprint.
    """
    if not word:
        return ""
    w = word.lower().strip()
    # Common substitutions
    _subs = [
        ("ph", "f"), ("ght", "t"), ("ck", "k"), ("sh", "x"),
        ("th", "0"), ("wh", "w"), ("wr", "r"), ("kn", "n"),
        ("gn", "n"), ("mb", "m"), ("sch", "sk"), ("tch", "ch"),
        ("dge", "j"), ("ae", "e"), ("oe", "e"), ("ie", "e"),
    ]
    for old, new in _subs:
        w = w.replace(old, new)
    # Remove doubled letters
    result = w[0] if w else ""
    for c in w[1:]:
        if c != result[-1]:
            result += c
    # Strip vowels after first char
    if len(result) > 1:
        result = result[0] + re.sub(r"[aeiouy]", "", result[1:])
    return result[:8]  # cap length


def name_similarity(name_a: str, name_b: str, is_person: bool = False) -> float:
    """Calculate similarity between two names (0-1).

    V4 multi-algorithm approach (returns highest score):
    1. Exact normalised match → 1.0
    2. RapidFuzz Levenshtein ratio (edit-distance)
    3. RapidFuzz token_set_ratio (word-reorder tolerant)
    4. RapidFuzz partial_ratio (substring/abbreviation tolerant)
    5. Jaccard token overlap (bag-of-words)
    6. Phonetic fingerprint match (catches misspellings)
    7. SequenceMatcher fallback
    """
    if not name_a or not name_b:
        return 0.0

    norm_fn = _normalise_person_name if is_person else _normalise_entity_name
    a = norm_fn(name_a)
    b = norm_fn(name_b)

    if not a or not b:
        return 0.0

    # Exact match
    if a == b:
        return 1.0

    scores = []

    # RapidFuzz algorithms (preferred — 10-50× faster than SequenceMatcher)
    if HAS_RAPIDFUZZ:
        scores.append(rf_fuzz.ratio(a, b) / 100.0)
        scores.append(rf_fuzz.token_set_ratio(a, b) / 100.0)
        scores.append(rf_fuzz.partial_ratio(a, b) / 100.0 * 0.90)  # slight discount
    else:
        # Fallback: SequenceMatcher (character-level)
        scores.append(SequenceMatcher(None, a, b).ratio())

    # Token set overlap (Jaccard similarity)
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if tokens_a and tokens_b:
        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b
        jaccard = len(intersection) / len(union)
        scores.append(jaccard)

        # Weighted token overlap (longer shared tokens count more)
        shared_chars = sum(len(t) for t in intersection)
        total_chars = sum(len(t) for t in union)
        if total_chars > 0:
            scores.append(shared_chars / total_chars)

    # Phonetic matching (catches misspellings / transliterations)
    tokens_a_list = a.split()
    tokens_b_list = b.split()
    if tokens_a_list and tokens_b_list:
        phon_a = {_metaphone_simple(t) for t in tokens_a_list if len(t) > 2}
        phon_b = {_metaphone_simple(t) for t in tokens_b_list if len(t) > 2}
        if phon_a and phon_b:
            phon_overlap = len(phon_a & phon_b) / max(len(phon_a | phon_b), 1)
            # Phonetic match alone is weaker evidence, scale to 0.85 max
            scores.append(phon_overlap * 0.85)

    # Substring containment
    if len(a) >= 4 and len(b) >= 4:
        if a in b or b in a:
            shorter = min(len(a), len(b))
            longer = max(len(a), len(b))
            scores.append(0.7 + 0.3 * (shorter / longer))

    return max(scores) if scores else 0.0


def find_similar_names(
    target: str,
    candidates: list[str],
    threshold: float = 0.65,
    is_person: bool = False,
    context: str = "",
) -> list[NameMatch]:
    """Find names in candidates that are similar to target.

    Args:
        target: The name to match against
        candidates: List of names to compare
        threshold: Minimum similarity (0-1)
        is_person: Whether names are person names (affects normalisation)
        context: Description of where candidates come from

    Returns:
        List of NameMatch objects sorted by similarity (highest first)
    """
    matches = []
    norm_fn = _normalise_person_name if is_person else _normalise_entity_name
    target_norm = norm_fn(target)

    if not target_norm:
        return []

    for candidate in candidates:
        if not candidate:
            continue

        cand_norm = norm_fn(candidate)
        if not cand_norm:
            continue

        # Skip self-matches
        if target_norm == cand_norm:
            continue

        sim = name_similarity(target, candidate, is_person)

        if sim >= threshold:
            # Determine match type
            if sim >= 0.98:
                match_type = "exact"
            elif sim >= 0.85:
                match_type = "fuzzy"
            elif target_norm in cand_norm or cand_norm in target_norm:
                match_type = "partial"
            else:
                match_type = "fuzzy"

            # Risk assessment
            risk_note = ""
            if sim >= 0.85:
                risk_note = "High similarity — likely same or closely related entity"
            elif sim >= 0.75:
                risk_note = "Moderate similarity — may be related entity or variant name"
            else:
                risk_note = "Some similarity — verify if connected"

            matches.append(NameMatch(
                source_name=target,
                matched_name=candidate,
                similarity=round(sim, 3),
                match_type=match_type,
                context=context,
                risk_note=risk_note,
            ))

    matches.sort(key=lambda m: m.similarity, reverse=True)
    return matches


# ═══════════════════════════════════════════════════════════════════════════════
# OVERLAP DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def detect_entity_overlaps(
    entity_name: str,
    entity_type: str = "charity",
    trustees: list[str] | None = None,
    officers: list[dict[str, Any]] | None = None,
    related_companies: list[dict[str, Any]] | None = None,
    trustee_appointments: dict[str, list[dict[str, Any]]] | None = None,
    adverse_results: list[dict[str, Any]] | None = None,
) -> OverlapReport:
    """Detect overlaps, duplicates, and connections between entities.

    Checks:
    1. Similar names among trustees/directors (conflicts of interest)
    2. Trustees who appear in each other's appointment lists
    3. Entity name matching mentions in adverse media
    4. Cross-entity name similarities
    """
    overlaps: list[EntityOverlap] = []
    name_matches: list[NameMatch] = []
    entities_checked = 0

    # ── 1. Trustee-to-trustee similarity ─────────────────────────────
    if trustees and len(trustees) >= 2:
        entities_checked += len(trustees)
        for i, t1 in enumerate(trustees):
            for t2 in trustees[i + 1:]:
                sim = name_similarity(t1, t2, is_person=True)
                if sim >= 0.60 and sim < 1.0:
                    significance = "high" if sim >= 0.80 else "medium"
                    overlaps.append(EntityOverlap(
                        entity_a=t1, entity_b=t2,
                        overlap_type="personnel",
                        detail=f"Trustees with similar names ({int(sim*100)}% match)",
                        similarity=sim,
                        significance=significance,
                    ))

    # ── 2. Trustee cross-appointments (same companies) ─────────────
    if trustee_appointments and len(trustee_appointments) >= 2:
        # Build company-to-trustees map
        company_trustees: dict[str, set[str]] = {}
        for trustee_name, appts in trustee_appointments.items():
            for appt in appts:
                co_num = appt.get("company_number", "")
                if co_num:
                    company_trustees.setdefault(co_num, set()).add(trustee_name)

        for co_num, shared_trustees in company_trustees.items():
            if len(shared_trustees) >= 2:
                names = sorted(shared_trustees)
                co_name = ""
                # Find company name from any trustee's appointments
                for t_name, appts in trustee_appointments.items():
                    for a in appts:
                        if a.get("company_number") == co_num:
                            co_name = a.get("company_name", co_num)
                            break
                    if co_name:
                        break

                overlaps.append(EntityOverlap(
                    entity_a=names[0], entity_b=names[1],
                    overlap_type="personnel",
                    detail=f"Both serve as officers at {co_name or co_num}"
                            + (f" (+ {len(names)-2} others)" if len(names) > 2 else ""),
                    similarity=1.0,
                    significance="medium",
                ))

    # ── 3. Officer name similar to entity name ──────────────────────
    all_person_names = list(trustees or [])
    if officers:
        entities_checked += len(officers)
        for officer in officers:
            oname = officer.get("name", "")
            if oname:
                all_person_names.append(oname)

    if entity_name and all_person_names:
        for pname in all_person_names:
            sim = name_similarity(entity_name, pname, is_person=False)
            if sim >= 0.50:
                name_matches.append(NameMatch(
                    source_name=entity_name,
                    matched_name=pname,
                    similarity=round(sim, 3),
                    match_type="partial" if sim < 0.85 else "fuzzy",
                    context="Entity name vs personnel",
                    risk_note="Personnel name resembles entity name — check for personal charity/company",
                ))

    # ── 4. Related company names vs entity name ─────────────────────
    if related_companies:
        entities_checked += len(related_companies)
        co_names = [c.get("company_name", c.get("name", "")) for c in related_companies if c.get("company_name") or c.get("name")]
        matches_found = find_similar_names(entity_name, co_names, threshold=0.55, context="Related companies")
        name_matches.extend(matches_found)

    # ── 5. Adverse results mentioning similar entities ───────────────
    if adverse_results and entity_name:
        entities_checked += len(adverse_results)
        for result in adverse_results[:20]:
            title = result.get("title", "")
            content = result.get("content", "")[:500]
            # Check if other entity names appear in adverse results
            for pname in (trustees or [])[:10]:
                norm_p = _normalise_person_name(pname)
                if norm_p and len(norm_p) > 5:
                    if norm_p in title.lower() or norm_p in content.lower():
                        name_matches.append(NameMatch(
                            source_name=pname,
                            matched_name=title[:100],
                            similarity=0.85,
                            match_type="partial",
                            context="Trustee name found in adverse media",
                            risk_note="Trustee mentioned in adverse media result",
                        ))

    # ── Build summary ────────────────────────────────────────────────
    high_sig = len([o for o in overlaps if o.significance == "high"])

    if not overlaps and not name_matches:
        summary = "No entity overlaps or suspicious name similarities detected."
    else:
        parts = []
        if overlaps:
            parts.append(f"{len(overlaps)} entity overlap(s) detected")
        if name_matches:
            parts.append(f"{len(name_matches)} name similarity match(es)")
        if high_sig:
            parts.append(f"{high_sig} high-significance")
        summary = " · ".join(parts)

    return OverlapReport(
        overlaps=overlaps,
        name_matches=name_matches[:20],  # Cap output size
        total_entities_checked=entities_checked,
        overlap_count=len(overlaps),
        high_significance_count=high_sig,
        summary=summary,
    )


def render_overlap_summary(report: OverlapReport) -> str:
    """Return HTML summary of entity overlaps for display."""
    if not report.overlaps and not report.name_matches:
        return """<div style="padding:8px 12px;border-radius:6px;background:#d4edda;
                    border:1px solid #c3e6cb;font-size:13px;color:#155724;">
                    ✅ No entity overlaps or suspicious name similarities detected.
                  </div>"""

    rows = ""
    for overlap in report.overlaps[:10]:
        sig_color = {"high": "#dc3545", "medium": "#ffc107", "low": "#6c757d"}.get(overlap.significance, "#6c757d")
        rows += f"""
        <tr>
            <td style="padding:5px;font-size:12px;">{overlap.entity_a}</td>
            <td style="padding:5px;font-size:12px;">↔</td>
            <td style="padding:5px;font-size:12px;">{overlap.entity_b}</td>
            <td style="padding:5px;font-size:12px;">{overlap.detail}</td>
            <td style="padding:5px;font-size:12px;"><span style="color:{sig_color};font-weight:600;">{overlap.significance.title()}</span></td>
        </tr>"""

    for nm in report.name_matches[:10]:
        rows += f"""
        <tr>
            <td style="padding:5px;font-size:12px;">{nm.source_name}</td>
            <td style="padding:5px;font-size:12px;">≈</td>
            <td style="padding:5px;font-size:12px;">{nm.matched_name[:60]}</td>
            <td style="padding:5px;font-size:12px;">{nm.context} ({int(nm.similarity*100)}%)</td>
            <td style="padding:5px;font-size:12px;color:#856404;">{nm.risk_note[:60]}</td>
        </tr>"""

    return f"""
    <div style="margin:8px 0;">
        <div style="font-size:13px;font-weight:600;margin-bottom:6px;color:#333;">
            🔗 {report.overlap_count} overlap(s) · {len(report.name_matches)} name match(es)
            {f'· <span style="color:#dc3545;">{report.high_significance_count} high-significance</span>' if report.high_significance_count else ''}
        </div>
        <table style="width:100%;border-collapse:collapse;font-size:12px;">
            <tbody>{rows}</tbody>
        </table>
    </div>
    """
