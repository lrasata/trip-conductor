"""Lambda entry point: SQS queue `notification-events`, fed by an EventBridge rule with
TripConfirmed and TripCancelled. Never by a rebuild: replays go to the projector's rule only.

For each event:

1. Record the notification as SENDING (conditional write, one per trip: a trip ends once).
2. Look up the customer's email in customer-contacts (this service's own table, standing in
   for a customer service). No contact: record NO_CONTACT and stop, there's nobody to tell.
3. Send the email with SES, then record SENT with the SES message ID.

A trip already SENT or NO_CONTACT is skipped, so a redelivery never sends a second email. If
the function stops between 3's send and its record, the retry sends again: email is at least
once, the safe side for "your booking is confirmed".

The customer's email is never logged and never leaves this service except to SES.
"""

import json
import logging
import os
from datetime import datetime, timezone
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError
from event_envelope import check_supported

from notification_service import message

SERVICE = "notification"
HANDLES = (("TripConfirmed", 1), ("TripCancelled", 1))
SENDING, SENT, NO_CONTACT = "SENDING", "SENT", "NO_CONTACT"

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


@lru_cache(maxsize=1)
def _aws():
    dynamodb = boto3.resource("dynamodb")
    return {
        "notifications": dynamodb.Table(os.environ["TABLE_NAME"]),
        "contacts": dynamodb.Table(os.environ["CONTACTS_TABLE_NAME"]),
        "ses": boto3.client("ses"),
    }


def _log(level: int, message_text: str, **fields) -> None:
    logger.log(level, json.dumps({"message": message_text, "service": SERVICE, **fields}))


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _claim(trip_id: str, envelope: dict) -> bool:
    """Record the notification as SENDING. False if this trip was dealt with already."""
    try:
        _aws()["notifications"].put_item(
            Item={
                "trip_id": trip_id,
                "message_type": envelope["message_type"],
                "source_message_id": envelope["message_id"],
                "status": SENDING,
                "created_at": _utc_now(),
            },
            ConditionExpression="attribute_not_exists(trip_id) OR #status = :sending",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":sending": SENDING},
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _record(trip_id: str, status: str, **fields) -> None:
    names = {"#status": "status"}
    values = {":status": status, ":at": _utc_now()}
    update = "SET #status = :status, finished_at = :at"
    for field, value in fields.items():
        update += f", {field} = :{field}"
        values[f":{field}"] = value
    _aws()["notifications"].update_item(
        Key={"trip_id": trip_id},
        UpdateExpression=update,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def process(envelope: dict) -> str:
    """Handle one event. Returns what happened, for logs and tests."""
    check_supported(envelope, *HANDLES)
    trip_id = envelope["correlation_id"]
    data = envelope["data"]

    if not _claim(trip_id, envelope):
        return "duplicate"

    contact = _aws()["contacts"].get_item(Key={"customer_id": data["customer_id"]}).get("Item")
    if not contact or not contact.get("email"):
        _record(trip_id, NO_CONTACT)
        _log(logging.WARNING, "no contact for this customer", correlation_id=trip_id, customer_id=data["customer_id"])
        return "no_contact"

    email = message.build(envelope["message_type"], trip_id, data)
    sent = _aws()["ses"].send_email(
        Source=os.environ["SENDER_EMAIL"],
        Destination={"ToAddresses": [contact["email"]]},
        Message={
            "Subject": {"Data": email.subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": email.text, "Charset": "UTF-8"}},
        },
    )
    _record(trip_id, SENT, ses_message_id=sent["MessageId"])
    return "sent"


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
                error=type(exc).__name__,  # not the text: an SES error can quote the address
            )
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
