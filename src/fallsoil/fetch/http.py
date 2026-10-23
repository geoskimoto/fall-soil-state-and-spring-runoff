"""The single HTTP layer: bounded retry with exponential backoff, friendly errors.

Timeouts, connection errors and 5xx responses are retried; 4xx responses (including 429) never are.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Callable, Mapping
from urllib.parse import urlsplit

import requests

from fallsoil import __version__
from fallsoil.errors import HttpError

log = logging.getLogger(__name__)

DEFAULT_BACKOFF_S = 0.5
BACKOFF_ENV = "FALLSOIL_HTTP_BACKOFF_S"
USER_AGENT = f"fallsoil/{__version__} (research; https://github.com/geoskimoto/fall-soil-state-and-spring-runoff)"


def _default_backoff() -> float:
    raw = os.environ.get(BACKOFF_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_BACKOFF_S
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_BACKOFF_S


def get(
    url: str,
    params: Mapping | None = None,
    *,
    timeout_s: float = 30.0,
    max_retries: int = 3,
    backoff_base_s: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    """GET `url` and return the body bytes. Raises HttpError (never a raw requests exception)."""
    base = _default_backoff() if backoff_base_s is None else backoff_base_s
    host = urlsplit(url).netloc or url
    attempts = 0
    problem = "the request failed"
    status: int | None = None

    for attempt in range(max_retries + 1):
        attempts = attempt + 1
        try:
            resp = requests.get(url, params=params, timeout=timeout_s, headers={"User-Agent": USER_AGENT})
        except requests.exceptions.Timeout:
            problem, status = f"the request to {host} timed out", None
        except requests.exceptions.ConnectionError:
            problem, status = f"could not connect to {host}", None
        except requests.exceptions.RequestException as exc:
            raise HttpError(
                f"The request to {host} could not be made ({type(exc).__name__}).", attempts=attempts
            ) from exc
        else:
            if 200 <= resp.status_code < 300:
                return resp.content
            status = resp.status_code
            if status < 500:
                raise HttpError(
                    f"{host} rejected the request with HTTP status {status}; this will not be retried.",
                    status_code=status,
                    attempts=attempts,
                )
            problem = f"{host} returned HTTP status {status}"

        if attempt < max_retries:
            delay = base * 2**attempt
            log.warning("%s (attempt %d of %d); retrying in %.1fs", problem, attempts, max_retries + 1, delay)
            sleep(delay)

    raise HttpError(
        f"Gave up after {attempts} attempt{'s' if attempts != 1 else ''}: {problem}. "
        "The service may be down or slow; try again later.",
        status_code=status,
        attempts=attempts,
    )
