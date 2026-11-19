"""Lambda entry point: SQS queue `projector-events`, fed by an EventBridge rule with every trip
event (not CircuitOpened / CircuitClosed). Also the target of a rebuild's replay.

For each event:

1. Read the trip's view (consistent read).
2. Skip it if its message_id was applied already: a redelivery or a replay.
3. Apply it (projection.py) and write the view back, on condition that nobody else wrote it
   in between (optimistic locking on `version`). If someone did, start again from 1.

The view holds no customer email and no payment token: no event carries them.
"""

import json
import logging
import os
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

from projector_service import projection

SERVICE = "projector"
MAX_ATTEMPTS = 5  # a few events of the same trip can land at once; more is a real problem

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


@lru_cache(maxsize=1)
def _aws():
    return {"table": boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])}


def _log(level: int, message: str, **fields) -> None:
    logger.log(level, json.dumps({"message": message, "service": SERVICE, **fields}))


class ConcurrentUpdates(Exception):
    """The view kept changing under us; SQS will retry the message."""


def _write(view: dict, previous_version: int | None) -> bool:
    if previous_version is None:
        condition = {"ConditionExpression": "attribute_not_exists(trip_id)"}
    else:
        condition = {"ConditionExpression": "version = :version", "ExpressionAttributeValues": {":version": previous_version}}
    try:
        _aws()["table"].put_item(Item=view, **condition)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return False


def process(envelope: dict) -> str:
    """Apply one event. Returns what happened, for logs and tests."""
    if envelope.get("message_type") not in projection.PROJECTED:
        return "ignored"  # not about a trip: nothing to update, nothing to retry

    trip_id = envelope["correlation_id"]
    for _ in range(MAX_ATTEMPTS):
        view = _aws()["table"].get_item(Key={"trip_id": trip_id}, ConsistentRead=True).get("Item")
        if projection.already_applied(view, envelope):
            return "duplicate"
        updated = projection.apply(view, envelope)
        if _write(updated, None if view is None else view["version"]):
            return "applied"
    raise ConcurrentUpdates(trip_id)


def handler(event: dict, context: object) -> dict:
    failures = []
    for record in event.get("Records", []):
        correlation_id = None
        try:
            envelope = json.loads(record["body"])["detail"]  # the EventBridge event wraps the envelope
            correlation_id = envelope.get("correlation_id")
            outcome = process(envelope)
            _log(
                logging.INFO,
                f"{envelope.get('message_type')}: {outcome}",
                correlation_id=correlation_id,
                message_id=envelope.get("message_id"),
            )
        except Exception as exc:
            _log(
                logging.ERROR,
                "processing failed",
                correlation_id=correlation_id,
                sqs_message_id=record.get("messageId"),
                error=type(exc).__name__,
            )
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
