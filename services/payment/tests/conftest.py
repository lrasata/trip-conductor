import json
import uuid
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from payment_service import handler as handler_module

TOKEN = "task-token-that-must-never-be-logged"
CARD = "tok_card_that_must_never_be_logged"
CHARGE = {"payment_id": "ch_abc", "amount": 61800, "currency": "EUR"}


class FakeVendor:
    """Stands in for payment_service.vendor: scripted answers, and a record of the calls."""

    def __init__(self):
        self.charge_result = CHARGE
        self.decline_code = None
        self.failure = None
        self.charged = []

    def charge(self, trip_id, payment_token, amount, currency):
        self.charged.append((trip_id, payment_token, amount, currency))
        if self.failure:
            raise self.failure
        if self.decline_code:
            return None, self.decline_code
        return self.charge_result, None


def create_table(name, key):
    return boto3.resource("dynamodb").create_table(
        TableName=name,
        KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def aws(monkeypatch):
    """Fake DynamoDB (moto), a scripted payment provider, and recording Step Functions and
    EventBridge clients: moto can't answer a task token."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("TABLE_NAME", "trip-conductor-test-payments")
    monkeypatch.setenv("BREAKER_TABLE_NAME", "trip-conductor-test-circuit-breakers")
    monkeypatch.setenv("EVENT_BUS_NAME", "trip-conductor-test-trip-events")
    monkeypatch.setenv("BREAKER_FAILURE_THRESHOLD", "2")

    fake_vendor = FakeVendor()
    monkeypatch.setattr(handler_module, "vendor", fake_vendor)

    with mock_aws():
        table = create_table("trip-conductor-test-payments", "trip_id")
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


def charge(trip_id="trp_123", token=TOKEN, payment_token=CARD, amount=61800):
    return {
        "message_id": str(uuid.uuid4()),
        "message_kind": "command",
        "message_type": "ChargePayment",
        "message_version": 1,
        "occurred_at": "2026-11-07T10:00:00.000Z",
        "correlation_id": trip_id,
        "producer": "booking-saga",
        "task_token": token,
        "data": {"payment_token": payment_token, "amount": amount, "currency": "EUR"},
    }


def sqs_event(*envelopes):
    return {
        "Records": [
            {"messageId": f"msg-{i}", "body": json.dumps(envelope), "eventSource": "aws:sqs"}
            for i, envelope in enumerate(envelopes)
        ]
    }
