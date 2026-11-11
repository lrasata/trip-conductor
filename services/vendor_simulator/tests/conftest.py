import json

import boto3
import pytest
from moto import mock_aws

from vendor_simulator_service import handler as handler_module

PREFIX = "/trip-conductor/test/chaos/"


@pytest.fixture
def aws(monkeypatch):
    """Fake AWS (moto): a chaos parameter per vendor, with no chaos set."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("CHAOS_PARAMETER_PREFIX", PREFIX)

    with mock_aws():
        ssm = boto3.client("ssm")
        handler_module._aws.cache_clear()
        handler_module._chaos_cache.clear()

        def set_chaos(vendor="airline", **settings):
            ssm.put_parameter(Name=PREFIX + vendor, Type="String", Overwrite=True, Value=json.dumps(settings))
            handler_module._chaos_cache.clear()

        for vendor in ("airline", "hotel", "payment"):
            set_chaos(vendor, failure_rate=0, latency_ms=0)
        yield set_chaos

    handler_module._aws.cache_clear()


def place_hold_request(offer_id="fl_lis_weekend", passengers=2, reference="trp_123"):
    return {
        "routeKey": "POST /airline/holds",
        "headers": {"idempotency-key": reference} if reference else {},
        "body": json.dumps({"offer_id": offer_id, "passengers": passengers}),
    }


def room_hold_request(offer_id="ht_lis_centre", check_in="2026-12-04", check_out="2026-12-06", guests=2):
    return {
        "routeKey": "POST /hotel/holds",
        "headers": {"idempotency-key": "trp_123"},
        "body": json.dumps({"offer_id": offer_id, "check_in": check_in, "check_out": check_out, "guests": guests}),
    }


def charge_request(payment_token="tok_visa", amount=61800, currency="EUR", reference="trp_123"):
    return {
        "routeKey": "POST /payment/charges",
        "headers": {"idempotency-key": reference} if reference else {},
        "body": json.dumps({"payment_token": payment_token, "amount": amount, "currency": currency}),
    }


def confirm_request(vendor="airline", reference="trp_123"):
    return {"routeKey": f"POST /{vendor}/holds/{{reference}}/confirm", "pathParameters": {"reference": reference}}
