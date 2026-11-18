"""What the API shows of a trip's view, and the paging token. No AWS calls.

The stored view also holds the projector's bookkeeping (applied message IDs, version) and the
reason a trip needs attention, which is for operators: none of it is shown.
"""

import base64
import binascii
import json
import re
from decimal import Decimal

ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
DEFAULT_LIMIT = 20
MAX_LIMIT = 50

TRIP_FIELDS = ["trip_id", "customer_id", "status", "created_at", "updated_at", "total_amount", "currency", "cancellation", "legs", "timeline"]
SUMMARY_FIELDS = ["trip_id", "status", "created_at", "updated_at", "total_amount", "currency", "cancellation"]


class BadRequest(Exception):
    pass


def _plain(value):
    """DynamoDB returns numbers as Decimal, which json can't write. Amounts are whole cents."""
    if isinstance(value, Decimal):
        return int(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def trip(item: dict) -> dict:
    return _plain({field: item[field] for field in TRIP_FIELDS if field in item})


def summary(item: dict) -> dict:
    return _plain({field: item[field] for field in SUMMARY_FIELDS if field in item})


def checked_id(value: str | None, name: str) -> str:
    if not value or not ID.match(value):
        raise BadRequest(f"{name} is missing or invalid")
    return value


def limit(value: str | None) -> int:
    if value is None:
        return DEFAULT_LIMIT
    if not value.isdigit() or not 1 <= int(value) <= MAX_LIMIT:
        raise BadRequest(f"limit must be a whole number from 1 to {MAX_LIMIT}")
    return int(value)


def encode_next(last_key: dict | None) -> str | None:
    if not last_key:
        return None
    return base64.urlsafe_b64encode(json.dumps(last_key, sort_keys=True).encode()).decode()


def decode_next(token: str | None, customer_id: str) -> dict | None:
    """The token is the query's LastEvaluatedKey. It must belong to the same customer."""
    if token is None:
        return None
    try:
        key = json.loads(base64.urlsafe_b64decode(token.encode()))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise BadRequest("next is invalid") from exc
    expected = {"trip_id", "customer_id", "created_at"}
    if not isinstance(key, dict) or set(key) != expected or key["customer_id"] != customer_id:
        raise BadRequest("next is invalid")
    if not all(isinstance(value, str) for value in key.values()):
        raise BadRequest("next is invalid")
    return key
