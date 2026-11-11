"""HTTP calls to the payment provider. Only ever called through the circuit breaker.

The trip ID is the idempotency key: charging the same trip twice gives the same charge, so a
retry after a timeout can't take the money twice.
"""

import http.client
import json
import os
import urllib.error
import urllib.request

from circuit_breaker import VendorFailure

DECLINED_STATUS = 402
THROTTLED_STATUS = 429  # the vendor is overloaded: a failure, not a refusal


def _request(path: str, reference: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        os.environ["VENDOR_URL"].rstrip("/") + path,
        method="POST",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "idempotency-key": reference},
    )
    try:
        with urllib.request.urlopen(request, timeout=float(os.environ.get("VENDOR_TIMEOUT_SECONDS", "3"))) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        if exc.code >= 500 or exc.code == THROTTLED_STATUS:
            raise VendorFailure(f"payment provider answered {exc.code}") from exc
        raw = exc.read()
        return exc.code, json.loads(raw) if raw else {}
    except (OSError, http.client.HTTPException) as exc:  # timeouts and connection errors
        raise VendorFailure(type(exc).__name__) from exc


def charge(trip_id: str, payment_token: str, amount: int, currency: str) -> tuple[dict | None, str | None]:
    """(the charge, None), or (None, the decline code): a decline is a healthy vendor saying no."""
    status, body = _request(
        "/payment/charges", trip_id, {"payment_token": payment_token, "amount": amount, "currency": currency}
    )
    if status == DECLINED_STATUS:
        return None, body.get("decline_code", "declined")
    if status != 201:
        raise ValueError(f"unexpected answer from the payment provider: {status}")
    return {"payment_id": body["charge_id"], "amount": body["amount"], "currency": body["currency"]}, None
