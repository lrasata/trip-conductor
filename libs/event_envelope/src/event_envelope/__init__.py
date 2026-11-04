"""The message envelope (docs/events/envelope.schema.json), shared by every service.

Only events are built here: commands are built by the state machine.
"""

import json
import uuid
from datetime import datetime, timezone


class UnsupportedMessage(Exception):
    """Wrong message type, or a version this service doesn't understand."""


def new_event(message_type: str, correlation_id: str, producer: str, data: dict, version: int = 1) -> dict:
    return {
        "message_id": str(uuid.uuid4()),
        "message_kind": "event",
        "message_type": message_type,
        "message_version": version,
        # Milliseconds, because the read model sorts a trip's timeline on this.
        "occurred_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "correlation_id": correlation_id,
        "producer": producer,
        "data": data,
    }


def to_entry(event: dict, bus_name: str) -> dict:
    """The event as an EventBridge PutEvents entry (see catalog.md)."""
    return {
        "EventBusName": bus_name,
        "Source": f"trip-conductor.{event['producer']}",
        "DetailType": event["message_type"],
        "Detail": json.dumps(event),
    }


def check_supported(envelope: dict, *accepted: tuple[str, int]) -> None:
    """Reject anything but the given (message_type, message_version) pairs."""
    received = (envelope.get("message_type"), envelope.get("message_version"))
    if received not in accepted:
        raise UnsupportedMessage(f"{received[0]} v{received[1]}")
