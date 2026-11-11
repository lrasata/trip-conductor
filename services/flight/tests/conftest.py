import json
import uuid
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from flight_service import handler as handler_module

TOKEN = "task-token-that-must-never-be-logged"
HOLD = {
    "reservation_id": "hold_abc",
    "hold_expires_at": "2026-11-07T10:15:00Z",
    "amount": 37800,
    "currency": "EUR",
}


class FakeVendor:
    """Stands in for flight_service.vendor: scripted answers, and a record of the calls."""

    def __init__(self):
        self.hold = HOLD
        self.failure = None
        self.placed = []
        self.released = []
        self.confirmed = []
        self.before_answering = lambda: None

    def place_hold(self, trip_id, offer_id, passengers):
        self.placed.append((trip_id, offer_id, passengers))
        self.before_answering()
        if self.failure:
            raise self.failure
        return self.hold

    def confirm_hold(self, trip_id):
        self.confirmed.append(trip_id)
        if self.failure:
            raise self.failure
        return "AI-0A1B2C3D"

    def release_hold(self, trip_id):
        self.released.append(trip_id)
        if self.failure:
            raise self.failure


def create_table(name, key):
    return boto3.resource("dynamodb").create_table(
        TableName=name,
        KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def aws(monkeypatch):
    """Fake DynamoDB (moto), a scripted airline, and recording Step Functions and EventBridge
    clients: moto can't answer a task token."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("TABLE_NAME", "trip-conductor-test-flight-reservations")
    monkeypatch.setenv("BREAKER_TABLE_NAME", "trip-conductor-test-circuit-breakers")
    monkeypatch.setenv("EVENT_BUS_NAME", "trip-conductor-test-trip-events")
    monkeypatch.setenv("BREAKER_FAILURE_THRESHOLD", "2")

    fake_vendor = FakeVendor()
    monkeypatch.setattr(handler_module, "vendor", fake_vendor)

    with mock_aws():
        table = create_table("trip-conductor-test-flight-reservations", "trip_id")
        breakers = create_table("trip-conductor-test-circuit-breakers", "vendor")
        sfn = MagicMock()
        events = MagicMock()
        events.put_events.return_value = {"FailedEntryCount": 0}

        handler_module._aws.cache_clear()
        monkeypatch.setattr(
            handler_module.boto3,
            "client",
            lambda service: {"stepfunctions": sfn, "events": events}[service],
        )

        def published():
            return [
                json.loads(entry["Detail"])
                for call in events.put_events.call_args_list
                for entry in call.kwargs["Entries"]
            ]

        yield {
            "table": table,
            "breakers": breakers,
            "sfn": sfn,
            "events": events,
            "vendor": fake_vendor,
            "published": published,
        }

    handler_module._aws.cache_clear()


def command(message_type, trip_id="trp_123", token=TOKEN, **data):
    return {
        "message_id": str(uuid.uuid4()),
        "message_kind": "command",
        "message_type": message_type,
        "message_version": 1,
        "occurred_at": "2026-11-07T10:00:00.000Z",
        "correlation_id": trip_id,
        "producer": "booking-saga",
        "task_token": token,
        "data": data,
    }


def reserve(trip_id="trp_123", token=TOKEN):
    return command("ReserveFlight", trip_id, token, offer_id="fl_lis_weekend", passengers=2)


def release(trip_id="trp_123", token=TOKEN, reason="HOTEL_UNAVAILABLE"):
    return command("ReleaseFlight", trip_id, token, reason=reason)


def sqs_event(*envelopes):
    return {
        "Records": [
            {"messageId": f"msg-{i}", "body": json.dumps(envelope), "eventSource": "aws:sqs"}
            for i, envelope in enumerate(envelopes)
        ]
    }


def confirm(trip_id="trp_123", token=TOKEN):
    return command("ConfirmFlight", trip_id, token, reservation_id=HOLD["reservation_id"])
