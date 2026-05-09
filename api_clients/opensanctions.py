"""
api_clients/opensanctions.py — OpenSanctions API client (paid tier).

OpenSanctions aggregates sanctions, PEP, and watchlist data from ~300
sources worldwide. As of 2024 they require an API key for all access
(€0.10 per call on the cloud tier, or a separate license for bulk /
on-premises use).

This module is a thin live-API client. It is intentionally NOT included
in `default_providers()` — it activates only when an explicit
``OPENSANCTIONS_API_KEY`` env var is set. The architecture is ready;
just add the key to ``.env`` and the provider becomes available.

Source:    https://www.opensanctions.org/
Pricing:   €0.10 / API call (pay-as-you-go) or bulk license
Licensing: CC-BY 4.0 NonCommercial; commercial use requires a data licence

Public API
----------
- match_entity(name, *, schema, dataset="default") -> list[dict]
    Calls /match/<dataset> with the given name, returns the raw
    OpenSanctions match results. Raises ``OpenSanctionsKeyMissing``
    if no API key is configured.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

import requests

from config import get_ssl_verify

log = logging.getLogger("hrcob.api_clients.opensanctions")

OPENSANCTIONS_BASE = "https://api.opensanctions.org"
ENV_KEY_NAME = "OPENSANCTIONS_API_KEY"

Schema = Literal["Person", "Organization", "Company", "LegalEntity"]


class OpenSanctionsKeyMissing(RuntimeError):
    """Raised when the API is invoked without the required key.

    Add ``OPENSANCTIONS_API_KEY=<your-key>`` to ``.env`` to enable.
    """


def _get_api_key() -> str:
    key = os.getenv(ENV_KEY_NAME, "").strip()
    if not key:
        raise OpenSanctionsKeyMissing(
            f"{ENV_KEY_NAME} is not set. OpenSanctions requires a paid API "
            f"key. Sign up at https://www.opensanctions.org/api/ for a "
            f"30-day trial, then add the key to .env."
        )
    return key


def is_configured() -> bool:
    """Cheap check: can we use OpenSanctions without raising?"""
    return bool(os.getenv(ENV_KEY_NAME, "").strip())


def match_entity(
    name: str,
    *,
    schema: Schema = "LegalEntity",
    dataset: str = "default",
    threshold: float = 0.7,
    limit: int = 5,
    timeout: int = 30,
) -> list[dict]:
    """Match a single name against OpenSanctions.

    Parameters
    ----------
    name : str
        The full name to screen.
    schema : str
        OpenSanctions schema. "Person" for individuals, "Organization"
        or "Company" for legal entities, "LegalEntity" for either.
    dataset : str
        Which OpenSanctions collection to query. "default" is the full
        sanctions + PEP + risk dataset; "sanctions" is sanctions only.
    threshold : float
        Minimum match score (0-1). OpenSanctions default is 0.7.
    limit : int
        Max matches to return per query.

    Returns
    -------
    list[dict]
        Raw OpenSanctions match objects, in score order. Each contains
        ``id``, ``caption``, ``schema``, ``properties`` (with
        ``topics``, ``sanctions``, etc.), and ``score``.

    Raises
    ------
    OpenSanctionsKeyMissing
        If no API key is configured.
    requests.HTTPError
        For non-2xx responses.
    """
    api_key = _get_api_key()

    url = f"{OPENSANCTIONS_BASE}/match/{dataset}"
    payload = {
        "queries": {
            "q1": {"schema": schema, "properties": {"name": [name]}},
        },
        "threshold": threshold,
        "limit": limit,
    }
    headers = {
        "Authorization": f"ApiKey {api_key}",
        "Content-Type": "application/json",
    }

    log.debug(f"OpenSanctions match request: {name!r} ({schema})")
    resp = requests.post(
        url,
        json=payload,
        headers=headers,
        timeout=timeout,
        verify=get_ssl_verify(),
    )
    resp.raise_for_status()
    data = resp.json()

    # Response shape: {"responses": {"q1": {"results": [...], "total": {...}}}}
    return (
        data.get("responses", {})
        .get("q1", {})
        .get("results", [])
    )
