import json
import logging
from datetime import datetime, timezone

import pytest
from conftest import charge_request, confirm_request, place_hold_request, room_hold_request

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


# ---------------------------------------------------------------- confirmations


@pytest.mark.parametrize("vendor", ["airline", "hotel"])
def test_confirming_gives_a_stable_booking_reference(aws, vendor):
    first = handler(confirm_request(vendor), None)
    second = handler(confirm_request(vendor), None)

    assert first["statusCode"] == 200
    assert json.loads(first["body"]) == json.loads(second["body"])
    assert json.loads(first["body"])["booking_reference"].startswith(vendor[:2].upper() + "-")


def test_chaos_can_be_limited_to_confirmations(aws):
    aws("hotel", failure_rate=1, latency_ms=0, operations=["confirm"])

    assert handler(room_hold_request(), None)["statusCode"] == 201
    assert handler(confirm_request("hotel"), None)["statusCode"] == 503


# ---------------------------------------------------------------- payment


def test_charges_a_card(aws):
    response = handler(charge_request(amount=61800), None)

    assert response["statusCode"] == 201
    charge = json.loads(response["body"])
    assert charge["amount"] == 61800
    assert charge["charge_id"].startswith("ch_")


def test_the_same_reference_is_charged_once(aws):
    first = json.loads(handler(charge_request(), None)["body"])
    second = json.loads(handler(charge_request(), None)["body"])

    assert first["charge_id"] == second["charge_id"]


@pytest.mark.parametrize(
    "token, decline_code",
    [("tok_declined", "card_declined"), ("tok_insufficient_funds", "insufficient_funds"), ("tok_unknown", "invalid_token")],
)
def test_declines_with_a_code(aws, token, decline_code):
    response = handler(charge_request(payment_token=token), None)

    assert response["statusCode"] == 402
    assert json.loads(response["body"])["decline_code"] == decline_code


@pytest.mark.parametrize("amount, currency", [(0, "EUR"), (100, "EURO")])
def test_rejects_an_amount_it_cannot_charge(aws, amount, currency):
    assert handler(charge_request(amount=amount, currency=currency), None)["statusCode"] == 400


def test_a_charge_requires_an_idempotency_key(aws):
    assert handler(charge_request(reference=None), None)["statusCode"] == 400


def test_the_card_token_is_never_logged(aws, caplog):
    caplog.set_level(logging.INFO)

    handler(charge_request(payment_token="tok_visa"), None)
    handler(charge_request(payment_token="tok_declined"), None)

    assert "tok_" not in caplog.text
