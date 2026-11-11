import json
import logging
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from circuit_breaker import VendorFailure
from conftest import CARD, CHARGE, TOKEN, charge, sqs_event
from jsonschema import Draft202012Validator

from payment_service.handler import handler

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


def test_charge_answers_and_publishes(aws):
    result = run(charge())

    assert result == {"batchItemFailures": []}
    assert stored(aws)["status"] == "CHARGED"
    assert aws["vendor"].charged == [("trp_123", CARD, 61800, "EUR")]
    success = aws["sfn"].send_task_success.call_args.kwargs
    assert success["taskToken"] == TOKEN
    assert json.loads(success["output"]) == {"payment_id": "ch_abc"}
    event = aws["published"]()[0]
    assert event["message_type"] == "PaymentCharged"
    assert event["data"] == CHARGE
    validate(event, "payment-charged")


def test_answers_before_publishing(aws):
    order = []
    aws["sfn"].send_task_success.side_effect = lambda **kwargs: order.append("answer")
    aws["events"].put_events.side_effect = lambda **kwargs: order.append("publish") or {"FailedEntryCount": 0}

    run(charge())

    assert order == ["answer", "publish"]


def test_a_redelivered_command_never_charges_twice(aws):
    run(charge())
    run(charge(token="redelivered-token"))

    assert len(aws["vendor"].charged) == 1
    assert aws["sfn"].send_task_success.call_args.kwargs["taskToken"] == "redelivered-token"
    assert len(aws["published"]()) == 1


def test_a_decline_is_stored_answered_and_published(aws):
    aws["vendor"].decline_code = "card_declined"

    run(charge())

    assert stored(aws)["status"] == "DECLINED"
    answers_failure(aws, "PaymentDeclined")
    event = aws["published"]()[0]
    assert event["message_type"] == "PaymentDeclined"
    assert event["data"] == {"decline_code": "card_declined"}
    validate(event, "payment-declined")


def test_a_redelivered_decline_is_declined_again(aws):
    aws["vendor"].decline_code = "card_declined"
    run(charge())
    aws["vendor"].decline_code = None

    run(charge(token="redelivered-token"))

    assert len(aws["vendor"].charged) == 1
    answers_failure(aws, "PaymentDeclined")


def test_declines_do_not_count_against_the_breaker(aws):
    aws["vendor"].decline_code = "card_declined"

    run(charge("trp_1"), charge("trp_2"), charge("trp_3"))

    assert "Item" not in aws["breakers"].get_item(Key={"vendor": "payment"})


def test_a_vendor_failure_is_not_stored_so_a_retry_calls_the_vendor_again(aws):
    aws["vendor"].failure = VendorFailure("503")

    run(charge())

    assert stored(aws) is None
    answers_failure(aws, "VendorUnavailable")

    aws["vendor"].failure = None
    run(charge(token="retry-token"))

    assert len(aws["vendor"].charged) == 2
    assert stored(aws)["status"] == "CHARGED"


def test_an_open_breaker_fails_fast_and_is_announced(aws):
    aws["vendor"].failure = VendorFailure("503")

    run(charge("trp_1"), charge("trp_2"), charge("trp_3"))  # the threshold is 2 in tests

    assert len(aws["vendor"].charged) == 2
    opened = aws["published"]()
    assert [event["message_type"] for event in opened] == ["CircuitOpened"]
    assert opened[0]["correlation_id"] == "vendor:payment"
    validate(opened[0], "circuit-opened")


def test_a_saga_that_gave_up_is_ignored(aws):
    aws["sfn"].send_task_success.side_effect = task_error("TaskTimedOut")

    assert run(charge()) == {"batchItemFailures": []}
    assert len(aws["published"]()) == 1


def test_a_failed_publish_is_retried_with_the_same_event(aws):
    aws["events"].put_events.return_value = {"FailedEntryCount": 1}

    assert run(charge()) == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}

    aws["events"].put_events.return_value = {"FailedEntryCount": 0}
    assert run(charge(token="retry-token")) == {"batchItemFailures": []}

    first, second = aws["published"]()
    assert first["message_id"] == second["message_id"]
    assert len(aws["vendor"].charged) == 1


def test_an_unknown_command_fails_only_that_message(aws):
    unknown = {**charge("trp_1"), "message_version": 2}

    assert run(unknown, charge("trp_2")) == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}
    assert stored(aws, "trp_2")["status"] == "CHARGED"


@pytest.mark.parametrize("decline_code", [None, "card_declined"])
def test_no_token_is_ever_logged_stored_or_published(aws, caplog, decline_code):
    caplog.set_level(logging.INFO)
    aws["vendor"].decline_code = decline_code
    aws["sfn"].send_task_success.side_effect = task_error("ThrottlingException")

    run(charge(), {**charge(), "message_version": 9})

    for secret in (TOKEN, CARD):
        assert secret not in caplog.text
        assert secret not in json.dumps(aws["published"]())
        assert secret not in json.dumps(stored(aws), default=str)


def test_every_log_line_has_the_correlation_id(aws, caplog):
    caplog.set_level(logging.INFO)

    run(charge())

    lines = [json.loads(record.message) for record in caplog.records if record.message.startswith("{")]
    assert lines
    assert all(line["correlation_id"] == "trp_123" for line in lines)
