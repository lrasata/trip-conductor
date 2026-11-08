import io
import json
import urllib.error
from unittest.mock import MagicMock

import pytest
from circuit_breaker import VendorFailure

from hotel_service import vendor


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


def http_error(code):
    return urllib.error.HTTPError("https://vendors.example", code, "error", {}, io.BytesIO())


def test_place_hold_sends_the_trip_as_reference(monkeypatch):
    opened = answering(
        monkeypatch,
        201,
        {"hold_id": "hold_abc", "expires_at": "2026-11-07T10:15:00Z", "amount": 100, "currency": "EUR"},
    )

    hold = vendor.place_hold("trp_123", "ht_lis_centre", "2026-12-04", "2026-12-06", 2)

    assert hold == {
        "reservation_id": "hold_abc",
        "hold_expires_at": "2026-11-07T10:15:00Z",
        "amount": 100,
        "currency": "EUR",
    }
    request = opened.call_args.args[0]
    assert request.full_url == "https://vendors.example/hotel/holds"
    assert request.get_header("Idempotency-key") == "trp_123"
    assert json.loads(request.data) == {
        "offer_id": "ht_lis_centre",
        "check_in": "2026-12-04",
        "check_out": "2026-12-06",
        "guests": 2,
    }


def test_a_refusal_is_a_normal_answer(monkeypatch):
    raising(monkeypatch, http_error(409))

    assert vendor.place_hold("trp_123", "ht_sold_out", "2026-12-04", "2026-12-06", 1) is None


@pytest.mark.parametrize(
    "failure",
    [http_error(500), http_error(503), http_error(429), TimeoutError(), urllib.error.URLError("no route")],
    ids=["500", "503", "throttled", "timeout", "connection"],
)
def test_what_counts_as_a_vendor_failure(monkeypatch, failure):
    raising(monkeypatch, failure)

    with pytest.raises(VendorFailure):
        vendor.place_hold("trp_123", "ht_lis_centre", "2026-12-04", "2026-12-06", 1)


def test_a_bad_request_is_a_bug_not_a_vendor_failure(monkeypatch):
    raising(monkeypatch, http_error(400))

    with pytest.raises(ValueError):
        vendor.place_hold("trp_123", "ht_lis_centre", "2026-12-04", "2026-12-06", 1)


def test_release_is_by_trip(monkeypatch):
    opened = answering(monkeypatch, 204)

    vendor.release_hold("trp_123")

    request = opened.call_args.args[0]
    assert request.get_method() == "DELETE"
    assert request.full_url == "https://vendors.example/hotel/holds/trp_123"
