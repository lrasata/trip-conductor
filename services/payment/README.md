# Payment service

Charges the card once both holds are in place. This is the saga's pivot: after a successful charge, the booking only moves forward.

| | |
|---|---|
| **Listens to** | `ChargePayment` on the `payment-commands` queue |
| **Publishes** | `PaymentCharged`, `PaymentDeclined`, `CircuitOpened`, `CircuitClosed` |
| **Owns** | `payments` table, the `payment` item in `circuit-breakers` |

## What it does

Each command is answered with its task token. A redelivered command is answered from the stored charge, without calling the provider again. Only final results are stored: `VendorUnavailable` isn't, so the saga's next retry really tries the provider.

The trip ID is the provider's idempotency key. A retry after a timeout gets the charge that may already have gone through, instead of a second one.

A decline (`PaymentDeclined`) is a business answer: the saga releases the hotel, then the flight. There is nothing to refund, because nothing was charged.

The payment token is the card. It's passed to the provider and nowhere else: never logged, stored or published.

## Code

| File | |
|---|---|
| `handler.py` | The AWS calls: SQS batch, DynamoDB, task tokens, events |
| `charge.py` | What a charge looks like and how it answers a command. No AWS calls |
| `vendor.py` | HTTP calls to the payment provider. Only ever called through the circuit breaker |

## Limits

If the provider takes the money but keeps failing to say so through every retry, the saga cancels the trip with a charge in place. A real system reconciles with the provider's records; that's out of scope here.

## Run the tests

```bash
pip install -r services/payment/requirements-dev.txt
pytest services/payment
```
