"""Safe provider diagnostics and a bounded retry policy, independent of game state."""

import math
import os
import time
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime

import httpx
from openai import APIConnectionError, APITimeoutError, OpenAI

from ai_dm.errors import ConfigurationError, ProviderCategory, ProviderError


_QUOTA_CODES = frozenset({"insufficient_quota", "billing_hard_limit_reached", "insufficient_credits"})
_TEMPORARY_STATUSES = frozenset({500, 502, 503, 504})


def _retry_after(headers) -> float | None:
    """Accept finite nonnegative seconds, milliseconds or an HTTP date."""
    for name, divisor in (("retry-after-ms", 1000), ("retry-after", 1)):
        raw = headers.get(name)
        if raw is None:
            continue
        try:
            value = float(raw) / divisor
        except (TypeError, ValueError, OverflowError):
            if name != "retry-after":
                continue
            try:
                date = parsedate_to_datetime(raw)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                value = max(0.0, date.timestamp() - time.time())
            except (TypeError, ValueError, OverflowError, OSError):
                continue
        if math.isfinite(value) and value >= 0:
            return value
    return None


def provider_error(exc: Exception) -> ProviderError:
    """Never expose response bodies, URLs, headers or exception messages to players."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    headers = getattr(response, "headers", {})
    body = getattr(exc, "body", None)
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            body = exc.response.json()
        except ValueError:
            body = None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body = body["error"]
    quota = isinstance(body, dict) and any(
        isinstance(body.get(key), str) and body[key] in _QUOTA_CODES for key in ("code", "type")
    )
    category: ProviderCategory = "unknown"
    if isinstance(exc, (APITimeoutError, httpx.TimeoutException, TimeoutError)):
        category = "timeout"
    elif status == 401:
        category = "authentication"
    elif status == 403:
        category = "permission"
    elif status == 402 or (status in (400, 429) and quota):
        category = "quota"
    elif status == 429:
        category = "rate_limit"
    elif status in _TEMPORARY_STATUSES:
        category = "unavailable"
    elif status is not None and 400 <= status < 500:
        category = "invalid_request"
    elif isinstance(exc, (APIConnectionError, httpx.TransportError, ConnectionError)):
        category = "connection"
    return ProviderError(
        category=category, status_code=status, retry_after=_retry_after(headers),
        retryable=category in ("rate_limit", "unavailable"),
    )


@dataclass(frozen=True)
class RetryPolicy:
    max_wait_seconds: float

    @classmethod
    def from_env(cls) -> "RetryPolicy":
        try:
            value = float(os.getenv("AI_DM_MAX_RETRY_WAIT_SECONDS", "10"))
            if not math.isfinite(value) or not 0 <= value <= 60:
                raise ValueError("Expected a finite value between 0 and 60.")
        except ValueError as exc:
            raise ConfigurationError("AI_DM_MAX_RETRY_WAIT_SECONDS должен быть числом от 0 до 60.") from exc
        return cls(value)

    def delay_for(self, error: ProviderError) -> float | None:
        if not error.retryable or self.max_wait_seconds == 0:
            return None
        delay = error.retry_after if error.retry_after is not None else 1.0
        # Do not retry earlier than requested just to fit our wait limit.
        return delay if delay <= self.max_wait_seconds else None


def without_sdk_retries(client):
    """Copy supplied SDK options without changing the caller's client or transport."""
    if isinstance(client, OpenAI) and client.max_retries != 0:
        return client.with_options(max_retries=0)
    return client
