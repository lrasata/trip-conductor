import itertools
import random

import pytest
from boto3.dynamodb.conditions import Key
from conftest import CONFIRMED, DECLINED, TRIP, event, sqs_event, story
from event_envelope import UnsupportedMessage

from projector_service import handler as handler_module
from projector_service import projection
from projector_service.handler import handler


def run(*envelopes):
    return handler(sqs_event(*envelopes), None)


def view(table, trip_id=TRIP):
    return table.get_item(Key={"trip_id": trip_id}).get("Item")


def fold(envelopes):
    result = None
    for envelope in envelopes:
        if not projection.already_applied(result, envelope):
            result = projection.apply(result, envelope)
    return result


def test_the_happy_path_ends_confirmed_with_seven_events(table):
    assert run(*story(CONFIRMED)) == {"batchItemFailures": []}

    trip = view(table)
    assert trip["status"] == "CONFIRMED"
    assert trip["customer_id"] == "cus_demo"
    assert trip["created_at"] == "2026-11-18T10:00:00.000Z"
    assert trip["total_amount"] == 61800
    assert trip["legs"]["flight"]["status"] == "CONFIRMED"
    assert trip["legs"]["flight"]["booking_reference"] == "FL-ABC"
    assert trip["legs"]["hotel"]["status"] == "CONFIRMED"
    assert trip["legs"]["payment"]["status"] == "CHARGED"
    assert [entry["type"] for entry in trip["timeline"]] == CONFIRMED
    assert trip["updated_at"] == "2026-11-18T10:00:06.000Z"


def test_a_declined_card_ends_cancelled_with_both_legs_released(table):
    run(*story(DECLINED))

    trip = view(table)
    assert trip["status"] == "CANCELLED"
    assert trip["cancellation"] == {"reason": "PAYMENT_DECLINED", "failed_step": "ChargePayment"}
    assert trip["legs"]["flight"]["status"] == "RELEASED"
    assert trip["legs"]["hotel"]["status"] == "RELEASED"
    assert trip["legs"]["payment"] == {"status": "DECLINED", "decline_code": "card_declined"}


def test_needs_attention_is_shown(table):
    run(*story(["TripRequested", "FlightReserved", "HotelReserved", "PaymentCharged", "FlightConfirmed", "TripNeedsAttention"]))

    trip = view(table)
    assert trip["status"] == "NEEDS_ATTENTION"
    assert trip["attention"] == {"failed_step": "ConfirmBooking", "error": "VendorUnavailable"}


def test_a_redelivered_event_changes_nothing(table):
    events = story(CONFIRMED)
    run(*events)
    before = view(table)

    run(events[3], events[0], events[6])

    assert view(table) == before


def test_a_late_reservation_does_not_resurrect_a_released_hold(table):
    reserved, released = event("FlightReserved", 1), event("FlightReleased", 5)

    run(released, reserved)

    flight = view(table)["legs"]["flight"]
    assert flight["status"] == "RELEASED"
    assert flight["amount"] == 25800  # its data still fills in
    assert [entry["type"] for entry in view(table)["timeline"]] == ["FlightReserved", "FlightReleased"]


def test_a_terminal_status_never_moves_back(table):
    run(event("TripCancelled", 5), event("TripRequested", 0), event("FlightReserved", 1))

    assert view(table)["status"] == "CANCELLED"


def test_a_view_can_start_from_any_event(table):
    run(event("HotelReserved", 2))

    trip = view(table)
    assert trip["status"] == "REQUESTED"
    assert "customer_id" not in trip  # not in "my trips" until TripRequested arrives


@pytest.mark.parametrize("types", [CONFIRMED, DECLINED], ids=["confirmed", "declined"])
def test_any_order_gives_the_same_view(types):
    events = story(types)
    expected = fold(events)

    for _ in range(200):
        shuffled = random.sample(events, len(events))
        assert fold(shuffled) == expected


def test_any_order_with_duplicates_gives_the_same_view():
    events = story(DECLINED[:5])
    expected = fold(events)

    for order in itertools.permutations(events + events[:2]):
        assert fold(order) == expected


def test_trips_are_listed_by_customer_newest_first(table):
    run(*story(["TripRequested"], trip_id="trp_old"))
    run(event("TripRequested", trip_id="trp_new", requested_at="2026-11-19T10:00:00.000Z"))

    found = table.query(
        IndexName="by-customer",
        KeyConditionExpression=Key("customer_id").eq("cus_demo"),
        ScanIndexForward=False,
    )["Items"]

    assert [item["trip_id"] for item in found] == ["trp_new", "trp_old"]


def test_circuit_events_are_ignored(table):
    circuit = event("CircuitOpened", trip_id="vendor:airline")
    circuit["producer"] = "flight-service"

    assert run(circuit) == {"batchItemFailures": []}
    assert view(table, "vendor:airline") is None


def test_an_unknown_version_fails_only_that_message(table):
    result = run(event("TripRequested", version=2), event("FlightReserved", 1))

    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}
    assert view(table)["legs"]["flight"]["status"] == "RESERVED"


def test_an_unknown_version_is_rejected_by_the_projection():
    with pytest.raises(UnsupportedMessage):
        projection.apply(None, event("TripRequested", version=2))


def test_a_concurrent_write_is_retried_on_the_new_view(table, monkeypatch):
    run(event("TripRequested", 0))
    other_writer = event("FlightReserved", 1)
    real_apply = projection.apply
    calls = []

    def apply_with_interference(current, envelope):
        if not calls:  # someone else updates the trip between our read and our write
            table.put_item(Item=real_apply(current, other_writer))
        calls.append(envelope["message_type"])
        return real_apply(current, envelope)

    monkeypatch.setattr(handler_module.projection, "apply", apply_with_interference)

    run(event("HotelReserved", 2))

    trip = view(table)
    assert calls == ["HotelReserved", "HotelReserved"]
    assert set(trip["legs"]) == {"flight", "hotel"}
    assert trip["version"] == 3


def test_a_view_that_keeps_changing_is_left_to_sqs(table, monkeypatch):
    run(event("TripRequested", 0))
    monkeypatch.setattr(handler_module, "_write", lambda view, version: False)

    assert run(event("FlightReserved", 1)) == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}
