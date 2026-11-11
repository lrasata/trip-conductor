import json
import logging
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from circuit_breaker import VendorFailure
from conftest import HOLD, TOKEN, confirm, release, reserve, sqs_event
from jsonschema import Draft202012Validator

from hotel_service.handler import handler

EVENTS_DIR = Path(__file__).parents[3] / "docs/events"


def validate(event, schema_name):
    for name in ("envelope", schema_name):
        schema = json.loads((EVENTS_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
        document = event["data"] if name == schema_name else event
        Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(document)


def run(*commands):
    return handler(sqs_event(*commands), None)


def stored(aws, trip_id="trp_123"):
    return aws["table"].get_item(Key={"trip_id": trip_id}).get("Item")


def answers_failure(aws, error):
    assert aws["sfn"].send_task_failure.call_args.kwargs["error"] == error


def task_error(code):
    return ClientError({"Error": {"Code": code, "Message": "gone"}}, "SendTaskSuccess")


# ---------------------------------------------------------------- ReserveHotel


def test_reserve_holds_answers_and_publishes(aws):
    result = run(reserve())

    assert result == {"batchItemFailures": []}
    assert stored(aws)["status"] == "HELD"
    success = aws["sfn"].send_task_success.call_args.kwargs
    assert success["taskToken"] == TOKEN
    assert json.loads(success["output"]) == HOLD
    event = aws["published"]()[0]
    assert event["message_type"] == "HotelReserved"
    assert event["correlation_id"] == "trp_123"
    validate(event, "hotel-reserved")


def test_answers_before_publishing(aws):
    order = []
    aws["sfn"].send_task_success.side_effect = lambda **kwargs: order.append("answer")
    aws["events"].put_events.side_effect = lambda **kwargs: order.append("publish") or {"FailedEntryCount": 0}

    run(reserve())

    assert order == ["answer", "publish"]


def test_published_event_is_no_longer_pending(aws):
    run(reserve())

    assert "pending_event" not in stored(aws)


def test_a_redelivered_command_returns_the_stored_result(aws):
    run(reserve())
    run(reserve(token="redelivered-token"))

    assert len(aws["vendor"].placed) == 1
    assert aws["sfn"].send_task_success.call_count == 2
    assert aws["sfn"].send_task_success.call_args.kwargs["taskToken"] == "redelivered-token"
    assert len(aws["published"]()) == 1


def test_sold_out_is_stored_and_answered_as_a_business_error(aws):
    aws["vendor"].hold = None

    run(reserve())

    assert stored(aws)["status"] == "UNAVAILABLE"
    answers_failure(aws, "HotelUnavailable")
    assert aws["published"]() == []


def test_sold_out_does_not_count_against_the_breaker(aws):
    aws["vendor"].hold = None

    run(reserve("trp_1"), reserve("trp_2"), reserve("trp_3"))

    assert "Item" not in aws["breakers"].get_item(Key={"vendor": "hotel"})


def test_a_vendor_failure_is_not_stored_so_a_retry_calls_the_vendor_again(aws):
    aws["vendor"].failure = VendorFailure("503")

    run(reserve())

    assert stored(aws) is None
    answers_failure(aws, "VendorUnavailable")

    aws["vendor"].failure = None
    run(reserve(token="retry-token"))

    assert len(aws["vendor"].placed) == 2
    assert aws["sfn"].send_task_success.call_args.kwargs["taskToken"] == "retry-token"


def test_an_open_breaker_fails_fast_and_is_announced_once(aws):
    aws["vendor"].failure = VendorFailure("503")

    run(reserve("trp_1"), reserve("trp_2"), reserve("trp_3"))  # the threshold is 2 in tests

    assert len(aws["vendor"].placed) == 2  # the third never reached the hotel provider
    answers_failure(aws, "VendorUnavailable")
    opened = aws["published"]()
    assert [event["message_type"] for event in opened] == ["CircuitOpened"]
    assert opened[0]["correlation_id"] == "vendor:hotel"
    assert opened[0]["data"]["failure_count"] == 2
    validate(opened[0], "circuit-opened")


def test_a_saga_that_gave_up_is_ignored(aws):
    aws["sfn"].send_task_success.side_effect = task_error("TaskTimedOut")

    assert run(reserve()) == {"batchItemFailures": []}
    assert len(aws["published"]()) == 1  # what happened still happened


@pytest.mark.parametrize("code", ["InvalidToken", "TaskDoesNotExist"])
def test_an_invalid_token_is_ignored(aws, code):
    aws["sfn"].send_task_success.side_effect = task_error(code)

    assert run(reserve()) == {"batchItemFailures": []}


def test_a_failed_answer_is_retried(aws):
    aws["sfn"].send_task_success.side_effect = task_error("ThrottlingException")

    assert run(reserve()) == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}


def test_a_failed_publish_is_retried_with_the_same_event(aws):
    aws["events"].put_events.return_value = {"FailedEntryCount": 1}

    assert run(reserve()) == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}

    aws["events"].put_events.return_value = {"FailedEntryCount": 0}
    assert run(reserve(token="retry-token")) == {"batchItemFailures": []}

    first, second = aws["published"]()
    assert first["message_id"] == second["message_id"]
    assert len(aws["vendor"].placed) == 1


# ---------------------------------------------------------------- ReleaseHotel


def test_release_frees_the_hold_and_publishes(aws):
    run(reserve())

    result = run(release())

    assert result == {"batchItemFailures": []}
    assert stored(aws)["status"] == "RELEASED"
    assert aws["vendor"].released == ["trp_123"]
    assert json.loads(aws["sfn"].send_task_success.call_args.kwargs["output"]) == {}
    event = aws["published"]()[-1]
    assert event["message_type"] == "HotelReleased"
    assert event["data"] == {"reservation_id": HOLD["reservation_id"], "reason": "PAYMENT_DECLINED"}
    validate(event, "hotel-released")


def test_releasing_twice_succeeds_and_changes_nothing(aws):
    run(reserve())
    run(release())
    run(release(token="second"))

    assert aws["vendor"].released == ["trp_123"]
    assert aws["sfn"].send_task_success.call_args.kwargs["taskToken"] == "second"
    assert [event["message_type"] for event in aws["published"]()] == ["HotelReserved", "HotelReleased"]


def test_release_retries_after_the_hotel_provider_comes_back(aws):
    run(reserve())
    aws["vendor"].failure = VendorFailure("503")

    run(release())

    assert stored(aws)["status"] == "RELEASING"
    answers_failure(aws, "VendorUnavailable")

    aws["vendor"].failure = None
    run(release(token="retry"))

    assert stored(aws)["status"] == "RELEASED"
    assert aws["sfn"].send_task_success.call_args.kwargs["taskToken"] == "retry"


def test_release_without_a_reservation_still_calls_the_provider_and_stays_silent(aws):
    run(release(reason="TIMEOUT"))

    assert aws["vendor"].released == ["trp_123"]
    assert stored(aws)["status"] == "RELEASED"
    assert aws["sfn"].send_task_success.call_count == 1
    assert aws["published"]() == []


def test_releasing_a_sold_out_room_has_nothing_to_do(aws):
    aws["vendor"].hold = None
    run(reserve())

    run(release())

    assert aws["vendor"].released == []
    assert aws["sfn"].send_task_success.call_count == 1


def test_a_reserve_after_a_release_does_not_place_a_hold(aws):
    run(release(reason="TIMEOUT"))

    run(reserve(token="late"))

    assert aws["vendor"].placed == []
    answers_failure(aws, "HotelUnavailable")
    assert stored(aws)["status"] == "RELEASED"


def test_a_release_during_a_reserve_gives_the_hold_back(aws):
    aws["vendor"].before_answering = lambda: run(release(reason="TIMEOUT", token="release"))

    run(reserve(token="late"))

    assert stored(aws)["status"] == "RELEASED"
    assert aws["vendor"].released == ["trp_123", "trp_123"]  # the release, then the late hold
    answers_failure(aws, "HotelUnavailable")
    assert aws["published"]() == []


# ---------------------------------------------------------------- ConfirmHotel


def test_confirm_books_the_hold_and_publishes(aws):
    run(reserve())

    result = run(confirm())

    assert result == {"batchItemFailures": []}
    assert stored(aws)["status"] == "CONFIRMED"
    assert json.loads(aws["sfn"].send_task_success.call_args.kwargs["output"]) == {"booking_reference": "HO-0A1B2C3D"}
    event = aws["published"]()[-1]
    assert event["message_type"] == "HotelConfirmed"
    assert event["data"] == {"reservation_id": HOLD["reservation_id"], "booking_reference": "HO-0A1B2C3D"}
    validate(event, "hotel-confirmed")


def test_a_redelivered_confirm_returns_the_stored_booking(aws):
    run(reserve())
    run(confirm())
    run(confirm(token="redelivered"))

    assert aws["vendor"].confirmed == ["trp_123"]
    assert aws["sfn"].send_task_success.call_args.kwargs["taskToken"] == "redelivered"
    assert [event["message_type"] for event in aws["published"]()] == ["HotelReserved", "HotelConfirmed"]


def test_confirm_retries_after_the_hotel_provider_comes_back(aws):
    run(reserve())
    aws["vendor"].failure = VendorFailure("503")

    run(confirm())

    assert stored(aws)["status"] == "HELD"
    answers_failure(aws, "VendorUnavailable")

    aws["vendor"].failure = None
    run(confirm(token="retry"))

    assert stored(aws)["status"] == "CONFIRMED"


@pytest.mark.parametrize("hold", [None, HOLD], ids=["sold out", "nothing reserved"])
def test_only_a_held_reservation_can_be_confirmed(aws, hold):
    if hold is None:
        aws["vendor"].hold = None
        run(reserve())

    assert run(confirm()) == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}
    assert aws["vendor"].confirmed == []


def test_a_late_reserve_after_confirming_answers_the_hold(aws):
    run(reserve())
    run(confirm())

    run(reserve(token="late"))

    assert aws["sfn"].send_task_success.call_args.kwargs["taskToken"] == "late"
    assert len(aws["vendor"].placed) == 1


# ---------------------------------------------------------------- the batch


def test_an_unknown_command_fails_only_that_message(aws):
    unknown = {**reserve("trp_1"), "message_version": 2}

    result = run(unknown, reserve("trp_2"))

    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}
    assert stored(aws, "trp_2")["status"] == "HELD"


def test_a_malformed_message_fails_only_that_message(aws):
    event = sqs_event(reserve("trp_2"))
    event["Records"].insert(0, {"messageId": "bad", "body": "not json"})

    assert handler(event, None) == {"batchItemFailures": [{"itemIdentifier": "bad"}]}


def test_the_task_token_is_never_logged_or_published(aws, caplog):
    caplog.set_level(logging.INFO)
    aws["sfn"].send_task_success.side_effect = task_error("ThrottlingException")

    run(reserve(), {**reserve(), "message_version": 9})

    assert TOKEN not in caplog.text
    assert TOKEN not in json.dumps(aws["published"]())
    assert TOKEN not in json.dumps(stored(aws), default=str)


def test_every_log_line_has_the_correlation_id(aws, caplog):
    caplog.set_level(logging.INFO)

    run(reserve())

    lines = [json.loads(record.message) for record in caplog.records if record.message.startswith("{")]
    assert lines
    assert all(line["correlation_id"] == "trp_123" for line in lines)
