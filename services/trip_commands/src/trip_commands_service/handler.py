"""Lambda entry point for POST /trips (API Gateway REST API, Lambda proxy).

1. Validate the body and the Idempotency-Key header, and derive the trip ID from the
   customer and the key.
2. Start the booking saga with the trip ID as the execution name.
3. Answer 202 with the trip ID and where to poll for it.

A repeated request (same customer, same key) maps to the same execution name, which Step
Functions refuses to start twice: the client gets the same trip back and no second booking
starts (requirement F2). That holds even for a repeat with a different body, so a client
can't change a booking by resending it.

The payment token goes into the saga's input and nowhere else: never logged, never answered.
"""

import base64
import json
import logging
import os
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

from trip_commands_service import request

SERVICE = "trip-commands"

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


@lru_cache(maxsize=1)
def _aws():
    return {"sfn": boto3.client("stepfunctions")}


def _log(level: int, message: str, **fields) -> None:
    logger.log(level, json.dumps({"message": message, "service": SERVICE, **fields}))


def _response(status: int, body: dict, headers: dict | None = None) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **(headers or {})},
        "body": json.dumps(body),
    }


def _header(event: dict, name: str) -> str | None:
    # Header names are case-insensitive; API Gateway passes them as the client sent them.
    for key, value in (event.get("headers") or {}).items():
        if key.lower() == name.lower():
            return value
    return None


def _body(event: dict) -> object:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise request.ValidationError(["body must be valid JSON"]) from exc


def _location(event: dict, trip_id: str) -> str:
    # requestContext.path includes the stage (/v1/trips), so the link works as given.
    path = (event.get("requestContext") or {}).get("path") or "/trips"
    return f"{path.rstrip('/')}/{trip_id}"


def _start(booking: request.BookingRequest) -> str:
    try:
        _aws()["sfn"].start_execution(
            stateMachineArn=os.environ["STATE_MACHINE_ARN"],
            name=booking.trip_id,
            input=json.dumps(booking.saga_input),
        )
        return "started"
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ExecutionAlreadyExists":
            raise
        return "duplicate"


def handler(event: dict, context: object) -> dict:
    try:
        booking = request.parse(_body(event), _header(event, "Idempotency-Key"))
    except request.ValidationError as exc:
        _log(logging.INFO, "request rejected", errors=exc.errors)
        return _response(400, {"error": "validation_failed", "details": exc.errors})

    try:
        outcome = _start(booking)
    except Exception as exc:
        _log(logging.ERROR, "could not start the booking", correlation_id=booking.trip_id, error=type(exc).__name__)
        return _response(500, {"error": "internal_error"})

    _log(
        logging.INFO,
        f"booking {outcome}",
        correlation_id=booking.trip_id,
        customer_id=booking.saga_input["customer_id"],
    )
    location = _location(event, booking.trip_id)
    # No status: for a repeat the booking may be long finished. GET the location for it.
    return _response(202, {"trip_id": booking.trip_id, "location": location}, {"Location": location})
