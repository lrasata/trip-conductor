from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

from trip_queries_service import handler as handler_module

TABLE = "trip-conductor-test-trip-view"


def stored_view(trip_id="trp_123", customer_id="cus_demo", created_at="2026-11-18T10:00:00.000Z", status="CONFIRMED"):
    """A trip-view item as the projector writes it, numbers as DynamoDB returns them."""
    return {
        "trip_id": trip_id,
        "customer_id": customer_id,
        "created_at": created_at,
        "updated_at": "2026-11-18T10:00:06.000Z",
        "status": status,
        "total_amount": Decimal(61800),
        "currency": "EUR",
        "flight_offer_id": "fl_lis_weekend",
        "hotel_offer_id": "ht_lis_centre",
        "legs": {
            "flight": {"status": "CONFIRMED", "reservation_id": "fr_1", "amount": Decimal(25800), "currency": "EUR", "booking_reference": "FL-ABC"},
            "hotel": {"status": "CONFIRMED", "reservation_id": "hr_1", "amount": Decimal(36000), "currency": "EUR", "booking_reference": "HT-XYZ"},
            "payment": {"status": "CHARGED", "payment_id": "ch_1", "amount": Decimal(61800), "currency": "EUR"},
        },
        "timeline": [
            {"type": "TripRequested", "at": "2026-11-18T10:00:00.000Z"},
            {"type": "TripConfirmed", "at": "2026-11-18T10:00:06.000Z"},
        ],
        "attention": {"failed_step": "ConfirmBooking", "error": "VendorUnavailable"},
        "applied_message_ids": ["a", "b"],
        "version": Decimal(2),
    }


def api_event(trip_id=None, **query):
    return {
        "httpMethod": "GET",
        "resource": "/trips/{trip_id}" if trip_id else "/trips",
        "pathParameters": {"trip_id": trip_id} if trip_id else None,
        "queryStringParameters": query or None,
        "headers": {},
    }


@pytest.fixture
def table(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("TABLE_NAME", TABLE)
    with mock_aws():
        handler_module._aws.cache_clear()
        yield boto3.resource("dynamodb").create_table(
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
    handler_module._aws.cache_clear()
