"""
core/llm_client.py — LLM provider calls and routing.

Lifted from app.py with no behavioural changes. The two Streamlit
couplings (session_state model selection, st.toast notifications) are
replaced by explicit parameters: ``model_label`` and ``on_rate_limit``.

Public API
----------
- llm_generate(prompt, *, model_label=None, max_retries=3, on_rate_limit=None)
    Generate text via the selected (or first-available) LLM provider, with
    automatic fallback to other providers on rate-limit. Returns
    ``(text, cost_info)``.

This module has no Streamlit dependency.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from config import (
    LLM_PROVIDERS,
    _calc_cost,
    gemini_client,
    openai_client,
)

RateLimitCallback = Callable[[str, int, int, str], None]
"""(label, attempt_index, wait_seconds, status) -> None.
status is one of: 'retrying', 'exhausted'."""


def _call_gemini(prompt: str, model_name: str) -> tuple[str, dict]:
    response = gemini_client.models.generate_content(model=model_name, contents=prompt)
    usage = getattr(response, "usage_metadata", None)
    prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
    completion_tokens = getattr(usage, "candidates_token_count", 0) or 0
    cost = _calc_cost(model_name, prompt_tokens, completion_tokens)
    cost_info = {
        "model": model_name,
        "provider": "gemini",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_usd": cost,
    }
    return response.text or "", cost_info


def _call_openai(prompt: str, model_name: str) -> tuple[str, dict]:
    response = openai_client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": (
                "You are a professional KYC/AML compliance analyst. "
                "Write thorough, evidence-based due-diligence reports. "
                "Be analytical — interpret data, identify and contextualise risk indicators "
                "and control strengths, and make proportionate assessments. "
                "Use markdown: tables, bold, hyperlinks. "
                "Every sentence must add value. Never fabricate — if data is missing, "
                "say so explicitly."
            )},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=8000,
    )
    usage = response.usage
    prompt_tokens = usage.prompt_tokens if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0
    cost = _calc_cost(model_name, prompt_tokens, completion_tokens)
    cost_info = {
        "model": model_name,
        "provider": "openai",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_usd": cost,
    }
    text = ""
    if response.choices:
        text = response.choices[0].message.content or ""
    return text, cost_info


def llm_generate(
    prompt: str,
    *,
    model_label: Optional[str] = None,
    max_retries: int = 3,
    on_rate_limit: Optional[RateLimitCallback] = None,
) -> tuple[str, dict]:
    """Generate LLM output with provider fallback and rate-limit handling.

    Parameters
    ----------
    prompt : str
        The full user prompt.
    model_label : str, optional
        A key from ``config.LLM_PROVIDERS`` (e.g. "GPT-4.1 mini  [$0.014/report]").
        If None, the first available provider is used.
    max_retries : int
        Max retry attempts per provider on rate-limit before falling
        through to the next provider.
    on_rate_limit : callable, optional
        ``(label, attempt_index, wait_seconds, status)`` notification hook
        for UI integrations (e.g. Streamlit toast). Status is "retrying"
        or "exhausted". If omitted, rate-limit events are silent.

    Returns
    -------
    (text, cost_info) : tuple[str, dict]
        ``cost_info`` keys: model, provider, prompt_tokens,
        completion_tokens, total_tokens, cost_usd.
    """
    if not LLM_PROVIDERS:
        raise RuntimeError("No LLM providers configured (check API keys in .env)")

    all_labels = list(LLM_PROVIDERS.keys())
    selected = model_label if model_label in LLM_PROVIDERS else all_labels[0]
    ordered = [selected] + [l for l in all_labels if l != selected]

    last_err: Optional[Exception] = None
    for label in ordered:
        provider, model = LLM_PROVIDERS[label]
        for attempt in range(max_retries):
            try:
                if provider == "gemini":
                    return _call_gemini(prompt, model)
                else:
                    return _call_openai(prompt, model)
            except Exception as e:
                last_err = e
                err_str = str(e)
                is_rate_limit = (
                    "429" in err_str
                    or "RESOURCE_EXHAUSTED" in err_str
                    or "rate" in err_str.lower()
                )
                if is_rate_limit:
                    wait = min(2 ** attempt * 5, 60)
                    if attempt < max_retries - 1:
                        if on_rate_limit:
                            on_rate_limit(label, attempt, wait, "retrying")
                        time.sleep(wait)
                    else:
                        if on_rate_limit:
                            on_rate_limit(label, attempt, 0, "exhausted")
                        break
                else:
                    raise
    raise last_err if last_err else RuntimeError("LLM generation failed with no error")
