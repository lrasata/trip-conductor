"""A fake hotel provider: its offer catalog and how it answers a hold request. No AWS calls.

There is no state. A hold's ID comes from the caller's reference, so asking twice gives the
same hold, and releasing a hold always succeeds, as with a real idempotent vendor API.
"""

import hashlib
from datetime import date, datetime, timedelta, timezone

HOLD_MINUTES = 15

# Prices are per room and night, in minor units (cents).
OFFERS = {
    "ht_lis_centre": {"price": 12000, "currency": "EUR", "rooms": 25, "max_guests": 4},
    "ht_lis_riverside": {"price": 9500, "currency": "EUR", "rooms": 25, "max_guests": 2},
    "ht_sold_out": {"price": 8000, "currency": "EUR", "rooms": 0, "max_guests": 4},
}


def place_hold(
    reference: str, offer_id: str, check_in: str, check_out: str, guests: int, now: datetime | None = None
) -> dict | None:
    """The hold, or None if the hotel refuses (unknown offer, no room, too many guests).

    Raises ValueError for dates that aren't a stay: that's a bad request, not a refusal.
    """
    nights = (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days
    if nights < 1:
        raise ValueError("check_out must be after check_in")

    offer = OFFERS.get(offer_id)
    if offer is None or offer["rooms"] < 1 or offer["max_guests"] < guests:
        return None

    expires_at = (now or datetime.now(timezone.utc)) + timedelta(minutes=HOLD_MINUTES)
    return {
        "hold_id": "room_" + hashlib.sha256(reference.encode()).hexdigest()[:16],
        "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "amount": offer["price"] * nights,
        "currency": offer["currency"],
    }
