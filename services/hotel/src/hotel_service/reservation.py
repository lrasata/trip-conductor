"""What a reservation looks like and how a stored one answers a command. No AWS calls.

One item per trip, keyed on the trip ID. Only final results are stored: a vendor failure
isn't one, so the saga's next retry really tries the vendor again.
"""

from dataclasses import dataclass

HELD = "HELD"
UNAVAILABLE = "UNAVAILABLE"
RELEASING = "RELEASING"  # marked before calling the vendor, so a late ReserveHotel can see it
RELEASED = "RELEASED"
CONFIRMED = "CONFIRMED"  # after the payment: never released

RECORD_TTL_SECONDS = 86400  # outlives every redelivery; the hold itself expires at the vendor


@dataclass(frozen=True)
class Answer:
    output: dict | None = None
    error: str | None = None


def held_item(trip_id: str, hold: dict, now: int) -> dict:
    return {
        "trip_id": trip_id,
        "status": HELD,
        "reservation_id": hold["reservation_id"],
        "hold_expires_at": hold["hold_expires_at"],
        "amount": hold["amount"],
        "currency": hold["currency"],
        "expires_at": now + RECORD_TTL_SECONDS,
    }


def unavailable_item(trip_id: str, now: int) -> dict:
    return {"trip_id": trip_id, "status": UNAVAILABLE, "expires_at": now + RECORD_TTL_SECONDS}


def reserved_data(item: dict) -> dict:
    return {
        "reservation_id": item["reservation_id"],
        "hold_expires_at": item["hold_expires_at"],
        "amount": int(item["amount"]),  # DynamoDB returns a Decimal
        "currency": item["currency"],
    }


def reserve_answer(item: dict) -> Answer:
    """Also answers a late command: a released reservation can no longer be held."""
    if item["status"] in (HELD, CONFIRMED):
        return Answer(output=reserved_data(item))
    return Answer(error="HotelUnavailable")
