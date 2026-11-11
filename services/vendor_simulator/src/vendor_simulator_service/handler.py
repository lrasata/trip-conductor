"""Lambda entry point: the vendors' HTTP API (API Gateway HTTP API).

    POST   /airline/holds                      place a seat hold; Idempotency-Key is the caller's reference
    DELETE /airline/holds/{reference}          release the hold placed with that reference
    POST   /airline/holds/{reference}/confirm  turn the hold into a booking
    POST   /hotel/holds                        the same for rooms
    DELETE /hotel/holds/{reference}
    POST   /hotel/holds/{reference}/confirm
    POST   /payment/charges                    charge a card; Idempotency-Key is the caller's reference

Stands in for the real vendors and can be told to misbehave, one vendor at a time, with
`scripts/chaos.py`, which writes that vendor's settings to an SSM parameter. `operations`
limits the chaos to some calls (hold, release, confirm, charge), e.g. only confirmations. The
settings are cached for a few seconds, so a busy simulator doesn't turn into a busy SSM.
"""

import hashlib
import json
import logging
import os
import random
import time
from functools import lru_cache

import boto3

from vendor_simulator_service import airline, hotel, payment

SERVICE = "vendor-simulator"
CHAOS_CACHE_SECONDS = 5
NO_CHAOS = {"failure_rate": 0, "latency_ms": 0, "operations": None}  # None: every operation

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


def _idempotency_key(event: dict) -> str | None:
    return event.get("headers", {}).get("idempotency-key")


def _place_hold(vendor: str, event: dict) -> dict:
    reference = _idempotency_key(event)
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


def _release_hold(vendor: str, event: dict) -> dict:
    _log(logging.INFO, "hold released", vendor=vendor, correlation_id=event["pathParameters"]["reference"])
    return _response(204)


def _confirm_hold(vendor: str, event: dict) -> dict:
    """Always succeeds: holds aren't stored, so an expired one can't be told apart."""
    reference = event["pathParameters"]["reference"]
    digest = hashlib.sha256(f"{vendor}:{reference}".encode()).hexdigest()[:8].upper()
    booking_reference = f"{vendor[:2].upper()}-{digest}"
    _log(logging.INFO, "hold confirmed", vendor=vendor, correlation_id=reference, booking_reference=booking_reference)
    return _response(200, {"booking_reference": booking_reference})


def _charge(vendor: str, event: dict) -> dict:
    reference = _idempotency_key(event)
    if not reference:
        return _response(400, {"error": "missing_idempotency_key"})

    try:
        request = json.loads(event.get("body") or "")
        charge, decline_code = payment.charge(
            reference, request["payment_token"], int(request["amount"]), request["currency"]
        )
    except (ValueError, KeyError, TypeError):
        return _response(400, {"error": "invalid_request"})

    # The token is the card: it's never logged.
    if charge is None:
        _log(logging.INFO, "charge declined", vendor=vendor, correlation_id=reference, decline_code=decline_code)
        return _response(402, {"error": "declined", "decline_code": decline_code})

    _log(logging.INFO, "charged", vendor=vendor, correlation_id=reference, charge_id=charge["charge_id"])
    return _response(201, charge)


# route -> (vendor, operation, what answers it)
ROUTES = {
    "POST /airline/holds": ("airline", "hold", _place_hold),
    "DELETE /airline/holds/{reference}": ("airline", "release", _release_hold),
    "POST /airline/holds/{reference}/confirm": ("airline", "confirm", _confirm_hold),
    "POST /hotel/holds": ("hotel", "hold", _place_hold),
    "DELETE /hotel/holds/{reference}": ("hotel", "release", _release_hold),
    "POST /hotel/holds/{reference}/confirm": ("hotel", "confirm", _confirm_hold),
    "POST /payment/charges": ("payment", "charge", _charge),
}


def handler(event: dict, context: object) -> dict:
    route = ROUTES.get(event.get("routeKey", ""))
    if route is None:
        return _response(404, {"error": "not_found"})
    vendor, operation, answer = route

    chaos = _chaos_settings(vendor)
    if chaos["operations"] is None or operation in chaos["operations"]:
        if chaos["latency_ms"]:
            time.sleep(chaos["latency_ms"] / 1000)
        if random.random() < chaos["failure_rate"]:
            return _response(503, {"error": "chaos"})

    return answer(vendor, event)
