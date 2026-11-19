import json
import uuid

import boto3
import pytest
from moto import mock_aws

from projector_service import handler as handler_module

TABLE = "trip-conductor-test-trip-view"
TRIP = "trp_123"

# The data of each event, as the catalog defines it (docs/events/catalog.md).
DATA = {
    "TripRequested": {
        "customer_id": "cus_demo",
        "flight_offer_id": "fl_lis_weekend",
        "hotel_offer_id": "ht_lis_centre",
        "requested_at": "2026-11-18T10:00:00.000Z",
    },
    "FlightReserved": {"reservation_id": "fr_1", "hold_expires_at": "2026-11-18T10:15:00Z", "amount": 25800, "currency": "EUR"},
    "HotelReserved": {"reservation_id": "hr_1", "hold_expires_at": "2026-11-18T10:15:00Z", "amount": 36000, "currency": "EUR"},
    "PaymentCharged": {"payment_id": "ch_1", "amount": 61800, "currency": "EUR"},
    "PaymentDeclined": {"decline_code": "card_declined"},
    "FlightConfirmed": {"reservation_id": "fr_1", "booking_reference": "FL-ABC"},
    "HotelConfirmed": {"reservation_id": "hr_1", "booking_reference": "HT-XYZ"},
    "FlightReleased": {"reservation_id": "fr_1", "reason": "PAYMENT_DECLINED"},
    "HotelReleased": {"reservation_id": "hr_1", "reason": "PAYMENT_DECLINED"},
    "TripConfirmed": {
        "customer_id": "cus_demo",
        "flight_booking_reference": "FL-ABC",
        "hotel_booking_reference": "HT-XYZ",
        "total_amount": 61800,
        "currency": "EUR",
    },
    "TripCancelled": {"customer_id": "cus_demo", "reason": "PAYMENT_DECLINED", "failed_step": "ChargePayment"},
    "TripNeedsAttention": {"failed_step": "ConfirmBooking", "error": "VendorUnavailable"},
    "CircuitOpened": {"vendor": "airline", "failure_count": 5, "opened_at": "2026-11-18T10:00:00Z"},
}

# The happy path (D1) and a declined card (D3), in the order the saga produces them.
CONFIRMED = ["TripRequested", "FlightReserved", "HotelReserved", "PaymentCharged", "FlightConfirmed", "HotelConfirmed", "TripConfirmed"]
DECLINED = ["TripRequested", "FlightReserved", "HotelReserved", "PaymentDeclined", "HotelReleased", "FlightReleased", "TripCancelled"]


def event(message_type, second=0, trip_id=TRIP, version=1, **data):
    producer = {"Flight": "flight-service", "Hotel": "hotel-service", "Payment": "payment-service"}.get(
        next((p for p in ("Flight", "Hotel", "Payment") if message_type.startswith(p)), ""), "booking-saga"
    )
    return {
        "message_id": str(uuid.uuid4()),
        "message_kind": "event",
        "message_type": message_type,
        "message_version": version,
        "occurred_at": f"2026-11-18T10:00:{second:02d}.000Z",
        "correlation_id": trip_id,
        "producer": producer,
        "data": {**DATA[message_type], **data},
    }


def story(types, trip_id=TRIP):
    """The events of one trip, one second apart."""
    return [event(message_type, second, trip_id) for second, message_type in enumerate(types)]


def sqs_event(*envelopes):
    """What the projector's queue delivers: the EventBridge event, with the envelope as detail."""
    return {
        "Records": [
            {
                "messageId": f"msg-{i}",
                "body": json.dumps({
                    "version": "0",
                    "id": str(uuid.uuid4()),
                    "detail-type": envelope["message_type"],
                    "source": f"trip-conductor.{envelope['producer']}",
                    "detail": envelope,
                }),
            }
            for i, envelope in enumerate(envelopes)
        ]
    }


def create_view_table():
    """trip-view as Terraform defines it, with the by-customer index."""
    return boto3.resource("dynamodb").create_table(
        TableName=TABLE,
        KeySchema=[{"AttributeName": "trip_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "trip_id", "AttributeType": "S"},
            {"AttributeName": "customer_id", "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[{
            "IndexName": "by-customer",
            "KeySchema": [
                {"AttributeName": "customer_id", "KeyType": "HASH"},
                {"AttributeName": "created_at", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def table(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("TABLE_NAME", TABLE)
    with mock_aws():
        handler_module._aws.cache_clear()
        yield create_view_table()
    handler_module._aws.cache_clear()
