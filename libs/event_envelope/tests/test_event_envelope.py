import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from event_envelope import UnsupportedMessage, check_supported, new_event, to_entry

SCHEMA = json.loads((Path(__file__).parents[3] / "docs/events/envelope.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER)


def flight_reserved():
    return new_event("FlightReserved", "trp_123", "flight-service", {"reservation_id": "fr_1"})


def test_new_event_matches_the_envelope_schema():
    VALIDATOR.validate(flight_reserved())


def test_each_event_gets_its_own_message_id():
    assert flight_reserved()["message_id"] != flight_reserved()["message_id"]


def test_schema_rejects_a_task_token_on_an_event():
    with pytest.raises(ValidationError):
        VALIDATOR.validate({**flight_reserved(), "task_token": "secret"})


def test_schema_requires_a_task_token_on_a_command():
    command = {**flight_reserved(), "message_kind": "command", "message_type": "ReserveFlight"}
    with pytest.raises(ValidationError):
        VALIDATOR.validate(command)
    VALIDATOR.validate({**command, "task_token": "token"})


def test_entry_follows_the_catalog():
    event = flight_reserved()

    entry = to_entry(event, "trip-conductor-test-trip-events")

    assert entry["EventBusName"] == "trip-conductor-test-trip-events"
    assert entry["Source"] == "trip-conductor.flight-service"
    assert entry["DetailType"] == "FlightReserved"
    assert json.loads(entry["Detail"]) == event


def test_check_supported_accepts_a_listed_message():
    check_supported(flight_reserved(), ("FlightReleased", 1), ("FlightReserved", 1))


@pytest.mark.parametrize(
    "overrides",
    [{"message_type": "HotelReserved"}, {"message_version": 2}],
    ids=["wrong type", "unknown version"],
)
def test_check_supported_rejects_anything_else(overrides):
    with pytest.raises(UnsupportedMessage):
        check_supported({**flight_reserved(), **overrides}, ("FlightReserved", 1))
