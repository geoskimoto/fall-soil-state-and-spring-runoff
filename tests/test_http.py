"""fetch.http: bounded retry with backoff on timeout/5xx; never retries 4xx; friendly errors."""
import pytest
import requests
import responses
from hypothesis import given, settings, strategies as st

from fallsoil.errors import FallsoilError, FetchError, HttpError
from fallsoil.fetch import http

URL = "https://example.test/data"


def calls(rsps):
    return len(rsps.calls)


def test_success_returns_exact_bytes(mocked):
    mocked.add(responses.GET, URL, body=b"\x00\x01payload", status=200)
    assert http.get(URL) == b"\x00\x01payload"
    assert calls(mocked) == 1


def test_params_are_sent(mocked):
    mocked.add(responses.GET, URL, body=b"ok")
    http.get(URL, params={"a": "1", "b": "x y"})
    assert "a=1" in mocked.calls[0].request.url


def test_5xx_then_success_is_retried(mocked):
    mocked.add(responses.GET, URL, status=503)
    mocked.add(responses.GET, URL, status=500)
    mocked.add(responses.GET, URL, body=b"finally", status=200)
    assert http.get(URL, max_retries=3) == b"finally"
    assert calls(mocked) == 3


def test_persistent_5xx_is_bounded_and_raises_friendly_error(mocked):
    mocked.add(responses.GET, URL, status=500)
    with pytest.raises(HttpError) as ei:
        http.get(URL, max_retries=3)
    assert calls(mocked) == 4  # first attempt + 3 retries, no more
    e = ei.value
    assert isinstance(e, FetchError) and isinstance(e, FallsoilError)
    assert e.status_code == 500
    assert e.attempts == 4
    assert e.user_message and str(e) == e.user_message
    assert "Traceback" not in e.user_message and "500" in e.user_message


def test_zero_retries_means_single_attempt(mocked):
    mocked.add(responses.GET, URL, status=500)
    with pytest.raises(HttpError):
        http.get(URL, max_retries=0)
    assert calls(mocked) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 429])
def test_4xx_is_never_retried(mocked, status):
    mocked.add(responses.GET, URL, status=status)
    with pytest.raises(HttpError) as ei:
        http.get(URL, max_retries=5)
    assert calls(mocked) == 1
    assert ei.value.status_code == status
    assert str(status) in ei.value.user_message


def test_timeout_is_retried_then_raises_friendly_error(mocked):
    mocked.add(responses.GET, URL, body=requests.exceptions.ReadTimeout("slow"))
    with pytest.raises(HttpError) as ei:
        http.get(URL, max_retries=2)
    assert calls(mocked) == 3
    assert ei.value.status_code is None
    msg = ei.value.user_message.lower()
    assert "timed out" in msg or "timeout" in msg


def test_connection_error_is_retried_then_friendly(mocked):
    mocked.add(responses.GET, URL, body=requests.exceptions.ConnectionError("down"))
    with pytest.raises(HttpError) as ei:
        http.get(URL, max_retries=1)
    assert calls(mocked) == 2
    assert ei.value.user_message


def test_timeout_then_success(mocked):
    mocked.add(responses.GET, URL, body=requests.exceptions.ConnectTimeout("t"))
    mocked.add(responses.GET, URL, body=b"ok")
    assert http.get(URL, max_retries=2) == b"ok"


def test_backoff_is_exponential_nondecreasing_and_not_slept_after_last_attempt(mocked):
    mocked.add(responses.GET, URL, status=502)
    slept = []
    with pytest.raises(HttpError):
        http.get(URL, max_retries=3, backoff_base_s=1.0, sleep=slept.append)
    assert len(slept) == 3
    assert slept == sorted(slept)
    assert slept[0] > 0 and slept[-1] >= 2 * slept[0]


def test_no_sleep_when_first_attempt_succeeds(mocked):
    mocked.add(responses.GET, URL, body=b"ok")
    slept = []
    http.get(URL, sleep=slept.append)
    assert slept == []


def test_timeout_value_is_passed_to_requests(mocked):
    mocked.add(responses.GET, URL, body=b"ok")
    http.get(URL, timeout_s=7.5)
    assert mocked.calls[0].request.req_kwargs["timeout"] == 7.5


def test_default_timeout_is_finite(mocked):
    mocked.add(responses.GET, URL, body=b"ok")
    http.get(URL)
    t = mocked.calls[0].request.req_kwargs["timeout"]
    assert t is not None and 0 < (t if not isinstance(t, tuple) else max(t)) <= 120


@settings(max_examples=25, deadline=None)
@given(retries=st.integers(min_value=0, max_value=6))
def test_property_attempts_never_exceed_retries_plus_one(retries):
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, URL, status=500)
        with pytest.raises(HttpError):
            http.get(URL, max_retries=retries, backoff_base_s=0.0, sleep=lambda s: None)
        assert len(rsps.calls) == retries + 1
