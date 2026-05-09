"""
api_clients/un_sanctions.py — UN Security Council Consolidated List client.

Downloads the UN Security Council Consolidated List of Individuals and
Entities subject to UN-mandated sanctions, caches it locally, and
exposes parsed entries with aliases.

The list is published as XML at scsanctions.un.org. Free, no API key
required. UN sanctions implemented under Chapter VII of the UN Charter
are binding on all UN member states — these designations almost always
also appear on OFSI / OFAC, but UN as a separate citation is valuable
for non-UK/US use cases and for completeness.

Source:    https://www.un.org/securitycouncil/content/un-sc-consolidated-list
Direct XML: https://scsanctions.un.org/resources/xml/en/consolidated.xml
License:   UN open data, free to use with attribution.

Public API
----------
- download_un_list(force=False) -> Path
- load_un_list() -> list[UnEntry]
- UnEntry — one sanctioned subject with all alias names
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import get_ssl_verify

log = logging.getLogger("hrcob.api_clients.un_sanctions")

UN_XML_URL = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _PROJECT_ROOT / "data" / "sanctions"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ─── Data model ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class UnEntry:
    """One sanctioned subject from the UN Security Council list."""

    data_id: str  # UN unique identifier (DATAID)
    reference_number: str  # e.g. "CDi.001" (Country-code, individual, sequential)
    entry_type: str  # "Individual" | "Entity"
    names: tuple[str, ...]  # primary first, then aliases
    list_type: str  # e.g. "DRC", "ISIL/Al-Qaida"
    listed_on: str  # ISO date
    comments: str = ""
    nationality: str = ""

    @property
    def primary_name(self) -> str:
        return self.names[0] if self.names else ""

    @property
    def is_person(self) -> bool:
        return self.entry_type == "Individual"

    @property
    def is_entity(self) -> bool:
        return self.entry_type == "Entity"


# ─── Download + cache ──────────────────────────────────────────────────────

def _today_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _cache_path(stamp: str | None = None) -> Path:
    return _CACHE_DIR / f"un_consolidated_{stamp or _today_stamp()}.xml"


def download_un_list(force: bool = False) -> Path:
    """Download today's UN consolidated list XML. Idempotent."""
    target = _cache_path()
    if target.exists() and not force:
        return target

    log.info(f"Downloading UN consolidated list from {UN_XML_URL}")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; AML-pipeline/1.0)"}
    resp = requests.get(
        UN_XML_URL, timeout=60, verify=get_ssl_verify(), headers=headers,
    )
    resp.raise_for_status()
    target.write_bytes(resp.content)
    log.info(f"UN list saved to {target} ({len(resp.content):,} bytes)")
    return target


# ─── Parse ─────────────────────────────────────────────────────────────────

def _join_name_parts(elem: ET.Element) -> str:
    """Concatenate FIRST/SECOND/THIRD/FOURTH name parts."""
    parts: list[str] = []
    for tag in ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME"):
        sub = elem.find(tag)
        if sub is not None and sub.text:
            t = sub.text.strip()
            if t:
                parts.append(t)
    return " ".join(parts)


def _collect_aliases(elem: ET.Element, alias_tag: str) -> list[str]:
    """Find every ALIAS_NAME child inside repeated alias elements."""
    aliases: list[str] = []
    for alias_el in elem.findall(alias_tag):
        name_el = alias_el.find("ALIAS_NAME")
        if name_el is not None and name_el.text:
            t = name_el.text.strip()
            if t and t not in aliases:
                aliases.append(t)
    return aliases


def _parse_individual(elem: ET.Element) -> UnEntry | None:
    primary = _join_name_parts(elem)
    if not primary:
        return None
    aliases = _collect_aliases(elem, "INDIVIDUAL_ALIAS")
    names = [primary]
    for a in aliases:
        if a not in names:
            names.append(a)
    nationality_el = elem.find("NATIONALITY/VALUE")
    nationality = nationality_el.text.strip() if (nationality_el is not None and nationality_el.text) else ""
    comments_el = elem.find("COMMENTS1")
    comments = (comments_el.text or "")[:500].strip() if comments_el is not None else ""
    return UnEntry(
        data_id=(elem.findtext("DATAID") or "").strip(),
        reference_number=(elem.findtext("REFERENCE_NUMBER") or "").strip(),
        entry_type="Individual",
        names=tuple(names),
        list_type=(elem.findtext("UN_LIST_TYPE") or "").strip(),
        listed_on=(elem.findtext("LISTED_ON") or "").strip(),
        comments=comments,
        nationality=nationality,
    )


def _parse_entity(elem: ET.Element) -> UnEntry | None:
    primary = _join_name_parts(elem)
    if not primary:
        return None
    aliases = _collect_aliases(elem, "ENTITY_ALIAS")
    names = [primary]
    for a in aliases:
        if a not in names:
            names.append(a)
    comments_el = elem.find("COMMENTS1")
    comments = (comments_el.text or "")[:500].strip() if comments_el is not None else ""
    return UnEntry(
        data_id=(elem.findtext("DATAID") or "").strip(),
        reference_number=(elem.findtext("REFERENCE_NUMBER") or "").strip(),
        entry_type="Entity",
        names=tuple(names),
        list_type=(elem.findtext("UN_LIST_TYPE") or "").strip(),
        listed_on=(elem.findtext("LISTED_ON") or "").strip(),
        comments=comments,
    )


def load_un_list(*, xml_path: Path | None = None) -> list[UnEntry]:
    """Parse the UN consolidated list into a flat list of UnEntry."""
    if xml_path is None:
        xml_path = download_un_list()

    tree = ET.parse(xml_path)
    root = tree.getroot()

    entries: list[UnEntry] = []
    indivs = root.find("INDIVIDUALS")
    if indivs is not None:
        for el in indivs.findall("INDIVIDUAL"):
            entry = _parse_individual(el)
            if entry:
                entries.append(entry)

    ents = root.find("ENTITIES")
    if ents is not None:
        for el in ents.findall("ENTITY"):
            entry = _parse_entity(el)
            if entry:
                entries.append(entry)

    return entries
