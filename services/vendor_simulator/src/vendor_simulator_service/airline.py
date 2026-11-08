"""A fake airline: its offer catalog and how it answers a hold request. No AWS calls.

There is no state. A hold's ID comes from the caller's reference, so asking twice gives the
same hold, and releasing a hold always succeeds, as with a real idempotent vendor API.
"""

import hashlib
from datetime import datetime, timedelta, timezone

HOLD_MINUTES = 15

# Prices are in minor units (cents).
OFFERS = {
    "fl_lis_weekend": {"price": 18900, "currency": "EUR", "seats": 180},
    "fl_lis_evening": {"price": 15900, "currency": "EUR", "seats": 180},
    "fl_sold_out": {"price": 12900, "currency": "EUR", "seats": 0},
}


def place_hold(reference: str, offer_id: str, passengers: int, now: datetime | None = None) -> dict | None:
    """The hold, or None if the airline refuses (unknown offer or not enough seats)."""
    offer = OFFERS.get(offer_id)
    if offer is None or offer["seats"] < passengers:
        return None

    expires_at = (now or datetime.now(timezone.utc)) + timedelta(minutes=HOLD_MINUTES)
    return {
        "hold_id": "hold_" + hashlib.sha256(reference.encode()).hexdigest()[:16],
        "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "amount": offer["price"] * passengers,
        "currency": offer["currency"],
    }
