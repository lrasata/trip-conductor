import json
from datetime import datetime, timezone

import pytest
from conftest import place_hold_request, room_hold_request

from vendor_simulator_service import airline, hotel
from vendor_simulator_service.handler import handler


def test_places_a_hold_priced_per_passenger(aws):
    response = handler(place_hold_request(passengers=2), None)

    assert response["statusCode"] == 201
    hold = json.loads(response["body"])
    assert hold["amount"] == 37800
    assert hold["currency"] == "EUR"


def test_the_same_reference_gets_the_same_hold(aws):
    first = json.loads(handler(place_hold_request(), None)["body"])
    second = json.loads(handler(place_hold_request(), None)["body"])

    assert first["hold_id"] == second["hold_id"]


def test_a_hold_lasts_15_minutes():
    now = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)

    assert airline.place_hold("trp_123", "fl_lis_weekend", 1, now)["expires_at"] == "2026-11-07T10:15:00Z"


@pytest.mark.parametrize("offer_id", ["fl_sold_out", "fl_does_not_exist"])
def test_refuses_what_it_cannot_sell(aws, offer_id):
    assert handler(place_hold_request(offer_id=offer_id), None)["statusCode"] == 409


def test_requires_an_idempotency_key(aws):
    assert handler(place_hold_request(reference=None), None)["statusCode"] == 400


def test_rejects_a_malformed_body(aws):
    request = {**place_hold_request(), "body": "not json"}

    assert handler(request, None)["statusCode"] == 400


def test_releasing_always_succeeds(aws):
    request = {"routeKey": "DELETE /airline/holds/{reference}", "pathParameters": {"reference": "trp_123"}}

    assert handler(request, None)["statusCode"] == 204


def test_failure_rate_makes_it_fail(aws):
    aws(failure_rate=1, latency_ms=0)

    assert handler(place_hold_request(), None)["statusCode"] == 503


def test_latency_slows_it_down(aws, monkeypatch):
    slept = []
    monkeypatch.setattr("vendor_simulator_service.handler.time.sleep", slept.append)
    aws(failure_rate=0, latency_ms=8000)

    assert handler(place_hold_request(), None)["statusCode"] == 201
    assert slept == [8]


def test_still_answers_without_chaos_settings(aws, monkeypatch):
    monkeypatch.setenv("CHAOS_PARAMETER_PREFIX", "/does/not/exist/")

    assert handler(place_hold_request(), None)["statusCode"] == 201


def test_chaos_is_per_vendor(aws):
    aws("airline", failure_rate=1, latency_ms=0)

    assert handler(place_hold_request(), None)["statusCode"] == 503
    assert handler(room_hold_request(), None)["statusCode"] == 201


def test_an_unknown_route_is_not_found(aws):
    assert handler({"routeKey": "GET /bank/accounts"}, None)["statusCode"] == 404


# ---------------------------------------------------------------- hotel


def test_places_a_room_hold_priced_per_night(aws):
    response = handler(room_hold_request(check_in="2026-12-04", check_out="2026-12-07"), None)

    assert response["statusCode"] == 201
    hold = json.loads(response["body"])
    assert hold["amount"] == 3 * 12000
    assert hold["hold_id"].startswith("room_")


def test_a_room_hold_lasts_15_minutes():
    now = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)

    hold = hotel.place_hold("trp_123", "ht_lis_centre", "2026-12-04", "2026-12-06", 2, now)

    assert hold["expires_at"] == "2026-11-07T10:15:00Z"


@pytest.mark.parametrize(
    "offer_id, guests",
    [("ht_sold_out", 2), ("ht_does_not_exist", 2), ("ht_lis_riverside", 3)],
    ids=["sold out", "unknown", "too many guests"],
)
def test_the_hotel_refuses_what_it_cannot_sell(aws, offer_id, guests):
    assert handler(room_hold_request(offer_id=offer_id, guests=guests), None)["statusCode"] == 409


@pytest.mark.parametrize("check_out", ["2026-12-04", "2026-12-01", "not a date"])
def test_a_stay_must_last_a_night(aws, check_out):
    assert handler(room_hold_request(check_in="2026-12-04", check_out=check_out), None)["statusCode"] == 400


def test_releasing_a_room_always_succeeds(aws):
    request = {"routeKey": "DELETE /hotel/holds/{reference}", "pathParameters": {"reference": "trp_123"}}

    assert handler(request, None)["statusCode"] == 204
