import json

import boto3
import pytest
from moto import mock_aws

from vendor_simulator_service import handler as handler_module

PARAMETER = "/trip-conductor/test/chaos/airline"


@pytest.fixture
def aws(monkeypatch):
    """Fake AWS (moto): the chaos parameter, with no chaos set."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("CHAOS_PARAMETER", PARAMETER)

    with mock_aws():
        ssm = boto3.client("ssm")
        handler_module._aws.cache_clear()
        handler_module._chaos_cache.update(expires_at=0.0, settings=handler_module.NO_CHAOS)

        def set_chaos(**settings):
            ssm.put_parameter(Name=PARAMETER, Type="String", Overwrite=True, Value=json.dumps(settings))
            handler_module._chaos_cache["expires_at"] = 0.0

        set_chaos(failure_rate=0, latency_ms=0)
        yield set_chaos

    handler_module._aws.cache_clear()


def place_hold_request(offer_id="fl_lis_weekend", passengers=2, reference="trp_123"):
    return {
        "routeKey": "POST /airline/holds",
        "headers": {"idempotency-key": reference} if reference else {},
        "body": json.dumps({"offer_id": offer_id, "passengers": passengers}),
    }
