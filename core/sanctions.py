"""
core/sanctions.py — Sanctions screening abstraction + OFSI implementation.

Defines a provider interface so multiple sanctions sources (OFSI, OFAC,
EU, UN, OpenSanctions) can be plugged in behind the same screening API.
The pipeline calls ``screen_against_providers(name, schema, providers)``
and gets a unified ``list[SanctionsHit]`` back.

Public API
----------
- SanctionsHit                 — one match result with confidence + source
- SanctionsProvider (ABC)      — interface every provider implements
- OfsiProvider                 — UK Treasury OFSI consolidated list
- screen_against_providers(...) — convenience top-level function
- default_providers()          — returns provider instances based on config

Matching strategy
-----------------
- Names are normalised (case-folded, accents stripped, double-spaces
  collapsed) before comparison.
- Each candidate name from the input is compared against every alias of
  every entry of the right schema (person/entity).
- Uses rapidfuzz.fuzz.WRatio (Weighted Ratio) which handles word order,
  partial matches, and length differences gracefully.
- Threshold defaults: >= 88 = high-confidence match (report it),
  75–88 = possible match (note for analyst review). Configurable.
"""

from __future__ import annotations

import logging
import threading
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable, Literal

from rapidfuzz import fuzz, process

from api_clients.ofac import OfacEntry, load_ofac_list
from api_clients.ofsi import OfsiEntry, load_ofsi_list
from api_clients.un_sanctions import UnEntry, load_un_list
from api_clients import opensanctions as opensanctions_api

log = logging.getLogger("hrcob.core.sanctions")

Schema = Literal["person", "entity"]


# ─── Result type ───────────────────────────────────────────────────────────

@dataclass
class SanctionsHit:
    """One match between a queried name and a sanctioned subject."""

    queried_name: str
    matched_alias: str  # the specific alias name that triggered the match
    primary_name: str  # canonical primary name of the sanctioned subject
    score: float  # 0–100
    confidence: Literal["high", "possible"]
    source: str  # "OFSI" | "OFAC" | ...
    source_id: str  # provider-specific group/list id
    schema: Schema
    regime: str = ""
    listed_on: str = ""
    country: str = ""
    nationality: str = ""
    dob: str = ""
    statement_of_reasons: str = ""
    citation: str = ""  # canonical citation string for the report

    @property
    def matched_name(self) -> str:
        """Backwards-compat alias for primary_name."""
        return self.primary_name

    def to_dict(self) -> dict:
        return {
            "queried_name": self.queried_name,
            "matched_alias": self.matched_alias,
            "primary_name": self.primary_name,
            "score": round(self.score, 1),
            "confidence": self.confidence,
            "source": self.source,
            "source_id": self.source_id,
            "schema": self.schema,
            "regime": self.regime,
            "listed_on": self.listed_on,
            "country": self.country,
            "nationality": self.nationality,
            "dob": self.dob,
            "statement_of_reasons": self.statement_of_reasons,
            "citation": self.citation,
        }


# ─── Name normalisation ────────────────────────────────────────────────────

def _normalise(name: str) -> str:
    """Case-fold, strip accents, collapse whitespace."""
    if not name:
        return ""
    nfkd = unicodedata.normalize("NFKD", name)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(no_accents.casefold().split())


# ─── Provider interface ────────────────────────────────────────────────────

class SanctionsProvider(ABC):
    """Abstract sanctions data source."""

    name: str = "unknown"

    @abstractmethod
    def screen(
        self,
        queried_name: str,
        *,
        schema: Schema,
        high_threshold: float = 88.0,
        possible_threshold: float = 75.0,
    ) -> list[SanctionsHit]:
        """Return any matches for ``queried_name`` against this provider's data."""


# ─── OFSI implementation ───────────────────────────────────────────────────

class OfsiProvider(SanctionsProvider):
    """UK Treasury / OFSI consolidated sanctions list."""

    name = "OFSI"
    SOURCE_LABEL = "OFSI"
    CITATION_TMPL = (
        "OFSI Consolidated List of Financial Sanctions Targets in the UK "
        "(Source: HM Treasury / gov.uk, Group ID {group_id}, Regime: {regime})"
    )

    def __init__(self) -> None:
        # Lazy load + cache the parsed list (16MB CSV → ~5K entries)
        self._entries: list[OfsiEntry] | None = None
        self._lock = threading.Lock()
        # Index of (normalised_name, original_name, entry, schema_str)
        self._index: list[tuple[str, str, OfsiEntry, str]] | None = None

    def _ensure_loaded(self) -> None:
        if self._entries is not None:
            return
        with self._lock:
            if self._entries is not None:
                return
            log.info("Loading OFSI consolidated list…")
            entries = load_ofsi_list()
            index: list[tuple[str, str, OfsiEntry, str]] = []
            for entry in entries:
                schema = (
                    "person" if entry.is_person
                    else "entity" if entry.is_entity
                    else ""
                )
                if not schema:
                    continue  # skip ships for now
                for n in entry.names:
                    norm = _normalise(n)
                    if norm:
                        index.append((norm, n, entry, schema))
            self._entries = entries
            self._index = index
            log.info(
                f"OFSI loaded: {len(entries):,} subjects, "
                f"{len(index):,} alias index entries"
            )

    def screen(
        self,
        queried_name: str,
        *,
        schema: Schema,
        high_threshold: float = 88.0,
        possible_threshold: float = 80.0,
    ) -> list[SanctionsHit]:
        self._ensure_loaded()
        assert self._index is not None

        q = _normalise(queried_name)
        if not q:
            return []

        # Filter index by schema first to avoid cross-schema noise
        filtered = [(n, orig, e) for n, orig, e, s in self._index if s == schema]
        if not filtered:
            return []

        choices = [n for n, _, _ in filtered]

        # token_set_ratio is much stricter than WRatio: it requires word-level
        # overlap rather than rewarding substring matches. Drastically lower
        # false positive rate on short queries vs long indexed names.
        results = process.extract(
            q,
            choices,
            scorer=fuzz.token_set_ratio,
            score_cutoff=possible_threshold,
            limit=50,
        )

        # Tokenise the query once for the post-filter below
        q_tokens = {t for t in q.split() if len(t) >= 2}

        hits: list[SanctionsHit] = []
        seen_groups: set[str] = set()
        for matched_norm, score, idx in results:
            _, original_name, entry = filtered[idx]

            # Post-filter: reject single-word-alias false positives.
            # If the indexed alias has FEWER distinctive tokens than the
            # query, require at least 60% of the query's tokens to appear
            # in the alias. This kills "Yaseer Ahmed" → "AHMED" while
            # keeping "Putin" → "Vladimir PUTIN" (alias has more tokens
            # than query, so the rule doesn't fire).
            a_tokens = {t for t in matched_norm.split() if len(t) >= 2}
            if a_tokens and len(a_tokens) < len(q_tokens):
                common = q_tokens & a_tokens
                if not q_tokens or len(common) / len(q_tokens) < 0.6:
                    continue

            # Dedupe by Group ID — keep highest-scoring alias only
            if entry.group_id in seen_groups:
                continue
            seen_groups.add(entry.group_id)

            confidence: Literal["high", "possible"] = (
                "high" if score >= high_threshold else "possible"
            )
            hits.append(SanctionsHit(
                queried_name=queried_name,
                matched_alias=original_name,
                primary_name=entry.primary_name or original_name,
                score=float(score),
                confidence=confidence,
                source=self.SOURCE_LABEL,
                source_id=entry.group_id,
                schema=schema,
                regime=entry.regime,
                listed_on=entry.listed_on,
                country=entry.country,
                nationality=entry.nationality,
                dob=entry.dob,
                statement_of_reasons=entry.other_information,
                citation=self.CITATION_TMPL.format(
                    group_id=entry.group_id,
                    regime=entry.regime or "—",
                ),
            ))

        return hits


# ─── Top-level convenience ─────────────────────────────────────────────────

class OfacProvider(SanctionsProvider):
    """US Treasury / OFAC Specially Designated Nationals (SDN) list."""

    name = "OFAC"
    SOURCE_LABEL = "OFAC"
    CITATION_TMPL = (
        "OFAC Specially Designated Nationals and Blocked Persons (SDN) List "
        "(Source: US Treasury OFAC, ent_num {ent_num}, Program: {program})"
    )

    def __init__(self) -> None:
        self._entries: list[OfacEntry] | None = None
        self._lock = threading.Lock()
        self._index: list[tuple[str, str, OfacEntry, str]] | None = None

    def _ensure_loaded(self) -> None:
        if self._entries is not None:
            return
        with self._lock:
            if self._entries is not None:
                return
            log.info("Loading OFAC SDN list…")
            entries = load_ofac_list()
            index: list[tuple[str, str, OfacEntry, str]] = []
            for entry in entries:
                schema = (
                    "person" if entry.is_person
                    else "entity" if entry.is_entity
                    else ""
                )
                if not schema:
                    continue  # skip vessels / aircraft for v1
                for n in entry.names:
                    norm = _normalise(n)
                    if norm:
                        index.append((norm, n, entry, schema))
            self._entries = entries
            self._index = index
            log.info(
                f"OFAC loaded: {len(entries):,} subjects, "
                f"{len(index):,} alias index entries"
            )

    def screen(
        self,
        queried_name: str,
        *,
        schema: Schema,
        high_threshold: float = 88.0,
        possible_threshold: float = 80.0,
    ) -> list[SanctionsHit]:
        self._ensure_loaded()
        assert self._index is not None

        q = _normalise(queried_name)
        if not q:
            return []

        filtered = [(n, orig, e) for n, orig, e, s in self._index if s == schema]
        if not filtered:
            return []

        choices = [n for n, _, _ in filtered]
        results = process.extract(
            q,
            choices,
            scorer=fuzz.token_set_ratio,
            score_cutoff=possible_threshold,
            limit=50,
        )

        q_tokens = {t for t in q.split() if len(t) >= 2}

        hits: list[SanctionsHit] = []
        seen_ents: set[str] = set()
        for matched_norm, score, idx in results:
            _, original_name, entry = filtered[idx]

            # Same false-positive guard as OfsiProvider
            a_tokens = {t for t in matched_norm.split() if len(t) >= 2}
            if a_tokens and len(a_tokens) < len(q_tokens):
                common = q_tokens & a_tokens
                if not q_tokens or len(common) / len(q_tokens) < 0.6:
                    continue

            if entry.ent_num in seen_ents:
                continue
            seen_ents.add(entry.ent_num)

            confidence: Literal["high", "possible"] = (
                "high" if score >= high_threshold else "possible"
            )
            hits.append(SanctionsHit(
                queried_name=queried_name,
                matched_alias=original_name,
                primary_name=entry.primary_name or original_name,
                score=float(score),
                confidence=confidence,
                source=self.SOURCE_LABEL,
                source_id=entry.ent_num,
                schema=schema,
                regime=entry.program,
                listed_on="",  # OFAC publishes program-level listing dates separately
                country="",
                nationality="",
                dob="",
                statement_of_reasons=entry.remarks,
                citation=self.CITATION_TMPL.format(
                    ent_num=entry.ent_num,
                    program=entry.program or "—",
                ),
            ))

        return hits


class UnProvider(SanctionsProvider):
    """UN Security Council Consolidated List of Individuals and Entities.

    Free, authoritative; binding on all UN member states under Chapter VII
    of the UN Charter. Significant overlap with OFSI/OFAC since both
    implement UN designations, but UN as a primary citation is valuable
    for reports referencing international (vs. domestic) sanctions law.
    """

    name = "UN"
    SOURCE_LABEL = "UN"
    CITATION_TMPL = (
        "UN Security Council Consolidated List of Individuals and Entities "
        "subject to Sanctions (Source: scsanctions.un.org, "
        "Reference {reference_number}, List: {list_type})"
    )

    def __init__(self) -> None:
        self._entries: list[UnEntry] | None = None
        self._lock = threading.Lock()
        self._index: list[tuple[str, str, UnEntry, str]] | None = None

    def _ensure_loaded(self) -> None:
        if self._entries is not None:
            return
        with self._lock:
            if self._entries is not None:
                return
            log.info("Loading UN consolidated list…")
            entries = load_un_list()
            index: list[tuple[str, str, UnEntry, str]] = []
            for entry in entries:
                schema = (
                    "person" if entry.is_person
                    else "entity" if entry.is_entity
                    else ""
                )
                if not schema:
                    continue
                for n in entry.names:
                    norm = _normalise(n)
                    if norm:
                        index.append((norm, n, entry, schema))
            self._entries = entries
            self._index = index
            log.info(
                f"UN loaded: {len(entries):,} subjects, "
                f"{len(index):,} alias index entries"
            )

    def screen(
        self,
        queried_name: str,
        *,
        schema: Schema,
        high_threshold: float = 88.0,
        possible_threshold: float = 80.0,
    ) -> list[SanctionsHit]:
        self._ensure_loaded()
        assert self._index is not None

        q = _normalise(queried_name)
        if not q:
            return []

        filtered = [(n, orig, e) for n, orig, e, s in self._index if s == schema]
        if not filtered:
            return []

        choices = [n for n, _, _ in filtered]
        results = process.extract(
            q, choices, scorer=fuzz.token_set_ratio,
            score_cutoff=possible_threshold, limit=50,
        )

        q_tokens = {t for t in q.split() if len(t) >= 2}
        hits: list[SanctionsHit] = []
        seen_ids: set[str] = set()
        for matched_norm, score, idx in results:
            _, original_name, entry = filtered[idx]

            a_tokens = {t for t in matched_norm.split() if len(t) >= 2}
            if a_tokens and len(a_tokens) < len(q_tokens):
                common = q_tokens & a_tokens
                if not q_tokens or len(common) / len(q_tokens) < 0.6:
                    continue

            if entry.data_id in seen_ids:
                continue
            seen_ids.add(entry.data_id)

            confidence: Literal["high", "possible"] = (
                "high" if score >= high_threshold else "possible"
            )
            hits.append(SanctionsHit(
                queried_name=queried_name,
                matched_alias=original_name,
                primary_name=entry.primary_name or original_name,
                score=float(score),
                confidence=confidence,
                source=self.SOURCE_LABEL,
                source_id=entry.reference_number or entry.data_id,
                schema=schema,
                regime=entry.list_type,
                listed_on=entry.listed_on,
                country="",
                nationality=entry.nationality,
                dob="",
                statement_of_reasons=entry.comments,
                citation=self.CITATION_TMPL.format(
                    reference_number=entry.reference_number or entry.data_id,
                    list_type=entry.list_type or "—",
                ),
            ))

        return hits


class OpenSanctionsProvider(SanctionsProvider):
    """OpenSanctions paid-tier API provider (~300 lists incl. PEP).

    Activates automatically when ``OPENSANCTIONS_API_KEY`` is set in
    the environment. Charges ~€0.10 per call. Fundamentally a network
    call per query; no local index. Use sparingly — the local OFSI/
    OFAC providers should be the primary screening layer.
    """

    name = "OpenSanctions"
    SOURCE_LABEL = "OpenSanctions"
    CITATION_TMPL = (
        "OpenSanctions match (Source: opensanctions.org, "
        "ID {os_id}, schema: {os_schema})"
    )

    # OpenSanctions returns scores 0-1; we surface them as 0-100 to
    # match the local providers' convention.
    OS_HIGH = 0.88
    OS_POSSIBLE = 0.75

    def screen(
        self,
        queried_name: str,
        *,
        schema: Schema,
        high_threshold: float = 88.0,
        possible_threshold: float = 75.0,
    ) -> list[SanctionsHit]:
        if not opensanctions_api.is_configured():
            # Caller silently gets nothing — OpenSanctions is opt-in
            return []

        # Map our "person"/"entity" to OpenSanctions schemas
        os_schema = "Person" if schema == "person" else "Organization"

        try:
            results = opensanctions_api.match_entity(
                queried_name,
                schema=os_schema,
                threshold=possible_threshold / 100.0,
            )
        except Exception as e:
            log.warning(f"OpenSanctions API error for {queried_name!r}: {e}")
            return []

        hits: list[SanctionsHit] = []
        for r in results:
            score = float(r.get("score", 0)) * 100.0
            if score < possible_threshold:
                continue
            confidence: Literal["high", "possible"] = (
                "high" if score >= high_threshold else "possible"
            )
            props = r.get("properties", {}) or {}
            os_id = r.get("id", "")
            schemas = r.get("schema", os_schema)
            regimes = props.get("topics", []) or props.get("sanctions", [])
            regime = ", ".join(regimes[:3]) if isinstance(regimes, list) else str(regimes)

            hits.append(SanctionsHit(
                queried_name=queried_name,
                matched_alias=r.get("caption", queried_name),
                primary_name=r.get("caption", queried_name),
                score=score,
                confidence=confidence,
                source=self.SOURCE_LABEL,
                source_id=os_id,
                schema=schema,
                regime=regime,
                listed_on=", ".join(props.get("listingDate", [])[:1]) if props.get("listingDate") else "",
                country=", ".join(props.get("country", [])[:1]) if props.get("country") else "",
                nationality=", ".join(props.get("nationality", [])[:1]) if props.get("nationality") else "",
                dob=", ".join(props.get("birthDate", [])[:1]) if props.get("birthDate") else "",
                statement_of_reasons=str(props.get("description", [""])[0])[:500] if props.get("description") else "",
                citation=self.CITATION_TMPL.format(
                    os_id=os_id, os_schema=schemas,
                ),
            ))
        return hits


_default_provider_singletons: list[SanctionsProvider] | None = None


def default_providers() -> list[SanctionsProvider]:
    """Return the default list of providers for the current config.

    Always active (free + authoritative + downloadable):
        OFSI — UK Treasury consolidated list
        OFAC — US Treasury SDN list
        UN   — UN Security Council Consolidated List

    Opt-in (paid):
        OpenSanctions — added IFF OPENSANCTIONS_API_KEY env var is set

    Not yet integrated:
        EU — EU Consolidated List (their public download endpoint now
        requires registration; integrate when needed)

    Providers are cached as module-level singletons so the underlying
    CSV/XML files are parsed once per process.
    """
    global _default_provider_singletons
    if _default_provider_singletons is None:
        providers: list[SanctionsProvider] = [
            OfsiProvider(),
            OfacProvider(),
            UnProvider(),
        ]
        if opensanctions_api.is_configured():
            log.info("OpenSanctions API key detected — adding to default providers")
            providers.append(OpenSanctionsProvider())
        _default_provider_singletons = providers
    return _default_provider_singletons


def screen_against_providers(
    queried_name: str,
    *,
    schema: Schema,
    providers: Iterable[SanctionsProvider] | None = None,
    high_threshold: float = 88.0,
    possible_threshold: float = 75.0,
) -> list[SanctionsHit]:
    """Run a name through every provider and return the merged hits."""
    if providers is None:
        providers = default_providers()

    all_hits: list[SanctionsHit] = []
    for p in providers:
        try:
            hits = p.screen(
                queried_name,
                schema=schema,
                high_threshold=high_threshold,
                possible_threshold=possible_threshold,
            )
            all_hits.extend(hits)
        except Exception as e:
            log.warning(f"Provider {p.name} screening failed for {queried_name!r}: {e}")
    # Sort by score descending so the most likely match is first
    all_hits.sort(key=lambda h: h.score, reverse=True)
    return all_hits
