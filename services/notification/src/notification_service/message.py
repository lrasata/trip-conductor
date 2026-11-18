"""The email a customer gets when their trip ends. No AWS calls.

Built from the event alone: booking references and amounts, never card data.
"""

from dataclasses import dataclass

REASONS = {
    "FLIGHT_UNAVAILABLE": "the flight you chose is no longer available",
    "HOTEL_UNAVAILABLE": "the hotel you chose has no room left for your dates",
    "PAYMENT_DECLINED": "your card was declined",
    "VENDOR_UNAVAILABLE": "one of our partners could not be reached",
    "TIMEOUT": "one of our partners took too long to answer",
}


@dataclass(frozen=True)
class Email:
    subject: str
    text: str


def _money(cents: int, currency: str) -> str:
    return f"{cents // 100}.{cents % 100:02d} {currency}"


def confirmed(trip_id: str, data: dict) -> Email:
    return Email(
        subject="Your trip is booked",
        text=(
            "Good news: your trip is booked.\n\n"
            f"Flight booking reference: {data['flight_booking_reference']}\n"
            f"Hotel booking reference: {data['hotel_booking_reference']}\n"
            f"Charged: {_money(int(data['total_amount']), data['currency'])}\n\n"
            f"Trip: {trip_id}\n"
        ),
    )


def cancelled(trip_id: str, data: dict) -> Email:
    reason = REASONS.get(data["reason"], "something went wrong while booking")
    return Email(
        subject="Your trip could not be booked",
        text=(
            f"We could not book your trip: {reason}.\n\n"
            # No promise about the card: a charge that never got an answer may have gone
            # through (see services/payment, Limits).
            "Nothing was booked, and anything we held for you has been released.\n\n"
            f"Trip: {trip_id}\n"
        ),
    )


def build(message_type: str, trip_id: str, data: dict) -> Email:
    return confirmed(trip_id, data) if message_type == "TripConfirmed" else cancelled(trip_id, data)
