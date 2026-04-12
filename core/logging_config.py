"""
core/logging_config.py — Structured logging and observability for V3.

Provides a unified logging framework that tracks:
  - API call durations and status codes
  - Pipeline stage timing and transitions
  - LLM token usage and costs
  - Error/warning aggregation
  - Performance metrics

Uses Python's built-in logging with structured metadata stored in
LogRecord extras, plus a pipeline timer context manager.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generator


# ═══════════════════════════════════════════════════════════════════════════════
# LOGGER SETUP
# ═══════════════════════════════════════════════════════════════════════════════

_LOG_FORMAT = (
    "[%(asctime)s] %(levelname)-8s %(name)-25s │ %(message)s"
)
_DATE_FORMAT = "%H:%M:%S"


def get_logger(name: str) -> logging.Logger:
    """Get a named logger with the standard V3 format.

    Usage::

        from core.logging_config import get_logger
        log = get_logger(__name__)
        log.info("Starting charity analysis", extra={"charity_num": "123456"})
    """
    logger = logging.getLogger(f"hrcob.{name}")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE METRICS TRACKER
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class APICallMetric:
    """Record of a single external API call."""
    service: str
    endpoint: str
    start_time: float
    end_time: float = 0.0
    status_code: int = 0
    success: bool = True
    error: str = ""
    bytes_received: int = 0

    @property
    def duration_ms(self) -> float:
        return (self.end_time - self.start_time) * 1000


@dataclass
class StageMetric:
    """Record of a pipeline stage execution."""
    name: str
    start_time: float
    end_time: float = 0.0
    success: bool = True
    error: str = ""
    items_processed: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return self.end_time - self.start_time


@dataclass
class PipelineMetrics:
    """Aggregated metrics for an entire pipeline run."""
    pipeline_name: str
    entity_name: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    api_calls: list[APICallMetric] = field(default_factory=list)
    stages: list[StageMetric] = field(default_factory=list)
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def start(self):
        self.start_time = time.time()

    def finish(self):
        self.end_time = time.time()

    @property
    def total_duration_s(self) -> float:
        if self.end_time and self.start_time:
            return self.end_time - self.start_time
        return 0.0

    @property
    def total_api_calls(self) -> int:
        return len(self.api_calls)

    @property
    def failed_api_calls(self) -> int:
        return sum(1 for c in self.api_calls if not c.success)

    @property
    def total_llm_cost(self) -> float:
        return sum(c.get("cost_usd", 0.0) for c in self.llm_calls)

    @property
    def total_llm_tokens(self) -> int:
        return sum(c.get("total_tokens", 0) for c in self.llm_calls)

    def log_api_call(self, service: str, endpoint: str, *,
                     status_code: int = 200, success: bool = True,
                     error: str = "", duration_s: float = 0.0,
                     bytes_received: int = 0):
        """Record an API call metric."""
        now = time.time()
        self.api_calls.append(APICallMetric(
            service=service,
            endpoint=endpoint,
            start_time=now - duration_s,
            end_time=now,
            status_code=status_code,
            success=success,
            error=error,
            bytes_received=bytes_received,
        ))

    def log_llm_call(self, model: str, prompt_tokens: int,
                     completion_tokens: int, cost_usd: float,
                     duration_s: float = 0.0):
        """Record an LLM call metric."""
        self.llm_calls.append({
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost_usd": cost_usd,
            "duration_s": duration_s,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def summary(self) -> dict[str, Any]:
        """Return a summary dict for UI display or logging."""
        stage_times = {}
        for s in self.stages:
            stage_times[s.name] = {
                "duration_s": round(s.duration_s, 2),
                "success": s.success,
                "items": s.items_processed,
            }

        return {
            "pipeline": self.pipeline_name,
            "entity": self.entity_name,
            "total_duration_s": round(self.total_duration_s, 2),
            "api_calls_total": self.total_api_calls,
            "api_calls_failed": self.failed_api_calls,
            "llm_calls": len(self.llm_calls),
            "llm_total_tokens": self.total_llm_tokens,
            "llm_total_cost_usd": round(self.total_llm_cost, 6),
            "stages": stage_times,
            "errors": self.errors,
            "warnings": self.warnings,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# CONTEXT MANAGERS
# ═══════════════════════════════════════════════════════════════════════════════

@contextmanager
def track_stage(metrics: PipelineMetrics, stage_name: str,
                logger: logging.Logger | None = None) -> Generator[StageMetric, None, None]:
    """Context manager to track a pipeline stage's timing and success.

    Usage::

        with track_stage(metrics, "fetch_registry_data", log) as stage:
            data = fetch_charity_data(charity_num)
            stage.items_processed = 1
            stage.metadata["charity_name"] = data.get("name", "")
    """
    stage = StageMetric(name=stage_name, start_time=time.time())
    if logger:
        logger.info(f"▶ Starting: {stage_name}")
    try:
        yield stage
    except Exception as e:
        stage.success = False
        stage.error = str(e)
        metrics.errors.append(f"[{stage_name}] {e}")
        if logger:
            logger.error(f"✗ Failed: {stage_name} — {e}")
        raise
    finally:
        stage.end_time = time.time()
        metrics.stages.append(stage)
        if logger:
            logger.info(
                f"{'✓' if stage.success else '✗'} Completed: {stage_name} "
                f"({stage.duration_s:.2f}s)"
            )


@contextmanager
def track_api_call(metrics: PipelineMetrics, service: str, endpoint: str,
                   logger: logging.Logger | None = None):
    """Context manager to track an individual API call.

    Usage::

        with track_api_call(metrics, "companies_house", "/company/{num}"):
            response = requests.get(url, ...)
    """
    call = APICallMetric(service=service, endpoint=endpoint, start_time=time.time())
    try:
        yield call
    except Exception as e:
        call.success = False
        call.error = str(e)
        if logger:
            logger.warning(f"API call failed: {service}/{endpoint} — {e}")
    finally:
        call.end_time = time.time()
        metrics.api_calls.append(call)


# ═══════════════════════════════════════════════════════════════════════════════
# SIMPLE TIMER UTILITY
# ═══════════════════════════════════════════════════════════════════════════════

@contextmanager
def timer(label: str = "", logger: logging.Logger | None = None) -> Generator[dict, None, None]:
    """Simple context manager that measures elapsed time.

    Usage::

        with timer("fetch data", log) as t:
            do_work()
        print(t["elapsed_s"])
    """
    result = {"elapsed_s": 0.0, "label": label}
    start = time.time()
    try:
        yield result
    finally:
        result["elapsed_s"] = round(time.time() - start, 3)
        if logger and label:
            logger.info(f"⏱ {label}: {result['elapsed_s']}s")
