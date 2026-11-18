"""Lambda entry point for GET /trips/{trip_id} and GET /trips?customer_id= (API Gateway REST
API, Lambda proxy). The query side of CQRS: it reads only trip-view, never the services.

The view is updated asynchronously, so a trip booked a moment ago can be a 404 for a
moment: clients poll the Location they got from POST /trips.
"""

import json
import logging
import os
from functools import lru_cache

import boto3
from boto3.dynamodb.conditions import Key

from trip_queries_service import views

SERVICE = "trip-queries"
CUSTOMER_INDEX = "by-customer"

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


@lru_cache(maxsize=1)
def _aws():
    return {"table": boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])}


def _log(level: int, message: str, **fields) -> None:
    logger.log(level, json.dumps({"message": message, "service": SERVICE, **fields}))


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        # A trip's status changes while the client polls: never serve it from a cache.
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body),
    }


def get_trip(trip_id: str) -> dict:
    item = _aws()["table"].get_item(Key={"trip_id": trip_id}).get("Item")
    if item is None:
        return _response(404, {"error": "not_found", "message": "No such trip yet. A trip booked a moment ago appears within seconds."})
    _log(logging.INFO, "trip read", correlation_id=trip_id)
    return _response(200, views.trip(item))


def list_trips(params: dict) -> dict:
    customer_id = views.checked_id(params.get("customer_id"), "customer_id")
    query = {
        "IndexName": CUSTOMER_INDEX,
        "KeyConditionExpression": Key("customer_id").eq(customer_id),
        "ScanIndexForward": False,  # newest first
        "Limit": views.limit(params.get("limit")),
    }
    start = views.decode_next(params.get("next"), customer_id)
    if start:
        query["ExclusiveStartKey"] = start
    page = _aws()["table"].query(**query)
    _log(logging.INFO, "trips listed", correlation_id=None, customer_id=customer_id, count=len(page["Items"]))
    return _response(200, {
        "trips": [views.summary(item) for item in page["Items"]],
        "next": views.encode_next(page.get("LastEvaluatedKey")),
    })


def handler(event: dict, context: object) -> dict:
    path_params = event.get("pathParameters") or {}
    try:
        if "trip_id" in path_params:
            return get_trip(views.checked_id(path_params["trip_id"], "trip_id"))
        return list_trips(event.get("queryStringParameters") or {})
    except views.BadRequest as exc:
        return _response(400, {"error": "bad_request", "message": str(exc)})
    except Exception as exc:
        _log(logging.ERROR, "query failed", correlation_id=path_params.get("trip_id"), error=type(exc).__name__)
        return _response(500, {"error": "internal_error"})
