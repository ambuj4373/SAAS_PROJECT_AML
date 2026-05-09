"""
api_clients/ofac.py — OFAC Specially Designated Nationals (SDN) List client.

Downloads the OFAC SDN list and its alias supplement, caches them
locally, and exposes parsed entries with aliases.

OFAC publishes the SDN list as three companion CSV files at
treasury.gov/ofac/downloads/:
- sdn.csv — primary records (ent_num, name, sdn_type, program, …)
- alt.csv — aliases (ent_num, alt_num, alt_type, alt_name, …)
- add.csv — addresses (not used here for v1)

Files are headerless, latin-1 encoded, with "-0-" as the empty placeholder.

Source: https://ofac.treasury.gov/specially-designated-nationals-and-blocked-persons-list-sdn-human-readable-lists
License: Free, US Government public domain.

Public API
----------
- download_ofac_files(force=False) -> tuple[Path, Path]
- load_ofac_list() -> list[OfacEntry]
- OfacEntry — one sanctioned subject with all alias names

This module mirrors api_clients/ofsi.py for consistency.
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import get_ssl_verify

log = logging.getLogger("hrcob.api_clients.ofac")

OFAC_SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn.csv"
OFAC_ALT_URL = "https://www.treasury.gov/ofac/downloads/alt.csv"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _PROJECT_ROOT / "data" / "sanctions"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ─── Column positions (no header in source files) ─────────────────────────

# sdn.csv columns (per OFAC spec):
_SDN_NAME = 1
_SDN_TYPE = 2
_SDN_PROGRAM = 3
_SDN_REMARKS = 11

# alt.csv columns:
_ALT_ENT_NUM = 0
_ALT_NAME = 3

_EMPTY = "-0-"


def _clean(value: str) -> str:
    """Strip OFAC's '-0-' empty placeholder and whitespace."""
    if not value:
        return ""
    s = value.strip()
    return "" if s == _EMPTY else s


# ─── Data model ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class OfacEntry:
    """One sanctioned subject from the OFAC SDN list."""

    ent_num: str  # OFAC entity ID
    sdn_type: str  # "individual" | "" | "vessel" | "aircraft"
    names: tuple[str, ...]  # primary first, then aliases
    program: str  # e.g. "RUSSIA-EO14024", "SDGT", "CUBA"
    remarks: str = ""  # free-form, often contains DOB/POB

    @property
    def primary_name(self) -> str:
        return self.names[0] if self.names else ""

    @property
    def is_person(self) -> bool:
        return self.sdn_type.lower() == "individual"

    @property
    def is_entity(self) -> bool:
        # OFAC marks plain entities with '-0-' (empty after _clean)
        return self.sdn_type == "" and not self.is_person

    @property
    def is_vessel(self) -> bool:
        return self.sdn_type.lower() == "vessel"


# ─── Download + cache ──────────────────────────────────────────────────────

def _today_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _cache_path(name: str, stamp: str | None = None) -> Path:
    return _CACHE_DIR / f"ofac_{name}_{stamp or _today_stamp()}.csv"


def download_ofac_files(force: bool = False) -> tuple[Path, Path]:
    """Download today's sdn.csv and alt.csv. Idempotent."""
    sdn_target = _cache_path("sdn")
    alt_target = _cache_path("alt")

    if not (sdn_target.exists() and alt_target.exists()) or force:
        log.info(f"Downloading OFAC SDN + alt from treasury.gov")
        verify = get_ssl_verify()
        for url, target in [(OFAC_SDN_URL, sdn_target), (OFAC_ALT_URL, alt_target)]:
            resp = requests.get(url, timeout=60, verify=verify)
            resp.raise_for_status()
            target.write_bytes(resp.content)
            log.info(f"  saved {target.name} ({len(resp.content):,} bytes)")

    return sdn_target, alt_target


# ─── Parse ─────────────────────────────────────────────────────────────────

def load_ofac_list(
    *,
    sdn_path: Path | None = None,
    alt_path: Path | None = None,
) -> list[OfacEntry]:
    """Load and parse the OFAC SDN list with aliases joined.

    Aliases (alt.csv) are looked up by ent_num and appended after the
    primary name from sdn.csv. If paths are omitted, today's cache is
    used (or downloaded if absent).
    """
    if sdn_path is None or alt_path is None:
        s, a = download_ofac_files()
        sdn_path = sdn_path or s
        alt_path = alt_path or a

    # Aliases keyed by ent_num
    aliases_by_ent: dict[str, list[str]] = defaultdict(list)
    with alt_path.open(encoding="latin-1") as f:
        for row in csv.reader(f):
            if len(row) <= _ALT_NAME:
                continue
            ent_num = row[_ALT_ENT_NUM].strip()
            name = _clean(row[_ALT_NAME])
            if ent_num and name:
                aliases_by_ent[ent_num].append(name)

    entries: list[OfacEntry] = []
    with sdn_path.open(encoding="latin-1") as f:
        for row in csv.reader(f):
            if len(row) <= _SDN_REMARKS:
                continue
            ent_num = row[0].strip()
            name = _clean(row[_SDN_NAME])
            if not ent_num or not name:
                continue
            sdn_type = _clean(row[_SDN_TYPE])
            program = _clean(row[_SDN_PROGRAM])
            remarks = _clean(row[_SDN_REMARKS])[:500]

            names: list[str] = [name]
            for a in aliases_by_ent.get(ent_num, []):
                if a != name and a not in names:
                    names.append(a)

            entries.append(OfacEntry(
                ent_num=ent_num,
                sdn_type=sdn_type,
                names=tuple(names),
                program=program,
                remarks=remarks,
            ))
    return entries
