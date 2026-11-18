import json
import uuid
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from notification_service import handler as handler_module

CUSTOMER_EMAIL = "traveller-must-never-be-logged@example.com"
SENDER = "ops@example.com"

DATA = {
    "TripConfirmed": {
        "customer_id": "cus_demo",
        "flight_booking_reference": "FL-ABC",
        "hotel_booking_reference": "HT-XYZ",
        "total_amount": 61800,
        "currency": "EUR",
    },
    "TripCancelled": {"customer_id": "cus_demo", "reason": "HOTEL_UNAVAILABLE", "failed_step": "ReserveHotel"},
    "TripNeedsAttention": {"failed_step": "ConfirmBooking", "error": "VendorUnavailable"},
}


def trip_event(message_type="TripConfirmed", trip_id="trp_123", version=1, **data):
    return {
        "message_id": str(uuid.uuid4()),
        "message_kind": "event",
        "message_type": message_type,
        "message_version": version,
        "occurred_at": "2026-11-18T10:00:06.000Z",
        "correlation_id": trip_id,
        "producer": "booking-saga",
        "data": {**DATA[message_type], **data},
    }


def sqs_event(*envelopes):
    return {
        "Records": [
            {
                "messageId": f"msg-{i}",
                "body": json.dumps({"detail-type": envelope["message_type"], "source": "trip-conductor.booking-saga", "detail": envelope}),
            }
            for i, envelope in enumerate(envelopes)
        ]
    }


def create_table(name, key):
    return boto3.resource("dynamodb").create_table(
        TableName=name,
        KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def aws(monkeypatch):
    """Fake DynamoDB (moto) and a recording SES client."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("TABLE_NAME", "trip-conductor-test-notifications")
    monkeypatch.setenv("CONTACTS_TABLE_NAME", "trip-conductor-test-customer-contacts")
    monkeypatch.setenv("SENDER_EMAIL", SENDER)

    with mock_aws():
        notifications = create_table("trip-conductor-test-notifications", "trip_id")
        contacts = create_table("trip-conductor-test-customer-contacts", "customer_id")
        contacts.put_item(Item={"customer_id": "cus_demo", "email": CUSTOMER_EMAIL})
        ses = MagicMock()
        ses.send_email.return_value = {"MessageId": "ses-1"}

        handler_module._aws.cache_clear()
        monkeypatch.setattr(handler_module.boto3, "client", lambda service: {"ses": ses}[service])
        yield {"notifications": notifications, "contacts": contacts, "ses": ses}

    handler_module._aws.cache_clear()
