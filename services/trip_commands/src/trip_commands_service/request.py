"""What a booking request must look like, and the trip ID it gets. No AWS calls.

The saga's input is rebuilt from the validated fields only, so nothing a client adds to the
body ever reaches the saga.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import date

ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
PAYMENT_TOKEN = re.compile(r"^tok_[A-Za-z0-9_]{1,64}$")
MAX_PEOPLE = 9


class ValidationError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class BookingRequest:
    trip_id: str
    saga_input: dict


def trip_id(customer_id: str, idempotency_key: str) -> str:
    """The same customer and key always give the same trip (requirement F2).

    It becomes the execution name: at most 80 letters, digits, - and _.
    """
    digest = hashlib.sha256(f"{customer_id}\n{idempotency_key}".encode("utf-8")).hexdigest()
    return f"trp_{digest[:32]}"


def _text(source: dict, field: str, pattern: re.Pattern, errors: list[str], label: str) -> str | None:
    value = source.get(field)
    if not isinstance(value, str) or not pattern.match(value):
        errors.append(f"{label} is missing or invalid")
        return None
    return value


def _count(source: dict, field: str, errors: list[str], label: str) -> int | None:
    value = source.get(field)
    # bool is an int in Python: refuse "passengers": true
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_PEOPLE:
        errors.append(f"{label} must be a whole number from 1 to {MAX_PEOPLE}")
        return None
    return value


def _date(source: dict, field: str, errors: list[str], label: str) -> date | None:
    value = source.get(field)
    if isinstance(value, str) and len(value) == 10:  # fromisoformat would also take 20261204
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    errors.append(f"{label} must be a date (YYYY-MM-DD)")
    return None


def _object(body: dict, field: str, errors: list[str]) -> dict | None:
    value = body.get(field)
    if not isinstance(value, dict):
        errors.append(f"{field} must be an object")
        return None
    return value


def parse(body: object, idempotency_key: str | None) -> BookingRequest:
    if not isinstance(body, dict):
        raise ValidationError(["body must be a JSON object"])

    errors: list[str] = []
    if idempotency_key is None or not IDEMPOTENCY_KEY.match(idempotency_key):
        errors.append("Idempotency-Key header is missing or invalid (1-128 letters, digits, - _ . :)")

    customer_id = _text(body, "customer_id", ID, errors, "customer_id")
    payment_token = _text(body, "payment_token", PAYMENT_TOKEN, errors, "payment_token")

    flight = _object(body, "flight", errors)
    flight_offer = passengers = None
    if flight is not None:
        flight_offer = _text(flight, "offer_id", ID, errors, "flight.offer_id")
        passengers = _count(flight, "passengers", errors, "flight.passengers")

    hotel = _object(body, "hotel", errors)
    hotel_offer = check_in = check_out = guests = None
    if hotel is not None:
        hotel_offer = _text(hotel, "offer_id", ID, errors, "hotel.offer_id")
        check_in = _date(hotel, "check_in", errors, "hotel.check_in")
        check_out = _date(hotel, "check_out", errors, "hotel.check_out")
        guests = _count(hotel, "guests", errors, "hotel.guests")
        if check_in and check_out and check_out <= check_in:
            errors.append("hotel.check_out must be after hotel.check_in")

    if errors:
        raise ValidationError(errors)

    return BookingRequest(
        trip_id=trip_id(customer_id, idempotency_key),
        saga_input={
            "customer_id": customer_id,
            "flight": {"offer_id": flight_offer, "passengers": passengers},
            "hotel": {
                "offer_id": hotel_offer,
                "check_in": check_in.isoformat(),
                "check_out": check_out.isoformat(),
                "guests": guests,
            },
            "payment_token": payment_token,
        },
    )
