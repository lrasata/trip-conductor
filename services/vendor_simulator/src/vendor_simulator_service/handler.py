"""Lambda entry point: the vendors' HTTP API (API Gateway HTTP API).

    POST   /airline/holds              place a hold; Idempotency-Key is the caller's reference
    DELETE /airline/holds/{reference}  release the hold placed with that reference

Stands in for the real vendors and can be told to misbehave with `scripts/chaos.py`, which
writes the vendor's settings to an SSM parameter. They are cached for a few seconds, so a
busy simulator doesn't turn into a busy SSM.
"""

import json
import logging
import os
import random
import time
from functools import lru_cache

import boto3

from vendor_simulator_service import airline

SERVICE = "vendor-simulator"
CHAOS_CACHE_SECONDS = 5
NO_CHAOS = {"failure_rate": 0, "latency_ms": 0}

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

_chaos_cache = {"expires_at": 0.0, "settings": NO_CHAOS}


@lru_cache(maxsize=1)
def _aws():
    return {"ssm": boto3.client("ssm")}


def _log(level: int, message: str, **fields) -> None:
    logger.log(level, json.dumps({"message": message, "service": SERVICE, **fields}))


def _chaos_settings() -> dict:
    if time.monotonic() < _chaos_cache["expires_at"]:
        return _chaos_cache["settings"]

    try:
        value = _aws()["ssm"].get_parameter(Name=os.environ["CHAOS_PARAMETER"])["Parameter"]["Value"]
        settings = {**NO_CHAOS, **json.loads(value)}
    except Exception as exc:
        # A simulator without chaos settings should still answer.
        _log(logging.WARNING, "chaos settings unavailable", error=type(exc).__name__)
        settings = NO_CHAOS

    _chaos_cache.update(expires_at=time.monotonic() + CHAOS_CACHE_SECONDS, settings=settings)
    return settings


def _response(status: int, body: dict | None = None) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body) if body is not None else "",
    }


def _place_hold(event: dict) -> dict:
    reference = event.get("headers", {}).get("idempotency-key")
    if not reference:
        return _response(400, {"error": "missing_idempotency_key"})

    try:
        request = json.loads(event.get("body") or "")
        hold = airline.place_hold(reference, request["offer_id"], int(request["passengers"]))
    except (ValueError, KeyError, TypeError):
        return _response(400, {"error": "invalid_request"})

    if hold is None:
        _log(logging.INFO, "hold refused", correlation_id=reference, offer_id=request["offer_id"])
        return _response(409, {"error": "unavailable"})

    _log(logging.INFO, "hold placed", correlation_id=reference, hold_id=hold["hold_id"])
    return _response(201, hold)


def handler(event: dict, context: object) -> dict:
    chaos = _chaos_settings()
    if chaos["latency_ms"]:
        time.sleep(chaos["latency_ms"] / 1000)
    if random.random() < chaos["failure_rate"]:
        return _response(503, {"error": "chaos"})

    route = event.get("routeKey")
    if route == "POST /airline/holds":
        return _place_hold(event)
    if route == "DELETE /airline/holds/{reference}":
        _log(logging.INFO, "hold released", correlation_id=event["pathParameters"]["reference"])
        return _response(204)
    return _response(404, {"error": "not_found"})
