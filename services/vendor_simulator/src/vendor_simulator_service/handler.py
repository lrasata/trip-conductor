"""Lambda entry point: the vendors' HTTP API (API Gateway HTTP API).

    POST   /airline/holds              place a seat hold; Idempotency-Key is the caller's reference
    DELETE /airline/holds/{reference}  release the hold placed with that reference
    POST   /hotel/holds                place a room hold
    DELETE /hotel/holds/{reference}

Stands in for the real vendors and can be told to misbehave, one vendor at a time, with
`scripts/chaos.py`, which writes that vendor's settings to an SSM parameter. They are cached
for a few seconds, so a busy simulator doesn't turn into a busy SSM.
"""

import json
import logging
import os
import random
import time
from functools import lru_cache

import boto3

from vendor_simulator_service import airline, hotel

SERVICE = "vendor-simulator"
CHAOS_CACHE_SECONDS = 5
NO_CHAOS = {"failure_rate": 0, "latency_ms": 0}

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

_chaos_cache: dict[str, tuple[float, dict]] = {}  # vendor -> (expires at, settings)


@lru_cache(maxsize=1)
def _aws():
    return {"ssm": boto3.client("ssm")}


def _log(level: int, message: str, **fields) -> None:
    logger.log(level, json.dumps({"message": message, "service": SERVICE, **fields}))


def _chaos_settings(vendor: str) -> dict:
    cached = _chaos_cache.get(vendor)
    if cached and time.monotonic() < cached[0]:
        return cached[1]

    try:
        name = os.environ["CHAOS_PARAMETER_PREFIX"] + vendor
        value = _aws()["ssm"].get_parameter(Name=name)["Parameter"]["Value"]
        settings = {**NO_CHAOS, **json.loads(value)}
    except Exception as exc:
        # A simulator without chaos settings should still answer.
        _log(logging.WARNING, "chaos settings unavailable", vendor=vendor, error=type(exc).__name__)
        settings = NO_CHAOS

    _chaos_cache[vendor] = (time.monotonic() + CHAOS_CACHE_SECONDS, settings)
    return settings


def _response(status: int, body: dict | None = None) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body) if body is not None else "",
    }


def _airline_hold(reference: str, request: dict) -> dict | None:
    return airline.place_hold(reference, request["offer_id"], int(request["passengers"]))


def _hotel_hold(reference: str, request: dict) -> dict | None:
    return hotel.place_hold(
        reference, request["offer_id"], request["check_in"], request["check_out"], int(request["guests"])
    )


PLACE_HOLD = {"airline": _airline_hold, "hotel": _hotel_hold}


def _place_hold(vendor: str, event: dict) -> dict:
    reference = event.get("headers", {}).get("idempotency-key")
    if not reference:
        return _response(400, {"error": "missing_idempotency_key"})

    try:
        request = json.loads(event.get("body") or "")
        hold = PLACE_HOLD[vendor](reference, request)
    except (ValueError, KeyError, TypeError):
        return _response(400, {"error": "invalid_request"})

    if hold is None:
        _log(logging.INFO, "hold refused", vendor=vendor, correlation_id=reference, offer_id=request["offer_id"])
        return _response(409, {"error": "unavailable"})

    _log(logging.INFO, "hold placed", vendor=vendor, correlation_id=reference, hold_id=hold["hold_id"])
    return _response(201, hold)


def handler(event: dict, context: object) -> dict:
    method, _, path = event.get("routeKey", "").partition(" ")
    vendor = path.split("/")[1] if path.count("/") > 1 else ""
    if vendor not in PLACE_HOLD:
        return _response(404, {"error": "not_found"})

    chaos = _chaos_settings(vendor)
    if chaos["latency_ms"]:
        time.sleep(chaos["latency_ms"] / 1000)
    if random.random() < chaos["failure_rate"]:
        return _response(503, {"error": "chaos"})

    if path == f"/{vendor}/holds" and method == "POST":
        return _place_hold(vendor, event)
    if path == f"/{vendor}/holds/{{reference}}" and method == "DELETE":
        reference = event["pathParameters"]["reference"]
        _log(logging.INFO, "hold released", vendor=vendor, correlation_id=reference)
        return _response(204)
    return _response(404, {"error": "not_found"})
