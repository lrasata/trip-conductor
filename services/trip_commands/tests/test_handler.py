import base64
import json
import logging
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from conftest import CARD, STATE_MACHINE_ARN, api_event, booking

from trip_commands_service.handler import handler
from trip_commands_service.request import trip_id

REQUESTS_DIR = Path(__file__).parents[3] / "scripts/requests"


def started(sfn):
    kwargs = sfn.start_execution.call_args.kwargs
    return kwargs["name"], json.loads(kwargs["input"])


def test_starts_the_saga_and_answers_202(sfn):
    response = handler(api_event(booking()), None)

    assert response["statusCode"] == 202
    body = json.loads(response["body"])
    expected_id = trip_id("cus_demo", "key-001")
    assert body == {"trip_id": expected_id, "location": f"/v1/trips/{expected_id}"}
    assert response["headers"]["Location"] == f"/v1/trips/{expected_id}"
    assert sfn.start_execution.call_args.kwargs["stateMachineArn"] == STATE_MACHINE_ARN
    name, saga_input = started(sfn)
    assert name == expected_id
    assert saga_input == booking()


def test_the_trip_id_is_a_valid_execution_name():
    value = trip_id("cus_demo", "key-001")

    assert value.startswith("trp_")
    assert len(value) <= 80
    assert value.replace("_", "").isalnum()


def test_the_same_customer_and_key_give_the_same_trip():
    assert trip_id("cus_demo", "key-001") == trip_id("cus_demo", "key-001")
    assert trip_id("cus_demo", "key-001") != trip_id("cus_demo", "key-002")
    assert trip_id("cus_demo", "key-001") != trip_id("cus_other", "key-001")


def test_a_repeated_request_returns_the_same_trip(sfn):
    sfn.start_execution.side_effect = [
        {"executionArn": "arn:1"},
        ClientError({"Error": {"Code": "ExecutionAlreadyExists", "Message": "exists"}}, "StartExecution"),
    ]

    first = json.loads(handler(api_event(booking()), None)["body"])
    second = handler(api_event(booking()), None)

    assert second["statusCode"] == 202
    assert json.loads(second["body"]) == first


def test_only_validated_fields_reach_the_saga(sfn):
    handler(api_event(booking(extra="ignored", flight={"offer_id": "fl_x", "passengers": 1, "seat": "1A"})), None)

    _, saga_input = started(sfn)
    assert "extra" not in saga_input
    assert saga_input["flight"] == {"offer_id": "fl_x", "passengers": 1}


def test_the_header_name_is_case_insensitive(sfn):
    event = api_event(booking(), idempotency_key=None)
    event["headers"]["idempotency-key"] = "key-001"

    assert handler(event, None)["statusCode"] == 202


def test_a_base64_body_is_accepted(sfn):
    event = api_event(booking())
    event["body"] = base64.b64encode(event["body"].encode()).decode()
    event["isBase64Encoded"] = True

    assert handler(event, None)["statusCode"] == 202


@pytest.mark.parametrize(
    "event, error",
    [
        (api_event(booking(), idempotency_key=None), "Idempotency-Key"),
        (api_event(booking(), idempotency_key="has spaces"), "Idempotency-Key"),
        (api_event(None, raw="{not json"), "valid JSON"),
        (api_event(None, raw="[]"), "JSON object"),
        (api_event(booking(customer_id="")), "customer_id"),
        (api_event(booking(payment_token="4111111111111111")), "payment_token"),
        (api_event(booking(flight="fl_lis_weekend")), "flight must be an object"),
        (api_event(booking(flight={"offer_id": "fl_x", "passengers": 0})), "flight.passengers"),
        (api_event(booking(flight={"offer_id": "fl_x", "passengers": True})), "flight.passengers"),
        (api_event(booking(hotel={"offer_id": "ht_x", "check_in": "04/12/2099", "check_out": "2099-12-06", "guests": 2})), "hotel.check_in"),
        (api_event(booking(hotel={"offer_id": "ht_x", "check_in": "2099-12-06", "check_out": "2099-12-06", "guests": 2})), "after hotel.check_in"),
        (api_event(booking(hotel={"offer_id": "ht_x", "check_in": "2099-12-04", "check_out": "2099-12-06", "guests": 10})), "hotel.guests"),
    ],
)
def test_an_invalid_request_is_rejected_without_starting_anything(sfn, event, error):
    response = handler(event, None)

    assert response["statusCode"] == 400
    body = json.loads(response["body"])
    assert body["error"] == "validation_failed"
    assert any(error in detail for detail in body["details"])
    sfn.start_execution.assert_not_called()


def test_all_errors_are_reported_at_once(sfn):
    response = handler(api_event({"flight": {}, "hotel": {}}, idempotency_key=None), None)

    assert len(json.loads(response["body"])["details"]) >= 5


def test_a_step_functions_failure_is_a_500(sfn):
    sfn.start_execution.side_effect = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "StartExecution")

    response = handler(api_event(booking()), None)

    assert response["statusCode"] == 500
    assert json.loads(response["body"]) == {"error": "internal_error"}


@pytest.mark.parametrize("name", sorted(path.name for path in REQUESTS_DIR.glob("*.json")))
def test_the_sample_requests_are_valid_bodies(sfn, name):
    body = json.loads((REQUESTS_DIR / name).read_text(encoding="utf-8"))

    assert handler(api_event(body), None)["statusCode"] == 202


def test_the_payment_token_is_never_logged_or_answered(sfn, caplog):
    caplog.set_level(logging.DEBUG)
    sfn.start_execution.side_effect = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "StartExecution")

    responses = [
        handler(api_event(booking()), None),
        handler(api_event(booking(customer_id="")), None),
    ]
    sfn.start_execution.side_effect = None
    responses.append(handler(api_event(booking()), None))

    assert CARD not in caplog.text
    assert all(CARD not in json.dumps(response) for response in responses)
