"""
api_clients/ofsi.py — OFSI Consolidated UK Sanctions List client.

Downloads the official UK government consolidated sanctions list, caches
it locally, and exposes a parsed list of sanctioned entries with their
aliases.

The OFSI list is published by the UK Treasury (HM Treasury / Office of
Financial Sanctions Implementation) and is the authoritative source for
UK sanctions screening. It is free, requires no API key, and is licensed
for any use under the UK Open Government Licence.

Source: https://www.gov.uk/government/publications/the-uk-sanctions-list
Direct CSV: https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.csv

Public API
----------
- download_ofsi_list(force=False) -> Path
- load_ofsi_list() -> list[OfsiEntry]
- OfsiEntry — one sanctioned individual, entity or ship, with all alias names

Each row in the CSV represents one *name variation*. Multiple rows share
the same Group ID for a single sanctioned subject (primary name + aliases).
This module groups rows by Group ID into one OfsiEntry per subject.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import get_ssl_verify

log = logging.getLogger("hrcob.api_clients.ofsi")

OFSI_CSV_URL = (
    "https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.csv"
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _PROJECT_ROOT / "data" / "sanctions"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ─── Data model ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class OfsiEntry:
    """One sanctioned subject (individual, entity or ship).

    Multiple rows in the OFSI CSV are aggregated into a single entry,
    keyed by Group ID. ``names`` contains every name variation listed
    (primary name first, then aliases).
    """

    group_id: str
    group_type: str  # "Individual" | "Entity" | "Ship"
    names: tuple[str, ...]  # primary first, then aliases
    regime: str  # e.g. "Russia", "ISIL (Da'esh) and Al-Qaida"
    listed_on: str  # date string from OFSI, e.g. "09/12/2022"
    country: str = ""  # entity country, "" for individuals/ships
    nationality: str = ""  # individual nationality
    dob: str = ""  # individual DoB
    other_information: str = ""  # statement of reasons (truncated)

    @property
    def primary_name(self) -> str:
        return self.names[0] if self.names else ""

    @property
    def is_person(self) -> bool:
        return self.group_type == "Individual"

    @property
    def is_entity(self) -> bool:
        return self.group_type == "Entity"


# ─── Download + cache ──────────────────────────────────────────────────────

def _today_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _cache_path_for(stamp: str | None = None) -> Path:
    return _CACHE_DIR / f"ofsi_conlist_{stamp or _today_stamp()}.csv"


def _latest_cached() -> Path | None:
    """Return the most-recently-dated OFSI cache file, or None."""
    matches = sorted(_CACHE_DIR.glob("ofsi_conlist_*.csv"))
    return matches[-1] if matches else None


def download_ofsi_list(force: bool = False) -> Path:
    """Download the OFSI CSV to the data cache. Idempotent.

    If a cache file from today already exists, it is reused. Pass
    ``force=True`` to re-download regardless.
    """
    target = _cache_path_for()
    if target.exists() and not force:
        return target

    log.info(f"Downloading OFSI consolidated list from {OFSI_CSV_URL}")
    resp = requests.get(OFSI_CSV_URL, timeout=60, verify=get_ssl_verify())
    resp.raise_for_status()
    target.write_bytes(resp.content)
    log.info(f"OFSI list saved to {target} ({len(resp.content):,} bytes)")
    return target


# ─── Parse ─────────────────────────────────────────────────────────────────

def _row_full_name(row: dict) -> str:
    """Concatenate Name 1..Name 6 into a single space-separated name."""
    parts = [row.get(f"Name {i}", "").strip() for i in range(1, 7)]
    return " ".join(p for p in parts if p)


def load_ofsi_list(*, csv_path: Path | None = None) -> list[OfsiEntry]:
    """Load and parse the OFSI list, grouped by Group ID.

    If ``csv_path`` is None, downloads a fresh copy if today's cache is
    absent, otherwise reuses today's cache. To always use the most recent
    cached copy without trying to download (e.g. offline tests), pass the
    return value of ``_latest_cached()`` explicitly.
    """
    if csv_path is None:
        csv_path = download_ofsi_list()

    # group_id -> dict accumulator
    groups: dict[str, dict] = {}

    with csv_path.open(encoding="utf-8") as f:
        # First line is "Last Updated,DD/MM/YYYY"
        first = f.readline()
        if not first.lower().startswith("last updated"):
            f.seek(0)
        reader = csv.DictReader(f)
        for row in reader:
            gid = (row.get("Group ID") or "").strip()
            if not gid:
                continue
            full = _row_full_name(row)
            if not full:
                continue
            alias_type = (row.get("Alias Type") or "").strip().lower()
            is_primary = "primary" in alias_type

            grp = groups.setdefault(gid, {
                "group_id": gid,
                "group_type": (row.get("Group Type") or "").strip(),
                "primary_name": "",
                "aliases": [],
                "regime": (row.get("Regime") or "").strip(),
                "listed_on": (row.get("Listed On") or "").strip(),
                "country": (row.get("Country") or "").strip(),
                "nationality": (row.get("Nationality") or "").strip(),
                "dob": (row.get("DOB") or "").strip(),
                "other_information": (
                    (row.get("Other Information") or "").strip()[:500]
                ),
            })

            if is_primary and not grp["primary_name"]:
                grp["primary_name"] = full
            elif full not in grp["aliases"]:
                grp["aliases"].append(full)

    entries: list[OfsiEntry] = []
    for grp in groups.values():
        names: list[str] = []
        if grp["primary_name"]:
            names.append(grp["primary_name"])
        names.extend(a for a in grp["aliases"] if a != grp["primary_name"])
        if not names:
            continue
        entries.append(OfsiEntry(
            group_id=grp["group_id"],
            group_type=grp["group_type"],
            names=tuple(names),
            regime=grp["regime"],
            listed_on=grp["listed_on"],
            country=grp["country"],
            nationality=grp["nationality"],
            dob=grp["dob"],
            other_information=grp["other_information"],
        ))
    return entries
