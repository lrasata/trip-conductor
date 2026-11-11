"""HTTP calls to the airline. Only ever called through the circuit breaker.

The trip ID is the reference the airline knows a hold by: it makes placing a hold idempotent,
and lets a hold be released even if the answer that named it never arrived.
"""

import http.client
import json
import os
import urllib.error
import urllib.request

from circuit_breaker import VendorFailure

UNAVAILABLE_STATUS = 409
THROTTLED_STATUS = 429  # the vendor is overloaded: a failure, not a refusal


def _request(method: str, path: str, reference: str, body: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(
        os.environ["VENDOR_URL"].rstrip("/") + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json", "idempotency-key": reference},
    )
    try:
        with urllib.request.urlopen(request, timeout=float(os.environ.get("VENDOR_TIMEOUT_SECONDS", "3"))) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        if exc.code >= 500 or exc.code == THROTTLED_STATUS:
            raise VendorFailure(f"airline answered {exc.code}") from exc
        return exc.code, {}
    except (OSError, http.client.HTTPException) as exc:  # timeouts and connection errors
        raise VendorFailure(type(exc).__name__) from exc


def place_hold(trip_id: str, offer_id: str, passengers: int) -> dict | None:
    """The hold, or None if the airline refuses: that's a healthy vendor saying no."""
    status, body = _request("POST", "/airline/holds", trip_id, {"offer_id": offer_id, "passengers": passengers})
    if status == UNAVAILABLE_STATUS:
        return None
    if status != 201:
        raise ValueError(f"unexpected answer from the airline: {status}")
    return {
        "reservation_id": body["hold_id"],
        "hold_expires_at": body["expires_at"],
        "amount": body["amount"],
        "currency": body["currency"],
    }


def confirm_hold(trip_id: str) -> str:
    """The booking reference. Confirming twice gives the same one."""
    status, body = _request("POST", f"/airline/holds/{trip_id}/confirm", trip_id)
    if status != 200:
        raise ValueError(f"unexpected answer from the airline: {status}")
    return body["booking_reference"]


def release_hold(trip_id: str) -> None:
    status, _ = _request("DELETE", f"/airline/holds/{trip_id}", trip_id)
    if status != 204:
        raise ValueError(f"unexpected answer from the airline: {status}")
