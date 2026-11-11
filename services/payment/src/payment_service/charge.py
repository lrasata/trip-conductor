"""What a charge looks like and how a stored one answers a command. No AWS calls.

One item per trip, keyed on the trip ID. Only final results are stored: a vendor failure
isn't one, so the saga's next retry really tries the provider again. The payment token is
never stored.
"""

from dataclasses import dataclass

CHARGED = "CHARGED"
DECLINED = "DECLINED"

RECORD_TTL_SECONDS = 86400  # outlives every redelivery


@dataclass(frozen=True)
class Answer:
    output: dict | None = None
    error: str | None = None


def charged_item(trip_id: str, charge: dict, now: int) -> dict:
    return {
        "trip_id": trip_id,
        "status": CHARGED,
        "payment_id": charge["payment_id"],
        "amount": charge["amount"],
        "currency": charge["currency"],
        "expires_at": now + RECORD_TTL_SECONDS,
    }


def declined_item(trip_id: str, decline_code: str, now: int) -> dict:
    return {
        "trip_id": trip_id,
        "status": DECLINED,
        "decline_code": decline_code,
        "expires_at": now + RECORD_TTL_SECONDS,
    }


def event_data(item: dict) -> dict:
    if item["status"] == CHARGED:
        return {
            "payment_id": item["payment_id"],
            "amount": int(item["amount"]),  # DynamoDB returns a Decimal
            "currency": item["currency"],
        }
    return {"decline_code": item["decline_code"]}


def event_type(item: dict) -> str:
    return "PaymentCharged" if item["status"] == CHARGED else "PaymentDeclined"


def charge_answer(item: dict) -> Answer:
    if item["status"] == CHARGED:
        return Answer(output={"payment_id": item["payment_id"]})
    return Answer(error="PaymentDeclined")
