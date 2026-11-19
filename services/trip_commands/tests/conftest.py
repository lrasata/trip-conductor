import json
from unittest.mock import MagicMock

import pytest

from trip_commands_service import handler as handler_module

STATE_MACHINE_ARN = "arn:aws:states:eu-central-1:123456789012:stateMachine:trip-conductor-test-booking-saga"
CARD = "tok_card_that_must_never_be_logged"


@pytest.fixture
def sfn(monkeypatch):
    """A recording Step Functions client: the handler's only AWS call is StartExecution."""
    monkeypatch.setenv("STATE_MACHINE_ARN", STATE_MACHINE_ARN)
    client = MagicMock()
    monkeypatch.setattr(handler_module, "_aws", lambda: {"sfn": client})
    return client


def booking(**overrides):
    body = {
        "customer_id": "cus_demo",
        "flight": {"offer_id": "fl_lis_weekend", "passengers": 2},
        "hotel": {"offer_id": "ht_alfama", "check_in": "2099-12-04", "check_out": "2099-12-06", "guests": 2},
        "payment_token": CARD,
    }
    body.update(overrides)
    return body


def api_event(body, idempotency_key="key-001", raw=None):
    headers = {"Content-Type": "application/json"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return {
        "resource": "/trips",
        "path": "/trips",
        "httpMethod": "POST",
        "headers": headers,
        "requestContext": {"path": "/v1/trips", "stage": "v1"},
        "body": raw if raw is not None else json.dumps(body),
        "isBase64Encoded": False,
    }
