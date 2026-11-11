"""A fake payment provider: how it answers a charge. No AWS calls.

There is no state. A charge's ID comes from the caller's reference, so charging twice with the
same reference gives the same charge, as with a real idempotent payment API. Test tokens
decide the outcome, like a real provider's sandbox.
"""

import hashlib

# token -> decline code, or None to accept
TEST_TOKENS = {
    "tok_visa": None,
    "tok_mastercard": None,
    "tok_declined": "card_declined",
    "tok_insufficient_funds": "insufficient_funds",
}


def charge(reference: str, payment_token: str, amount: int, currency: str) -> tuple[dict | None, str | None]:
    """(the charge, None), or (None, the decline code).

    Raises ValueError for an amount or currency that can't be charged: that's a bad request.
    """
    if amount < 1 or len(currency) != 3:
        raise ValueError("invalid amount or currency")

    decline_code = TEST_TOKENS.get(payment_token, "invalid_token")
    if decline_code:
        return None, decline_code
    return {
        "charge_id": "ch_" + hashlib.sha256(reference.encode()).hexdigest()[:16],
        "amount": amount,
        "currency": currency,
    }, None
