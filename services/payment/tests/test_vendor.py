import io
import json
import urllib.error
from unittest.mock import MagicMock

import pytest
from circuit_breaker import VendorFailure

from payment_service import vendor


@pytest.fixture(autouse=True)
def vendor_url(monkeypatch):
    monkeypatch.setenv("VENDOR_URL", "https://vendors.example/")


def answering(monkeypatch, status, body=None):
    response = MagicMock(status=status)
    response.read.return_value = json.dumps(body).encode() if body is not None else b""
    response.__enter__.return_value = response
    opened = MagicMock(return_value=response)
    monkeypatch.setattr(vendor.urllib.request, "urlopen", opened)
    return opened


def raising(monkeypatch, exc):
    monkeypatch.setattr(vendor.urllib.request, "urlopen", MagicMock(side_effect=exc))


def http_error(code, body=b""):
    return urllib.error.HTTPError("https://vendors.example", code, "error", {}, io.BytesIO(body))


def test_charge_sends_the_trip_as_idempotency_key(monkeypatch):
    opened = answering(monkeypatch, 201, {"charge_id": "ch_abc", "amount": 61800, "currency": "EUR"})

    result = vendor.charge("trp_123", "tok_visa", 61800, "EUR")

    assert result == ({"payment_id": "ch_abc", "amount": 61800, "currency": "EUR"}, None)
    request = opened.call_args.args[0]
    assert request.full_url == "https://vendors.example/payment/charges"
    assert request.get_header("Idempotency-key") == "trp_123"
    assert json.loads(request.data) == {"payment_token": "tok_visa", "amount": 61800, "currency": "EUR"}


def test_a_decline_is_a_normal_answer(monkeypatch):
    raising(monkeypatch, http_error(402, json.dumps({"decline_code": "card_declined"}).encode()))

    assert vendor.charge("trp_123", "tok_declined", 100, "EUR") == (None, "card_declined")


@pytest.mark.parametrize(
    "failure",
    [http_error(500), http_error(503), http_error(429), TimeoutError(), urllib.error.URLError("no route")],
    ids=["500", "503", "throttled", "timeout", "connection"],
)
def test_what_counts_as_a_vendor_failure(monkeypatch, failure):
    raising(monkeypatch, failure)

    with pytest.raises(VendorFailure):
        vendor.charge("trp_123", "tok_visa", 100, "EUR")


def test_a_bad_request_is_a_bug_not_a_vendor_failure(monkeypatch):
    raising(monkeypatch, http_error(400))

    with pytest.raises(ValueError):
        vendor.charge("trp_123", "tok_visa", 100, "EUR")
