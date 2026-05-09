"""
reports/ — End-to-end report orchestrators.

The ``pipeline/`` package gathers data and builds the LLM prompt. The
``reports/`` package wraps that with the LLM call, self-verification,
structured-output parsing, and audit logging — i.e. everything required
to produce a final report bundle ready for rendering.

Public API:
    generate_charity_report(charity_number, **opts) -> CharityReportBundle
    generate_company_report(company_number, **opts) -> CompanyReportBundle

This package has no Streamlit dependency. UI integrations should call
the public functions and render the returned bundle.
"""

from reports.charity import CharityReportBundle, generate_charity_report
from reports.company import CompanyReportBundle, generate_company_report

__all__ = [
    "generate_charity_report",
    "CharityReportBundle",
    "generate_company_report",
    "CompanyReportBundle",
]
