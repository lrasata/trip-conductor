"""Lambda entry point: SQS queue `payment-commands` (ChargePayment).

The saga's pivot: once a charge succeeds, the booking only moves forward. For each command:

1. Look at the stored charge. A redelivered command is answered from it, with the token of
   the command at hand, and the provider isn't called again.
2. Call the provider through the circuit breaker. `VendorUnavailable` is not stored. The
   trip ID is the provider's idempotency key, so a retry never charges twice.
3. Store the result together with its event (conditional write).
4. Answer the saga, which may have given up already: that answer is ignored.
5. Publish the stored event and clear it. A retry publishes the same event, same message_id.

The task token and the payment token are credentials: never logged, never stored, never
published.
"""

import json
import logging
import os
from datetime import datetime, timezone
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError
from circuit_breaker import Breaker, CircuitOpen, Config, VendorFailure
from event_envelope import check_supported, new_event, to_entry

from payment_service import charge, vendor
from payment_service.charge import Answer

SERVICE = "payment-service"
VENDOR = "payment"
HANDLES = (("ChargePayment", 1),)
VENDOR_UNAVAILABLE = (CircuitOpen, VendorFailure)
TASK_GONE = ("TaskTimedOut", "InvalidToken", "TaskDoesNotExist")

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


@lru_cache(maxsize=1)
def _aws():
    dynamodb = boto3.resource("dynamodb")
    return {
        "table": dynamodb.Table(os.environ["TABLE_NAME"]),
        "sfn": boto3.client("stepfunctions"),
        "events": boto3.client("events"),
        "breaker": Breaker(
            dynamodb.Table(os.environ["BREAKER_TABLE_NAME"]),
            VENDOR,
            Config.from_env(),
            on_change=_announce_breaker_change,
            on_store_error=_log_breaker_store_error,
        ),
    }


def _log(level: int, message: str, **fields) -> None:
    logger.log(level, json.dumps({"message": message, "service": SERVICE, **fields}))


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch_now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _put_event(event: dict) -> None:
    response = _aws()["events"].put_events(Entries=[to_entry(event, os.environ["EVENT_BUS_NAME"])])
    if response["FailedEntryCount"]:
        raise RuntimeError(f"could not publish {event['message_type']}")
    _log(
        logging.INFO,
        "event published",
        correlation_id=event["correlation_id"],
        message_type=event["message_type"],
        message_id=event["message_id"],
    )


def _announce_breaker_change(change: str, failure_count: int) -> None:
    correlation_id = f"vendor:{VENDOR}"
    if change == "opened":
        event = new_event(
            "CircuitOpened",
            correlation_id,
            SERVICE,
            {"vendor": VENDOR, "failure_count": failure_count, "opened_at": _utc_now()},
        )
    else:
        event = new_event("CircuitClosed", correlation_id, SERVICE, {"vendor": VENDOR, "closed_at": _utc_now()})

    _log(logging.WARNING, f"circuit {change}", correlation_id=correlation_id)
    try:
        _put_event(event)
    except Exception as exc:  # a booking must not fail because a breaker event did
        _log(logging.ERROR, "could not announce the circuit change", correlation_id=correlation_id, error=type(exc).__name__)


def _log_breaker_store_error(exc: Exception) -> None:
    _log(logging.WARNING, "breaker store unavailable, failing open", correlation_id=f"vendor:{VENDOR}", error=type(exc).__name__)


def _stored(trip_id: str) -> dict | None:
    return _aws()["table"].get_item(Key={"trip_id": trip_id}, ConsistentRead=True).get("Item")


def _answer(token: str, answer: Answer, correlation_id: str) -> None:
    try:
        if answer.error:
            _aws()["sfn"].send_task_failure(taskToken=token, error=answer.error, cause=answer.error)
        else:
            _aws()["sfn"].send_task_success(taskToken=token, output=json.dumps(answer.output))
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in TASK_GONE:
            raise
        _log(logging.INFO, "the saga no longer waits for this answer", correlation_id=correlation_id)


def _publish_pending(item: dict) -> None:
    pending = item.get("pending_event")
    if not pending:
        return
    _put_event(json.loads(pending))
    _aws()["table"].update_item(Key={"trip_id": item["trip_id"]}, UpdateExpression="REMOVE pending_event")


def process(envelope: dict) -> str:
    """Handle one command. Returns what happened, for logs and tests."""
    check_supported(envelope, *HANDLES)
    trip_id = envelope["correlation_id"]
    token = envelope["task_token"]
    item = _stored(trip_id)
    outcome = "duplicate"

    if item is None:
        data = envelope["data"]
        try:
            result, decline_code = _aws()["breaker"].call(
                lambda: vendor.charge(trip_id, data["payment_token"], data["amount"], data["currency"])
            )
        except VENDOR_UNAVAILABLE as exc:
            _log(logging.WARNING, "payment provider unavailable", correlation_id=trip_id, error=type(exc).__name__)
            _answer(token, Answer(error="VendorUnavailable"), trip_id)
            return "vendor_unavailable"

        if result is None:
            item = charge.declined_item(trip_id, decline_code, _epoch_now())
        else:
            item = charge.charged_item(trip_id, result, _epoch_now())
        event = new_event(charge.event_type(item), trip_id, SERVICE, charge.event_data(item))
        item["pending_event"] = json.dumps(event)

        try:
            _aws()["table"].put_item(Item=item, ConditionExpression="attribute_not_exists(trip_id)")
            outcome = item["status"].lower()
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            item = _stored(trip_id)  # another delivery got there first, with the same charge

    _answer(token, charge.charge_answer(item), trip_id)
    _publish_pending(item)
    return outcome


def handler(event: dict, context: object) -> dict:
    failures = []
    for record in event.get("Records", []):
        correlation_id = None
        try:
            envelope = json.loads(record["body"])
            correlation_id = envelope.get("correlation_id")
            outcome = process(envelope)
            _log(
                logging.INFO,
                f"{envelope['message_type']}: {outcome}",
                correlation_id=correlation_id,
                message_id=envelope.get("message_id"),
            )
        except Exception as exc:
            _log(
                logging.ERROR,
                "processing failed",
                correlation_id=correlation_id,
                sqs_message_id=record.get("messageId"),
                error=type(exc).__name__,  # not the text: it could quote the message
            )
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
