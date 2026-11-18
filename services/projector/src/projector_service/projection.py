"""How one event changes a trip's view (ADR 0006). No AWS calls.

Events arrive at least once and in any order, and a rebuild replays all of them. So applying
them must give the same view whatever the order, and applying one twice must change nothing:

* each applied `message_id` is recorded, and an event already recorded is skipped;
* the trip's status only moves forward, and never leaves a terminal status;
* each leg (flight, hotel, payment) is updated on its own, and its status only moves
  forward too: a FlightReserved arriving after FlightReleased doesn't resurrect the hold;
* fields are filled in, never overwritten, so a late event can't replace a newer value;
* the timeline is kept sorted by when things happened, not by when they arrived.
"""

import copy
from datetime import datetime

from event_envelope import check_supported

REQUESTED = "REQUESTED"
TERMINAL = {"CONFIRMED", "CANCELLED", "NEEDS_ATTENTION"}

# event type -> (leg, status the leg moves to)
LEG_EVENTS = {
    "FlightReserved": ("flight", "RESERVED"),
    "FlightConfirmed": ("flight", "CONFIRMED"),
    "FlightReleased": ("flight", "RELEASED"),
    "HotelReserved": ("hotel", "RESERVED"),
    "HotelConfirmed": ("hotel", "CONFIRMED"),
    "HotelReleased": ("hotel", "RELEASED"),
    "PaymentCharged": ("payment", "CHARGED"),
    "PaymentDeclined": ("payment", "DECLINED"),
}
LEG_RANK = {"RESERVED": 1, "CONFIRMED": 2, "RELEASED": 2, "CHARGED": 2, "DECLINED": 2}

# event type -> trip status it ends in
TRIP_ENDS = {"TripConfirmed": "CONFIRMED", "TripCancelled": "CANCELLED", "TripNeedsAttention": "NEEDS_ATTENTION"}

PROJECTED = {"TripRequested", *LEG_EVENTS, *TRIP_ENDS}


class NotATripEvent(Exception):
    """An event the view has no use for, like CircuitOpened. The rule should filter them out."""


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _fill(target: dict, values: dict) -> None:
    for key, value in values.items():
        if value is not None:
            target.setdefault(key, value)


def new_view(trip_id: str) -> dict:
    return {"trip_id": trip_id, "status": REQUESTED, "legs": {}, "timeline": [], "applied_message_ids": [], "version": 0}


def already_applied(view: dict | None, envelope: dict) -> bool:
    return view is not None and envelope["message_id"] in view["applied_message_ids"]


def apply(view: dict | None, envelope: dict) -> dict:
    """Return the view with the event applied. The input view is left untouched."""
    message_type = envelope.get("message_type")
    if message_type not in PROJECTED:
        raise NotATripEvent(message_type)
    check_supported(envelope, (message_type, 1))  # a version we don't know is an error, not a skip

    trip_id = envelope["correlation_id"]
    view = copy.deepcopy(view) if view is not None else new_view(trip_id)
    data = envelope["data"]
    occurred_at = envelope["occurred_at"]

    if message_type == "TripRequested":
        _fill(view, {
            "customer_id": data["customer_id"],
            "created_at": data["requested_at"],
            "flight_offer_id": data["flight_offer_id"],
            "hotel_offer_id": data["hotel_offer_id"],
        })
    elif message_type in LEG_EVENTS:
        leg_name, status = LEG_EVENTS[message_type]
        leg = view["legs"].setdefault(leg_name, {})
        if LEG_RANK[status] > LEG_RANK.get(leg.get("status"), 0):
            leg["status"] = status
        _fill(leg, data)
    else:
        if view["status"] not in TERMINAL:
            view["status"] = TRIP_ENDS[message_type]
        _fill(view, {"customer_id": data.get("customer_id")})
        if message_type == "TripConfirmed":
            _fill(view, {"total_amount": data["total_amount"], "currency": data["currency"]})
            _fill(view["legs"].setdefault("flight", {}), {"booking_reference": data["flight_booking_reference"]})
            _fill(view["legs"].setdefault("hotel", {}), {"booking_reference": data["hotel_booking_reference"]})
        elif message_type == "TripCancelled":
            _fill(view, {"cancellation": {"reason": data["reason"], "failed_step": data["failed_step"]}})
        else:
            _fill(view, {"attention": {"failed_step": data["failed_step"], "error": data["error"]}})

    view["timeline"] = sorted(
        [*view["timeline"], {"type": message_type, "at": occurred_at}],
        key=lambda entry: (_parse_time(entry["at"]), entry["type"]),
    )
    view["applied_message_ids"] = sorted([*view["applied_message_ids"], envelope["message_id"]])
    view["updated_at"] = view["timeline"][-1]["at"]
    view["version"] = int(view["version"]) + 1
    return view
