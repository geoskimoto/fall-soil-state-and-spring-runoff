"""Supplementary fetch.http tests: status boundaries, defaults, exact backoff schedule, env override, logging."""
import logging
import math

import pytest
import requests
import responses

from fallsoil.errors import HttpError
from fallsoil.fetch import http

URL = "https://example.test/data"
ENV = "FALLSOIL_HTTP_BACKOFF_S"


def run_failing(mocked, status=500, **kw):
    """Call http.get with a recording sleep; return (error, sleeps)."""
    sleeps = []
    mocked.add(responses.GET, URL, status=status)
    with pytest.raises(HttpError) as ei:
        http.get(URL, sleep=sleeps.append, **kw)
    return ei.value, sleeps


# ---------------------------------------------------------------- status boundaries
@pytest.mark.parametrize("status", [200, 201, 202, 299])
def test_2xx_range_is_success(mocked, status):
    mocked.add(responses.GET, URL, body=b"x", status=status)
    assert http.get(URL) == b"x"
    assert len(mocked.calls) == 1


@pytest.mark.parametrize("status", [199, 300, 304, 399])
def test_non_2xx_non_5xx_is_failure_and_not_retried(mocked, status):
    err, sleeps = run_failing(mocked, status=status)
    assert len(mocked.calls) == 1 and sleeps == []
    assert err.status_code == status and err.attempts == 1


def test_499_is_not_retried(mocked):
    err, sleeps = run_failing(mocked, status=499)
    assert len(mocked.calls) == 1 and sleeps == [] and err.status_code == 499


@pytest.mark.parametrize("status", [500, 502, 503, 504, 599])
def test_5xx_including_both_ends_is_retried(mocked, status):
    err, sleeps = run_failing(mocked, status=status, backoff_base_s=0.0)
    assert len(mocked.calls) == 4 and len(sleeps) == 3
    assert err.status_code == status and err.attempts == 4
    assert str(status) in str(err)


def test_4xx_message_includes_status_code(mocked):
    err, _ = run_failing(mocked, status=404)
    assert "404" in str(err) and err.status_code == 404


# ---------------------------------------------------------------- defaults
def test_default_max_retries_is_three_so_four_attempts(mocked):
    err, sleeps = run_failing(mocked, status=503)
    assert len(mocked.calls) == 4 and err.attempts == 4 and len(sleeps) == 3


def test_default_timeout_is_finite_and_sensible(mocked):
    mocked.add(responses.GET, URL, body=b"ok")
    http.get(URL)
    t = mocked.calls[0].request.req_kwargs["timeout"]
    assert t is not None and math.isfinite(t) and 0 < t <= 120


def test_explicit_timeout_is_passed_through(mocked):
    mocked.add(responses.GET, URL, body=b"ok")
    http.get(URL, timeout_s=7.5)
    assert mocked.calls[0].request.req_kwargs["timeout"] == 7.5


def test_user_agent_header_is_set_and_identifies_the_package(mocked):
    mocked.add(responses.GET, URL, body=b"ok")
    http.get(URL)
    ua = mocked.calls[0].request.headers["User-Agent"]
    assert ua.startswith("fallsoil/")


# ---------------------------------------------------------------- backoff schedule
def assert_backoff(sleeps, base, n):
    """INTERFACE.md: exponential, non-decreasing sleeps `base * 2**k`; jitter allowed if non-decreasing.

    So: n sleeps, never decreasing, each at least base*2**k and at most twice that (jitter ceiling).
    """
    assert len(sleeps) == n
    assert sleeps == sorted(sleeps)
    for k, s in enumerate(sleeps):
        assert base * 2**k <= s <= 2 * base * 2**k


def test_sleeps_follow_base_times_two_to_the_k(mocked):
    _, sleeps = run_failing(mocked, status=500, backoff_base_s=0.5)
    assert_backoff(sleeps, 0.5, 3)


def test_sleep_schedule_scales_with_base_and_retry_count(mocked):
    _, sleeps = run_failing(mocked, status=500, backoff_base_s=0.25, max_retries=5)
    assert_backoff(sleeps, 0.25, 5)
    assert len(mocked.calls) == 6


def test_no_sleep_after_the_last_attempt(mocked):
    _, sleeps = run_failing(mocked, status=500, backoff_base_s=1.0, max_retries=1)
    assert_backoff(sleeps, 1.0, 1) and len(mocked.calls) == 2


def test_zero_retries_never_sleeps(mocked):
    _, sleeps = run_failing(mocked, status=500, max_retries=0, backoff_base_s=1.0)
    assert sleeps == [] and len(mocked.calls) == 1


def test_sleeps_happen_only_between_failures_before_success(mocked):
    sleeps = []
    mocked.add(responses.GET, URL, status=503)
    mocked.add(responses.GET, URL, status=502)
    mocked.add(responses.GET, URL, body=b"yes")
    assert http.get(URL, backoff_base_s=0.5, sleep=sleeps.append) == b"yes"
    assert_backoff(sleeps, 0.5, 2)


def test_timeouts_and_connection_errors_use_the_same_schedule(mocked):
    for exc in (requests.exceptions.Timeout(), requests.exceptions.ConnectionError()):
        mocked.reset()
        sleeps = []
        mocked.add(responses.GET, URL, body=exc)
        with pytest.raises(HttpError) as ei:
            http.get(URL, backoff_base_s=0.5, sleep=sleeps.append)
        assert_backoff(sleeps, 0.5, 3)
        assert ei.value.status_code is None and ei.value.attempts == 4


def test_timeout_message_says_timed_out_and_connection_message_is_friendly(mocked):
    mocked.add(responses.GET, URL, body=requests.exceptions.Timeout())
    with pytest.raises(HttpError) as ei:
        http.get(URL, max_retries=0)
    assert "timed out" in str(ei.value).lower() or "timeout" in str(ei.value).lower()
    mocked.reset()
    mocked.add(responses.GET, URL, body=requests.exceptions.ConnectionError())
    with pytest.raises(HttpError) as ei:
        http.get(URL, max_retries=0)
    assert "Traceback" not in str(ei.value) and "example.test" in str(ei.value)


# ---------------------------------------------------------------- env override
def schedule(mocked):
    _, sleeps = run_failing(mocked, status=500)
    return sleeps


def test_env_value_is_used_when_no_explicit_base(mocked, monkeypatch):
    monkeypatch.setenv(ENV, "0.25")
    assert_backoff(schedule(mocked), 0.25, 3)


def test_env_zero_means_no_waiting(mocked, monkeypatch):
    monkeypatch.setenv(ENV, "0")
    assert schedule(mocked) == [0.0, 0.0, 0.0]


def test_negative_env_value_is_clamped_to_zero(mocked, monkeypatch):
    monkeypatch.setenv(ENV, "-3")
    sleeps = schedule(mocked)
    assert sleeps == [0.0, 0.0, 0.0]


@pytest.mark.parametrize("raw", ["abc", "", "   ", "1,5", "nope"])
def test_garbage_or_blank_env_falls_back_to_default_half_second(mocked, monkeypatch, raw):
    monkeypatch.setenv(ENV, raw)
    assert_backoff(schedule(mocked), 0.5, 3)


def test_unset_env_uses_default_half_second(mocked, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    assert_backoff(schedule(mocked), 0.5, 3)


def test_explicit_base_overrides_env(mocked, monkeypatch):
    monkeypatch.setenv(ENV, "9")
    _, sleeps = run_failing(mocked, status=500, backoff_base_s=0.0)
    assert sleeps == [0.0, 0.0, 0.0]


def test_env_with_surrounding_whitespace_is_still_a_number(mocked, monkeypatch):
    monkeypatch.setenv(ENV, " 0.1 ")
    assert schedule(mocked) == pytest.approx([0.1, 0.2, 0.4])


# ---------------------------------------------------------------- logging
def test_each_retry_logs_a_warning_and_the_last_attempt_does_not(mocked, caplog):
    with caplog.at_level(logging.WARNING, logger="fallsoil"):
        run_failing(mocked, status=503, backoff_base_s=0.0)
    warns = [r for r in caplog.records if r.levelno == logging.WARNING and r.name.startswith("fallsoil")]
    assert len(warns) == 3
    text = [r.getMessage() for r in warns]
    assert all("example.test" in t for t in text)
    assert "1" in text[0] and "2" in text[1] and "3" in text[2]  # attempt counter advances


def test_success_first_try_logs_nothing(mocked, caplog):
    mocked.add(responses.GET, URL, body=b"ok")
    with caplog.at_level(logging.WARNING, logger="fallsoil"):
        http.get(URL)
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_4xx_does_not_log_retry_warning(mocked, caplog):
    with caplog.at_level(logging.WARNING, logger="fallsoil"):
        run_failing(mocked, status=404)
    assert [r for r in caplog.records if "retry" in r.getMessage().lower()] == []


# ---------------------------------------------------------------- other requests exceptions
@pytest.mark.parametrize("exc", [requests.exceptions.TooManyRedirects(), requests.exceptions.InvalidURL(),
                                 requests.exceptions.ChunkedEncodingError()])
def test_other_request_exceptions_become_http_error_without_retry(mocked, exc):
    sleeps = []
    mocked.add(responses.GET, URL, body=exc)
    with pytest.raises(HttpError) as ei:
        http.get(URL, sleep=sleeps.append)
    assert len(mocked.calls) == 1 and sleeps == []
    assert ei.value.attempts == 1 and ei.value.status_code is None
    assert ei.value.user_message and "Traceback" not in str(ei.value)


def test_malformed_url_without_scheme_raises_http_error_not_requests_error():
    with pytest.raises(HttpError):
        http.get("not-a-url", sleep=lambda s: None)
